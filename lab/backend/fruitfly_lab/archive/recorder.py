"""Run lifecycle: record MCAP + Parquet, seal with a manifest, recover after crashes."""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer

from ..protocol import PROTOCOL_VERSION
from .catalog import Catalog, canonical_json, now_ns
from .store import ObjectStore, read_journal, sha256_file

SNAPSHOT_TOPIC = "/fly/snapshot"
EVENT_TOPIC = "/lab/event"
MCAP_NAME = "telemetry.mcap"
PARQUET_NAME = "snapshots.parquet"
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1

GIB = 1024**3
DEFAULT_MARGIN_BYTES = 5 * GIB
# Measured on a real walking run (22/09/2026): ~12.4 kB per snapshot, MCAP + Parquet.
EST_BYTES_PER_SNAPSHOT = 16 * 1024
SPACE_CHECK_EVERY = 500
PARQUET_BATCH = 256

VECTOR_FIELDS = (
    "body_pos_mm",
    "body_quat_wxyz",
    "joint_angle_rad",
    "joint_velocity_rad_s",
    "actuator_force",
    "contact_force_contact_frame",
    "contact_pos_mm_world",
)

PARQUET_SCHEMA = pa.schema(
    [
        ("sequence", pa.int64()),
        ("sim_time_s", pa.float64()),
        ("status", pa.string()),
        ("root_x_mm", pa.float64()),
        ("root_y_mm", pa.float64()),
        ("root_z_mm", pa.float64()),
        ("contacts", pa.int8()),
        *[(name, pa.list_(pa.float32())) for name in VECTOR_FIELDS],
        ("contact_found", pa.list_(pa.bool_())),
    ]
)


class InsufficientSpace(RuntimeError):
    pass


class RecordingError(RuntimeError):
    pass


@dataclass
class ArchiveLayout:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    @property
    def catalog_path(self) -> Path:
        return self.root / "catalog.sqlite"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    def run_staging(self, run_id: str) -> Path:
        return self.staging / run_id


def free_bytes(path: Path) -> int:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def ensure_space(path: Path, needed_bytes: int, margin_bytes: int = DEFAULT_MARGIN_BYTES) -> None:
    free = free_bytes(path)
    if free - needed_bytes < margin_bytes:
        raise InsufficientSpace(
            f"spazio insufficiente: liberi {free / GIB:.1f} GB, servono "
            f"{needed_bytes / GIB:.2f} GB piu' {margin_bytes / GIB:.1f} GB di margine"
        )


def estimate_run_bytes(duration_sim_s: float, timestep_s: float, record_every_steps: int) -> int:
    snapshots = math.ceil(duration_sim_s / (timestep_s * record_every_steps)) + 1
    return snapshots * EST_BYTES_PER_SNAPSHOT


def code_version(repo: Path) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def environment_info() -> dict[str, Any]:
    versions = {}
    for package in ("flygym", "mujoco", "numpy", "mcap", "pyarrow", "fastapi"):
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": versions,
        "protocol_version": PROTOCOL_VERSION,
    }


def _snapshot_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    root = snapshot["body_pos_mm"][0] if snapshot["body_pos_mm"] else [math.nan] * 3
    row = {
        "sequence": int(snapshot["sequence"]),
        "sim_time_s": float(snapshot["sim_time_s"]),
        "status": snapshot["status"],
        "root_x_mm": float(root[0]),
        "root_y_mm": float(root[1]),
        "root_z_mm": float(root[2]),
        "contacts": int(sum(bool(v) for v in snapshot["contact_found"])),
        "contact_found": [bool(v) for v in snapshot["contact_found"]],
    }
    for name in VECTOR_FIELDS:
        value = snapshot[name]
        if value and isinstance(value[0], list):
            value = [x for row_ in value for x in row_]
        row[name] = [float(x) for x in value]
    return row


