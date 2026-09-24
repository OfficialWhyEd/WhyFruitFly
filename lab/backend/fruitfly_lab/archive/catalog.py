"""SQLite catalog: index of projects, runs and immutable artifacts.

SQLite only indexes. Telemetry lives in MCAP, analytics in Parquet, both stored
as content-addressed files. Immutability is enforced by triggers inside the
database, so no code path (including future ones) can edit a closed run.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

TERMINAL_STATES = ("sealed", "failed", "interrupted")

MIGRATIONS: tuple[str, ...] = (
    # 1: hierarchy, runs, content-addressed blobs, artifacts, metrics.
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        question TEXT NOT NULL DEFAULT '',
        created_ns INTEGER NOT NULL
    );
    CREATE TABLE campaigns (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL,
        hypothesis TEXT NOT NULL DEFAULT '',
        created_ns INTEGER NOT NULL,
        UNIQUE (project_id, name)
    );
    CREATE TABLE experiments (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(id),
        name TEXT NOT NULL,
        config_json TEXT NOT NULL,
        created_ns INTEGER NOT NULL,
        UNIQUE (campaign_id, name)
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL REFERENCES experiments(id),
        parent_run_id TEXT REFERENCES runs(id),
        seed INTEGER NOT NULL,
        state TEXT NOT NULL CHECK (state IN
            ('recording', 'finalizing', 'sealed', 'failed', 'interrupted')),
        config_json TEXT NOT NULL,
        code_version TEXT NOT NULL,
        environment_json TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        stop_reason TEXT,
        error TEXT,
        manifest_sha256 TEXT,
        created_ns INTEGER NOT NULL,
        closed_ns INTEGER
    );
    CREATE INDEX runs_by_experiment ON runs (experiment_id, created_ns);
    CREATE INDEX runs_by_state ON runs (state);
    CREATE TABLE blobs (
        sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        relpath TEXT NOT NULL UNIQUE CHECK (relpath NOT LIKE '/%' AND relpath NOT LIKE '%:%'),
        created_ns INTEGER NOT NULL
    );
    CREATE TABLE artifacts (
        run_id TEXT NOT NULL REFERENCES runs(id),
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        media_type TEXT NOT NULL,
        sha256 TEXT NOT NULL REFERENCES blobs(sha256),
        size_bytes INTEGER NOT NULL,
        PRIMARY KEY (run_id, name)
    );
    CREATE TABLE run_metrics (
        run_id TEXT NOT NULL REFERENCES runs(id),
        key TEXT NOT NULL,
        value REAL NOT NULL,
        PRIMARY KEY (run_id, key)
    );

    -- Nothing is ever deleted.
    CREATE TRIGGER runs_never_deleted BEFORE DELETE ON runs
    BEGIN SELECT RAISE(ABORT, 'runs are never deleted'); END;
    CREATE TRIGGER blobs_never_deleted BEFORE DELETE ON blobs
    BEGIN SELECT RAISE(ABORT, 'blobs are never deleted'); END;
    CREATE TRIGGER blobs_never_updated BEFORE UPDATE ON blobs
    BEGIN SELECT RAISE(ABORT, 'blobs are immutable'); END;
    CREATE TRIGGER artifacts_never_deleted BEFORE DELETE ON artifacts
    BEGIN SELECT RAISE(ABORT, 'artifacts are never deleted'); END;
    CREATE TRIGGER artifacts_never_updated BEFORE UPDATE ON artifacts
    BEGIN SELECT RAISE(ABORT, 'artifacts are immutable'); END;
    CREATE TRIGGER metrics_never_deleted BEFORE DELETE ON run_metrics
    BEGIN SELECT RAISE(ABORT, 'metrics are never deleted'); END;
    CREATE TRIGGER metrics_never_updated BEFORE UPDATE ON run_metrics
    BEGIN SELECT RAISE(ABORT, 'metrics are immutable'); END;
    CREATE TRIGGER projects_never_deleted BEFORE DELETE ON projects
    BEGIN SELECT RAISE(ABORT, 'projects are never deleted'); END;
    CREATE TRIGGER campaigns_never_deleted BEFORE DELETE ON campaigns
    BEGIN SELECT RAISE(ABORT, 'campaigns are never deleted'); END;
    CREATE TRIGGER experiments_never_deleted BEFORE DELETE ON experiments
    BEGIN SELECT RAISE(ABORT, 'experiments are never deleted'); END;
    CREATE TRIGGER experiments_config_frozen BEFORE UPDATE OF config_json ON experiments
    BEGIN SELECT RAISE(ABORT, 'experiment config is immutable, create a new experiment'); END;

    -- A closed run can never change again.
    CREATE TRIGGER runs_closed_are_immutable BEFORE UPDATE ON runs
    WHEN OLD.state IN ('sealed', 'failed', 'interrupted')
    BEGIN SELECT RAISE(ABORT, 'run is closed and immutable'); END;
    CREATE TRIGGER runs_identity_frozen BEFORE UPDATE OF
        id, experiment_id, parent_run_id, seed, config_json, code_version,
        environment_json, created_ns ON runs
    BEGIN SELECT RAISE(ABORT, 'run identity is immutable'); END;
    CREATE TRIGGER runs_valid_transition BEFORE UPDATE OF state ON runs
    WHEN NOT (
        OLD.state = NEW.state
        OR (OLD.state = 'recording' AND NEW.state IN ('finalizing', 'failed', 'interrupted'))
        OR (OLD.state = 'finalizing' AND NEW.state IN ('sealed', 'failed', 'interrupted'))
    )
    BEGIN SELECT RAISE(ABORT, 'invalid run state transition'); END;
    CREATE TRIGGER runs_sealed_needs_manifest BEFORE UPDATE OF state ON runs
    WHEN NEW.state = 'sealed' AND NEW.manifest_sha256 IS NULL
    BEGIN SELECT RAISE(ABORT, 'a sealed run needs a manifest'); END;
    CREATE TRIGGER runs_start_recording BEFORE INSERT ON runs
    WHEN NEW.state NOT IN ('recording', 'finalizing')
    BEGIN SELECT RAISE(ABORT, 'runs start open'); END;

    -- Artifacts and metrics attach only while the run is still open.
    CREATE TRIGGER artifacts_only_open_runs BEFORE INSERT ON artifacts
    WHEN (SELECT state FROM runs WHERE id = NEW.run_id) NOT IN ('recording', 'finalizing')
    BEGIN SELECT RAISE(ABORT, 'run is closed, artifacts cannot be added'); END;
    CREATE TRIGGER metrics_only_open_runs BEFORE INSERT ON run_metrics
    WHEN (SELECT state FROM runs WHERE id = NEW.run_id) NOT IN ('recording', 'finalizing')
    BEGIN SELECT RAISE(ABORT, 'run is closed, metrics cannot be added'); END;
    """,
    # 2: freeze archival identity of the hierarchy (review 23/09/2026).
    """
    CREATE TRIGGER campaigns_identity_frozen BEFORE UPDATE OF id, project_id, created_ns ON campaigns
    BEGIN SELECT RAISE(ABORT, 'campaign identity is immutable'); END;
    CREATE TRIGGER experiments_identity_frozen BEFORE UPDATE OF id, campaign_id, created_ns ON experiments
    BEGIN SELECT RAISE(ABORT, 'experiment identity is immutable'); END;
    CREATE TRIGGER projects_identity_frozen BEFORE UPDATE OF id, created_ns ON projects
    BEGIN SELECT RAISE(ABORT, 'project identity is immutable'); END;
    """,
)


