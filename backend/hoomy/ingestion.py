"""Durable, project-scoped public conversation projections from enrolled devices."""

import hashlib
import json
import re
import uuid

from .store import StoreError, encode, lexical_absolute, now, require_object, string, timestamp, within


MAX_EVENT_BYTES = 24 * 1024 * 1024
MAX_PROJECTED_BYTES = 18 * 1024 * 1024
MAX_MESSAGES = 10000
MAX_MESSAGE_BYTES = 256 * 1024
MAX_SEQUENCE = 9007199254740991
HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeEncodeError):
        raise StoreError("Invalid canonical projection.") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def closed(value, required, optional=()):
    require_object(value)
    if set(value) - set(required) - set(optional) or set(required) - set(value):
        raise StoreError("Invalid ingestion fields.")
    return value


def sha256(value):
    if not isinstance(value, str) or not HEX_DIGEST.fullmatch(value):
        raise StoreError("Invalid content digest.")
    return value


def event_uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise StoreError("Invalid event identifier.") from None
    return value


def natural(value, label, maximum=MAX_SEQUENCE, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise StoreError("Invalid " + label + ".")
    return value


def identity(value, fields):
    closed(value, fields)
    for field in fields:
        string(value[field], field, maximum=256)
    return value


def path(value):
    string(value, "source path", maximum=4096)
    if lexical_absolute(value) != value:
        raise StoreError("Source paths must be normalized absolute paths.")
    return value


def dated(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise StoreError("Invalid RFC3339 timestamp.")
    timestamp(value)


def validate(event):
    require_object(event)
    excluded = event.get("type") == "conversation.source_excluded"
    common = ("schema_version", "event_id", "type", "project_id", "policy_revision",
              "connector", "session_id", "source_location", "sequence", "observed_at")
    closed(event, (*common, "exclusion") if excluded else (*common, "source_sha256",
                  "projected_sha256", "title", "coverage", "projection"))
    if type(event["schema_version"]) is not int or event["schema_version"] != 1 or \
            event["type"] not in ("conversation.projected", "conversation.source_excluded") or event["connector"] != "codex":
        raise StoreError("Unsupported ingestion schema.")
    event_uuid(event["event_id"])
    string(event["project_id"], "project identifier", maximum=256)
    string(event["session_id"], "source session identifier", maximum=256)
    natural(event["policy_revision"], "policy revision", minimum=1)
    natural(event["sequence"], "source sequence", minimum=1)
    dated(event["observed_at"])
    source = closed(event["source_location"], ("path", "cwd", "archive_occurrence", "modified_at"))
    path(source["path"])
    path(source["cwd"])
    if not source["path"].endswith(".jsonl") or type(source["archive_occurrence"]) is not bool:
        raise StoreError("Invalid source occurrence.")
    dated(source["modified_at"])
    if excluded:
        exclusion = closed(event["exclusion"], ("reason", "discriminator", "header_sha256"))
        if exclusion["reason"] != "internal_approval_review" or exclusion["discriminator"] not in ("legacy_subagent_guardian", "internal_guardian"):
            raise StoreError("Unsupported source exclusion.")
        sha256(exclusion["header_sha256"])
        if len(canonical(event)) > MAX_EVENT_BYTES:
            raise StoreError("Excluded event is too large.", 413)
        # The internal placeholder satisfies old immutable-version foreign keys.
        return canonical({"version": 1, "messages": []})
    sha256(event["source_sha256"])
    sha256(event["projected_sha256"])
    string(event["title"], "source title", empty=True, maximum=512)
    coverage = closed(event["coverage"], ("excluded_records", "excluded_sensitive_messages", "excluded_context_messages", "excluded_oversized_records"))
    for key, count in coverage.items():
        natural(count, key)
    projection = closed(event["projection"], ("version", "messages"))
    if type(projection["version"]) is not int or projection["version"] != 1:
        raise StoreError("Unsupported projection schema.")
    messages = projection["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise StoreError("Invalid projected message count.")
    previous_line = 0
    for message in messages:
        closed(message, ("role", "text", "line_start", "line_end", "timestamp"))
        if message["role"] not in ("user", "assistant"):
            raise StoreError("Only public user and assistant messages are accepted.")
        string(message["text"], "public message", empty=True, maximum=MAX_MESSAGE_BYTES)
        try:
            size = len(message["text"].encode("utf-8"))
        except UnicodeEncodeError:
            raise StoreError("Invalid Unicode message.") from None
        if size > MAX_MESSAGE_BYTES:
            raise StoreError("Projected message is too large.", 413)
        natural(message["line_start"], "line reference", minimum=1)
        natural(message["line_end"], "line reference", minimum=message["line_start"])
        if message["line_start"] < previous_line:
            raise StoreError("Projection line references must retain source order.")
        previous_line = message["line_start"]
        if message["timestamp"] is not None:
            dated(message["timestamp"])
    projected = canonical(projection)
    if len(projected) > MAX_PROJECTED_BYTES or len(canonical(event)) > MAX_EVENT_BYTES:
        raise StoreError("Projected event is too large.", 413)
    if hashlib.sha256(projected).hexdigest() != event["projected_sha256"]:
        raise StoreError("Projected content digest does not match.")
    return projected


def admit(principal, policy, event):
    identity(principal, ("account_id", "workspace_id", "device_id"))
    closed(policy, ("id", "revision", "content_tier", "collection_enabled", "roots", "exclusions", "project_bindings"))
    if policy["id"] != principal["device_id"] or policy["revision"] != event["policy_revision"]:
        raise StoreError("Collection policy changed.", 409)
    natural(policy["revision"], "policy revision", minimum=1)
    if policy["content_tier"] != "conversation_visible_v1" or policy["collection_enabled"] is not True:
        raise StoreError("Visible conversation collection is not approved.", 403)
    for field in ("roots", "exclusions"):
        if not isinstance(policy[field], list) or len(policy[field]) > 32:
            raise StoreError("Invalid collection paths.")
        for value in policy[field]:
            path(value)
    if not policy["roots"] or any(value.rsplit("/", 1)[-1] not in ("sessions", "archived_sessions") for value in policy["roots"]):
        raise StoreError("An approved history directory is required.", 403)
    bindings = policy["project_bindings"]
    if not isinstance(bindings, list) or len(bindings) != 1:
        raise StoreError("The pilot requires one approved project.", 403)
    binding = closed(bindings[0], ("project_id", "cwd_roots"))
    if binding["project_id"] != event["project_id"] or not isinstance(binding["cwd_roots"], list) or not 1 <= len(binding["cwd_roots"]) <= 32:
        raise StoreError("Source is outside the approved project.", 403)
    for value in binding["cwd_roots"]:
        path(value)
    source = event["source_location"]
    if not any(within(source["path"], root) for root in policy["roots"]) or \
            not any(within(source["cwd"], root) for root in binding["cwd_roots"]) or \
            any(within(source["cwd"], excluded) or within(source["path"], excluded) for excluded in policy["exclusions"]):
        raise StoreError("Source is outside the approved collection scope.", 403)


class Ingestion:
    def __init__(self, store):
        self.store = store
        # Legacy raw snapshots and approved context retain their original tables.
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS ingestion_blobs (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                project_id TEXT NOT NULL, projected_sha256 TEXT NOT NULL,
                projection BLOB NOT NULL, message_count INTEGER NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(account_id, workspace_id, project_id, projected_sha256)
            );
            CREATE TABLE IF NOT EXISTS ingestion_locations (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                device_id TEXT NOT NULL, project_id TEXT NOT NULL, connector TEXT NOT NULL,
                session_id TEXT NOT NULL, source_path TEXT NOT NULL, archive_occurrence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(account_id, workspace_id, device_id, project_id, connector, session_id, source_path, archive_occurrence)
            );
            CREATE TABLE IF NOT EXISTS ingestion_versions (
                id TEXT PRIMARY KEY, location_id TEXT NOT NULL REFERENCES ingestion_locations(id),
                sequence INTEGER NOT NULL, blob_id TEXT NOT NULL REFERENCES ingestion_blobs(id),
                source_sha256 TEXT NOT NULL, version_sha256 TEXT NOT NULL,
                metadata TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(location_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS ingestion_events (
                account_id TEXT NOT NULL, device_id TEXT NOT NULL, event_id TEXT NOT NULL,
                event_sha256 TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES ingestion_versions(id),
                manifest TEXT NOT NULL, receipt TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(account_id, device_id, event_id)
            );
            CREATE INDEX IF NOT EXISTS ingestion_project_sources ON ingestion_locations(project_id);
            CREATE INDEX IF NOT EXISTS ingestion_location_versions ON ingestion_versions(location_id, sequence DESC);
            CREATE TABLE IF NOT EXISTS ingestion_source_exclusions (
                location_id TEXT PRIMARY KEY REFERENCES ingestion_locations(id),
                reason TEXT NOT NULL, discriminator TEXT NOT NULL, header_sha256 TEXT NOT NULL,
                event_id TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)

    def accept(self, payload):
        closed(payload, ("principal", "policy", "event"))
        event, principal, policy = payload["event"], payload["principal"], payload["policy"]
        projected = validate(event)
        admit(principal, policy, event)
        event_hash = digest(event)
        key = (principal["account_id"], principal["device_id"], event["event_id"])
        with self.store.lock, self.store.db:
            existing = self.store.db.execute("SELECT e.event_sha256,e.receipt,l.workspace_id FROM ingestion_events e JOIN ingestion_versions v ON v.id=e.version_id JOIN ingestion_locations l ON l.id=v.location_id WHERE e.account_id=? AND e.device_id=? AND e.event_id=?", key).fetchone()
            if existing:
                if existing["event_sha256"] != event_hash or existing["workspace_id"] != principal["workspace_id"]:
                    raise StoreError("Event identifier already describes different content.", 409)
                return json.loads(existing["receipt"])
            project = self.store._resolve(event["project_id"])
            if project["id"] != event["project_id"] or self.store._project(project)["dismissed"]:
                raise StoreError("The approved project is no longer available.", 409)
            scope = [principal["account_id"], principal["workspace_id"], event["project_id"]]
            source = event["source_location"]
            location_id = "remote:" + digest([*scope, principal["device_id"], event["connector"],
                event["session_id"], source["path"], source["archive_occurrence"]])
            excluded = event["type"] == "conversation.source_excluded"
            if not excluded and self.store.db.execute("SELECT 1 FROM ingestion_source_exclusions WHERE location_id=?", (location_id,)).fetchone():
                raise StoreError("Internal control sources cannot be projected.", 409)
            projected_hash = hashlib.sha256(projected).hexdigest()
            source_hash = event["exclusion"]["header_sha256"] if excluded else event["source_sha256"]
            blob_id = "projection:" + digest([*scope, projected_hash])
            metadata = {key: event[key] for key in (("source_location", "observed_at", "policy_revision", "exclusion") if excluded else ("source_location", "observed_at", "title", "coverage", "policy_revision"))}
            if excluded:
                metadata.update(type=event["type"], hash_scope="session_header", classification_verified=False)
            version_hash = digest([location_id, event["sequence"], source_hash, projected_hash, metadata])
            version_id = "remote-version:" + version_hash
            previous = self.store.db.execute("SELECT version_sha256,id FROM ingestion_versions WHERE location_id=? AND sequence=?", (location_id, event["sequence"])).fetchone()
            if previous and previous["version_sha256"] != version_hash:
                raise StoreError("Source sequence already describes different content.", 409)
            received_at = now()
            self.store.db.execute("INSERT OR IGNORE INTO ingestion_blobs VALUES (?,?,?,?,?,?,?,?)", (blob_id, *scope,
                projected_hash, projected, 0 if excluded else len(event["projection"]["messages"]), received_at))
            self.store.db.execute("INSERT OR IGNORE INTO ingestion_locations VALUES (?,?,?,?,?,?,?,?,?,?)", (location_id,
                principal["account_id"], principal["workspace_id"], principal["device_id"], event["project_id"],
                event["connector"], event["session_id"], source["path"], int(source["archive_occurrence"]), received_at))
            self.store.db.execute("INSERT OR IGNORE INTO ingestion_versions VALUES (?,?,?,?,?,?,?,?)", (version_id,
                location_id, event["sequence"], blob_id, source_hash, version_hash, encode(metadata), received_at))
            receipt = {"receipt_id": event["event_id"], "event_id": event["event_id"], "project_id": event["project_id"],
                "event_sha256": event_hash, "state": "stored",
                "inference_state": "not_requested", "received_at": received_at, "source_version_id": version_id}
            if excluded:
                receipt.update(event_type=event["type"], header_sha256=source_hash)
                self.store.db.execute("INSERT OR IGNORE INTO ingestion_source_exclusions VALUES (?,?,?,?,?,?)", (location_id,
                    event["exclusion"]["reason"], event["exclusion"]["discriminator"], source_hash, event["event_id"], received_at))
            else:
                receipt["projected_sha256"] = projected_hash
            manifest = {key: value for key, value in event.items() if key != "projection"}
            self.store.db.execute("INSERT INTO ingestion_events VALUES (?,?,?,?,?,?,?,?)", (*key, event_hash,
                version_id, encode(manifest), encode(receipt), received_at))
            return receipt

    def receipt(self, payload):
        closed(payload, ("principal", "event_id"))
        principal = identity(payload["principal"], ("account_id", "workspace_id", "device_id"))
        event_uuid(payload["event_id"])
        with self.store.lock:
            row = self.store.db.execute("SELECT e.receipt FROM ingestion_events e JOIN ingestion_versions v ON v.id=e.version_id JOIN ingestion_locations l ON l.id=v.location_id WHERE e.account_id=? AND e.device_id=? AND e.event_id=? AND l.workspace_id=?", (principal["account_id"], principal["device_id"], payload["event_id"], principal["workspace_id"])).fetchone()
            if not row:
                raise StoreError("Receipt not found.", 404)
            return json.loads(row[0])

    def source_ids(self, project_id):
        return [row[0] for row in self.store.db.execute("SELECT l.id FROM ingestion_locations l WHERE l.project_id=? AND NOT EXISTS (SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id) ORDER BY l.rowid", (project_id,))]

    def coverage(self, project_id):
        locations = self.store.db.execute("SELECT count(*) FROM ingestion_locations WHERE project_id=?", (project_id,)).fetchone()[0]
        versions = self.store.db.execute("SELECT count(*) FROM ingestion_versions v JOIN ingestion_locations l ON l.id=v.location_id WHERE l.project_id=?", (project_id,)).fetchone()[0]
        excluded = self.store.db.execute("SELECT count(*) FROM ingestion_source_exclusions x JOIN ingestion_locations l ON l.id=x.location_id WHERE l.project_id=?", (project_id,)).fetchone()[0]
        blobs = self.store.db.execute("SELECT count(*),coalesce(sum(message_count),0) FROM ingestion_blobs WHERE project_id=? AND message_count>0", (project_id,)).fetchone()
        active = self.store.db.execute("SELECT count(*),coalesce(sum(message_count),0) FROM ingestion_blobs WHERE id IN (SELECT DISTINCT v.blob_id FROM ingestion_versions v JOIN ingestion_locations l ON l.id=v.location_id WHERE l.project_id=? AND v.sequence=(SELECT max(sequence) FROM ingestion_versions WHERE location_id=l.id) AND NOT EXISTS (SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id))", (project_id,)).fetchone()
        active_versions = self.store.db.execute("SELECT count(*) FROM ingestion_versions v JOIN ingestion_locations l ON l.id=v.location_id WHERE l.project_id=? AND NOT EXISTS (SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id)", (project_id,)).fetchone()[0]
        return {"source_locations": locations - excluded, "immutable_versions": active_versions,
                "unique_projection_blobs": active[0], "unique_projected_messages": active[1],
                "excluded_internal_sources": excluded,
                "retained_history": {"source_locations": locations, "immutable_versions": versions,
                    "unique_projection_blobs": blobs[0], "unique_projected_messages": blobs[1]},
                "content_tier": "conversation_visible_v1", "inference_state": "not_requested"}

    def metadata(self, source_ids=None):
        if source_ids is not None and not source_ids:
            return []
        values = tuple(dict.fromkeys(source_ids)) if source_ids is not None else ()
        selection = " WHERE NOT EXISTS (SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id)"
        if source_ids is not None:
            selection += " AND l.id IN (" + ",".join("?" for _ in values) + ")"
        rows = self.store.db.execute("SELECT l.*,v.id version_id,v.sequence,v.source_sha256,v.metadata,v.created_at received_at,b.projected_sha256,b.message_count,(SELECT count(*) FROM ingestion_versions WHERE location_id=l.id) version_count FROM ingestion_locations l JOIN ingestion_versions v ON v.location_id=l.id AND v.sequence=(SELECT max(sequence) FROM ingestion_versions WHERE location_id=l.id) JOIN ingestion_blobs b ON b.id=v.blob_id" + selection + " ORDER BY l.rowid", values)
        result = []
        for row in rows:
            data = json.loads(row["metadata"])
            result.append({"id": row["id"], "snapshot_id": row["version_id"], "title": data["title"],
                "modified_at": data["source_location"]["modified_at"], "status": "collected",
                "warning": "Projection partielle : des enregistrements trop volumineux ont été exclus." if data["coverage"]["excluded_oversized_records"] else None,
                "source_kind": "conversation_visible_v1", "cached": True, "device_id": row["device_id"],
                "connector": row["connector"], "original_session_id": row["session_id"],
                "source_location": data["source_location"], "observed_at": data["observed_at"],
                "received_at": row["received_at"],
                "sequence": row["sequence"], "version_count": row["version_count"], "message_count": row["message_count"],
                "source_sha256": row["source_sha256"], "source_sha256_verified": False,
                "projected_sha256": row["projected_sha256"], "coverage": data["coverage"],
                "partial": bool(data["coverage"]["excluded_oversized_records"]),
                "inference_state": "not_requested"})
        return result

    def evidence(self, payload):
        closed(payload, ("principal", "project_id", "source_id"), ("offset", "limit"))
        principal = identity(payload["principal"], ("account_id", "workspace_id"))
        string(payload["project_id"], "project identifier", maximum=256)
        string(payload["source_id"], "source identifier", maximum=256)
        offset = natural(payload.get("offset", 0), "message offset", maximum=MAX_MESSAGES)
        limit = natural(payload.get("limit", 20), "message limit", maximum=50, minimum=1)
        with self.store.lock:
            project = self.store._resolve(payload["project_id"])
            if project["id"] != payload["project_id"] or self.store._project(project)["dismissed"]:
                raise StoreError("Project evidence not found.", 404)
            row = self.store.db.execute("SELECT l.id,l.device_id,l.connector,l.session_id,v.id version_id,v.sequence,v.source_sha256,v.metadata,b.projected_sha256,b.projection FROM ingestion_locations l JOIN ingestion_versions v ON v.location_id=l.id AND v.sequence=(SELECT max(sequence) FROM ingestion_versions WHERE location_id=l.id) JOIN ingestion_blobs b ON b.id=v.blob_id WHERE l.id=? AND l.project_id=? AND l.account_id=? AND l.workspace_id=? AND NOT EXISTS (SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id)", (payload["source_id"], payload["project_id"], principal["account_id"], principal["workspace_id"])).fetchone()
            if not row:
                raise StoreError("Project evidence not found.", 404)
            data = json.loads(row["metadata"])
            all_messages = json.loads(row["projection"])["messages"]
            messages, budget = [], 256 * 1024
            for index in range(offset, min(len(all_messages), offset + limit)):
                message = all_messages[index]
                raw = message["text"].encode("utf-8")
                excerpt = raw[:min(8192, budget)].decode("utf-8", errors="ignore")
                budget -= len(excerpt.encode("utf-8"))
                messages.append(dict(message, id=f"{row['projected_sha256']}:{message['line_start']}:{message['line_end']}:{index}",
                    text=excerpt, text_truncated=len(excerpt.encode("utf-8")) < len(raw), text_bytes=len(raw)))
                if budget <= 0:
                    break
            end = offset + len(messages)
            return {"project_id": payload["project_id"], "source_id": row["id"], "source_version_id": row["version_id"],
                "projected_sha256": row["projected_sha256"], "source_sha256": row["source_sha256"],
                "source_sha256_verified": False, "sequence": row["sequence"], "messages": messages,
                "provenance": {"device_id": row["device_id"], "connector": row["connector"],
                    "original_session_id": row["session_id"], "source_location": data["source_location"]},
                "offset": offset, "limit": limit, "total_messages": len(all_messages),
                "next_offset": end if end < len(all_messages) else None, "coverage": dict(data["coverage"],
                    content_tier="conversation_visible_v1", cached=True, inference_state="not_requested",
                    source_partial=bool(data["coverage"]["excluded_oversized_records"]),
                    partial=bool(data["coverage"]["excluded_oversized_records"] or offset or end < len(all_messages) or any(m["text_truncated"] for m in messages)))}