@dataclass
class RunRecorder:
    """Owns one open run. Lives in the engine process, the single archive writer."""

    catalog: Catalog
    store: ObjectStore
    layout: ArchiveLayout
    run_id: str
    model_metadata: dict[str, Any]
    margin_bytes: int = DEFAULT_MARGIN_BYTES
    snapshots: int = 0
    events: int = 0
    _first: dict[str, Any] | None = None
    _last: dict[str, Any] | None = None
    _rows: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False

    @classmethod
    def begin(
        cls,
        *,
        catalog: Catalog,
        store: ObjectStore,
        layout: ArchiveLayout,
        experiment_id: str,
        seed: int,
        config: dict[str, Any],
        model_metadata: dict[str, Any],
        notes: str = "",
        estimated_bytes: int = 0,
        margin_bytes: int = DEFAULT_MARGIN_BYTES,
        repo: Path | None = None,
    ) -> "RunRecorder":
        ensure_space(layout.root, estimated_bytes, margin_bytes)
        run_id = catalog.create_run(
            experiment_id=experiment_id,
            seed=seed,
            config=config,
            code_version=code_version(repo) if repo else "unknown",
            environment=environment_info(),
            notes=notes,
        )
        recorder = cls(catalog, store, layout, run_id, model_metadata, margin_bytes)
        try:
            recorder._open_writers()
        except BaseException as exc:
            recorder.closed = True
            recorder._close_partial_writers()
            fail_run(catalog, store, layout, run_id, f"apertura fallita: {type(exc).__name__}: {exc}")
            raise
        return recorder

    def _close_partial_writers(self) -> None:
        for name in ("_parquet", "_mcap_handle"):
            handle = getattr(self, name, None)
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    @property
    def staging(self) -> Path:
        return self.layout.run_staging(self.run_id)

    def _open_writers(self) -> None:
        self.staging.mkdir(parents=True, exist_ok=False)
        self._mcap_handle = open(self.staging / MCAP_NAME, "wb")
        self._mcap = Writer(
            self._mcap_handle,
            compression=CompressionType.ZSTD,
            enable_data_crcs=True,
        )
        self._mcap.start(profile="", library="fruitfly-lab")
        run = self.catalog.run(self.run_id)
        self._mcap.add_metadata(
            "fruitfly_lab.run",
            {
                "run_id": self.run_id,
                "seed": str(run["seed"]),
                "config": run["config_json"],
                "code_version": run["code_version"],
                "protocol_version": str(PROTOCOL_VERSION),
            },
        )
        self._mcap.add_metadata(
            "fruitfly_lab.model", {"json": canonical_json(self.model_metadata)}
        )
        snapshot_schema = self._mcap.register_schema(
            name="fruitfly_lab.FlySnapshot",
            encoding="jsonschema",
            data=json.dumps({"type": "object", "title": "FlySnapshot v1"}).encode(),
        )
        event_schema = self._mcap.register_schema(
            name="fruitfly_lab.LabEvent",
            encoding="jsonschema",
            data=json.dumps({"type": "object", "title": "LabEvent v1"}).encode(),
        )
        self._snapshot_channel = self._mcap.register_channel(
            topic=SNAPSHOT_TOPIC, message_encoding="json", schema_id=snapshot_schema
        )
        self._event_channel = self._mcap.register_channel(
            topic=EVENT_TOPIC, message_encoding="json", schema_id=event_schema
        )
        self._parquet = pq.ParquetWriter(
            self.staging / PARQUET_NAME, PARQUET_SCHEMA, compression="zstd"
        )

    def record_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._check_open()
        sim_ns = int(round(float(snapshot["sim_time_s"]) * 1e9))
        self._mcap.add_message(
            channel_id=self._snapshot_channel,
            log_time=sim_ns,
            publish_time=time.time_ns(),
            sequence=int(snapshot["sequence"]) & 0xFFFFFFFF,
            data=json.dumps(snapshot, separators=(",", ":")).encode(),
        )
        self._rows.append(_snapshot_row(snapshot))
        if len(self._rows) >= PARQUET_BATCH:
            self._flush_rows()
        if self._first is None:
            self._first = snapshot
        self._last = snapshot
        self.snapshots += 1
        if self.snapshots % SPACE_CHECK_EVERY == 0:
            ensure_space(self.layout.root, 0, self.margin_bytes)

    def record_event(self, kind: str, payload: dict[str, Any] | None = None, sim_time_s: float | None = None) -> None:
        self._check_open()
        if sim_time_s is None:
            sim_time_s = float(self._last["sim_time_s"]) if self._last else 0.0
        body = {"kind": kind, "wall_time_ns": time.time_ns(), **(payload or {})}
        self._mcap.add_message(
            channel_id=self._event_channel,
            log_time=int(round(sim_time_s * 1e9)),
            publish_time=time.time_ns(),
            sequence=self.events,
            data=json.dumps(body, separators=(",", ":")).encode(),
        )
        self.events += 1

    def _flush_rows(self) -> None:
        if self._rows:
            self._parquet.write_table(pa.Table.from_pylist(self._rows, schema=PARQUET_SCHEMA))
            self._rows.clear()

    def metrics(self) -> dict[str, float]:
        result: dict[str, float] = {"snapshots": float(self.snapshots), "events": float(self.events)}
        if self._first and self._last:
            first_root = self._first["body_pos_mm"][0]
            last_root = self._last["body_pos_mm"][0]
            dx = last_root[0] - first_root[0]
            dy = last_root[1] - first_root[1]
            duration = float(self._last["sim_time_s"]) - float(self._first["sim_time_s"])
            result.update(
                {
                    "sim_duration_s": duration,
                    "final_sim_time_s": float(self._last["sim_time_s"]),
                    "displacement_mm": math.hypot(dx, dy),
                    "displacement_x_mm": dx,
                    "displacement_y_mm": dy,
                    "final_root_z_mm": float(last_root[2]),
                }
            )
            if duration > 0:
                result["mean_speed_mm_s"] = math.hypot(dx, dy) / duration
        return result

    def finalize(self, stop_reason: str) -> str:
        """Close writers, verify, store artifacts and seal. Returns the manifest hash."""
        self._check_open()
        try:
            self.record_event("run_end", {"stop_reason": stop_reason})
            self.closed = True
            try:
                self._close_writers()
            except BaseException:
                self._close_partial_writers()
                raise
            counts = verify_mcap(self.staging / MCAP_NAME)
            if counts[SNAPSHOT_TOPIC] != self.snapshots:
                raise RecordingError(
                    f"MCAP has {counts[SNAPSHOT_TOPIC]} snapshots, expected {self.snapshots}"
                )
            parquet_rows = pq.ParquetFile(self.staging / PARQUET_NAME).metadata.num_rows
            if parquet_rows != self.snapshots:
                raise RecordingError(f"Parquet has {parquet_rows} rows, expected {self.snapshots}")
            metrics = self.metrics()
            with self.catalog.transaction() as db:
                self.catalog.set_state(db, self.run_id, "finalizing", stop_reason=stop_reason)
            return seal_run(
                self.catalog,
                self.store,
                self.layout,
                self.run_id,
                metrics=metrics,
                extra_counts={"mcap_messages": counts},
            )
        except BaseException as exc:
            fail_run(self.catalog, self.store, self.layout, self.run_id, f"{type(exc).__name__}: {exc}")
            raise

    def abort(self, error: str) -> None:
        """Close what can be closed and keep every partial file."""
        if not self.closed:
            try:
                self._close_writers()
            finally:
                self.closed = True
        fail_run(self.catalog, self.store, self.layout, self.run_id, error)

    def _close_writers(self) -> None:
        try:
            self._flush_rows()
            self._parquet.close()
        finally:
            try:
                self._mcap.finish()
            finally:
                self._mcap_handle.flush()
                os.fsync(self._mcap_handle.fileno())
                self._mcap_handle.close()

    def _check_open(self) -> None:
        if self.closed:
            raise RecordingError("run already closed")


