"""Prototype asynchronous adapter for episodic Fruit Fly connectome decisions.

The worker process is deliberately independent from the MuJoCo engine process.
Only serializable episode requests, selected spikes and behavioral signals cross
the boundary. The real backend imports Brian2 and the Shiu model inside the
worker, never in the caller.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import multiprocessing as mp
import os
import queue
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_MAP_PATH = HERE / "behavior-map-v1.json"
SHIU_ROOT = REPO_ROOT / "cervello" / "modello"
SHIU_MODEL_VERSION = "shiu-91bdd1e7dcf193f3e7ca5a8933497fcef63b7960-flywire630"

ModelKind = Literal["fake", "real"]
AdapterStatus = Literal["ok", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class EpisodeRequest:
    stimulus: str
    duration_s: float = 1.0
    intensity: float = 1.0
    seed: int = 0
    episode_id: str = ""

    def normalized(self) -> "EpisodeRequest":
        if self.duration_s <= 0:
            raise ValueError("duration_s must be positive")
        if not 0 <= self.intensity <= 2:
            raise ValueError("intensity must be between 0 and 2")
        return replace(self, episode_id=self.episode_id or uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class SpikeEvent:
    flywire_id: int
    t_s: float


@dataclass(frozen=True, slots=True)
class BehaviorSignal:
    locomotion: Literal["go", "stop"]
    turn: float
    extend_proboscis: bool


@dataclass(frozen=True, slots=True)
class EpisodeDecision:
    episode_id: str
    status: AdapterStatus
    model: ModelKind
    model_version: str
    map_id: str
    map_sha256: str
    stimulus: str
    duration_s: float
    intensity: float
    seed: int
    spikes: tuple[SpikeEvent, ...]
    rates_hz: dict[str, float]
    behavior: BehaviorSignal
    elapsed_wall_s: float
    peak_worker_tree_rss_bytes: int
    error: str | None = None
    worker_restarted: bool = False

    def to_archive_record(self) -> dict[str, Any]:
        """Lossless JSON-compatible record for MCAP and Parquet projection."""
        return {
            "schema_version": 1,
            "episode_id": self.episode_id,
            "status": self.status,
            "model": self.model,
            "model_version": self.model_version,
            "map_id": self.map_id,
            "map_sha256": self.map_sha256,
            "stimulus": self.stimulus,
            "duration_s": self.duration_s,
            "intensity": self.intensity,
            "seed": self.seed,
            "spikes": [
                {"flywire_id": str(spike.flywire_id), "t_s": spike.t_s}
                for spike in self.spikes
            ],
            "rates_hz": self.rates_hz,
            "decision": asdict(self.behavior),
            "elapsed_wall_s": self.elapsed_wall_s,
            "peak_worker_tree_rss_bytes": self.peak_worker_tree_rss_bytes,
            "error": self.error,
            "worker_restarted": self.worker_restarted,
        }


class BehaviorMapper:
    """Versioned conversion from selected neuron spikes to behavior signals."""

    def __init__(self, path: Path = DEFAULT_MAP_PATH) -> None:
        data = Path(path).read_bytes()
        raw = json.loads(data)
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported behavior map schema")
        self.map_id = str(raw["map_id"])
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.groups = {
            name: tuple(int(value) for value in values)
            for name, values in raw["groups"].items()
        }
        self.thresholds = {
            name: float(value) for name, value in raw["thresholds_hz"].items()
        }
        self.selected_ids = frozenset(
            flywire_id for values in self.groups.values() for flywire_id in values
        )

    def map(
        self, spikes: tuple[SpikeEvent, ...], duration_s: float
    ) -> tuple[dict[str, float], BehaviorSignal]:
        counts: dict[int, int] = {}
        for spike in spikes:
            if spike.flywire_id in self.selected_ids:
                counts[spike.flywire_id] = counts.get(spike.flywire_id, 0) + 1

        def population_rate(name: str) -> float:
            neurons = self.groups[name]
            return sum(counts.get(neuron, 0) for neuron in neurons) / (
                duration_s * len(neurons)
            )

        rates = {name: population_rate(name) for name in self.groups}
        walk_rate = (rates["walk_left"] + rates["walk_right"]) / 2
        halted = rates["halt"] >= self.thresholds["halt_on"]
        locomotion: Literal["go", "stop"] = (
            "go"
            if walk_rate >= self.thresholds["go_on"] and not halted
            else "stop"
        )
        turn = (
            rates["steer_right"] - rates["steer_left"]
        ) / self.thresholds["turn_full_scale"]
        turn = max(-1.0, min(1.0, turn)) if locomotion == "go" else 0.0
        behavior = BehaviorSignal(
            locomotion=locomotion,
            turn=turn,
            extend_proboscis=(
                rates["proboscis_motor"] >= self.thresholds["proboscis_on"]
            ),
        )
        rates["walk"] = walk_rate
        return rates, behavior


class FakeConnectomeModel:
    """Fast deterministic backend with biologically named output populations."""

    def __init__(self, mapper: BehaviorMapper) -> None:
        self.mapper = mapper

    def run(self, episode: EpisodeRequest) -> tuple[SpikeEvent, ...]:
        if episode.stimulus == "slow":
            time.sleep(2.0)
        if episode.stimulus == "fail":
            raise RuntimeError("requested fake backend failure")

        rates: dict[str, float] = {}
        if episode.stimulus == "sugar":
            rates.update(
                {
                    "sugar_right": 150.0,
                    "feeding_interneurons": 12.0,
                    "proboscis_motor": 70.0,
                }
            )
        elif episode.stimulus in {"walk", "turn_left", "turn_right", "halt"}:
            rates.update({"walk_left": 30.0, "walk_right": 30.0})
        elif episode.stimulus != "none":
            raise ValueError(f"unsupported stimulus: {episode.stimulus}")

        if episode.stimulus == "turn_left":
            rates.update({"steer_left": 40.0, "steer_right": 5.0})
        elif episode.stimulus == "turn_right":
            rates.update({"steer_left": 5.0, "steer_right": 40.0})
        elif episode.stimulus == "halt":
            rates["halt"] = 30.0

        spikes: list[SpikeEvent] = []
        for group, rate_hz in rates.items():
            for flywire_id in self.mapper.groups[group]:
                count = int(round(rate_hz * episode.intensity * episode.duration_s))
                for index in range(count):
                    spikes.append(
                        SpikeEvent(
                            flywire_id,
                            (index + 1) * episode.duration_s / (count + 1),
                        )
                    )
        spikes.sort(key=lambda item: (item.t_s, item.flywire_id))
        return tuple(spikes)


class ShiuConnectomeModel:
    """Single-trial adapter around the unchanged Shiu et al. Brian2 model."""

    def __init__(self, mapper: BehaviorMapper) -> None:
        sys.dont_write_bytecode = True
        sys.path.insert(0, str(SHIU_ROOT))
        import numpy as np
        import pandas as pd
        from brian2 import Hz, ms, prefs, seed
        from model import default_params, run_trial

        prefs.codegen.target = "numpy"
        self.np = np
        self.pd = pd
        self.Hz = Hz
        self.ms = ms
        self.seed_brian = seed
        self.default_params = default_params
        self.run_trial = run_trial
        self.mapper = mapper
        self.path_comp = SHIU_ROOT / "2023_03_23_completeness_630_final.csv"
        self.path_con = SHIU_ROOT / "2023_03_23_connectivity_630_final.parquet"
        completeness = pd.read_csv(self.path_comp, index_col=0)
        self.flywire_ids = tuple(int(value) for value in completeness.index)
        self.flywire_to_index = {
            flywire_id: index for index, flywire_id in enumerate(self.flywire_ids)
        }

    def run(self, episode: EpisodeRequest) -> tuple[SpikeEvent, ...]:
        if episode.stimulus != "sugar":
            raise ValueError("real backend v1 supports only the sugar stimulus")
        self.np.random.seed(episode.seed)
        self.seed_brian(episode.seed)
        params = dict(self.default_params)
        params["t_run"] = episode.duration_s * 1000 * self.ms
        params["n_run"] = 1
        params["r_poi"] = 150 * episode.intensity * self.Hz
        excited = [
            self.flywire_to_index[value]
            for value in self.mapper.groups["sugar_right"]
        ]
        trains = self.run_trial(
            excited,
            [],
            [],
            self.path_comp,
            self.path_con,
            params,
        )
        selected: list[SpikeEvent] = []
        for brian_index, times in trains.items():
            flywire_id = self.flywire_ids[int(brian_index)]
            if flywire_id not in self.mapper.selected_ids:
                continue
            selected.extend(
                SpikeEvent(flywire_id, float(value)) for value in times
            )
        selected.sort(key=lambda item: (item.t_s, item.flywire_id))
        return tuple(selected)


def _worker_main(
    requests: Any, responses: Any, model_kind: ModelKind, map_path: str
) -> None:
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    mapper = BehaviorMapper(Path(map_path))
    backend = (
        FakeConnectomeModel(mapper)
        if model_kind == "fake"
        else ShiuConnectomeModel(mapper)
    )
    while True:
        message = requests.get()
        if message is None:
            return
        episode = EpisodeRequest(**message)
        started = time.perf_counter()
        try:
            spikes = backend.run(episode)
            rates, behavior = mapper.map(spikes, episode.duration_s)
            responses.put(
                {
                    "type": "result",
                    "episode_id": episode.episode_id,
                    "spikes": [asdict(item) for item in spikes],
                    "rates_hz": rates,
                    "behavior": asdict(behavior),
                    "elapsed_wall_s": time.perf_counter() - started,
                }
            )
        except BaseException as exc:
            responses.put(
                {
                    "type": "error",
                    "episode_id": episode.episode_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_wall_s": time.perf_counter() - started,
                }
            )


class AsyncConnectomeAdapter:
    """One-request-at-a-time asynchronous facade over a disposable process."""

    def __init__(
        self,
        model: ModelKind = "fake",
        map_path: Path = DEFAULT_MAP_PATH,
        default_timeout_s: float = 5.0,
    ) -> None:
        if model not in {"fake", "real"}:
            raise ValueError("model must be 'fake' or 'real'")
        self.model = model
        self.map_path = Path(map_path).resolve()
        self.mapper = BehaviorMapper(self.map_path)
        self.default_timeout_s = float(default_timeout_s)
        self.generation = 0
        self._lock = asyncio.Lock()
        self._process: mp.Process | None = None
        self._requests: Any = None
        self._responses: Any = None
        self._start_worker()

    @property
    def worker_pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    async def decide(
        self, episode: EpisodeRequest, timeout_s: float | None = None
    ) -> EpisodeDecision:
        episode = episode.normalized()
        timeout = self.default_timeout_s if timeout_s is None else float(timeout_s)
        if timeout <= 0:
            raise ValueError("timeout_s must be positive")
        async with self._lock:
            if self._process is None or not self._process.is_alive():
                self._restart_worker()
            self._requests.put_nowait(asdict(episode))
            deadline = time.monotonic() + timeout
            peak_rss = 0
            while time.monotonic() < deadline:
                peak_rss = max(peak_rss, self._worker_tree_rss())
                try:
                    response = self._responses.get_nowait()
                except queue.Empty:
                    if self._process is None or not self._process.is_alive():
                        return self._safe_failure(
                            episode,
                            "error",
                            "connectome worker exited unexpectedly",
                            peak_rss,
                        )
                    await asyncio.sleep(0.01)
                    continue
                if response.get("episode_id") != episode.episode_id:
                    continue
                peak_rss = max(peak_rss, self._worker_tree_rss())
                if response["type"] == "error":
                    return self._safe_failure(
                        episode,
                        "error",
                        response["error"],
                        peak_rss,
                        elapsed_wall_s=float(response["elapsed_wall_s"]),
                    )
                return EpisodeDecision(
                    episode_id=episode.episode_id,
                    status="ok",
                    model=self.model,
                    model_version=(
                        "fake-v1" if self.model == "fake" else SHIU_MODEL_VERSION
                    ),
                    map_id=self.mapper.map_id,
                    map_sha256=self.mapper.sha256,
                    stimulus=episode.stimulus,
                    duration_s=episode.duration_s,
                    intensity=episode.intensity,
                    seed=episode.seed,
                    spikes=tuple(SpikeEvent(**item) for item in response["spikes"]),
                    rates_hz={
                        key: float(value) for key, value in response["rates_hz"].items()
                    },
                    behavior=BehaviorSignal(**response["behavior"]),
                    elapsed_wall_s=float(response["elapsed_wall_s"]),
                    peak_worker_tree_rss_bytes=peak_rss,
                )
            return self._safe_failure(
                episode,
                "timeout",
                f"connectome episode exceeded {timeout:.3f} s",
                peak_rss,
                elapsed_wall_s=timeout,
            )

    def close(self) -> None:
        self._stop_worker(force=False)

    def _safe_failure(
        self,
        episode: EpisodeRequest,
        status: Literal["timeout", "error"],
        error: str,
        peak_rss: int,
        elapsed_wall_s: float = 0.0,
    ) -> EpisodeDecision:
        self._restart_worker()
        return EpisodeDecision(
            episode_id=episode.episode_id,
            status=status,
            model=self.model,
            model_version=(
                "fake-v1" if self.model == "fake" else SHIU_MODEL_VERSION
            ),
            map_id=self.mapper.map_id,
            map_sha256=self.mapper.sha256,
            stimulus=episode.stimulus,
            duration_s=episode.duration_s,
            intensity=episode.intensity,
            seed=episode.seed,
            spikes=(),
            rates_hz={},
            behavior=BehaviorSignal("stop", 0.0, False),
            elapsed_wall_s=elapsed_wall_s,
            peak_worker_tree_rss_bytes=peak_rss,
            error=error,
            worker_restarted=True,
        )

    def _start_worker(self) -> None:
        context = mp.get_context("spawn")
        self._requests = context.Queue(maxsize=1)
        self._responses = context.Queue(maxsize=1)
        self._process = context.Process(
            target=_worker_main,
            args=(
                self._requests,
                self._responses,
                self.model,
                str(self.map_path),
            ),
            name="fruitfly-connectome",
        )
        self._process.start()
        self.generation += 1

    def _stop_worker(self, force: bool) -> None:
        process = self._process
        if process is None:
            return
        if process.is_alive() and not force:
            try:
                self._requests.put_nowait(None)
            except queue.Full:
                force = True
            if not force:
                process.join(2.0)
        if process.is_alive():
            process.terminate()
            process.join(5.0)
        if process.is_alive():
            process.kill()
            process.join(2.0)
        for endpoint in (self._requests, self._responses):
            try:
                endpoint.close()
                endpoint.join_thread()
            except Exception:
                pass
        self._process = None

    def _restart_worker(self) -> None:
        self._stop_worker(force=True)
        self._start_worker()

    def _worker_tree_rss(self) -> int:
        process = self._process
        if process is None or process.pid is None:
            return 0
        try:
            import psutil

            root = psutil.Process(process.pid)
            return root.memory_info().rss + sum(
                child.memory_info().rss for child in root.children(recursive=True)
            )
        except Exception:
            return 0


async def _run_cli(args: argparse.Namespace) -> int:
    adapter = AsyncConnectomeAdapter(
        model=args.model,
        map_path=args.map,
        default_timeout_s=args.timeout,
    )
    try:
        decision = await adapter.decide(
            EpisodeRequest(
                stimulus=args.stimulus,
                duration_s=args.duration,
                intensity=args.intensity,
                seed=args.seed,
            )
        )
        record = decision.to_archive_record()
        if args.summary:
            record = {
                key: value
                for key, value in record.items()
                if key not in {"spikes"}
            }
            record["selected_spike_count"] = len(decision.spikes)
        print(json.dumps(record, indent=2, sort_keys=True))
        return 0 if decision.status == "ok" else 2
    finally:
        adapter.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("fake", "real"), default="fake")
    parser.add_argument("--stimulus", default="sugar")
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--intensity", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP_PATH)
    parser.add_argument("--summary", action="store_true")
    return asyncio.run(_run_cli(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
