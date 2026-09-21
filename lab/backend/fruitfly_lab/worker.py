"""Dedicated process that exclusively owns MuJoCo and FlyGym state."""

from __future__ import annotations

import queue
import time
from multiprocessing.queues import Queue
from typing import Any

from .engine import EngineStatus, FlyGymLocomotionEngine


def _put_latest(events: Queue, message: dict[str, Any]) -> None:
    try:
        events.put_nowait(message)
    except queue.Full:
        try:
            events.get_nowait()
        except queue.Empty:
            pass
        events.put_nowait(message)


def run_engine_worker(
    commands: Queue,
    events: Queue,
    publish_every_steps: int = 250,
    step_batch: int = 25,
) -> None:
    """Run in a spawned child process. Only serializable values cross queues."""

    engine: FlyGymLocomotionEngine | None = None
    try:
        engine = FlyGymLocomotionEngine()
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
                    events.put({"type": "ack", "request_id": request_id, "command": kind})
                    return
                if kind == "start":
                    engine.start()
                elif kind == "pause":
                    engine.pause()
                elif kind == "stop":
                    engine.stop()
                elif kind == "reset":
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
                    },
                )
                last_heartbeat = now
    except BaseException as exc:
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
        if engine is not None:
            engine.close()