def verify_mcap(path: Path) -> dict[str, int]:
    """Read every record with CRC validation. Returns message counts per topic."""
    counts: dict[str, int] = {SNAPSHOT_TOPIC: 0, EVENT_TOPIC: 0}
    with open(path, "rb") as handle:
        reader = make_reader(handle, validate_crcs=True)
        for _schema, channel, message in reader.iter_messages(log_time_order=False):
            json.loads(message.data)
            counts[channel.topic] = counts.get(channel.topic, 0) + 1
    return counts


def _media_type(name: str) -> tuple[str, str]:
    if name.endswith(".mcap"):
        return "telemetry", "application/x-mcap"
    if name.endswith(".parquet"):
        return "table", "application/vnd.apache.parquet"
    if name.endswith(".json"):
        return "manifest", "application/json"
    return "file", "application/octet-stream"


def _store_staging_files(
    catalog: Catalog, store: ObjectStore, layout: ArchiveLayout, run_id: str, names: list[str]
) -> dict[str, dict[str, Any]]:
    staging = layout.run_staging(run_id)
    stored: dict[str, dict[str, Any]] = {}
    # Recover moves that happened before a crash (journal written before os.replace).
    for entry in read_journal(staging):
        name = str(entry["name"])
        if name in names and not (staging / name).exists() and store.verify(str(entry["sha256"])):
            blob = store.register(str(entry["sha256"]), int(entry["size_bytes"]))
            stored[name] = {"sha256": blob.sha256, "size_bytes": blob.size_bytes}
    for name in names:
        if name in stored:
            continue
        path = staging / name
        if path.is_file():
            blob = store.ingest_move(path, journal_dir=staging, name=name)
            stored[name] = {"sha256": blob.sha256, "size_bytes": blob.size_bytes}
    with catalog.transaction() as db:
        existing = {
            row["name"] for row in db.execute("SELECT name FROM artifacts WHERE run_id = ?", (run_id,))
        }
        for name, info in stored.items():
            if name in existing:
                continue
            kind, media_type = _media_type(name)
            db.execute(
                "INSERT INTO artifacts (run_id, name, kind, media_type, sha256, size_bytes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, name, kind, media_type, info["sha256"], info["size_bytes"]),
            )
    return stored


