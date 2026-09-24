from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

from fruitfly_lab.archive import Archive
from fruitfly_lab.archive.recorder import SNAPSHOT_TOPIC
from fruitfly_lab.archive.replay import iter_telemetry, read_table, verify_run
from fruitfly_lab.worker import run_engine_worker


class WorkerArchiveTests(unittest.TestCase):
    """Real FlyGym engine: start, walk, stop, and the run must come back sealed."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fruitfly-worker-"))

    def tearDown(self) -> None:
        for path in self.tmp.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o666)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_run_is_sealed_with_real_telemetry(self) -> None:
        commands: queue.Queue = queue.Queue()
        events: queue.Queue = queue.Queue()
        archive_dir = self.tmp / "archive"
        thread = threading.Thread(
            target=run_engine_worker,
            args=(commands, events),
            kwargs={"archive_dir": str(archive_dir)},
            daemon=True,
        )
        thread.start()

        def send(kind: str) -> None:
            commands.put({"type": kind, "request_id": str(uuid.uuid4())})

        def wait_for(predicate, timeout: float = 60.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    message = events.get(timeout=0.5)
                except queue.Empty:
                    continue
                if message.get("type") == "fatal":
                    self.fail(message["error"])
                if predicate(message):
                    return message
            self.fail("timeout waiting for worker")

        wait_for(lambda m: m["type"] == "metadata")
        send("start")
        recording = wait_for(lambda m: m["type"] == "archive" and m["event"] == "recording")
        wait_for(lambda m: m["type"] == "snapshot" and m["payload"]["sequence"] >= 2000)
        send("pause")
        send("start")
        wait_for(lambda m: m["type"] == "snapshot" and m["payload"]["sequence"] >= 3000)
        send("stop")
        sealed = wait_for(lambda m: m["type"] == "archive" and m["event"] in ("sealed", "failed"))
        self.assertEqual(sealed["event"], "sealed", sealed)
        self.assertEqual(sealed["run_id"], recording["run_id"])
        send("shutdown")
        thread.join(30)
        self.assertFalse(thread.is_alive())

        archive = Archive(archive_dir)
        try:
            run_id = sealed["run_id"]
            run = archive.catalog.run(run_id)
            self.assertEqual(run["state"], "sealed")
            self.assertEqual(run["stop_reason"], "arresto")
            info = verify_run(archive.catalog, archive.store, run_id)
            self.assertEqual(info["manifest"]["config"]["controller"], "tripod")
            messages = list(iter_telemetry(archive.catalog, archive.store, run_id))
            snapshots = [m for topic, m in messages if topic == SNAPSHOT_TOPIC]
            kinds = [m["kind"] for topic, m in messages if topic != SNAPSHOT_TOPIC]
            self.assertEqual(kinds[:2], ["start", "run"])
            self.assertIn("pause", kinds)
            self.assertIn("resume", kinds)
            self.assertEqual(kinds[-1], "run_end")
            self.assertGreaterEqual(snapshots[-1]["sequence"], 3000)
            self.assertEqual(len(snapshots[0]["body_pos_mm"]), 69)
            sequences = [s["sequence"] for s in snapshots]
            self.assertEqual(sequences, sorted(sequences))
            table = read_table(archive.catalog, archive.store, run_id)
            self.assertEqual(table.num_rows, len(snapshots))
            metrics = archive.catalog.metrics(run_id)
            self.assertGreater(metrics["sim_duration_s"], 0.29)
            stored = sum(a["size_bytes"] for a in archive.catalog.artifacts(run_id))
            print(f"\n  run reale: {len(snapshots)} snapshot, {stored} byte, "
                  f"{stored / len(snapshots):.0f} byte/snapshot, "
                  f"spostamento {metrics['displacement_mm']:.3f} mm")
        finally:
            archive.close()


if __name__ == "__main__":
    unittest.main()
