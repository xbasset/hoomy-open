"""Private immutable captures and checkpoints committed in one SQLite transaction."""

import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .client import AgentError
from .policy import MAX_EVENT, canonical, sha256
from .storage import private_directory


class Backpressure(AgentError):
    pass


class Outbox:
    def __init__(self, directory, max_bytes=256 * 1024 * 1024, max_events=4096):
        self.directory = private_directory(directory)
        self.path = self.directory / "outbox.sqlite3"
        self.max_bytes, self.max_events = max_bytes, max_events
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            try:
                descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
            except OSError:
                raise AgentError("Cannot open the private outbox safely.") from None
        except OSError:
            raise AgentError("Cannot open the private outbox safely.") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AgentError("Outbox requires an owner-only regular file.")
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.execute("PRAGMA auto_vacuum=FULL")
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS checkpoints (
            source_key TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
            source_digest TEXT NOT NULL, policy_revision INTEGER NOT NULL,
            fingerprint TEXT NOT NULL DEFAULT '');
          CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, source_key TEXT NOT NULL, sequence INTEGER NOT NULL,
            policy_revision INTEGER NOT NULL, event_digest TEXT NOT NULL,
            payload BLOB NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            retry_at INTEGER NOT NULL DEFAULT 0, UNIQUE(source_key,sequence));
          CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        if "fingerprint" not in {row[1] for row in self.db.execute("PRAGMA table_info(checkpoints)")}:
            self.db.execute("ALTER TABLE checkpoints ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")
        if "projection_adapter_revision" not in {row[1] for row in self.db.execute("PRAGMA table_info(checkpoints)")}:
            self.db.execute("ALTER TABLE checkpoints ADD COLUMN projection_adapter_revision INTEGER NOT NULL DEFAULT 1")
            self.db.commit()

    def close(self):
        self.db.close()

    def capture(self, source_key, source_digest, revision, build, fingerprint=None, adapter_revision=1):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self.db.execute("SELECT sequence,source_digest,policy_revision,projection_adapter_revision FROM checkpoints WHERE source_key=?", (source_key,)).fetchone()
            if prior and prior[1:] == (source_digest, revision, adapter_revision):
                self.db.execute("UPDATE checkpoints SET fingerprint=? WHERE source_key=?", (canonical(fingerprint or []).decode(), source_key))
                self.db.commit()
                return None
            sequence = prior[0] + 1 if prior else 1
            event = build(sequence, str(uuid.uuid4()))
            payload = canonical(event)
            used, count = self.db.execute("SELECT coalesce(sum(length(payload)),0),count(*) FROM events").fetchone()
            if len(payload) > MAX_EVENT or used + len(payload) > self.max_bytes or count >= self.max_events:
                raise Backpressure("Outbox capacity reached; capture/checkpoint did not advance.")
            if not prior and self.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] >= 10000:
                raise Backpressure("Source checkpoint capacity reached; capture did not advance.")
            self.db.execute("INSERT INTO events(event_id,source_key,sequence,policy_revision,event_digest,payload) VALUES(?,?,?,?,?,?)",
                            (event["event_id"], source_key, sequence, revision, sha256(payload), payload))
            self.db.execute("INSERT INTO checkpoints(source_key,sequence,source_digest,policy_revision,fingerprint,projection_adapter_revision) VALUES(?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET sequence=excluded.sequence,source_digest=excluded.source_digest,policy_revision=excluded.policy_revision,fingerprint=excluded.fingerprint,projection_adapter_revision=excluded.projection_adapter_revision",
                            (source_key, sequence, source_digest, revision, canonical(fingerprint or []).decode(), adapter_revision))
            self.db.commit()
            return event["event_id"]
        except Exception:
            self.db.rollback()
            raise

    def pending(self, revision, now, limit=8):
        rows = self.db.execute("SELECT event_id,payload,event_digest FROM events WHERE policy_revision=? AND retry_at<=? ORDER BY rowid LIMIT ?", (revision, int(now), limit)).fetchall()
        result = []
        for event_id, payload, digest in rows:
            try:
                event = json.loads(payload)
                if canonical(event) != payload or sha256(payload) != digest or event["event_id"] != event_id or event["policy_revision"] != revision:
                    raise AgentError("Captured outbox bytes failed integrity validation; delivery stopped.")
            except (ValueError, KeyError, UnicodeError):
                raise AgentError("Captured outbox is damaged; delivery stopped.") from None
            result.append((event, digest))
        return result

    def accepted(self, event, digest, receipt):
        if event.get("type") not in ("conversation.projected", "conversation.source_excluded"):
            raise AgentError("Receipt event type is unsupported; capture was retained.")
        expected = {"receipt_id": event["event_id"], "event_id": event["event_id"], "project_id": event["project_id"],
                    "event_sha256": digest,
                    "state": "stored", "inference_state": "not_requested"}
        if event.get("type") == "conversation.source_excluded":
            expected.update(event_type="conversation.source_excluded", header_sha256=event["exclusion"]["header_sha256"])
        else:
            expected["projected_sha256"] = event["projected_sha256"]
        if not isinstance(receipt, dict) or any(receipt.get(key) != value for key, value in expected.items()):
            raise AgentError("Receipt does not match the immutable captured event.")
        if set(receipt) != set(expected) | {"source_version_id", "received_at"}:
            raise AgentError("Receipt does not match the closed event contract.")
        if not isinstance(receipt.get("source_version_id"), str) or not receipt["source_version_id"] or not isinstance(receipt.get("received_at"), str):
            raise AgentError("Receipt is incomplete; queued capture was retained.")
        with self.db:
            self.db.execute("DELETE FROM events WHERE event_id=? AND event_digest=?", (event["event_id"], digest))

    def failed(self, event_id, now):
        with self.db:
            row = self.db.execute("SELECT attempts FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row:
                attempts = min(row[0] + 1, 20)
                self.db.execute("UPDATE events SET attempts=?,retry_at=? WHERE event_id=?", (attempts, int(now) + min(300, 5 * 2 ** min(attempts - 1, 6)), event_id))

    def counts(self):
        count, size = self.db.execute("SELECT count(*),coalesce(sum(length(payload)),0) FROM events").fetchone()
        return {"outbox_count": count, "outbox_bytes": size}

    def cursor(self):
        row = self.db.execute("SELECT value FROM settings WHERE name='scan_cursor'").fetchone()
        return row[0] if row else ""

    def unchanged(self, source_key, fingerprint, revision, adapter_revision=1):
        row = self.db.execute("SELECT fingerprint,policy_revision,projection_adapter_revision FROM checkpoints WHERE source_key=?", (source_key,)).fetchone()
        return bool(row and row == (canonical(list(fingerprint)).decode(), revision, adapter_revision))

    def set_cursor(self, value):
        with self.db:
            self.db.execute("INSERT INTO settings VALUES('scan_cursor',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (value,))
