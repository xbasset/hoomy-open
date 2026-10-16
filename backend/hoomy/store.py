"""Private, versioned discovery state; model assertions never overwrite corrections."""

import base64
import binascii
import hashlib
import json
import math
import os
import posixpath
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SECTIONS = ("intentions", "outcomes", "open_questions", "next_steps")
STATUSES = ("unclear", "active", "paused", "completed")


class StoreError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def require_object(value):
    if not isinstance(value, dict):
        raise StoreError("Expected a JSON object.")
    return value


def string(value, label, *, empty=False, maximum=100000):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum:
        raise StoreError("Invalid " + label + ".")
    return value


def identifier(value):
    return string(value, "identifier", maximum=512)


def boolean(value, label):
    if not isinstance(value, bool):
        raise StoreError("Invalid " + label + ".")
    return value


def timestamp(value):
    value = string(value, "timestamp", maximum=80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise StoreError("Invalid timestamp.") from None
    if parsed.tzinfo is None:
        raise StoreError("Timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def lexical_absolute(value):
    """Normalize a host POSIX path without resolving it inside the container."""
    if not value or "\x00" in value:
        return None
    normalized = posixpath.normpath(value)
    if not normalized.startswith("/") or normalized.startswith("//"):
        return None
    return normalized


def within(candidate, root):
    return candidate == root or root == "/" or candidate.startswith(root + "/")


def central_origin(origin):
    string(origin, "central origin", maximum=2048)
    try:
        parsed = urlsplit(origin)
        parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or "\\" in origin or any(char.isspace() or ord(char) < 32 for char in origin)):
            raise ValueError()
    except ValueError:
        raise StoreError("A credential-free HTTPS origin is required.") from None
    return origin.rstrip("/")


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        database = self.data_dir / "hoomy.sqlite3"
        descriptor = os.open(database, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(descriptor)
        database.chmod(0o600)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.execute("PRAGMA secure_delete = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, status TEXT NOT NULL,
                failure TEXT, assignment_locked INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, source_path TEXT NOT NULL,
                modified_at TEXT NOT NULL, cwd TEXT NOT NULL, raw BLOB NOT NULL,
                messages TEXT NOT NULL, warnings TEXT NOT NULL, title TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analyses (
                snapshot_id TEXT PRIMARY KEY, brief TEXT NOT NULL, proposals TEXT NOT NULL,
                method TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, inferred TEXT NOT NULL, overrides TEXT NOT NULL,
                merged_into TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS aliases (name TEXT PRIMARY KEY, project_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_freshness (project_id TEXT PRIMARY KEY, source_time TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS associations (
                session_id TEXT NOT NULL, project_id TEXT NOT NULL,
                PRIMARY KEY (session_id, project_id)
            );
            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, kind TEXT NOT NULL,
                summary TEXT NOT NULL, before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                evidence TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                snapshot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)
        with self.db:
            for key, value in {
                "control": {"running": False, "inference_enabled": False,
                            "scope_confirmed": False, "exclusions": [], "generation": str(uuid.uuid4())},
                "worker": {"last_seen": None, "status": None, "message": None},
                "discovery": {"excluded": 0, "failures": []},
            }.items():
                self.db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, encode(value)))
            control = self._get("control")
            control.setdefault("scope_confirmed", False)
            control.setdefault("generation", str(uuid.uuid4()))
            control.setdefault("mode", "overview")
            self._set("control", control)
        from .overview import Overview
        self.overview = Overview(self)
        from .act import Act
        self.act = Act(self)
        from .project_dialogue import ProjectDialogue
        self.dialogue = ProjectDialogue(self)
        from .ideas import Ideas
        self.ideas = Ideas(self)
        from .research_explorer import ResearchExplorer
        self.explorer = ResearchExplorer(self)
        from .ingestion import Ingestion
        self.ingestion = Ingestion(self)
        from .project_state import ProjectState
        self.project_state = ProjectState(self)
        for filename in ("hoomy.sqlite3-wal", "hoomy.sqlite3-shm"):
            sidecar = self.data_dir / filename
            if sidecar.exists():
                sidecar.chmod(0o600)

    def close(self):
        self.db.close()

    def _get(self, key):
        return json.loads(self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0])

    def _set(self, key, value):
        self.db.execute("UPDATE settings SET value=? WHERE key=?", (encode(value), key))

    def _generation(self, payload):
        if payload.get("generation") != self._get("control")["generation"]:
            raise StoreError("Discovery was reset; discard old work and refresh state.", 409)

    def _current(self, session_id):
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier(session_id),)).fetchone()
        if row is None:
            raise StoreError("Conversation not found.", 404)
        return row

    def _fresh(self, session_id, snapshot_id):
        row = self._current(session_id)
        if row["snapshot_id"] != identifier(snapshot_id):
            raise StoreError("Conversation changed; analyze its current snapshot.", 409)
        return row

    def _resolve(self, project_id):
        project_id = identifier(project_id)
        seen = set()
        while project_id not in seen:
            seen.add(project_id)
            row = self.db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise StoreError("Project not found.", 404)
            if row["merged_into"] is None:
                return row
            project_id = row["merged_into"]
        raise StoreError("Invalid project redirect.", 409)

    def instance_authority(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='instance_authority'").fetchone()
        if row is None:
            return None
        try:
            authority = json.loads(row[0])
            if not isinstance(authority, dict) or set(authority) != {"mode", "origin"} or authority["mode"] != "central_read_only":
                raise ValueError()
            central_origin(authority["origin"])
        except (ValueError, TypeError, StoreError):
            raise StoreError("La configuration d’autorité centrale est indisponible.", 503) from None
        return authority

    def require_instance_writable(self):
        if self.instance_authority():
            raise StoreError("Hoomy local est une archive centralisée. Utilise Hoomy Remote Acceptance pour lire et modifier la référence centrale.", 409)

    def configure_instance_authority(self, origin):
        """Operator-only archive switch; preserve business scope and per-project fences."""
        origin = central_origin(origin) if origin is not None else None
        with self.lock, self.db:
            if origin is None:
                self.db.execute("DELETE FROM settings WHERE key='instance_authority'")
                return None
            authority = {"mode": "central_read_only", "origin": origin}
            self.db.execute("INSERT INTO settings VALUES ('instance_authority',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(authority),))
            control = self._get("control")
            control.update(running=False, inference_enabled=False)
            self._set("control", control)
            return authority

    def project_authority(self, project_id):
        instance = self.instance_authority()
        if instance:
            return instance
        row = self.db.execute("SELECT value FROM settings WHERE key='project_authorities'").fetchone()
        if row is None:
            return None
        authorities = json.loads(row[0])
        if not isinstance(authorities, dict):
            raise StoreError("La configuration d’autorité du projet est indisponible.", 503)
        origin = authorities.get(self._resolve(project_id)["id"])
        return {"mode": "central_read_only", "origin": origin} if origin else None

    def require_project_writable(self, project_id):
        if self.project_authority(project_id):
            raise StoreError("Ce projet est centralisé. Modifie son contexte et son identité depuis la connexion Hoomy distante.", 409)

    def configure_project_authority(self, project_id, origin):
        """Operator-only configuration; no public or private HTTP mutation route."""
        if origin is not None:
            origin = central_origin(origin)
        with self.lock, self.db:
            pid = self._resolve(project_id)["id"]
            row = self.db.execute("SELECT value FROM settings WHERE key='project_authorities'").fetchone()
            authorities = json.loads(row[0]) if row else {}
            if origin:
                authorities[pid] = origin
            else:
                authorities.pop(pid, None)
            self.db.execute("INSERT INTO settings VALUES ('project_authorities',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (encode(authorities),))
            return self.project_authority(pid)

    def _project(self, row):
        inferred, overrides = json.loads(row["inferred"]), json.loads(row["overrides"])
        result = {"name": "", "purpose": "", "status": "unclear", "note": "", "dismissed": False,
                  "activity": "active", "recognition_rules": ""}
        result.update(inferred)
        result.update(overrides)
        linked = [r[0] for r in self.db.execute("SELECT session_id FROM associations WHERE project_id=? ORDER BY rowid", (row["id"],))]
        linked.extend(r[0] for r in self.db.execute("SELECT session_id FROM catalog_links WHERE project_id=? ORDER BY rowid", (row["id"],)))
        if hasattr(self, "ingestion"):
            linked.extend(self.ingestion.source_ids(row["id"]))
        result.update({"id": row["id"], "confirmed_fields": list(overrides),
                       "updated_at": row["updated_at"], "session_ids": list(dict.fromkeys(linked))})
        return result

    def _change(self, project_id, kind, summary, before, after, evidence=None):
        self.db.execute("INSERT INTO changes (project_id,kind,summary,before_state,after_state,evidence,created_at) "
                        "VALUES (?,?,?,?,?,?,?)", (project_id, kind, summary, encode(before), encode(after),
                                                 encode(evidence or []), now()))

    def health(self):
        with self.lock:
            if self.db.execute("SELECT 1 FROM settings WHERE key='control'").fetchone() is None:
                raise StoreError("The local service is not ready.", 503)
            authority = self.instance_authority()
            return {"status": "ok", **({"authority": authority} if authority else {})}

    def project_list(self):
        with self.lock:
            return {"projects": [self._project(row) for row in self.db.execute(
                "SELECT * FROM projects WHERE merged_into IS NULL ORDER BY rowid")]}

    def _session_metadata(self, session_ids=None):
        from .catalog import clean_title
        with self.lock:
            sessions = []
            counts = {"analyzed": 0, "pending": 0, "failed": 0}
            ids = tuple(dict.fromkeys(session_ids)) if session_ids is not None else None
            if ids == ():
                return sessions, counts
            selection = " WHERE s.id IN (" + ",".join("?" for _ in ids) + ")" if ids is not None else ""
            query = "SELECT s.id,s.snapshot_id,s.status,s.failure,p.title,p.modified_at,p.warnings " \
                    "FROM sessions s JOIN snapshots p ON p.id=s.snapshot_id" + selection + " ORDER BY p.modified_at DESC,s.id"
            for row in self.db.execute(query, ids or ()):
                item = dict(row)
                warnings = json.loads(item.pop("warnings"))
                item["warning"] = item.pop("failure") or ("; ".join(warnings) if warnings else None)
                sessions.append(item)
                counts[item["status"]] += 1
            known = {item["id"]: item for item in sessions}
            selection = " WHERE session_id IN (" + ",".join("?" for _ in ids) + ")" if ids is not None else ""
            for row in self.db.execute("SELECT metadata FROM catalog" + selection + " ORDER BY rowid", ids or ()):
                metadata = json.loads(row[0])
                metadata["title"] = clean_title(metadata["title"]) or "Conversation sans titre exploitable"
                if metadata["session_id"] in known:
                    existing = known[metadata["session_id"]]
                    existing["title"] = metadata["title"]
                    if existing["status"] == "analyzed" and timestamp(metadata["modified_at"]) > timestamp(existing["modified_at"]):
                        existing["status"] = "updated"
                else:
                    sessions.append({"id": metadata["session_id"], "snapshot_id": "", "title": metadata["title"],
                                     "modified_at": metadata["modified_at"], "status": "indexed", "warning": None})
            remote = self.ingestion.metadata(ids)
            sessions.extend(remote)
            if remote:
                counts["collected"] = len(remote)
            return sessions, counts

    def session_metadata(self, session_ids=None):
        return self._session_metadata(session_ids)[0]

    def state(self):
        with self.lock:
            sessions, counts = self._session_metadata()
            discovery = self._get("discovery")
            coverage = {"discovered": len(sessions) + discovery["excluded"] + len(discovery["failures"]),
                        **counts, "excluded": discovery["excluded"]}
            coverage["failed"] += len(discovery["failures"])
            changes = []
            for row in self.db.execute("SELECT * FROM changes ORDER BY id DESC LIMIT 200"):
                item = dict(row)
                item["id"] = str(item["id"])
                item["before"] = json.loads(item.pop("before_state"))
                item["after"] = json.loads(item.pop("after_state"))
                item["evidence"] = json.loads(item["evidence"])
                changes.append(item)
            authority = self.instance_authority()
            return {**({"authority": authority} if authority else {}),
                    "act_pending": self.db.execute("SELECT 1 FROM act_requests WHERE status='queued'").fetchone() is not None,
                    "idea_checks_pending": self.db.execute("SELECT 1 FROM ideas WHERE json_extract(data,'$.verification_due')=1 AND coalesce(json_extract(data,'$.pool_active'),1)=1 AND coalesce(json_extract(data,'$.verification_attempts'),0)<1").fetchone() is not None,
                    "control": self._get("control"), "worker": self._get("worker"), "coverage": coverage,
                    "projects": [self._project(row) for row in self.db.execute(
                        "SELECT * FROM projects WHERE merged_into IS NULL ORDER BY rowid")],
                    "changes": changes, "sessions": sessions, "discovery_failures": discovery["failures"],
                    "overview": self.overview.summary()}

    def control(self, payload):
        require_object(payload)
        if set(payload) - {"running", "inference_enabled", "exclusions", "consent", "mode"}:
            raise StoreError("Unknown control field.")
        with self.lock, self.db:
            current = self._get("control")
            for field in ("running", "inference_enabled", "consent"):
                if field in payload:
                    boolean(payload[field], field)
            if payload.get("running") and not current["scope_confirmed"] and payload.get("consent") is not True:
                raise StoreError("Confirm discovery scope before starting.", 409)
            if payload.get("inference_enabled") and not current["inference_enabled"] and payload.get("consent") is not True:
                raise StoreError("Consent is required before external inference.", 409)
            if "exclusions" in payload:
                excluded = payload["exclusions"]
                if not isinstance(excluded, list) or len(excluded) > 1000:
                    raise StoreError("Invalid exclusions.")
                normalized = []
                for value in excluded:
                    value = string(value, "exclusion", maximum=4096)
                    value = lexical_absolute(value)
                    if value is None:
                        raise StoreError("Exclusions must be absolute paths.")
                    normalized.append(value)
                excluded = list(dict.fromkeys(normalized))
                if excluded != current["exclusions"] and (self.db.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone() or
                                                         self.db.execute("SELECT 1 FROM catalog LIMIT 1").fetchone()):
                    raise StoreError("Scope cannot change after ingestion without an explicit deletion workflow.", 409)
                current["exclusions"] = excluded
            if payload.get("consent") is True:
                current["scope_confirmed"] = True
            if payload.get("running") is False and current["running"]:
                plan = self._get("overview")
                if plan.get("completion_phase") == "agent":
                    # Reject late agent output even if pause is immediately followed by resume.
                    plan["request_id"] = str(uuid.uuid4())
                    self.db.execute("UPDATE catalog SET scan_id=? WHERE scan_id=?",
                                    (plan["request_id"], self._get("overview")["request_id"]))
                    self._set("overview", plan)
            for field in ("running", "inference_enabled"):
                if field in payload:
                    current[field] = payload[field]
            if "mode" in payload:
                if payload["mode"] not in ("overview", "deep", "full"):
                    raise StoreError("Invalid discovery mode.")
                current["mode"] = payload["mode"]
            if payload.get("running") and not self._get("control")["running"]:
                plan = self._get("overview")
                if current["mode"] == "deep" and plan.get("deep_project_id"):
                    if self._project(self._resolve(plan["deep_project_id"]))["activity"] == "inactive":
                        raise StoreError("Réactivez ce projet avant de reprendre la lecture détaillée.", 409)
                if plan["stage"] == "awaiting_consent" and current["inference_enabled"]:
                    plan.update(stage="classifying", local_only=False, message=None)
                    self._set("overview", plan)
                if plan["stage"] == "error":
                    plan.update(stage="deepening" if current["mode"] == "deep" else
                                ("classifying" if plan["inventory_complete"] else "inventory"),
                                retry_count=0, retry_at=0, message=None)
                    self._set("overview", plan)
            self._set("control", current)
            return current

    def worker(self, payload):
        require_object(payload)
        result = {"status": string(payload.get("status"), "worker status", maximum=100),
                  "message": string(payload.get("message", ""), "worker message", empty=True, maximum=1000),
                  "last_seen": now()}
        with self.lock, self.db:
            self._generation(payload)
            self._set("worker", result)
        return result

    def ingest(self, payload):
        require_object(payload)
        session_id, snapshot_id = identifier(payload.get("session_id")), identifier(payload.get("snapshot_id"))
        source_path = string(payload.get("source_path"), "source path", maximum=4096)
        modified_at = timestamp(payload.get("modified_at"))
        cwd = string(payload.get("cwd", ""), "working directory", empty=True, maximum=4096)
        try:
            raw = base64.b64decode(string(payload.get("raw_b64"), "raw snapshot", empty=True,
                                          maximum=80 * 1024 * 1024), validate=True)
        except (ValueError, binascii.Error):
            raise StoreError("Invalid snapshot encoding.") from None
        if hashlib.sha256(raw).hexdigest() != snapshot_id:
            raise StoreError("Snapshot identifier must match the SHA-256 of the raw bytes.")
        messages = payload.get("messages")
        if not isinstance(messages, list) or len(messages) > 200000:
            raise StoreError("Invalid messages.")
        message_ids = set()
        for message in messages:
            require_object(message)
            mid = identifier(message.get("id"))
            if mid in message_ids:
                raise StoreError("Duplicate message identifier.")
            message_ids.add(mid)
            string(message.get("role"), "message role", maximum=100)
            string(message.get("text"), "message text", empty=True, maximum=32 * 1024 * 1024)
            for field in ("line_start", "line_end"):
                if type(message.get(field)) is not int or message[field] < 1:
                    raise StoreError("Invalid source line reference.")
            if message["line_end"] < message["line_start"]:
                raise StoreError("Invalid source line range.")
            if message.get("timestamp") is not None:
                timestamp(message["timestamp"])
        warnings = payload.get("warnings", [])
        if not isinstance(warnings, list) or len(warnings) > 1000:
            raise StoreError("Invalid warnings.")
        for warning in warnings:
            string(warning, "warning", maximum=1000)
        from .catalog import clean_title
        title = clean_title(payload.get("title")) or next((clean_title(m["text"].strip().splitlines()[0]) for m in messages
                      if m["role"] == "user" and m["text"].strip() and clean_title(m["text"].strip().splitlines()[0])), "Conversation sans titre")
        with self.lock, self.db:
            self._generation(payload)
            control = self._get("control")
            if not control["scope_confirmed"]:
                raise StoreError("Confirm discovery scope before ingestion.", 409)
            normalized_cwd = lexical_absolute(cwd)
            if control["exclusions"] and normalized_cwd is None:
                raise StoreError("Conversation scope could not be verified.", 403)
            cwd = normalized_cwd or ""
            for prefix in control["exclusions"]:
                if within(cwd, prefix):
                    raise StoreError("Conversation is outside the approved scope.", 403)
            existing = self.db.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if existing is not None:
                if (existing["session_id"] != session_id or existing["raw"] != raw or
                        existing["messages"] != encode(messages)):
                    raise StoreError("Snapshot identifier already describes different content.", 409)
                return {"session_id": session_id, "changed": False}
            self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", (
                snapshot_id, session_id, source_path, modified_at, cwd, raw, encode(messages), encode(warnings), title, now()))
            current = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if current is None:
                self.db.execute("INSERT INTO sessions (id,snapshot_id,status) VALUES (?,?,?)", (session_id, snapshot_id, "pending"))
            else:
                self.db.execute("UPDATE sessions SET snapshot_id=?,status='pending',failure=NULL WHERE id=?", (snapshot_id, session_id))
            recognized = self.db.execute("SELECT project_id FROM catalog_links WHERE session_id=?", (session_id,)).fetchall()
            for project in recognized:
                self.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (session_id, project[0]))
            if recognized or self.db.execute("SELECT 1 FROM title_decisions WHERE session_id=?", (session_id,)).fetchone():
                self.db.execute("UPDATE sessions SET assignment_locked=1 WHERE id=?", (session_id,))
            return {"session_id": session_id, "changed": True}

    def discovery(self, payload):
        require_object(payload)
        if type(payload.get("excluded")) is not int or payload["excluded"] < 0:
            raise StoreError("Invalid excluded count.")
        failures = payload.get("failures", [])
        if not isinstance(failures, list) or len(failures) > 100000:
            raise StoreError("Invalid discovery failures.")
        unique = {}
        for failure in failures:
            require_object(failure)
            sid = identifier(failure.get("source_id"))
            unique[sid] = {"source_id": sid, "reason": string(failure.get("reason"), "failure reason", maximum=1000)}
        result = {"excluded": payload["excluded"], "failures": list(unique.values())}
        with self.lock, self.db:
            self._generation(payload)
            self._set("discovery", result)
        return result

    def session(self, session_id):
        from .catalog import clean_title
        with self.lock:
            if identifier(session_id).startswith("remote:"):
                raise StoreError("Remote evidence requires a project-scoped cached read.", 403)
            if self.db.execute("SELECT 1 FROM sessions WHERE id=?", (identifier(session_id),)).fetchone() is None:
                row = self.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (session_id,)).fetchone()
                if row:
                    metadata = json.loads(row[0])
                    return {"id": session_id, "snapshot_id": "", "title": clean_title(metadata["title"]) or "Conversation sans titre exploitable",
                            "modified_at": metadata["modified_at"], "status": "indexed", "warning": None,
                            "messages": [], "brief": None, "review": None}
            current = self._current(session_id)
            return self.snapshot(current["snapshot_id"])

    def snapshot(self, snapshot_id):
        with self.lock:
            snapshot = self.db.execute("SELECT * FROM snapshots WHERE id=?", (identifier(snapshot_id),)).fetchone()
            if snapshot is None:
                raise StoreError("Snapshot not found.", 404)
            current = self._current(snapshot["session_id"])
            is_current = current["snapshot_id"] == snapshot_id
            analysis = self.db.execute("SELECT * FROM analyses WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            review = self.db.execute("SELECT payload FROM reviews WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            result = {key: snapshot[key] for key in ("source_path", "modified_at", "cwd", "title", "created_at")}
            warnings = json.loads(snapshot["warnings"])
            result.update({"id": snapshot["session_id"], "snapshot_id": snapshot_id, "is_current": is_current,
                           "status": current["status"] if is_current else ("analyzed" if analysis else "pending"),
                           "warning": (current["failure"] if is_current else None) or ("; ".join(warnings) if warnings else None), "warnings": warnings,
                           "messages": json.loads(snapshot["messages"]), "brief": json.loads(analysis["brief"]) if analysis else None,
                           "method": json.loads(analysis["method"]) if analysis else None,
                           "review": json.loads(review[0]) if review else None})
            metadata = self.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (snapshot["session_id"],)).fetchone()
            if metadata:
                from .catalog import clean_title
                result["title"] = clean_title(json.loads(metadata[0])["title"]) or "Conversation sans titre exploitable"
            return result

    def _evidence(self, evidence, messages):
        if not isinstance(evidence, list) or not evidence or len(evidence) > 100:
            raise StoreError("Every assertion needs source evidence.")
        for citation in evidence:
            require_object(citation)
            mid = identifier(citation.get("message_id"))
            quote = string(citation.get("quote"), "evidence quote", maximum=100000)
            if mid not in messages or quote not in messages[mid]:
                raise StoreError("Evidence must quote an exact span of a referenced message.")

    def analysis(self, session_id, payload):
        require_object(payload)
        with self.lock, self.db:
            self._generation(payload)
            current = self._fresh(session_id, payload.get("snapshot_id"))
            existing = self.db.execute("SELECT 1 FROM analyses WHERE snapshot_id=?", (current["snapshot_id"],)).fetchone()
            if existing:
                raise StoreError("This snapshot already has an immutable analysis.", 409)
            messages = {m["id"]: m["text"] for m in self.session(session_id)["messages"]}
            brief = require_object(payload.get("brief"))
            if set(brief) != set(SECTIONS):
                raise StoreError("Brief must contain all four supported sections.")
            for section in SECTIONS:
                if not isinstance(brief[section], list) or len(brief[section]) > 100:
                    raise StoreError("Invalid brief section.")
                for statement in brief[section]:
                    require_object(statement)
                    string(statement.get("text"), "statement", maximum=10000)
                    if statement.get("kind") not in ("reported", "inferred", "suggested"):
                        raise StoreError("Invalid statement kind.")
                    self._evidence(statement.get("evidence"), messages)
            proposals = payload.get("projects")
            if not isinstance(proposals, list) or len(proposals) > 100:
                raise StoreError("Invalid project proposals.")
            method = require_object(payload.get("method"))
            if not method or len(method) > 30:
                raise StoreError("Analysis method metadata is required.")
            for key, value in method.items():
                string(key, "method field", maximum=100)
                if not isinstance(value, (str, int, float, bool, type(None))) or (isinstance(value, float) and not math.isfinite(value)):
                    raise StoreError("Method metadata must use finite scalar values.")
                if isinstance(value, str):
                    string(value, "method value", empty=True, maximum=2000)
            # Validate the whole response before any identity or association mutation.
            for proposal in proposals:
                require_object(proposal)
                for field in ("name", "purpose", "rationale"):
                    string(proposal.get(field), "project " + field, maximum=10000)
                if proposal.get("status") not in STATUSES:
                    raise StoreError("Invalid project status.")
                self._evidence(proposal.get("evidence"), messages)
                if proposal.get("existing_project_id") is not None:
                    self._resolve(proposal["existing_project_id"])
            previous_ids = {r[0] for r in self.db.execute("SELECT project_id FROM associations WHERE session_id=?", (session_id,))}
            source_time = self.db.execute("SELECT modified_at FROM snapshots WHERE id=?", (current["snapshot_id"],)).fetchone()[0]
            linked = set()
            for proposal in proposals:
                row = None
                if proposal.get("existing_project_id"):
                    row = self._resolve(proposal["existing_project_id"])
                else:
                    alias = self.db.execute("SELECT project_id FROM aliases WHERE name=?", (proposal["name"].strip().casefold(),)).fetchone()
                    if alias:
                        row = self._resolve(alias[0])
                if current["assignment_locked"] and (row is None or row["id"] not in previous_ids):
                    continue
                is_new = row is None
                before = {} if is_new else self._project(row)
                inferred = {field: proposal[field] for field in ("name", "purpose", "status")}
                evidence = [{"session_id": session_id, "snapshot_id": current["snapshot_id"], **e} for e in proposal["evidence"]]
                if row is None:
                    pid, created = str(uuid.uuid4()), now()
                    self.db.execute("INSERT INTO projects VALUES (?,?,?,NULL,?,?)", (pid, encode(inferred), "{}", created, created))
                    self.db.execute("INSERT INTO project_freshness VALUES (?,?)", (pid, source_time))
                    row = self._resolve(pid)
                else:
                    # New observations update only inferred values; overrides remain authoritative.
                    freshness = self.db.execute("SELECT source_time FROM project_freshness WHERE project_id=?", (row["id"],)).fetchone()
                    if freshness is None or timestamp(source_time) >= timestamp(freshness[0]):
                        self.db.execute("UPDATE projects SET inferred=?,updated_at=? WHERE id=?", (encode(inferred), now(), row["id"]))
                        self.db.execute("INSERT INTO project_freshness VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET source_time=excluded.source_time", (row["id"], source_time))
                    row = self._resolve(row["id"])
                linked.add(row["id"])
                self.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (session_id, row["id"]))
                after = self._project(row)
                if is_new:
                    self._change(row["id"], "discovered", proposal["rationale"], before, after, evidence)
                elif any(before[field] != after[field] for field in ("name", "purpose", "status")):
                    self._change(row["id"], "revised", proposal["rationale"], before, after, evidence)
                elif before["session_ids"] != after["session_ids"]:
                    self._change(row["id"], "connected", proposal["rationale"], before, after, evidence)
            if not current["assignment_locked"]:
                for removed in previous_ids - linked:
                    self.db.execute("DELETE FROM associations WHERE session_id=? AND project_id=?", (session_id, removed))
            self.db.execute("INSERT INTO analyses VALUES (?,?,?,?,?)", (current["snapshot_id"], encode(brief), encode(proposals), encode(method), now()))
            self.db.execute("UPDATE sessions SET status='analyzed',failure=NULL WHERE id=?", (session_id,))
            return {"session_id": session_id, "status": "analyzed"}

    def failure(self, session_id, payload):
        require_object(payload)
        reason = string(payload.get("reason"), "failure reason", maximum=1000)
        with self.lock, self.db:
            self._generation(payload)
            current = self._fresh(session_id, payload.get("snapshot_id"))
            if current["status"] == "analyzed":
                raise StoreError("Cannot fail an already analyzed snapshot.", 409)
            self.db.execute("UPDATE sessions SET status='failed',failure=? WHERE id=?", (reason, session_id))
        return {"session_id": session_id, "status": "failed"}

    def retry(self, session_id):
        with self.lock, self.db:
            current = self._current(session_id)
            if current["status"] == "analyzed":
                raise StoreError("This snapshot is already analyzed.", 409)
            self.db.execute("UPDATE sessions SET status='pending',failure=NULL WHERE id=?", (session_id,))
        return {"session_id": session_id, "status": "pending"}

    def correct(self, project_id, payload):
        require_object(payload)
        fields = {"name", "purpose", "status", "note", "dismissed", "activity", "recognition_rules"}
        if not payload or set(payload) - fields:
            raise StoreError("Provide supported correction fields.")
        for field, value in payload.items():
            if field == "dismissed":
                boolean(value, field)
            else:
                string(value, field, empty=field in ("purpose", "note", "recognition_rules"), maximum=2000 if field == "recognition_rules" else 10000)
        if "activity" in payload and payload["activity"] not in ("active", "inactive"):
            raise StoreError("Invalid project activity.")
        if "status" in payload and payload["status"] not in STATUSES:
            raise StoreError("Invalid project status.")
        with self.lock, self.db:
            row = self._resolve(project_id)
            self.require_project_writable(row["id"])
            before = self._project(row)
            overrides = json.loads(row["overrides"])
            overrides.update(payload)
            self.db.execute("UPDATE projects SET overrides=?,updated_at=? WHERE id=?", (encode(overrides), now(), row["id"]))
            if "name" in payload:
                self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)", (before["name"].strip().casefold(), row["id"]))
                self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)", (payload["name"].strip().casefold(), row["id"]))
            after = self._project(self._resolve(row["id"]))
            if payload.get("activity") == "inactive":
                plan = self._get("overview")
                control = self._get("control")
                if control["mode"] == "deep" and plan.get("deep_project_id") == row["id"]:
                    control["running"] = False
                    self._set("control", control)
            self._change(row["id"], "user_correction", "You corrected this project.", before, after)
            return after

    def merge(self, payload):
        require_object(payload)
        sources = payload.get("source_ids")
        if not isinstance(sources, list) or not sources or len(sources) > 100:
            raise StoreError("Select at least one source project.")
        with self.lock, self.db:
            if self._get("control")["running"]:
                raise StoreError("Mettez la lecture en pause avant de regrouper les projets.", 409)
            target = self._resolve(payload.get("target_id"))
            source_rows = {row["id"]: row for row in (self._resolve(sid) for sid in sources)}
            source_rows.pop(target["id"], None)
            for project in (target, *source_rows.values()):
                self.require_project_writable(project["id"])
            if not source_rows:
                raise StoreError("Select distinct source and target projects.")
            before = {"target": self._project(target), "sources": [self._project(row) for row in source_rows.values()]}
            overrides = json.loads(target["overrides"])
            for field in ("name", "purpose", "activity"):
                overrides.setdefault(field, before["target"][field])
            target_name = self._project(target)["name"]
            for source in source_rows.values():
                self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)", (self._project(source)["name"].strip().casefold(), target["id"]))
                for field, value in json.loads(source["overrides"]).items():
                    if field != "name":
                        overrides.setdefault(field, value)
                for session in self.db.execute("SELECT session_id FROM associations WHERE project_id=?", (source["id"],)).fetchall():
                    self.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (session[0], target["id"]))
                self.db.execute("DELETE FROM associations WHERE project_id=?", (source["id"],))
                self.db.execute("INSERT OR IGNORE INTO catalog_links SELECT ?,session_id FROM catalog_links WHERE project_id=?", (target["id"], source["id"]))
                self.db.execute("DELETE FROM catalog_links WHERE project_id=?", (source["id"],))
                self.db.execute("UPDATE projects SET merged_into=?,updated_at=? WHERE id=?", (target["id"], now(), source["id"]))
                self.db.execute("UPDATE aliases SET project_id=? WHERE project_id=?", (target["id"], source["id"]))
                self.db.execute("UPDATE title_decisions SET project_id=? WHERE project_id=?", (target["id"], source["id"]))
                for decision in self.db.execute("SELECT session_id,rejected_projects FROM title_decisions").fetchall():
                    rejected = set(json.loads(decision["rejected_projects"]))
                    if source["id"] in rejected:
                        rejected.discard(source["id"])
                        rejected.add(target["id"])
                        self.db.execute("UPDATE title_decisions SET rejected_projects=? WHERE session_id=?", (encode(sorted(rejected)), decision["session_id"]))
            overrides["name"] = target_name
            self.db.execute("UPDATE projects SET overrides=?,updated_at=? WHERE id=?", (encode(overrides), now(), target["id"]))
            after = self._project(self._resolve(target["id"]))
            self._change(target["id"], "user_merge", "You merged these projects; target values win conflicts, previous corrections remain in history.", before, after)
            return after

    def split(self, project_id, payload):
        require_object(payload)
        sessions = payload.get("session_ids")
        if not isinstance(sessions, list) or not sessions or len(sessions) > 100000:
            raise StoreError("Select conversations to split.")
        sessions = list(dict.fromkeys(identifier(sid) for sid in sessions))
        name = string(payload.get("name"), "project name", maximum=10000)
        purpose = string(payload.get("purpose"), "project purpose", empty=True, maximum=10000)
        with self.lock, self.db:
            source = self._resolve(project_id)
            self.require_project_writable(source["id"])
            before = self._project(source)
            if not set(sessions).issubset(before["session_ids"]):
                raise StoreError("Split conversations must belong to the selected project.")
            pid, created = str(uuid.uuid4()), now()
            inferred = {"name": name, "purpose": purpose, "status": "unclear"}
            overrides = {"name": name, "purpose": purpose}
            self.db.execute("INSERT INTO projects VALUES (?,?,?,NULL,?,?)", (pid, encode(inferred), encode(overrides), created, created))
            for sid in sessions:
                if self.db.execute("SELECT 1 FROM catalog_links WHERE project_id=? AND session_id=?", (source["id"], sid)).fetchone():
                    self.db.execute("DELETE FROM catalog_links WHERE project_id=? AND session_id=?", (source["id"], sid))
                    self.db.execute("INSERT OR IGNORE INTO catalog_links VALUES (?,?)", (pid, sid))
                self.db.execute("DELETE FROM associations WHERE session_id=? AND project_id=?", (sid, source["id"]))
                if self.db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                    self.db.execute("INSERT INTO associations VALUES (?,?)", (sid, pid))
                self.db.execute("UPDATE sessions SET assignment_locked=1 WHERE id=?", (sid,))
                self.db.execute("UPDATE title_decisions SET project_id=? WHERE session_id=? AND project_id=?", (pid, sid, source["id"]))
            after = self._project(self._resolve(pid))
            self._change(pid, "user_split", "You separated these conversations; their project assignments are now preserved.", before, after)
            return after

    def review(self, session_id, payload):
        require_object(payload)
        if payload.get("verdict") not in ("accept", "minor_edit", "major_edit", "reject"):
            raise StoreError("Invalid review verdict.")
        seconds = payload.get("seconds")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400:
            raise StoreError("Invalid review duration.")
        boolean(payload.get("evidence_valid"), "evidence validity")
        edits = payload.get("edits", [])
        if not isinstance(edits, list) or len(edits) > 400:
            raise StoreError("Invalid review edits.")
        with self.lock, self.db:
            current = self._fresh(session_id, payload.get("snapshot_id"))
            brief = self.session(session_id)["brief"]
            if brief is None:
                raise StoreError("Analyze the conversation before reviewing it.", 409)
            seen = set()
            for edit in edits:
                require_object(edit)
                section, index = edit.get("section"), edit.get("index")
                if section not in SECTIONS or type(index) is not int or not 0 <= index < len(brief[section]):
                    raise StoreError("Review edit must identify an existing assertion.")
                if (section, index) in seen:
                    raise StoreError("Duplicate review edit.")
                seen.add((section, index))
                if edit.get("text") is not None:
                    string(edit["text"], "review text", maximum=10000)
            result = {"snapshot_id": current["snapshot_id"], "verdict": payload["verdict"], "seconds": seconds,
                      "evidence_valid": payload["evidence_valid"], "edits": edits}
            self.db.execute("INSERT INTO reviews VALUES (?,?,?,?) ON CONFLICT(snapshot_id) DO UPDATE SET "
                            "payload=excluded.payload,created_at=excluded.created_at", (current["snapshot_id"], session_id, encode(result), now()))
            return result

    def pilot(self):
        with self.lock:
            rows = self.db.execute("SELECT payload FROM reviews r WHERE r.rowid = "
                                   "(SELECT r2.rowid FROM reviews r2 WHERE r2.session_id=r.session_id ORDER BY r2.created_at DESC,r2.rowid DESC LIMIT 1)").fetchall()
            reviews = [json.loads(row[0]) for row in rows]
            times = sorted(r["seconds"] for r in reviews)
            n = len(times)
            median = None if not n else (times[n // 2] if n % 2 else (times[n // 2 - 1] + times[n // 2]) / 2)
            return {"status": "development", "reviewed_sessions": n,
                    "verdict_counts": {v: sum(r["verdict"] == v for r in reviews) for v in ("accept", "minor_edit", "major_edit", "reject")},
                    "evidence_invalid_count": sum(not r["evidence_valid"] for r in reviews),
                    "median_review_seconds": median, "pass": None,
                    "note": "Development reviews only. A separate frozen ten-session pilot has not been run."}

    def reset(self, payload):
        require_object(payload)
        if payload.get("confirm") != "DELETE_HOOMY_DATA":
            raise StoreError("Explicit reset confirmation is required.", 409)
        with self.lock:
            with self.db:
                if any(self.project_authority(row[0]) for row in self.db.execute("SELECT id FROM projects WHERE merged_into IS NULL")):
                    raise StoreError("Un projet est centralisé. La réinitialisation locale ne peut pas supprimer son contexte.", 409)
                self.db.execute('DELETE FROM project_context_answers')
                self.db.execute('DELETE FROM research_feedback')
                for table in ("idea_jobs", "idea_observations", "ideas", "act_facts", "act_feedback", "act_requests", "automatic_links", "recognition_excerpts", "title_decisions", "title_assessments", "outline_members", "catalog_links", "outlines", "catalog", "reviews", "changes", "associations", "project_freshness", "aliases", "projects", "analyses", "snapshots", "sessions"):
                    self.db.execute("DELETE FROM " + table)
                self._set("control", {"running": False, "inference_enabled": False, "scope_confirmed": False,
                                      "exclusions": [], "generation": str(uuid.uuid4()), "mode": "overview"})
                from .overview import initial_overview
                self._set("overview", initial_overview())
                self._set("worker", {"last_seen": None, "status": None, "message": None})
                self._set("discovery", {"excluded": 0, "failures": []})
                self._set("legacy_coverage_note", "")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.execute("VACUUM")
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"reset": True, "control": self._get("control"),
                    "message": "Hoomy data was reset. Original Codex files were not accessed or changed."}
