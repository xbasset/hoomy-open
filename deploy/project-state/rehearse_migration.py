"""Rehearse additive state tables on a new private copy of project data only."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from hoomy.store import Store


STATE_TABLES = {"project_state_assertions", "project_state_calls", "project_state_citations",
                "project_state_inputs", "project_state_requests", "project_state_runs",
                "project_state_retry_links"}


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def fingerprints(path):
    """Hash exact SQLite types/bytes without retaining row contents."""
    result = {}
    database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        for name, sql in database.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            columns = [row[1] for row in database.execute("PRAGMA table_info(" + quote(name) + ")")]
            digest = hashlib.sha256(sql.encode("utf-8"))
            count = 0
            for (rowid,) in database.execute("SELECT rowid FROM " + quote(name) + " ORDER BY rowid"):
                count += 1
                digest.update(("row:" + str(rowid) + ";").encode())
                for column in columns:
                    kind = database.execute("SELECT typeof(" + quote(column) + ") FROM " + quote(name) + " WHERE rowid=?", (rowid,)).fetchone()[0]
                    digest.update((column + ":" + kind + ":").encode())
                    if kind in ("text", "blob"):
                        with database.blobopen(name, column, rowid, readonly=True) as blob:
                            digest.update((str(len(blob)) + ":").encode())
                            for block in iter(lambda: blob.read(65536), b""):
                                digest.update(block)
                    else:
                        value = database.execute("SELECT " + quote(column) + " FROM " + quote(name) + " WHERE rowid=?", (rowid,)).fetchone()[0]
                        digest.update(json.dumps(value, allow_nan=False, separators=(",", ":")).encode())
                    digest.update(b";")
            result[name] = {"rows": count, "sha256": digest.hexdigest()}
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("rehearsal_integrity_failed")
        return result
    finally:
        database.close()


def rehearse(snapshot, output, *, expected_new_tables=None, snapshot_sha256=None):
    snapshot, output = Path(snapshot).absolute(), Path(output).absolute()
    info = snapshot.lstat()
    if snapshot.name != "hoomy.sqlite3" or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("invalid_project_snapshot")
    if any(part.is_symlink() for part in [snapshot.parent, *snapshot.parents]):
        raise ValueError("symlink_snapshot_parent")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("output_must_be_new")
    if expected_new_tables is not None and (not set(expected_new_tables) <= STATE_TABLES or len(set(expected_new_tables)) != len(expected_new_tables)):
        raise ValueError("unexpected_migration_table")
    wal = snapshot.with_name(snapshot.name + "-wal")
    if wal.exists() and (wal.is_symlink() or wal.stat().st_size != 0):
        raise ValueError("snapshot_wal_not_empty")
    original_sha = file_digest(snapshot)
    if snapshot_sha256 is not None and snapshot_sha256 != original_sha:
        raise ValueError("snapshot_sha_mismatch")
    if shutil.disk_usage(output.parent).free < 2 * info.st_size + 256 * 1024 * 1024:
        raise ValueError("insufficient_rehearsal_capacity")
    output.mkdir(mode=0o700)
    target = output / "hoomy.sqlite3"
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with snapshot.open("rb") as source, target.open("wb") as copied:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            copied.write(block)
        copied.flush()
        os.fsync(copied.fileno())
    if file_digest(target) != original_sha or file_digest(snapshot) != original_sha:
        raise ValueError("snapshot_sha_mismatch")
    before = fingerprints(target)
    # The original snapshot is never passed to Store's writable initializer.
    store = Store(str(output))
    try:
        after = fingerprints(target)
        if any(after.get(name) != value for name, value in before.items()):
            raise ValueError("existing_table_fingerprints_changed")
        new = sorted(set(after) - set(before))
        if not set(new) <= STATE_TABLES or expected_new_tables is not None and set(new) != set(expected_new_tables):
            raise ValueError("unexpected_migration_table")
        if file_digest(snapshot) != original_sha:
            raise ValueError("snapshot_sha_mismatch")
        return {"status": "rehearsed", "original_opened_read_only": True,
                "existing_table_counts_unchanged": True, "existing_table_fingerprints_unchanged": True,
                "existing_tables_preserved": len(before), "snapshot_sha256": original_sha,
                "snapshot_sha256_unchanged": True, "integrity": "ok", "new_tables": new,
                "state_generation_enabled": bool(store.project_state.enabled_project_id)}
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-db", required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--snapshot-sha256")
    parser.add_argument("--expected-new-table", action="append", dest="expected_new_tables")
    args = parser.parse_args()
    try:
        result = rehearse(args.snapshot_db, args.output_directory, expected_new_tables=args.expected_new_tables, snapshot_sha256=args.snapshot_sha256)
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({"status": "blocked", "failure_code": "migration_rehearsal_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
