"""Read sealed runs back, verifying every hash and CRC on the way."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pyarrow.parquet as pq
from mcap.reader import make_reader

from .catalog import Catalog
from .recorder import EVENT_TOPIC, MANIFEST_NAME, MCAP_NAME, PARQUET_NAME, SNAPSHOT_TOPIC
from .store import IntegrityError, ObjectStore, sha256_file


def verify_run(catalog: Catalog, store: ObjectStore, run_id: str) -> dict[str, Any]:
    """Recompute SHA-256 of every artifact and check the manifest agrees with the catalog."""
    run = catalog.run(run_id)
    artifacts = {row["name"]: row for row in catalog.artifacts(run_id)}
    for name, row in artifacts.items():
        sha, size = sha256_file(store.path_of(row["relpath"]))
        if sha != row["sha256"] or size != row["size_bytes"]:
            raise IntegrityError(f"{name} of run {run_id} does not match its hash")
    if run["state"] != "sealed":
        return {"run_id": run_id, "state": run["state"], "artifacts": len(artifacts)}
    manifest_row = artifacts.get(MANIFEST_NAME)
    if manifest_row is None or manifest_row["sha256"] != run["manifest_sha256"]:
        raise IntegrityError(f"manifest of run {run_id} does not match the catalog")
    manifest = json.loads(store.path_of(manifest_row["relpath"]).read_text(encoding="utf-8"))
    for name, info in manifest["artifacts"].items():
        if artifacts[name]["sha256"] != info["sha256"]:
            raise IntegrityError(f"manifest lists a different {name} for run {run_id}")
    return {"run_id": run_id, "state": "sealed", "artifacts": len(artifacts), "manifest": manifest}


def iter_telemetry(catalog: Catalog, store: ObjectStore, run_id: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (topic, message) in simulated-time order with CRC validation."""
    verify_run(catalog, store, run_id)
    row = next(r for r in catalog.artifacts(run_id) if r["name"] == MCAP_NAME)
    with open(store.path_of(row["relpath"]), "rb") as handle:
        reader = make_reader(handle, validate_crcs=True)
        for _schema, channel, message in reader.iter_messages(
            topics=[SNAPSHOT_TOPIC, EVENT_TOPIC], log_time_order=True
        ):
            yield channel.topic, json.loads(message.data)


def read_table(catalog: Catalog, store: ObjectStore, run_id: str):
    verify_run(catalog, store, run_id)
    row = next(r for r in catalog.artifacts(run_id) if r["name"] == PARQUET_NAME)
    return pq.read_table(store.path_of(row["relpath"]))


def model_metadata(catalog: Catalog, store: ObjectStore, run_id: str) -> dict[str, Any]:
    verify_run(catalog, store, run_id)
    row = next(r for r in catalog.artifacts(run_id) if r["name"] == MCAP_NAME)
    with open(store.path_of(row["relpath"]), "rb") as handle:
        reader = make_reader(handle, validate_crcs=True)
        for record in reader.iter_metadata():
            if record.name == "fruitfly_lab.model":
                return json.loads(record.metadata["json"])
    return {}
