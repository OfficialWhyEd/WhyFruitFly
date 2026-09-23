from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fruitfly_lab.archive import Archive, InsufficientSpace, IntegrityError, RunRecorder
from fruitfly_lab.archive import recorder as recorder_module
from fruitfly_lab.archive import store as store_module
from fruitfly_lab.archive.bundle import export_campaign, import_bundle
from fruitfly_lab.archive.recorder import MCAP_NAME, PARQUET_NAME, SNAPSHOT_TOPIC
from fruitfly_lab.archive.replay import iter_telemetry, read_table, verify_run

MODEL = {"body_names": ["thorax", "head"], "joint_names": ["j0"], "actuator_names": ["a0"], "leg_names": ["lf"]}


def fake_snapshot(sequence: int) -> dict:
    t = sequence * 0.0001
    return {
        "protocol_version": 1,
        "sequence": sequence,
        "status": "running",
        "sim_time_s": t,
        "body_pos_mm": [[t * 10, t * 2, 1.0], [t * 10 + 0.5, t * 2, 1.1]],
        "body_quat_wxyz": [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],
        "joint_angle_rad": [0.1 * sequence],
        "joint_velocity_rad_s": [0.2],
        "actuator_force": [0.3],
        "contact_found": [sequence % 2 == 0],
        "contact_force_contact_frame": [[0.0, 0.0, 1.0]],
        "contact_pos_mm_world": [[0.0, 0.0, 0.0]],
    }


class ArchiveTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fruitfly-archive-"))
        self.archive = Archive(self.tmp / "archive")
        catalog = self.archive.catalog
        project = catalog.ensure_project("Prova", "La mosca cammina dritta?")
        self.campaign = catalog.ensure_campaign(project, "Camminata", "Il tripode e' stabile")
        self.experiment = catalog.ensure_experiment(self.campaign, "tripode 4 Hz", {"step_frequency_hz": 4.0})

    def tearDown(self) -> None:
        self.archive.close()
        for path in self.tmp.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o666)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def begin(self, **kwargs) -> RunRecorder:
        a = self.archive
        return RunRecorder.begin(
            catalog=a.catalog, store=a.store, layout=a.layout,
            experiment_id=self.experiment, seed=kwargs.pop("seed", 7),
            config={"step_frequency_hz": 4.0}, model_metadata=MODEL, margin_bytes=0, **kwargs,
        )

    def record(self, n: int = 600, **kwargs) -> str:
        rec = self.begin(**kwargs)
        rec.record_event("start")
        for i in range(n):
            rec.record_snapshot(fake_snapshot(i * 25))
        rec.finalize("stop")
        return rec.run_id


class CatalogTests(ArchiveTestCase):
    def test_schema_has_wal_foreign_keys_and_version(self) -> None:
        conn = self.archive.catalog.conn
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(self.archive.catalog.schema_version, 2)

    def test_reopening_does_not_rerun_migrations(self) -> None:
        self.archive.close()
        self.archive = Archive(self.tmp / "archive")
        self.assertEqual(self.archive.catalog.schema_version, 2)

    def test_experiment_config_cannot_change_silently(self) -> None:
        with self.assertRaises(ValueError):
            self.archive.catalog.ensure_experiment(self.campaign, "tripode 4 Hz", {"step_frequency_hz": 5.0})


