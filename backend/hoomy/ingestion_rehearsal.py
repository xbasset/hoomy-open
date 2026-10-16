"""Rehearse additive ingestion on an isolated backup copy; emit hashes and counts only."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

from .ingestion import digest
from .store import Store, encode


FAILURE_STAGE = "preflight"


def fingerprints(database, tables=None, row_limits=None):
    if tables is None:
        tables = [row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'ingestion_%' ORDER BY name")]
    result = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        checksum = hashlib.sha256()
        count = 0
        query = "SELECT * FROM " + quoted + " ORDER BY rowid"
        parameters = ()
        if row_limits is not None:
            query += " LIMIT ?"
            parameters = (row_limits[table],)
        for row in database.execute(query, parameters):
            count += 1
            for value in row:
                raw = value if isinstance(value, bytes) else encode(value).encode("utf-8")
                checksum.update(type(value).__name__.encode("ascii") + b":" + str(len(raw)).encode("ascii") + b":" + raw)
        result[table] = {"rows": count, "sha256": checksum.hexdigest()}
    return result


def rehearse(backup, destination, project_id):
    global FAILURE_STAGE
    FAILURE_STAGE = "preflight"
    backup = Path(backup).resolve(strict=True)
    destination = Path(destination).resolve()
    if not backup.is_file() or (destination / "hoomy.sqlite3").exists() or backup.parent == destination:
        raise ValueError("Use an existing backup and a fresh isolated destination.")
    if any((parent / ".git").exists() for parent in (destination, *destination.parents)):
        raise ValueError("Rehearsal data must remain outside a Git checkout.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(destination).free < backup.stat().st_size * 1.2 + 100 * 1024 * 1024:
        raise ValueError("Insufficient free space for a bounded rehearsal copy.")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(backup) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError("A frozen checkpointed backup is required.")
    source_stat = backup.stat()
    source_identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    FAILURE_STAGE = "backup_copy"
    # immutable avoids WAL/SHM creation on a read-only, frozen backup mount.
    original = sqlite3.connect(backup.as_uri() + "?mode=ro&immutable=1", uri=True)
    copied = sqlite3.connect(destination / "hoomy.sqlite3")
    try:
        original.backup(copied)
    finally:
        original.close()
    if source_identity(backup.stat()) != source_identity(source_stat):
        copied.close()
        raise ValueError("The backup changed during rehearsal copying.")
    FAILURE_STAGE = "legacy_baseline"
    try:
        if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The isolated backup copy failed integrity checking.")
        before = fingerprints(copied)
        ingestion_tables = [row[0] for row in copied.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ingestion_%' ORDER BY name")]
        ingestion_before = fingerprints(copied, ingestion_tables)
    finally:
        copied.close()
    FAILURE_STAGE = "additive_initialization"
    store = Store(destination)
    try:
        if fingerprints(store.db, before) != before:
            raise ValueError("Store initialization changed legacy evidence or context.")
        if fingerprints(store.db, ingestion_tables) != ingestion_before:
            raise ValueError("Store initialization changed existing ingestion history.")
        FAILURE_STAGE = "project_context"
        profile = store.dialogue.profile(project_id)
        projection = {"version": 1, "messages": [{"role": "user", "text": "Isolated migration rehearsal evidence.",
                                                  "line_start": 2, "line_end": 2, "timestamp": None}]}
        event = {"schema_version": 1, "event_id": str(uuid.uuid4()), "type": "conversation.projected",
            "project_id": project_id, "policy_revision": 2, "connector": "codex", "session_id": "rehearsal-session",
            "source_location": {"path": "/fictional-rehearsal/sessions/source.jsonl", "cwd": "/fictional-rehearsal/project",
                "archive_occurrence": False, "modified_at": "2026-10-12T12:00:00Z"}, "sequence": 1,
            "source_sha256": hashlib.sha256(b"declared fictional rehearsal source").hexdigest(),
            "projected_sha256": digest(projection), "observed_at": "2026-10-12T12:01:00Z", "title": "Isolated migration fixture",
            "coverage": {"excluded_records": 0, "excluded_sensitive_messages": 0, "excluded_context_messages": 0, "excluded_oversized_records": 0}, "projection": projection}
        payload = {"principal": {"account_id": "rehearsal-account", "workspace_id": "rehearsal-workspace", "device_id": "rehearsal-device"},
            "policy": {"id": "rehearsal-device", "revision": 2, "content_tier": "conversation_visible_v1", "collection_enabled": True,
                "roots": ["/fictional-rehearsal/sessions"], "exclusions": [],
                "project_bindings": [{"project_id": project_id, "cwd_roots": ["/fictional-rehearsal/project"]}]}, "event": event}
        FAILURE_STAGE = "fixture_ingestion"
        receipt = store.ingestion.accept(payload)
        # Identical projections retain distinct device occurrences in the isolated copy.
        other = json.loads(encode(payload))
        other["principal"]["device_id"] = other["policy"]["id"] = "rehearsal-device-b"
        other["event"]["event_id"] = str(uuid.uuid4())
        store.ingestion.accept(other)
        projected_coverage = store.ingestion.coverage(project_id)
        excluded = json.loads(encode(payload))
        excluded_event = excluded["event"]
        for key in ("projection", "coverage", "title", "source_sha256", "projected_sha256"):
            del excluded_event[key]
        excluded_event.update(event_id=str(uuid.uuid4()), type="conversation.source_excluded", sequence=2,
            exclusion={"reason": "internal_approval_review", "discriminator": "internal_guardian",
                       "header_sha256": hashlib.sha256(b"fictional internal rehearsal header").hexdigest()})
        excluded_receipt = store.ingestion.accept(excluded)
        if store.ingestion.accept(excluded) != excluded_receipt:
            raise ValueError("Excluded source replay changed its receipt.")
        FAILURE_STAGE = "legacy_preservation"
        if store.ingestion.accept(payload) != receipt or fingerprints(store.db, before) != before:
            raise ValueError("Ingestion changed legacy data or failed exact replay.")
        if fingerprints(store.db, ingestion_tables, {table: value["rows"] for table, value in ingestion_before.items()}) != ingestion_before:
            raise ValueError("Fixture ingestion changed retained ingestion history.")
        updated = store.dialogue.profile(project_id)
        if updated["revision"] != profile["revision"] or updated["fields"] != profile["fields"]:
            raise ValueError("Ingestion changed approved project context.")
        if store.db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The migrated rehearsal copy failed integrity checking.")
        result = {"integrity": "ok", "legacy_preserved": True, "context_revision_preserved": True,
                  "fixture_data_present": True, "deployment_safe": False,
                  "legacy_fingerprints": before, "fixture_projection_sha256": digest(projection),
                  "existing_ingestion_fingerprints": ingestion_before, "existing_ingestion_preserved": True,
                  "projected_coverage": projected_coverage, "excluded_coverage": store.ingestion.coverage(project_id),
                  "excluded_receipt_replay": True}
        FAILURE_STAGE = "checkpoint"
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return result
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(rehearse(args.backup, args.data_dir, args.project_id), sort_keys=True))
    except Exception as error:
        # Imported content and filesystem details must not escape via diagnostics.
        print(json.dumps({"integrity": "failed", "failure_stage": FAILURE_STAGE,
                          "error_type": type(error).__name__,
                          **({"sqlite_error_code": error.sqlite_errorcode, "sqlite_error_name": error.sqlite_errorname}
                             if isinstance(error, sqlite3.Error) and hasattr(error, "sqlite_errorcode") else {})}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
