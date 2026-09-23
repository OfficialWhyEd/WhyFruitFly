"""Dedicated process that exclusively owns MuJoCo and FlyGym state."""

from __future__ import annotations

import queue
import time
from multiprocessing.queues import Queue
from pathlib import Path
from typing import Any

from .engine import EngineStatus, FlyGymLocomotionEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_PROJECT = "Esplorazione libera"
LIVE_CAMPAIGN = "Live"
LIVE_ESTIMATE_SIM_S = 60.0


def _put_latest(events: Queue, message: dict[str, Any]) -> None:
    # Lossy path for live telemetry only. The queue also transports lossless
    # ack/archive/fatal messages, so do not call this helper for those events.
    # If producers grow, split telemetry and control into separate queues: a
    # full shared queue can otherwise evict whichever message is oldest.
    # When the queue is full the NEW lossy message is dropped instead of evicting
    # the oldest one, which could be an ack or an archive event (review 23/09).
    try:
        events.put_nowait(message)
    except queue.Full:
        pass


class LiveRecording:
    """Opens a run on start and seals it on stop, reset or shutdown."""

    def __init__(self, archive_dir: Path, engine: FlyGymLocomotionEngine, events: Queue, record_every_steps: int) -> None:
        from .archive import Archive

        self.archive = Archive(archive_dir)
        self.engine = engine
        self.events = events
        self.record_every_steps = record_every_steps
        self.next_record = 0
        self.recorder = None
        report = self.archive.recovery_report
        if report["interrupted_runs"] or report["registered_orphans"]:
            events.put({"type": "archive", "event": "recovered", "report": report})
        catalog = self.archive.catalog
        project = catalog.ensure_project(LIVE_PROJECT, "Prove dal vivo lanciate dal telefono")
        campaign = catalog.ensure_campaign(project, LIVE_CAMPAIGN)
        self.config = {
            "controller": "tripod",
            "step_frequency_hz": engine.step_frequency_hz,
            "timestep_s": engine.timestep_s,
            "record_every_steps": record_every_steps,
        }
        self.experiment_id = catalog.ensure_experiment(campaign, "Camminata a tripode", self.config)

    def begin(self) -> None:
        from .archive import RunRecorder, estimate_run_bytes

        if self.recorder is not None:
            return
        self.recorder = RunRecorder.begin(
            catalog=self.archive.catalog,
            store=self.archive.store,
            layout=self.archive.layout,
            experiment_id=self.experiment_id,
            seed=0,
            config=dict(self.config, start_sequence=self.engine.sequence),
            model_metadata=self.engine.metadata().to_dict(),
            # Admission reserve only, not a recording time limit. Long runs
            # continue while RunRecorder keeps checking the free-space margin.
            estimated_bytes=estimate_run_bytes(LIVE_ESTIMATE_SIM_S, self.engine.timestep_s, self.record_every_steps),
            repo=REPO_ROOT,
        )
        self.recorder.record_event("start")
        self.recorder.record_snapshot(self.engine.snapshot().to_dict())
        self.next_record = self.engine.sequence + self.record_every_steps
        self.events.put({"type": "archive", "event": "recording", "run_id": self.recorder.run_id})

    def event(self, kind: str) -> None:
        if self.recorder is not None:
            self.recorder.record_event(kind)

    def snapshot(self, snapshot: dict[str, Any]) -> None:
        if self.recorder is None or snapshot["sequence"] < self.next_record:
            return
        self.next_record = snapshot["sequence"] + self.record_every_steps
        try:
            self.recorder.record_snapshot(snapshot)
        except Exception as exc:
            # Out of space or a disk error: stop the run but keep what was recorded.
            self.engine.pause()
            self.finish(f"registrazione interrotta: {exc}")

    def finish(self, stop_reason: str) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return
        try:
            manifest = recorder.finalize(stop_reason)
            self.events.put(
                {"type": "archive", "event": "sealed", "run_id": recorder.run_id, "manifest_sha256": manifest}
            )
        except Exception as exc:
            self.events.put(
                {"type": "archive", "event": "failed", "run_id": recorder.run_id, "error": f"{type(exc).__name__}: {exc}"}
            )

    def abort(self, error: str) -> None:
        recorder, self.recorder = self.recorder, None
        if recorder is not None:
            recorder.abort(error)

    def close(self) -> None:
        self.archive.close()


