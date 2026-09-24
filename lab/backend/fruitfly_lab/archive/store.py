"""Content-addressed, write-once object store on the archive volume."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

from .catalog import Catalog, now_ns

CHUNK = 1024 * 1024
JOURNAL = "ingest.jsonl"


@dataclass(frozen=True, slots=True)
class StoredBlob:
    sha256: str
    size_bytes: int
    relpath: str


class IntegrityError(RuntimeError):
    pass


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def object_relpath(sha256: str) -> str:
    return f"objects/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def fsync_file(path: Path) -> None:
    with open(path, "rb+") as handle:
        os.fsync(handle.fileno())


class ObjectStore:
    def __init__(self, root: Path, catalog: Catalog) -> None:
        self.root = Path(root)
        self.catalog = catalog
        (self.root / "objects").mkdir(parents=True, exist_ok=True)

    def path_of(self, relpath: str) -> Path:
        # Only digest-derived paths are valid: nothing in the catalog can point outside objects/.
        name = relpath.rsplit("/", 1)[-1]
        if len(name) != 64 or any(c not in "0123456789abcdef" for c in name) or relpath != object_relpath(name):
            raise IntegrityError(f"invalid object path: {relpath!r}")
        return self.root / relpath

    def ingest_move(self, source: Path, *, journal_dir: Path, name: str) -> StoredBlob:
        """Hash a finished file and move it into the store (same volume, os.replace).

        The intent is journaled before the move, so a crash between the move and the
        catalog insert can be recovered without losing the link to the run.
        """
        sha, size = sha256_file(source)
        relpath = object_relpath(sha)
        target = self.path_of(relpath)
        _append_journal(journal_dir, {"name": name, "sha256": sha, "size_bytes": size})
        if target.exists():
            existing_sha, _ = sha256_file(target)
            if existing_sha != sha:
                raise IntegrityError(f"object {relpath} is corrupted")
            # Byte-identical copy already stored: the duplicate carries no new data.
            _make_writable(source)
            source.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            fsync_file(source)
            os.replace(source, target)
            _make_read_only(target)
        return self.register(sha, size)

    def ingest_copy(self, source: Path, *, tmp_dir: Path) -> StoredBlob:
        """Copy an external file in (used by import). The source is never modified."""
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"incoming-{os.getpid()}-{now_ns()}"
        shutil.copyfile(source, tmp)
        sha, size = sha256_file(tmp)
        target = self.path_of(object_relpath(sha))
        if target.exists():
            existing_sha, existing_size = sha256_file(target)
            if existing_sha != sha or existing_size != size:
                raise IntegrityError(f"stored object {sha} is corrupted, the new copy is kept in {tmp}")
            tmp.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            fsync_file(tmp)
            os.replace(tmp, target)
            _make_read_only(target)
        return self.register(sha, size)

    def register(self, sha: str, size: int) -> StoredBlob:
        relpath = object_relpath(sha)
        self.path_of(relpath)
        with self.catalog.transaction() as db:
            row = db.execute("SELECT size_bytes FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO blobs (sha256, size_bytes, relpath, created_ns) VALUES (?, ?, ?, ?)",
                    (sha, size, relpath, now_ns()),
                )
            elif row["size_bytes"] != size:
                raise IntegrityError(f"blob {sha} registered with a different size")
        return StoredBlob(sha, size, relpath)

    def verify(self, sha: str) -> bool:
        path = self.path_of(object_relpath(sha))
        if not path.is_file():
            return False
        actual, _ = sha256_file(path)
        return actual == sha

    def unregistered_objects(self) -> list[Path]:
        known = {row[0] for row in self.catalog.conn.execute("SELECT sha256 FROM blobs")}
        found = []
        for path in (self.root / "objects").rglob("*"):
            if path.is_file() and path.name not in known:
                found.append(path)
        return found


def read_journal(journal_dir: Path) -> list[dict[str, object]]:
    path = journal_dir / JOURNAL
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn last line from a crash: the move had not happened yet.
            continue
    return entries


def _append_journal(journal_dir: Path, entry: dict[str, object]) -> None:
    journal_dir.mkdir(parents=True, exist_ok=True)
    with open(journal_dir / JOURNAL, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