def seal_run(
    catalog: Catalog,
    store: ObjectStore,
    layout: ArchiveLayout,
    run_id: str,
    *,
    metrics: dict[str, float],
    extra_counts: dict[str, Any] | None = None,
) -> str:
    data_files = _store_staging_files(catalog, store, layout, run_id, [MCAP_NAME, PARQUET_NAME])
    run = catalog.run(run_id)
    manifest = {
        "manifest_format": MANIFEST_FORMAT,
        "run_id": run_id,
        "experiment_id": run["experiment_id"],
        "parent_run_id": run["parent_run_id"],
        "seed": run["seed"],
        "config": json.loads(run["config_json"]),
        "code_version": run["code_version"],
        "environment": json.loads(run["environment_json"]),
        "notes": run["notes"],
        "stop_reason": run["stop_reason"],
        "created_ns": run["created_ns"],
        "metrics": metrics,
        "counts": extra_counts or {},
        "artifacts": data_files,
    }
    manifest_path = layout.run_staging(run_id) / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    stored = _store_staging_files(catalog, store, layout, run_id, [MANIFEST_NAME])
    manifest_sha = stored[MANIFEST_NAME]["sha256"]
    with catalog.transaction() as db:
        for key, value in metrics.items():
            db.execute(
                "INSERT INTO run_metrics (run_id, key, value) VALUES (?, ?, ?)",
                (run_id, key, float(value)),
            )
        catalog.set_state(db, run_id, "sealed", manifest_sha256=manifest_sha)
    return manifest_sha


def fail_run(catalog: Catalog, store: ObjectStore, layout: ArchiveLayout, run_id: str, error: str, *, state: str = "failed") -> None:
    """Keep every partial file as an artifact, then close the run with the error."""
    staging = layout.run_staging(run_id)
    names = sorted(p.name for p in staging.iterdir() if p.is_file() and p.name != "ingest.jsonl") if staging.is_dir() else []
    names += [str(e["name"]) for e in read_journal(staging) if str(e["name"]) not in names]
    _store_staging_files(catalog, store, layout, run_id, names)
    with catalog.transaction() as db:
        catalog.set_state(db, run_id, state, error=error[:2000])


def recover(catalog: Catalog, store: ObjectStore, layout: ArchiveLayout) -> dict[str, Any]:
    """Run at startup. Closes runs left open by a crash without deleting anything."""
    report: dict[str, Any] = {"interrupted_runs": [], "registered_orphans": [], "tmp_files": []}
    report["errors"] = {}
    for run in catalog.open_runs():
        try:
            fail_run(
                catalog, store, layout, run["id"],
                "il processo si e' fermato prima della chiusura della run",
                state="interrupted",
            )
            report["interrupted_runs"].append(run["id"])
        except Exception as exc:
            # One locked or damaged run must not keep the whole archive closed; retried next start.
            report["errors"][run["id"]] = f"{type(exc).__name__}: {exc}"
    for path in store.unregistered_objects():
        sha, size = sha256_file(path)
        if sha == path.name:
            store.register(sha, size)
            report["registered_orphans"].append(sha)
    if layout.tmp.is_dir():
        report["tmp_files"] = [p.name for p in layout.tmp.iterdir() if p.is_file()]
    return report
