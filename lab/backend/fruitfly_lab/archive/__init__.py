"""Immutable experiment archive: SQLite catalog, MCAP telemetry, Parquet tables."""

from __future__ import annotations

import os
from pathlib import Path

from .catalog import Catalog
from .recorder import (
    ArchiveLayout,
    InsufficientSpace,
    RecordingError,
    RunRecorder,
    ensure_space,
    estimate_run_bytes,
    free_bytes,
    recover,
)
from .store import IntegrityError, ObjectStore


class ArchiveLocked(RuntimeError):
    pass


class _WriterLock:
    """Exclusive OS lock held for the whole life of the writer; released if the process dies."""

    def __init__(self, path: Path) -> None:
        self.handle = open(path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise ArchiveLocked(f"l'archivio {path.parent} e' gia' aperto da un altro processo") from exc

    def release(self) -> None:
        if os.name == "nt":
            import msvcrt

            try:
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        self.handle.close()


class Archive:
    """One archive root. Only one writer process at a time (enforced by a lock)."""

    def __init__(self, root: Path) -> None:
        self.layout = ArchiveLayout(Path(root))
        self.layout.root.mkdir(parents=True, exist_ok=True)
        self._lock = _WriterLock(self.layout.root / "writer.lock")
        try:
            self.catalog = Catalog(self.layout.catalog_path)
            self.store = ObjectStore(self.layout.root, self.catalog)
            # Recovery runs only while holding the lock, so it never touches a live writer's run.
            self.recovery_report = recover(self.catalog, self.store, self.layout)
        except BaseException:
            self._lock.release()
            raise

    def close(self) -> None:
        try:
            self.catalog.close()
        finally:
            self._lock.release()


__all__ = [
    "Archive",
    "ArchiveLocked",
    "ArchiveLayout",
    "Catalog",
    "InsufficientSpace",
    "IntegrityError",
    "ObjectStore",
    "RecordingError",
    "RunRecorder",
    "ensure_space",
    "estimate_run_bytes",
    "free_bytes",
    "recover",
]