def run_engine_worker(
    commands: Queue,
    events: Queue,
    publish_every_steps: int = 250,
    step_batch: int = 25,
    archive_dir: str | None = None,
    record_every_steps: int = 100,
) -> None:
    """Run in a spawned child process. Only serializable values cross queues."""

    engine: FlyGymLocomotionEngine | None = None
    live: LiveRecording | None = None
    try:
        engine = FlyGymLocomotionEngine()
        if archive_dir is not None:
            live = LiveRecording(Path(archive_dir), engine, events, record_every_steps)
        events.put({"type": "metadata", "payload": engine.metadata().to_dict()})
        events.put({"type": "snapshot", "payload": engine.snapshot().to_dict()})
        next_publish = publish_every_steps
        last_heartbeat = time.monotonic()

        while True:
            handled_command = False
            while True:
                try:
                    command = commands.get_nowait()
                except queue.Empty:
                    break
                handled_command = True
                request_id = str(command.get("request_id", ""))
                kind = command.get("type")
                if kind == "shutdown":
                    if live is not None:
                        live.finish("chiusura del laboratorio")
                    events.put({"type": "ack", "request_id": request_id, "command": kind})
                    return
                try:
                    if kind == "start":
                        if live is not None and engine.status is not EngineStatus.RUNNING:
                            live.begin()
                            live.event("resume" if engine.status is EngineStatus.PAUSED else "run")
                        engine.start()
                    elif kind == "pause":
                        engine.pause()
                        if live is not None:
                            live.event("pause")
                    elif kind == "stop":
                        engine.stop()
                        if live is not None:
                            live.finish("arresto")
                    elif kind == "reset":
                        if live is not None:
                            live.finish("reset")
                        engine.reset()
                        next_publish = publish_every_steps
                    else:
                        events.put(
                            {
                                "type": "error",
                                "request_id": request_id,
                                "error": f"unsupported command: {kind}",
                            }
                        )
                        continue
                except Exception as exc:
                    events.put({"type": "error", "request_id": request_id, "error": str(exc)})
                    continue
                events.put(
                    {
                        "type": "ack",
                        "request_id": request_id,
                        "command": kind,
                        "status": engine.status.value,
                    }
                )
                _put_latest(events, {"type": "snapshot", "payload": engine.snapshot().to_dict()})

            if engine.status is EngineStatus.RUNNING:
                snapshot = engine.step(step_batch)
                if live is not None:
                    live.snapshot(snapshot.to_dict())
                if snapshot.sequence >= next_publish:
                    _put_latest(events, {"type": "snapshot", "payload": snapshot.to_dict()})
                    next_publish = snapshot.sequence + publish_every_steps
            else:
                time.sleep(0.01 if handled_command else 0.025)

            now = time.monotonic()
            if now - last_heartbeat >= 1.0:
                _put_latest(
                    events,
                    {
                        "type": "heartbeat",
                        "status": engine.status.value,
                        "wall_time_ns": time.time_ns(),
                        "recording_run_id": live.recorder.run_id if live and live.recorder else None,
                    },
                )
                last_heartbeat = now
    except BaseException as exc:
        if live is not None:
            try:
                live.abort(f"{type(exc).__name__}: {exc}")
            except Exception:
                pass
        try:
            events.put(
                {
                    "type": "fatal",
                    "error": f"{type(exc).__name__}: {exc}",
                    "wall_time_ns": time.time_ns(),
                }
            )
        finally:
            raise
    finally:
        if live is not None:
            live.close()
        if engine is not None:
            engine.close()
