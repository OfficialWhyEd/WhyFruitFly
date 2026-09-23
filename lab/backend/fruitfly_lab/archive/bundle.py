"""Self-contained campaign bundles: export and verified import.

A bundle is a folder with bundle.json (catalog rows) and objects/<sha256>.
Import checks every hash before touching the target catalog and keeps ids,
relations and hashes exactly as they were.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .catalog import Catalog, now_ns
from .recorder import ArchiveLayout, ensure_space
from .store import IntegrityError, ObjectStore, sha256_file

BUNDLE_FORMAT = 1


def _rows(db, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, params)]


def export_campaign(catalog: Catalog, store: ObjectStore, campaign_id: str, destination: Path) -> Path:
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    db = catalog.conn
    campaign = _rows(db, "SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
    if not campaign:
        raise KeyError(campaign_id)
    project = _rows(db, "SELECT * FROM projects WHERE id = ?", (campaign[0]["project_id"],))
    experiments = _rows(db, "SELECT * FROM experiments WHERE campaign_id = ? ORDER BY created_ns", (campaign_id,))
    exp_ids = tuple(e["id"] for e in experiments)
    runs: list[dict[str, Any]] = []
    for exp_id in exp_ids:
        runs += _rows(
            db,
            "SELECT * FROM runs WHERE experiment_id = ? AND state IN ('sealed','failed','interrupted') "
            "ORDER BY created_ns",
            (exp_id,),
        )
    artifacts: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for run in runs:
        artifacts += _rows(db, "SELECT * FROM artifacts WHERE run_id = ? ORDER BY name", (run["id"],))
        metrics += _rows(db, "SELECT * FROM run_metrics WHERE run_id = ? ORDER BY key", (run["id"],))
    exported = {run["id"] for run in runs}
    runs.sort(key=lambda run: run["created_ns"])
    for run in runs:
        if run["parent_run_id"] and run["parent_run_id"] not in exported:
            raise ValueError(
                f"run {run['id']} derives from {run['parent_run_id']}, outside this campaign: export that campaign too"
            )
    blobs = {a["sha256"]: a["size_bytes"] for a in artifacts}
    destination.parent.mkdir(parents=True, exist_ok=True)
    ensure_space(destination.parent, sum(blobs.values()))

    tmp = destination.with_name(destination.name + ".partial")
    if tmp.exists():
        raise FileExistsError(f"{tmp} exists from an interrupted export: check it and remove it by hand")
    (tmp / "objects").mkdir(parents=True)
    for sha, size in blobs.items():
        target = tmp / "objects" / sha
        shutil.copyfile(store.path_of(f"objects/{sha[:2]}/{sha[2:4]}/{sha}"), target)
        actual, actual_size = sha256_file(target)
        if actual != sha or actual_size != size:
            raise IntegrityError(f"object {sha} is corrupted in the source archive")
    bundle = {
        "bundle_format": BUNDLE_FORMAT,
        "exported_ns": now_ns(),
        "project": project[0],
        "campaign": campaign[0],
        "experiments": experiments,
        "runs": runs,
        "artifacts": artifacts,
        "metrics": metrics,
        "blobs": blobs,
    }
    (tmp / "bundle.json").write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    tmp.rename(destination)
    return destination


def import_bundle(catalog: Catalog, store: ObjectStore, layout: ArchiveLayout, source: Path) -> dict[str, int]:
    source = Path(source)
    bundle = json.loads((source / "bundle.json").read_text(encoding="utf-8"))
    if bundle.get("bundle_format") != BUNDLE_FORMAT:
        raise ValueError("unsupported bundle format")
    # 1. Verify everything before writing anything.
    _validate_bundle(bundle)
    ensure_space(layout.root, sum(bundle["blobs"].values()))
    for sha, size in bundle["blobs"].items():
        actual, actual_size = sha256_file(source / "objects" / sha)
        if actual != sha or actual_size != size:
            raise IntegrityError(f"object {sha} in the bundle is corrupted")
    # 2. Objects (idempotent, content-addressed).
    for sha in bundle["blobs"]:
        store.ingest_copy(source / "objects" / sha, tmp_dir=layout.tmp)

    imported = {"runs": 0, "skipped_runs": 0}
    db = catalog.conn
    project, campaign = bundle["project"], bundle["campaign"]
    with catalog.transaction():
        _insert_or_same(db, "projects", project)
        _insert_or_same(db, "campaigns", campaign)
        for experiment in bundle["experiments"]:
            _insert_or_same(db, "experiments", experiment)
    by_run: dict[str, list[dict[str, Any]]] = {}
    for artifact in bundle["artifacts"]:
        by_run.setdefault(artifact["run_id"], []).append(artifact)
    metrics_by_run: dict[str, list[dict[str, Any]]] = {}
    for metric in bundle["metrics"]:
        metrics_by_run.setdefault(metric["run_id"], []).append(metric)

    for run in bundle["runs"]:
        existing = db.execute("SELECT manifest_sha256, state FROM runs WHERE id = ?", (run["id"],)).fetchone()
        if existing is not None:
            if existing["manifest_sha256"] != run["manifest_sha256"] or existing["state"] != run["state"]:
                raise IntegrityError(f"run {run['id']} already exists with different content")
            imported["skipped_runs"] += 1
            continue
        final_state = run["state"]
        opening_state = "finalizing" if final_state == "sealed" else "recording"
        with catalog.transaction():
            opened = dict(run, state=opening_state, closed_ns=None)
            _insert(db, "runs", opened)
            for artifact in by_run.get(run["id"], []):
                _insert(db, "artifacts", artifact)
            for metric in metrics_by_run.get(run["id"], []):
                _insert(db, "run_metrics", metric)
            catalog.set_state(
                db, run["id"], final_state,
                manifest_sha256=run["manifest_sha256"], closed_ns=run["closed_ns"],
            )
        imported["runs"] += 1
    return imported


COLUMNS = {
    "projects": ("id", "name", "question", "created_ns"),
    "campaigns": ("id", "project_id", "name", "hypothesis", "created_ns"),
    "experiments": ("id", "campaign_id", "name", "config_json", "created_ns"),
    "runs": (
        "id", "experiment_id", "parent_run_id", "seed", "state", "config_json", "code_version",
        "environment_json", "notes", "stop_reason", "error", "manifest_sha256", "created_ns", "closed_ns",
    ),
    "artifacts": ("run_id", "name", "kind", "media_type", "sha256", "size_bytes"),
    "run_metrics": ("run_id", "key", "value"),
}


def _check_row(table: str, row: Any) -> None:
    if not isinstance(row, dict) or set(row) != set(COLUMNS[table]):
        raise IntegrityError(f"bundle row for {table} has unexpected columns")


def _validate_bundle(bundle: dict[str, Any]) -> None:
    """Structural and logical checks: nothing is written unless all of them pass."""
    _check_row("projects", bundle["project"])
    _check_row("campaigns", bundle["campaign"])
    if bundle["campaign"]["project_id"] != bundle["project"]["id"]:
        raise IntegrityError("campaign does not belong to the bundled project")
    experiment_ids = set()
    for experiment in bundle["experiments"]:
        _check_row("experiments", experiment)
        if experiment["campaign_id"] != bundle["campaign"]["id"]:
            raise IntegrityError("experiment outside the bundled campaign")
        experiment_ids.add(experiment["id"])
    blobs = bundle["blobs"]
    if not isinstance(blobs, dict) or any(
        not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
        or not isinstance(size, int) or size < 0
        for sha, size in blobs.items()
    ):
        raise IntegrityError("invalid blob list")
    run_ids: set[str] = set()
    for run in bundle["runs"]:
        _check_row("runs", run)
        if run["experiment_id"] not in experiment_ids:
            raise IntegrityError(f"run {run['id']} outside the bundled experiments")
        if run["state"] not in ("sealed", "failed", "interrupted"):
            raise IntegrityError(f"run {run['id']} is not closed")
        if run["parent_run_id"] and run["parent_run_id"] not in run_ids:
            raise IntegrityError(f"run {run['id']} has a parent missing or listed after it")
        run_ids.add(run["id"])
    artifacts_by_run: dict[str, dict[str, dict[str, Any]]] = {}
    for artifact in bundle["artifacts"]:
        _check_row("artifacts", artifact)
        if artifact["run_id"] not in run_ids:
            raise IntegrityError("artifact of a run not in the bundle")
        if blobs.get(artifact["sha256"]) != artifact["size_bytes"]:
            raise IntegrityError(f"artifact {artifact['name']} does not match the blob list")
        artifacts_by_run.setdefault(artifact["run_id"], {})[artifact["name"]] = artifact
    for metric in bundle["metrics"]:
        _check_row("run_metrics", metric)
        if metric["run_id"] not in run_ids:
            raise IntegrityError("metric of a run not in the bundle")
    for run in bundle["runs"]:
        if run["state"] == "sealed":
            manifest = artifacts_by_run.get(run["id"], {}).get("manifest.json")
            if manifest is None or manifest["sha256"] != run["manifest_sha256"]:
                raise IntegrityError(f"sealed run {run['id']} has no matching manifest")


def _insert(db, table: str, row: dict[str, Any]) -> None:
    _check_row(table, row)
    columns = COLUMNS[table]
    db.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(row[c] for c in columns),
    )


def _insert_or_same(db, table: str, row: dict[str, Any]) -> None:
    existing = db.execute(f"SELECT * FROM {table} WHERE id = ?", (row["id"],)).fetchone()
    if existing is None:
        _insert(db, table, row)
    elif dict(existing) != row:
        raise IntegrityError(f"{table} {row['id']} already exists with different content")