class RecordAndSealTests(ArchiveTestCase):
    def test_sealed_run_round_trip(self) -> None:
        run_id = self.record(600)
        catalog, store = self.archive.catalog, self.archive.store
        run = catalog.run(run_id)
        self.assertEqual(run["state"], "sealed")
        self.assertEqual(len(run["manifest_sha256"]), 64)
        names = {a["name"] for a in catalog.artifacts(run_id)}
        self.assertEqual(names, {MCAP_NAME, PARQUET_NAME, "manifest.json"})
        for artifact in catalog.artifacts(run_id):
            self.assertFalse(Path(artifact["relpath"]).is_absolute())
        info = verify_run(catalog, store, run_id)
        self.assertEqual(info["manifest"]["seed"], 7)
        self.assertIn("flygym", info["manifest"]["environment"]["packages"])

        snapshots = [m for topic, m in iter_telemetry(catalog, store, run_id) if topic == SNAPSHOT_TOPIC]
        self.assertEqual(len(snapshots), 600)
        self.assertEqual(snapshots[-1], fake_snapshot(599 * 25))
        table = read_table(catalog, store, run_id)
        self.assertEqual(table.num_rows, 600)
        self.assertEqual(table.column("sequence")[-1].as_py(), 599 * 25)
        metrics = catalog.metrics(run_id)
        self.assertEqual(metrics["snapshots"], 600)
        self.assertAlmostEqual(metrics["displacement_x_mm"], 599 * 25 * 0.0001 * 10, places=6)
        self.assertFalse(self.archive.layout.run_staging(run_id).joinpath(MCAP_NAME).exists())

    def test_sealed_run_cannot_be_modified_or_deleted(self) -> None:
        run_id = self.record(10)
        db = self.archive.catalog.conn
        for sql in (
            "UPDATE runs SET notes = 'cambiata' WHERE id = ?",
            "DELETE FROM runs WHERE id = ?",
            "DELETE FROM artifacts WHERE run_id = ?",
            "UPDATE artifacts SET sha256 = sha256 WHERE run_id = ?",
            "DELETE FROM run_metrics WHERE run_id = ?",
            "INSERT INTO run_metrics (run_id, key, value) VALUES (?, 'extra', 1)",
        ):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                db.execute(sql, (run_id,))
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("DELETE FROM blobs")
        for artifact in self.archive.catalog.artifacts(run_id):
            with self.assertRaises(PermissionError):
                open(self.archive.store.path_of(artifact["relpath"]), "ab").close()

    def test_tampered_object_is_detected(self) -> None:
        run_id = self.record(10)
        artifact = next(a for a in self.archive.catalog.artifacts(run_id) if a["name"] == PARQUET_NAME)
        path = self.archive.store.path_of(artifact["relpath"])
        os.chmod(path, 0o666)
        with open(path, "ab") as handle:
            handle.write(b"x")
        with self.assertRaises(IntegrityError):
            verify_run(self.archive.catalog, self.archive.store, run_id)

    def test_same_seed_same_telemetry(self) -> None:
        first = self.record(50, seed=3)
        second = self.record(50, seed=3)
        a = [m for t, m in iter_telemetry(self.archive.catalog, self.archive.store, first) if t == SNAPSHOT_TOPIC]
        b = [m for t, m in iter_telemetry(self.archive.catalog, self.archive.store, second) if t == SNAPSHOT_TOPIC]
        self.assertEqual(a, b)
        self.assertNotEqual(first, second)
        # Parquet has no wall clock inside: identical content is stored once.
        pa_first = next(x for x in self.archive.catalog.artifacts(first) if x["name"] == PARQUET_NAME)
        pa_second = next(x for x in self.archive.catalog.artifacts(second) if x["name"] == PARQUET_NAME)
        self.assertEqual(pa_first["sha256"], pa_second["sha256"])


class SpaceTests(ArchiveTestCase):
    def test_insufficient_space_blocks_before_start(self) -> None:
        with mock.patch.object(recorder_module, "free_bytes", return_value=10):
            with self.assertRaises(InsufficientSpace):
                self.begin(estimated_bytes=1000)
        self.assertEqual(self.archive.catalog.list_runs(), [])


class CrashRecoveryTests(ArchiveTestCase):
    def test_crash_while_recording_keeps_partial_data(self) -> None:
        rec = self.begin()
        for i in range(300):
            rec.record_snapshot(fake_snapshot(i))
        run_id = rec.run_id
        rec._mcap_handle.flush()  # the process dies here: writers never closed
        self.archive.close()
        rec._mcap_handle.close()
        rec._parquet.close()
        self.archive = Archive(self.tmp / "archive")
        self.assertEqual(self.archive.recovery_report["interrupted_runs"], [run_id])
        run = self.archive.catalog.run(run_id)
        self.assertEqual(run["state"], "interrupted")
        names = {a["name"] for a in self.archive.catalog.artifacts(run_id)}
        self.assertIn(MCAP_NAME, names)
        verify_run(self.archive.catalog, self.archive.store, run_id)

    def _crash_during_seal(self, fail_on: str) -> str:
        rec = self.begin()
        for i in range(40):
            rec.record_snapshot(fake_snapshot(i))
        real_replace = os.replace
        real_register = store_module.ObjectStore.register

        def boom_replace(src, dst):
            if fail_on == "before_replace":
                raise KeyboardInterrupt("crash prima di os.replace")
            return real_replace(src, dst)

        def boom_register(self_, sha, size):
            if fail_on == "after_replace":
                raise KeyboardInterrupt("crash dopo os.replace")
            return real_register(self_, sha, size)

        with mock.patch.object(store_module.os, "replace", boom_replace), \
             mock.patch.object(store_module.ObjectStore, "register", boom_register), \
             mock.patch.object(recorder_module, "fail_run"):  # a real crash runs no cleanup
            with self.assertRaises(KeyboardInterrupt):
                rec.finalize("stop")
        self.archive.close()
        self.archive = Archive(self.tmp / "archive")
        return rec.run_id

    def test_crash_before_os_replace(self) -> None:
        run_id = self._crash_during_seal("before_replace")
        self.assertEqual(self.archive.catalog.run(run_id)["state"], "interrupted")
        names = {a["name"] for a in self.archive.catalog.artifacts(run_id)}
        self.assertEqual(names, {MCAP_NAME, PARQUET_NAME})
        verify_run(self.archive.catalog, self.archive.store, run_id)

    def test_crash_after_os_replace(self) -> None:
        run_id = self._crash_during_seal("after_replace")
        self.assertEqual(self.archive.catalog.run(run_id)["state"], "interrupted")
        names = {a["name"] for a in self.archive.catalog.artifacts(run_id)}
        self.assertEqual(names, {MCAP_NAME, PARQUET_NAME})
        verify_run(self.archive.catalog, self.archive.store, run_id)
        self.assertEqual(self.archive.store.unregistered_objects(), [])