def now_ns() -> int:
    return time.time_ns()


def new_id() -> str:
    return uuid.uuid4().hex


class Catalog:
    """Single-writer catalog. Readers may open their own read-only connection."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        if read_only:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self.conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.path, isolation_level=None)
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = FULL")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # Without this, INSERT OR REPLACE deletes rows without firing the DELETE triggers.
        self.conn.execute("PRAGMA recursive_triggers = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        if not read_only:
            self._migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_ns INTEGER NOT NULL)"
        )
        applied = {
            row[0] for row in self.conn.execute("SELECT version FROM schema_migrations")
        }
        for version, script in enumerate(MIGRATIONS, start=1):
            if version in applied:
                continue
            # executescript commits on its own, so the whole migration runs in one script.
            self.conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + f"\nINSERT INTO schema_migrations (version, applied_ns) VALUES ({version}, {now_ns()});\n"
                + "COMMIT;"
            )

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)

    # Hierarchy ---------------------------------------------------------

    def ensure_project(self, name: str, question: str = "") -> str:
        row = self.conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        project_id = new_id()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO projects (id, name, question, created_ns) VALUES (?, ?, ?, ?)",
                (project_id, name, question, now_ns()),
            )
        return project_id

    def ensure_campaign(self, project_id: str, name: str, hypothesis: str = "") -> str:
        row = self.conn.execute(
            "SELECT id FROM campaigns WHERE project_id = ? AND name = ?", (project_id, name)
        ).fetchone()
        if row:
            return row["id"]
        campaign_id = new_id()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO campaigns (id, project_id, name, hypothesis, created_ns) "
                "VALUES (?, ?, ?, ?, ?)",
                (campaign_id, project_id, name, hypothesis, now_ns()),
            )
        return campaign_id

    def ensure_experiment(self, campaign_id: str, name: str, config: dict[str, Any]) -> str:
        config_json = canonical_json(config)
        row = self.conn.execute(
            "SELECT id, config_json FROM experiments WHERE campaign_id = ? AND name = ?",
            (campaign_id, name),
        ).fetchone()
        if row:
            if row["config_json"] != config_json:
                raise ValueError(
                    f"experiment {name!r} already exists with a different config"
                )
            return row["id"]
        experiment_id = new_id()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO experiments (id, campaign_id, name, config_json, created_ns) "
                "VALUES (?, ?, ?, ?, ?)",
                (experiment_id, campaign_id, name, config_json, now_ns()),
            )
        return experiment_id

    # Runs ----------------------------------------------------------------

    def create_run(
        self,
        *,
        experiment_id: str,
        seed: int,
        config: dict[str, Any],
        code_version: str,
        environment: dict[str, Any],
        notes: str = "",
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> str:
        run_id = run_id or new_id()
        with self.transaction() as db:
            db.execute(
                "INSERT INTO runs (id, experiment_id, parent_run_id, seed, state, config_json, "
                "code_version, environment_json, notes, created_ns) "
                "VALUES (?, ?, ?, ?, 'recording', ?, ?, ?, ?, ?)",
                (
                    run_id,
                    experiment_id,
                    parent_run_id,
                    int(seed),
                    canonical_json(config),
                    code_version,
                    canonical_json(environment),
                    notes,
                    now_ns(),
                ),
            )
        return run_id

    def run(self, run_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    def set_state(
        self,
        db: sqlite3.Connection,
        run_id: str,
        state: str,
        *,
        stop_reason: str | None = None,
        error: str | None = None,
        manifest_sha256: str | None = None,
        closed_ns: int | None = None,
    ) -> None:
        closed = closed_ns if closed_ns is not None else (now_ns() if state in TERMINAL_STATES else None)
        db.execute(
            "UPDATE runs SET state = ?, "
            "stop_reason = COALESCE(?, stop_reason), error = COALESCE(?, error), "
            "manifest_sha256 = COALESCE(?, manifest_sha256), closed_ns = COALESCE(?, closed_ns) "
            "WHERE id = ?",
            (state, stop_reason, error, manifest_sha256, closed, run_id),
        )

    def open_runs(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM runs WHERE state IN ('recording', 'finalizing') ORDER BY created_ns"
            )
        )

    def artifacts(self, run_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT a.*, b.relpath FROM artifacts a JOIN blobs b USING (sha256) "
                "WHERE a.run_id = ? ORDER BY a.name",
                (run_id,),
            )
        )

    def metrics(self, run_id: str) -> dict[str, float]:
        return {
            row["key"]: row["value"]
            for row in self.conn.execute(
                "SELECT key, value FROM run_metrics WHERE run_id = ? ORDER BY key", (run_id,)
            )
        }

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT r.id, r.state, r.seed, r.stop_reason, r.error, r.created_ns, r.closed_ns, "
            "r.notes, e.name AS experiment, c.name AS campaign, p.name AS project "
            "FROM runs r JOIN experiments e ON e.id = r.experiment_id "
            "JOIN campaigns c ON c.id = e.campaign_id JOIN projects p ON p.id = c.project_id "
            "ORDER BY r.created_ns DESC LIMIT ?",
            (limit,),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = self.metrics(row["id"])
            item["artifact_bytes"] = self.conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts WHERE run_id = ?", (row["id"],)
            ).fetchone()[0]
            result.append(item)
        return result


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