class BundleTests(ArchiveTestCase):
    def test_export_import_preserves_relations_and_hashes(self) -> None:
        runs = [self.record(30, seed=s) for s in (1, 2, 3)]
        bundle = export_campaign(self.archive.catalog, self.archive.store, self.campaign, self.tmp / "export")
        target = Archive(self.tmp / "other")
        try:
            result = import_bundle(target.catalog, target.store, target.layout, bundle)
            self.assertEqual(result["runs"], 3)
            for run_id in runs:
                src, dst = self.archive.catalog.run(run_id), target.catalog.run(run_id)
                self.assertEqual(dict(src), dict(dst))
                self.assertEqual(
                    [dict(a) for a in self.archive.catalog.artifacts(run_id)],
                    [dict(a) for a in target.catalog.artifacts(run_id)],
                )
                self.assertEqual(self.archive.catalog.metrics(run_id), target.catalog.metrics(run_id))
                verify_run(target.catalog, target.store, run_id)
            again = import_bundle(target.catalog, target.store, target.layout, bundle)
            self.assertEqual(again, {"runs": 0, "skipped_runs": 3})
        finally:
            target.close()

    def test_corrupted_bundle_is_rejected_before_writing(self) -> None:
        self.record(10)
        bundle = export_campaign(self.archive.catalog, self.archive.store, self.campaign, self.tmp / "export")
        victim = next((bundle / "objects").iterdir())
        with open(victim, "ab") as handle:
            handle.write(b"rotto")
        target = Archive(self.tmp / "other")
        try:
            with self.assertRaises(IntegrityError):
                import_bundle(target.catalog, target.store, target.layout, bundle)
            self.assertEqual(target.catalog.list_runs(), [])
        finally:
            target.close()


class ReviewRegressionTests(ArchiveTestCase):
    """Holes found by the Codex review of 23/09/2026."""

    def test_insert_or_replace_cannot_bypass_immutability(self) -> None:
        run_id = self.record(5)
        db = self.archive.catalog.conn
        run = dict(self.archive.catalog.run(run_id))
        blob = dict(db.execute("SELECT * FROM blobs LIMIT 1").fetchone())
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute(
                "INSERT OR REPLACE INTO runs (id, experiment_id, seed, state, config_json, code_version, "
                "environment_json, created_ns) VALUES (?, ?, 1, 'recording', '{}', 'x', '{}', 1)",
                (run_id, run["experiment_id"]),
            )
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute(
                "INSERT OR REPLACE INTO blobs (sha256, size_bytes, relpath, created_ns) VALUES (?, 1, ?, 1)",
                (blob["sha256"], blob["relpath"]),
            )
        self.assertEqual(self.archive.catalog.run(run_id)["state"], "sealed")

    def test_hierarchy_identity_is_frozen(self) -> None:
        db = self.archive.catalog.conn
        other = self.archive.catalog.ensure_campaign(
            db.execute("SELECT project_id FROM campaigns").fetchone()[0], "Altra"
        )
        with self.assertRaises(sqlite3.DatabaseError):
            db.execute("UPDATE experiments SET campaign_id = ? WHERE id = ?", (other, self.experiment))
        self.assertEqual(self.archive.catalog.schema_version, 2)

    def test_second_writer_is_refused(self) -> None:
        from fruitfly_lab.archive import ArchiveLocked

        with self.assertRaises(ArchiveLocked):
            Archive(self.tmp / "archive")

    def test_path_outside_objects_is_refused(self) -> None:
        for bad in ("objects/../../catalog.sqlite", "..\\x", "objects/ab/cd/" + "a" * 64):
            with self.subTest(bad=bad), self.assertRaises(IntegrityError):
                self.archive.store.path_of(bad)

    def test_bundle_with_injected_column_is_refused(self) -> None:
        self.record(5)
        bundle = export_campaign(self.archive.catalog, self.archive.store, self.campaign, self.tmp / "export")
        data = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        data["project"]["name) VALUES ('x') --"] = "x"
        (bundle / "bundle.json").write_text(json.dumps(data), encoding="utf-8")
        target_root = self.tmp / "other"
        target = Archive(target_root)
        try:
            with self.assertRaises(IntegrityError):
                import_bundle(target.catalog, target.store, target.layout, bundle)
            self.assertEqual(target.catalog.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0], 0)
        finally:
            target.close()

    def test_corrupted_stored_object_is_not_trusted_on_import(self) -> None:
        self.record(5)
        bundle = export_campaign(self.archive.catalog, self.archive.store, self.campaign, self.tmp / "export")
        sha = next((bundle / "objects").iterdir()).name
        target = self.archive.store.path_of(f"objects/{sha[:2]}/{sha[2:4]}/{sha}")
        os.chmod(target, 0o666)
        with open(target, "ab") as handle:
            handle.write(b"x")
        with self.assertRaises(IntegrityError):
            self.archive.store.ingest_copy(bundle / "objects" / sha, tmp_dir=self.archive.layout.tmp)


if __name__ == "__main__":
    unittest.main()
