"""Cited project-state jobs consume only immutable, approved central projections."""

import json
import os
import time
import uuid

from .ingestion import canonical, closed, digest, event_uuid, natural, sha256
from .store import StoreError, encode, now, string

from .project_state_contract import (METHOD, METHOD_HASH, METHOD_CONFIG, BUDGET, PHASES, SCHEMAS,
    LEASE_SECONDS, DEADLINE_SECONDS, CHUNK_BYTES, MAX_CHUNKS, MAX_TOKENS, KINDS, STATES, REASONS,
    EFFORTS, RECONCILIATION_RESERVE, fits_input, serialized_bytes, model_payload, message_passages, allowed_passage_ids, required_machine_aliases, referenced_machine_aliases)

from .project_state_quality import filter_output, filter_model_output

OUTPUT_SCHEMA = SCHEMAS['reconcile']
FAILURES = ("cancelled", "deadline_exceeded", "lease_expired", "model_unavailable", "model_failed",
            "invalid_output", "usage_unknown", "budget_exceeded", "ambiguous_completion", "input_ineligible", "insufficient_device_evidence", "unsupported_device_scope")


class ProjectState:
    def __init__(self, store, enabled_project_id=None):
        self.store = store
        self.enabled_project_id = os.environ.get("HOOMY_STATE_PROJECT_ID", "") if enabled_project_id is None else enabled_project_id
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS project_state_runs (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                project_id TEXT NOT NULL, context_revision TEXT NOT NULL, corpus_revision TEXT NOT NULL,
                input_hash TEXT NOT NULL, manifest TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                deadline REAL, lease_expires REAL, worker_id TEXT, lease_id TEXT, failure_code TEXT,
                output TEXT, state_revision TEXT
            );
            CREATE TABLE IF NOT EXISTS project_state_requests (
                account_id TEXT NOT NULL, workspace_id TEXT NOT NULL, project_id TEXT NOT NULL,
                request_id TEXT NOT NULL, request_hash TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES project_state_runs(id),
                PRIMARY KEY(account_id,workspace_id,project_id,request_id)
            );
            CREATE TABLE IF NOT EXISTS project_state_inputs (
                run_id TEXT PRIMARY KEY REFERENCES project_state_runs(id), data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_state_calls (
                run_id TEXT NOT NULL REFERENCES project_state_runs(id), call_id TEXT NOT NULL, phase TEXT NOT NULL,
                chunk_index INTEGER, status TEXT NOT NULL, input TEXT NOT NULL, payload_hash TEXT NOT NULL,
                dispatched_at TEXT NOT NULL, output TEXT, output_hash TEXT, method TEXT, completed_at TEXT, failure_hash TEXT,
                PRIMARY KEY(run_id,call_id)
            );
            CREATE TABLE IF NOT EXISTS project_state_assertions (
                run_id TEXT NOT NULL REFERENCES project_state_runs(id), id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(run_id,id)
            );
            CREATE TABLE IF NOT EXISTS project_state_citations (
                run_id TEXT NOT NULL REFERENCES project_state_runs(id), id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(run_id,id)
            );
            CREATE TABLE IF NOT EXISTS project_state_retry_links (
                run_id TEXT PRIMARY KEY REFERENCES project_state_runs(id),
                parent_run_id TEXT NOT NULL UNIQUE REFERENCES project_state_runs(id),
                request_id TEXT NOT NULL, account_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
                project_id TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS project_state_scope ON project_state_runs(account_id,workspace_id,project_id);
        """)

    def _scope(self, payload, fields, optional=()):
        closed(payload, ("principal", "project_id", *fields), optional)
        principal = closed(payload["principal"], ("account_id", "workspace_id"))
        for key in principal:
            string(principal[key], key, maximum=256)
        pid = string(payload["project_id"], "project identifier", maximum=256)
        project = self.store._resolve(pid)
        if project["id"] != pid or self.store._project(project)["dismissed"]:
            raise StoreError("Project state not found.", 404)
        return principal, pid

    def _enabled(self, pid):
        if not self.enabled_project_id or pid != self.enabled_project_id:
            raise StoreError("Project-state generation is not enabled for this project.", 403)

    def _context(self, pid):
        control = self.store._get("control")
        answers = self.store.dialogue.context({pid})
        revision = digest([control["generation"], control["exclusions"], [item["id"] for item in answers]])
        fields = {item["dimension"]: {key: item.get(key) for key in ("id", "answer", "response", "answered_at")}
                  for item in answers}
        return revision, fields

    def _manifest(self, principal, pid):
        context_revision, fields = self._context(pid)
        rows = self.store.db.execute("""SELECT l.*,v.id version_id,v.sequence,v.metadata,v.source_sha256,
                    v.created_at received_at,b.projected_sha256,b.message_count
                FROM ingestion_locations l JOIN ingestion_versions v ON v.location_id=l.id
                    AND v.sequence=(SELECT max(sequence) FROM ingestion_versions WHERE location_id=l.id)
                JOIN ingestion_blobs b ON b.id=v.blob_id
                WHERE l.account_id=? AND l.workspace_id=? AND l.project_id=?
                    AND NOT EXISTS(SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id)
                ORDER BY l.id LIMIT 129""", (principal["account_id"], principal["workspace_id"], pid)).fetchall()
        if len(rows) > 128:
            raise StoreError("The project corpus exceeds the bounded pilot source limit.", 413)
        sources = []
        for row in rows:
            meta = json.loads(row["metadata"])
            sources.append({"source_id": row["id"], "source_version_id": row["version_id"],
                "sequence": row["sequence"], "projected_sha256": row["projected_sha256"],
                "device_id": row["device_id"], "connector": row["connector"], "session_id": row["session_id"],
                "source_location": meta["source_location"], "observed_at": meta["observed_at"],
                "received_at": row["received_at"], "source_sha256": row["source_sha256"],
                "source_sha256_verified": False, "title": meta["title"],
                "message_count": row["message_count"], "coverage": meta["coverage"]})
        exclusions = [dict(row) for row in self.store.db.execute("""SELECT l.id source_id,x.header_sha256
                FROM ingestion_source_exclusions x JOIN ingestion_locations l ON l.id=x.location_id
                WHERE l.account_id=? AND l.workspace_id=? AND l.project_id=? ORDER BY l.id""",
                (principal["account_id"], principal["workspace_id"], pid))]
        corpus_revision = digest({"sources": sources, "exclusions": exclusions})
        legacy = self.store.db.execute("""SELECT count(DISTINCT session_id) FROM (
                SELECT session_id FROM associations WHERE project_id=?
                UNION SELECT session_id FROM catalog_links WHERE project_id=?)""", (pid, pid)).fetchone()[0]
        messages = {}
        by_device = {}
        for source in sources:
            messages[source["projected_sha256"]] = source["message_count"]
            by_device[source["device_id"]] = by_device.get(source["device_id"], 0) + 1
        coverage = {"source_locations": len(sources), "unique_projection_blobs": len(messages),
            "eligible_messages": sum(messages.values()), "locations_by_device": by_device,
            "legacy_unassigned_sources": legacy, "excluded_internal_sources": len(exclusions),
            "partial_sources": sum(bool(s["coverage"]["excluded_oversized_records"]) for s in sources),
            "omissions": {key: sum(s["coverage"][key] for s in sources)
                         for key in ("excluded_records", "excluded_sensitive_messages", "excluded_context_messages", "excluded_oversized_records")}}
        return {"project_id": pid, "context_revision": context_revision, "corpus_revision": corpus_revision,
                "approved_context": fields, "sources": sources, "coverage": coverage, "method": METHOD,
                "method_hash": METHOD_HASH, "method_config": json.loads(encode(METHOD_CONFIG))}

    def _bounded_context(self, fields):
        context, truncated = {}, []
        for dimension, item in fields.items():
            answer = item.get("answer")
            value = answer if isinstance(answer, str) else None
            if value is not None:
                value = value.encode("utf-8")[:512].decode("utf-8", errors="ignore")
            context[dimension] = {"answer": value, "response": item.get("response"), "answered_at": item.get("answered_at")}
            if value != answer:
                truncated.append(dimension)
        while serialized_bytes(context) > 4096:
            key = max(context, key=lambda k: len((context[k]["answer"] or "").encode("utf-8")))
            value = context[key]["answer"]
            if not value:
                raise StoreError("Approved context metadata exceeds the bounded method policy.", 413)
            context[key]["answer"] = value.encode("utf-8")[:-1].decode("utf-8", errors="ignore")
            if key not in truncated:
                truncated.append(key)
        return context, sorted(truncated)

    def _inputs(self, manifest):
        groups = {}
        for source in manifest["sources"]:
            groups.setdefault(source["projected_sha256"], []).append(source)
        aliases, passages, chunks, slot_machines, clipped = {}, {}, [], [], 0
        context, context_truncated = self._bounded_context(manifest["approved_context"])
        machine_aliases = {device: "machine-" + str(index + 1)
            for index, device in enumerate(sorted(manifest["coverage"]["locations_by_device"]))}
        if len(machine_aliases) > 2:
            return {"aliases": {}, "passages": {}, "chunks": [], "coverage": manifest["coverage"],
                "provided_context": context, "sources": {}, "machine_aliases": machine_aliases, "slot_machines": []}
        queues = []
        for projected_hash, origins in sorted(groups.items()):
            row = self.store.db.execute("SELECT b.projection FROM ingestion_versions v JOIN ingestion_blobs b ON b.id=v.blob_id WHERE v.id=?",
                (origins[0]["source_version_id"],)).fetchone()
            messages = json.loads(row[0])["messages"]
            indices, left, right = [], 0, len(messages) - 1
            while left <= right and len(indices) < 8:
                for index in dict.fromkeys((right, left)):
                    if len(indices) < 8:
                        indices.append(index)
                left += 1
                right -= 1
            queues.append({"projected_hash": projected_hash, "origins": origins,
                "messages": [(index, messages[index]) for index in indices]})
        # Identical projections are one witness, assigned once with every origin retained.
        buckets = {device: [] for device in machine_aliases}
        for queue in queues:
            buckets[min(source["device_id"] for source in queue["origins"])].append(queue)
        selected = 0
        for device, ordered in buckets.items():
            current, full = [], False
            control = {"selection_partial": True, "required_machine_aliases": [machine_aliases[device]]}
            for round_index in range(8):
                for queue in ordered:
                    if not queue["messages"]:
                        continue
                    index, message = queue["messages"].pop(0)
                    raw = message["text"].encode("utf-8")
                    excerpt = raw[:1024].decode("utf-8", errors="ignore")
                    alias = "m" + str(selected + 1).zfill(4)
                    entry = {"message_alias": alias, "role": message["role"], "passages": message_passages(excerpt, selected + 1),
                        "timestamp": message["timestamp"], "truncated": excerpt != message["text"],
                        "machine_aliases": sorted({machine_aliases[s["device_id"]] for s in queue["origins"]})}
                    proposed = model_payload("extract", context, control, messages=current + [entry])
                    if not fits_input(proposed, "extract"):
                        if not current:
                            raise StoreError("A visible message cannot fit the bounded method policy.", 413)
                        full = True
                        break
                    aliases[alias] = {"message_id": f"{queue['projected_hash']}:{message['line_start']}:{message['line_end']}:{index}",
                        "message": {**message, "text": excerpt}, "truncated": entry["truncated"],
                        "origin_ids": [source["source_id"] for source in queue["origins"]]}
                    start = 0
                    for part in entry["passages"]:
                        end = start + len(part["text"])
                        passages[part["passage_id"]] = {"message_alias": alias, "quote": part["text"],
                            "char_start": start, "char_end": end}
                        start = end
                    current.append(entry)
                    selected += 1
                    clipped += int(entry["truncated"])
                if full:
                    break
            if current:
                chunks.append(current)
                slot_machines.append(machine_aliases[device])
        selected_sources = {}
        for alias, value in aliases.items():
            for source_id in value["origin_ids"]:
                origin = next(source for source in manifest["sources"] if source["source_id"] == source_id)
                record = selected_sources.setdefault(source_id, {"source_id": source_id, "source_version_id": origin["source_version_id"],
                    "device_id": origin["device_id"], "provided_messages": 0, "spans": [], "spans_truncated": False})
                record["provided_messages"] += 1
                if len(record["spans"]) < 64:
                    record["spans"].append({"message_alias": alias, "line_start": value["message"]["line_start"],
                        "line_end": value["message"]["line_end"], "text_truncated": value["truncated"],
                        "provided_utf8_bytes": len(value["message"]["text"].encode("utf-8"))})
                else:
                    record["spans_truncated"] = True
        provided_by_device = {}
        for source in selected_sources.values():
            provided_by_device[source["device_id"]] = provided_by_device.get(source["device_id"], 0) + 1
        if set(provided_by_device) != set(machine_aliases):
            raise StoreError("The bounded selection cannot represent every eligible machine.", 413)
        coverage = {**manifest["coverage"], "provided_messages": selected, "provided_sources": len(selected_sources),
            "provided_sources_by_device": provided_by_device, "selected_sources": list(selected_sources.values()),
            "unread_messages": manifest["coverage"]["eligible_messages"] - selected,
            "provided_passages": len(passages),
            "truncated_messages": clipped, "provided_bytes": sum(len(a["message"]["text"].encode()) for a in aliases.values()),
            "context_truncated_dimensions": context_truncated,
            "context_provided_utf8_bytes": serialized_bytes(context),
            "partial": bool(context_truncated or clipped or selected < manifest["coverage"]["eligible_messages"]
                or manifest["coverage"]["partial_sources"] or manifest["coverage"]["legacy_unassigned_sources"])}
        return {"aliases": aliases, "passages": passages, "chunks": chunks, "coverage": coverage, "machine_aliases": machine_aliases, "slot_machines": slot_machines,
            "provided_context": context, "sources": {s["source_id"]: s for s in manifest["sources"]}}

    def _run(self, principal, pid, run_id):
        event_uuid(run_id)
        row = self.store.db.execute("SELECT * FROM project_state_runs WHERE id=? AND account_id=? AND workspace_id=? AND project_id=?",
            (run_id, principal["account_id"], principal["workspace_id"], pid)).fetchone()
        if not row:
            raise StoreError("Project-state job not found.", 404)
        return row

    def _status(self, run):
        if run["status"] == "running" and (time.time() >= run["deadline"] or time.time() >= run["lease_expires"]):
            return "needs_review"
        return run["status"]

    def _method_info(self, run):
        manifest = json.loads(run["manifest"])
        config, stored = manifest.get("method_config"), manifest.get("method_hash")
        valid = isinstance(config, dict) and isinstance(stored, str) and digest(config) == stored
        return {"method_version": manifest.get("method"), "method_hash": stored if valid else None,
            "execution_compatible": bool(valid and stored == METHOD_HASH and manifest.get("method") == METHOD)}

    def _compatible(self, run):
        if not self._method_info(run)["execution_compatible"]:
            raise StoreError("The project-state method changed; reload before requesting new work.", 409)

    def _next(self, run):
        inputs = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (run["id"],)).fetchone()[0])
        calls = list(self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=?", (run["id"],)))
        for index in range(len(inputs["chunks"])):
            call = next((c for c in calls if c["phase"] == "extract" and c["chunk_index"] == index), None)
            if not call:
                return {"phase": "extract", "chunk_index": index}
            if call["status"] != "completed":
                return None
        if not any(c["phase"] == "reconcile" for c in calls):
            return {"phase": "reconcile", "chunk_index": None}
        return None

    def _job(self, run):
        calls = list(self.store.db.execute("SELECT status,phase,method FROM project_state_calls WHERE run_id=?", (run["id"],)))
        parent = self.store.db.execute("SELECT parent_run_id FROM project_state_retry_links WHERE run_id=?", (run["id"],)).fetchone()
        child = self.store.db.execute("SELECT run_id FROM project_state_retry_links WHERE parent_run_id=?", (run["id"],)).fetchone()
        return {"project_id": run["project_id"], "run_id": run["id"], "status": self._status(run), **self._method_info(run),
            "retry_of_run_id": parent[0] if parent else None, "retry_run_id": child[0] if child else None,
            "created_at": run["created_at"], "context_revision": run["context_revision"], "corpus_revision": run["corpus_revision"],
            "state_revision": run["state_revision"], "completed_calls": sum(c["status"] == "completed" for c in calls),
            "dispatched_calls": len(calls), "failure_code": run["failure_code"],
            "observed_tokens": sum(json.loads(c["method"])["usage"]["total_tokens"] for c in calls if c["method"]),
            "unknown_usage_calls": sum(not c["method"] or c["status"] == "interrupted" for c in calls)}

    def _eligible(self, origin, principal, pid):
        row = self.store.db.execute("""SELECT b.projected_sha256 FROM ingestion_locations l
                JOIN ingestion_versions v ON v.location_id=l.id AND v.sequence=(SELECT max(sequence) FROM ingestion_versions WHERE location_id=l.id)
                JOIN ingestion_blobs b ON b.id=v.blob_id WHERE l.id=? AND l.account_id=? AND l.workspace_id=? AND l.project_id=?
                AND NOT EXISTS(SELECT 1 FROM ingestion_source_exclusions x WHERE x.location_id=l.id)""",
            (origin["source_id"], principal["account_id"], principal["workspace_id"], pid)).fetchone()
        return bool(row and row[0] == origin["projected_sha256"])

    def _valid_citation(self, citation, principal, pid):
        return all(self._eligible(origin, principal, pid) for origin in citation["origins"])

    def read(self, payload):
        with self.store.lock:
            principal, pid = self._scope(payload, ())
            manifest = self._manifest(principal, pid)
            latest = self.store.db.execute("SELECT * FROM project_state_runs WHERE account_id=? AND workspace_id=? AND project_id=? ORDER BY rowid DESC LIMIT 1",
                (principal["account_id"], principal["workspace_id"], pid)).fetchone()
            ready = self.store.db.execute("SELECT * FROM project_state_runs WHERE account_id=? AND workspace_id=? AND project_id=? AND status='ready' ORDER BY rowid DESC LIMIT 1",
                (principal["account_id"], principal["workspace_id"], pid)).fetchone()
            state = None
            if ready:
                stored = json.loads(ready["output"])
                citations = {row["id"]: json.loads(row["data"]) for row in self.store.db.execute("SELECT * FROM project_state_citations WHERE run_id=?", (ready["id"],))}
                valid = {key: value for key, value in citations.items() if self._valid_citation(value, principal, pid)}
                assertions = [a for a in stored["assertions"] if all(cid in valid for cid in a["citation_ids"])]
                available = {a["key"] for a in assertions}
                uncertainties = [u for u in stored["uncertainties"] if all(cid in valid for cid in u["citation_ids"])]
                contradictions = [c for c in stored["contradictions"] if set(c["assertion_keys"]).issubset(available)]
                eligible_assertions, eligible_uncertainties = len(assertions), len(uncertainties)
                visible, quality = filter_output({**stored, "assertions": assertions, "uncertainties": uncertainties, "contradictions": contradictions},
                    lambda item: [{"text": valid[cid]["quote"], "message_id": valid[cid]["message_id"],
                        "char_start": valid[cid].get("char_start", 0), "char_end": valid[cid].get("char_end", len(valid[cid]["quote"]))}
                        for cid in item["citation_ids"]])
                for item in quality["excluded_items"]:
                    item["id"] = (assertions if item["section"] == "assertions" else uncertainties)[item.pop("index")]["id"]
                quality.update(raw_assertions=len(stored["assertions"]), raw_uncertainties=len(stored["uncertainties"]),
                    source_eligible_assertions=eligible_assertions, source_eligible_uncertainties=eligible_uncertainties)
                assertions, uncertainties, contradictions = visible["assertions"], visible["uncertainties"], visible["contradictions"]
                reasons = []
                if ready["context_revision"] != manifest["context_revision"]:
                    reasons.append("context_changed")
                if ready["corpus_revision"] != manifest["corpus_revision"]:
                    reasons.append("corpus_changed")
                if not self._method_info(ready)["execution_compatible"]:
                    reasons.append("method_changed")
                used_ids = {cid for item in [*assertions, *uncertainties] for cid in item["citation_ids"]}
                provided_devices = sorted(stored["coverage"].get("provided_sources_by_device", {}))
                cited_devices = sorted({origin["device_id"] for cid in used_ids for origin in valid[cid]["origins"]})
                device_coverage = {"provided_devices": provided_devices, "cited_devices": cited_devices,
                    "uncited_provided_devices": sorted(set(provided_devices) - set(cited_devices)),
                    "cross_machine_complete": bool(provided_devices and set(provided_devices).issubset(cited_devices))}
                visible_revision = digest({"state_revision": ready["state_revision"], "quality_policy": quality["policy_hash"],
                    "assertions": assertions, "uncertainties": uncertainties, "contradictions": contradictions, "citation_ids": sorted(used_ids)})
                state = {"quality": quality, "visible_revision": visible_revision, "device_coverage": device_coverage, **{key: stored[key] for key in ("generated_at", "coverage", "method")},
                    "run_id": ready["id"], "state_revision": ready["state_revision"],
                    "assertions": assertions, "uncertainties": uncertainties, "contradictions": contradictions,
                    "stale": bool(reasons), "stale_reasons": reasons,
                    "suppressed_assertions": len(stored["assertions"]) - eligible_assertions}
            return {"project_id": pid, "enabled": pid == self.enabled_project_id and bool(self.enabled_project_id),
                "method_version": METHOD, "method_hash": METHOD_HASH, "execution_policy": json.loads(encode(METHOD_CONFIG)),
                "context_revision": manifest["context_revision"], "corpus_revision": manifest["corpus_revision"],
                "coverage": manifest["coverage"], "state": state, "job": self._job(latest) if latest else None}

    def refresh(self, payload):
        with self.store.lock, self.store.db:
            principal, pid = self._scope(payload, ("request_id", "context_revision", "corpus_revision"), ("retry_of_run_id", "method_hash"))
            self._enabled(pid)
            if "method_hash" not in payload:
                raise StoreError("The project-state method must be reread before refreshing.", 409)
            sha256(payload["method_hash"])
            if payload["method_hash"] != METHOD_HASH:
                raise StoreError("The project-state method changed; reload before refreshing.", 409)
            event_uuid(payload["request_id"])
            sha256(payload["context_revision"])
            sha256(payload["corpus_revision"])
            if "retry_of_run_id" in payload:
                event_uuid(payload["retry_of_run_id"])
            request_hash = digest(payload)
            prior = self.store.db.execute("SELECT * FROM project_state_requests WHERE account_id=? AND workspace_id=? AND project_id=? AND request_id=?",
                (principal["account_id"], principal["workspace_id"], pid, payload["request_id"])).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise StoreError("Refresh identifier already describes another request.", 409)
                return self._job(self._run(principal, pid, prior["run_id"]))
            manifest = self._manifest(principal, pid)
            if payload["context_revision"] != manifest["context_revision"] or payload["corpus_revision"] != manifest["corpus_revision"]:
                raise StoreError("Project context or evidence changed; reload before refreshing.", 409)
            if not manifest["sources"]:
                raise StoreError("No eligible visible evidence is available.", 409)
            input_hash = digest(manifest)
            parent, preserved_inputs = None, None
            if "retry_of_run_id" in payload:
                parent = self._run(principal, pid, payload["retry_of_run_id"])
                self._compatible(parent)
                if parent["status"] not in ("failed", "needs_review"):
                    raise StoreError("Only a stored failed or review-required job permits an explicit new attempt.", 409)
                if parent["input_hash"] != input_hash or digest(json.loads(parent["manifest"])) != input_hash:
                    raise StoreError("The reviewed job inputs changed; reload before requesting new work.", 409)
                if self.store.db.execute("SELECT 1 FROM project_state_retry_links WHERE parent_run_id=?", (parent["id"],)).fetchone():
                    raise StoreError("This reviewed job already has a new attempt.", 409)
                preserved_inputs = self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (parent["id"],)).fetchone()
                if not preserved_inputs:
                    raise StoreError("The reviewed job inputs are unavailable.", 409)
                run = None
            else:
                run = self.store.db.execute("SELECT * FROM project_state_runs WHERE account_id=? AND workspace_id=? AND project_id=? AND input_hash=? ORDER BY rowid DESC LIMIT 1",
                    (principal["account_id"], principal["workspace_id"], pid, input_hash)).fetchone()
            if not run:
                active = next((row for row in self.store.db.execute("SELECT * FROM project_state_runs WHERE account_id=? AND workspace_id=? AND project_id=? AND status IN ('queued','running')",
                    (principal["account_id"], principal["workspace_id"], pid)) if self._method_info(row)["execution_compatible"] or self._status(row) == "running"), None)
                if active:
                    raise StoreError("A project-state job already needs completion or review.", 409)
                run_id = str(uuid.uuid4())
                self.store.db.execute("INSERT INTO project_state_runs(id,account_id,workspace_id,project_id,context_revision,corpus_revision,input_hash,manifest,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, principal["account_id"], principal["workspace_id"], pid, manifest["context_revision"],
                     manifest["corpus_revision"], input_hash, parent["manifest"] if parent else encode(manifest), "needs_review" if len(manifest["coverage"]["locations_by_device"]) > 2 else "queued", now()))
                self.store.db.execute("INSERT INTO project_state_inputs VALUES (?,?)", (run_id,
                    preserved_inputs[0] if parent else encode(self._inputs(manifest))))
                if len(manifest["coverage"]["locations_by_device"]) > 2:
                    self.store.db.execute("UPDATE project_state_runs SET failure_code='unsupported_device_scope' WHERE id=?", (run_id,))
                if parent:
                    self.store.db.execute("INSERT INTO project_state_retry_links VALUES (?,?,?,?,?,?,?)", (run_id,
                        parent["id"], payload["request_id"], principal["account_id"], principal["workspace_id"], pid, now()))
                run = self._run(principal, pid, run_id)
            self.store.db.execute("INSERT INTO project_state_requests VALUES (?,?,?,?,?,?)",
                (principal["account_id"], principal["workspace_id"], pid, payload["request_id"], request_hash, run["id"]))
            return self._job(run)

    def job(self, payload):
        with self.store.lock:
            principal, pid = self._scope(payload, (), ("run_id",))
            if "run_id" not in payload:
                row = next((candidate for candidate in self.store.db.execute("SELECT * FROM project_state_runs WHERE account_id=? AND workspace_id=? AND project_id=? AND status='queued' ORDER BY rowid",
                    (principal["account_id"], principal["workspace_id"], pid)) if self._method_info(candidate)["execution_compatible"]), None)
                return {"project_id": pid, "pending_run": self._job(row) if row else None}
            return self._job(self._run(principal, pid, payload["run_id"]))

    def _unsupported_scope(self, principal, pid):
        return len(self._manifest(principal, pid)["coverage"]["locations_by_device"]) > 2

    def _stop_scope(self, run):
        self.store.db.execute("UPDATE project_state_runs SET status='needs_review',failure_code='unsupported_device_scope' WHERE id=?", (run["id"],))
        return self._run({"account_id": run["account_id"], "workspace_id": run["workspace_id"]}, run["project_id"], run["id"])

    def claim(self, payload):
        with self.store.lock, self.store.db:
            principal, pid = self._scope(payload, ("run_id", "worker_id"))
            self._enabled(pid)
            event_uuid(payload["worker_id"])
            run = self._run(principal, pid, payload["run_id"])
            self._compatible(run)
            if self._status(run) not in ("queued", "running") or run["worker_id"] not in (None, payload["worker_id"]):
                raise StoreError("This job cannot be claimed without owner review.", 409)
            if self._unsupported_scope(principal, pid):
                return self._control(self._stop_scope(run), include_plan=True)
            if run["status"] == "queued":
                current = time.time()
                self.store.db.execute("UPDATE project_state_runs SET status='running',worker_id=?,lease_id=?,deadline=?,lease_expires=? WHERE id=?",
                    (payload["worker_id"], str(uuid.uuid4()), current + DEADLINE_SECONDS, current + LEASE_SECONDS, run["id"]))
                run = self._run(principal, pid, run["id"])
            return self._control(run, include_plan=True)

    def _control(self, run, include_plan=False):
        compatible = self._method_info(run)["execution_compatible"]
        result = {**self._job(run), "lease_id": run["lease_id"], "input_hash": run["input_hash"],
            "deadline_at": run["deadline"], "lease_expires_at": run["lease_expires"],
            "next": self._next(run) if compatible and self._status(run) == "running" else None,
            "budget": dict(BUDGET) if compatible else None,
            "execution_policy": json.loads(encode(METHOD_CONFIG)) if compatible else None}
        if include_plan:
            result["next_plan"] = self._plan(run, result["next"]) if result["next"] else None
        return result

    def _lease(self, payload, fields):
        principal, pid = self._scope(payload, ("run_id", "lease_id", "input_hash", *fields))
        self._enabled(pid)
        run = self._run(principal, pid, payload["run_id"])
        self._compatible(run)
        if payload["input_hash"] != run["input_hash"] or payload["lease_id"] != run["lease_id"]:
            raise StoreError("Project-state lease or input does not match.", 409)
        if self._status(run) != "running":
            raise StoreError("Project-state execution stopped or needs review.", 409)
        return principal, pid, run

    def heartbeat(self, payload):
        with self.store.lock, self.store.db:
            principal, pid, run = self._lease(payload, ())
            if self._unsupported_scope(principal, pid):
                return self._control(self._stop_scope(run))
            self.store.db.execute("UPDATE project_state_runs SET lease_expires=? WHERE id=?",
                (min(time.time() + LEASE_SECONDS, run["deadline"]), run["id"]))
            return self._control(self._run(principal, pid, run["id"]))

    def attempt(self, payload):
        with self.store.lock:
            with self.store.db:
                principal, pid, run = self._lease(payload, ("call_id", "phase", "chunk_index"))
                stopped = self._unsupported_scope(principal, pid)
                if stopped:
                    self._stop_scope(run)
            if stopped:
                raise StoreError("The approved pilot supports at most two source machines.", 409)
            with self.store.db:
                principal, pid, run = self._lease(payload, ("call_id", "phase", "chunk_index"))
                event_uuid(payload["call_id"])
                if payload["phase"] == "extract":
                    natural(payload["chunk_index"], "chunk index", maximum=MAX_CHUNKS - 1)
                elif payload["phase"] != "reconcile" or payload["chunk_index"] is not None:
                    raise StoreError("Invalid model phase.")
                prior = self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=? AND call_id=?", (run["id"], payload["call_id"])).fetchone()
                if prior:
                    raise StoreError("This model call was already dispatched; do not dispatch it again.", 409)
                expected = self._next(run)
                if expected != {"phase": payload["phase"], "chunk_index": payload["chunk_index"]}:
                    raise StoreError("The requested model slot is not ready.", 409)
                if payload["phase"] == "extract" and run["deadline"] - time.time() <= RECONCILIATION_RESERVE:
                    raise StoreError("The remaining deadline is reserved for reconciliation.", 409)
                plan = self._plan(run, expected)
                payload_hash = plan["payload_hash"]
                self.store.db.execute("INSERT INTO project_state_calls(run_id,call_id,phase,chunk_index,status,input,payload_hash,dispatched_at) VALUES (?,?,?,?,?,?,?,?)",
                    (run["id"], payload["call_id"], payload["phase"], payload["chunk_index"], "dispatched", encode(plan["input"]), payload_hash, now()))
                return {"project_id": pid, "run_id": run["id"], "call_id": payload["call_id"], **plan,
                    "execution_policy": json.loads(encode(METHOD_CONFIG))}

    def _plan(self, run, next_call):
        self._compatible(run)
        principal = {"account_id": run["account_id"], "workspace_id": run["workspace_id"]}
        pid, phase = run["project_id"], next_call["phase"]
        manifest = json.loads(run["manifest"])
        if not all(self._eligible(origin, principal, pid) for origin in manifest["sources"]):
            raise StoreError("Frozen inputs are no longer eligible.", 409)
        inputs = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (run["id"],)).fetchone()[0])
        if phase == "extract":
            control = {"selection_partial": True, "required_machine_aliases": [inputs["slot_machines"][next_call["chunk_index"]]]}
            model_input = model_payload("extract", inputs["provided_context"], control,
                messages=inputs["chunks"][next_call["chunk_index"]])
        else:
            extractions = []
            for row in self.store.db.execute("SELECT output,chunk_index FROM project_state_calls WHERE run_id=? AND phase='extract' AND status='completed' ORDER BY chunk_index", (run["id"],)):
                result = json.loads(row["output"])
                referenced = {ref["passage_id"] for item in [*result["assertions"], *result["uncertainties"]] for ref in item["source_refs"]}
                shared = [key for key in sorted(referenced) if len({inputs["sources"][sid]["device_id"] for sid in inputs["aliases"][inputs["passages"][key]["message_alias"]]["origin_ids"]}) == 2]
                extractions.append({"machine_alias": inputs["slot_machines"][row["chunk_index"]], "shared_passage_ids": shared, "result": result})
            referenced = allowed_passage_ids({"task": "reconcile", "extractions": extractions})
            resolved = [{"passage_id": key, "text": inputs["passages"][key]["quote"]} for key in sorted(referenced)]
            control = {"selection_partial": True, "required_machine_aliases": sorted(inputs["machine_aliases"].values())}
            headers = [{key: value for key, value in group.items() if key != "result"} for group in extractions]
            if serialized_bytes({"control": control, "extractions": headers}) > METHOD_CONFIG["coverage_prompt_bytes"]:
                raise StoreError("Machine evidence headers exceed the bounded method policy.", 413)
            model_input = model_payload("reconcile", inputs["provided_context"], control,
                extractions=extractions, passages=resolved)
        if not fits_input(model_input, phase):
            raise StoreError("The model input exceeds the complete bounded envelope.", 413)
        return {**next_call, "input": model_input, "output_schema": SCHEMAS[phase],
            "phase_config": dict(PHASES[phase]), "method_version": METHOD, "method_hash": METHOD_HASH,
            "payload_hash": digest(model_input)}

    def _validate_output(self, output, inputs, allowed, phase="reconcile"):
        closed(output, ("schema_version", "assertions", "uncertainties", "contradictions"))
        if type(output["schema_version"]) is not int or output["schema_version"] != 1:
            raise StoreError("Invalid state output schema.")
        for field, maximum in ((key, value["maxItems"]) for key, value in SCHEMAS[phase]["properties"].items() if key != "schema_version"):
            if not isinstance(output[field], list) or len(output[field]) > maximum:
                raise StoreError("Invalid state output count.")
        count, keys = 0, {}
        for assertion in output["assertions"]:
            closed(assertion, ("key", "topic", "text", "kind", "reported_state", "limitations", "source_refs"))
            for field, maximum in (("key", 40), ("topic", 80), ("text", 240)):
                string(assertion[field], field, maximum=maximum)
            if assertion["key"] in keys or assertion["kind"] not in KINDS or assertion["reported_state"] not in STATES:
                raise StoreError("Invalid assertion identity or kind.")
            keys[assertion["key"]] = assertion
            if not isinstance(assertion["limitations"], list) or len(assertion["limitations"]) > 1:
                raise StoreError("Invalid assertion limitations.")
            for limit in assertion["limitations"]:
                string(limit, "limitation", maximum=120)
        for uncertainty in output["uncertainties"]:
            closed(uncertainty, ("reason", "text", "source_refs"))
            if uncertainty["reason"] not in REASONS:
                raise StoreError("Invalid uncertainty reason.")
            string(uncertainty["text"], "uncertainty", maximum=180)
        for item in [*output["assertions"], *output["uncertainties"]]:
            refs = item["source_refs"]
            if not isinstance(refs, list) or not 1 <= len(refs) <= 2:
                raise StoreError("Every state assertion needs read evidence.")
            count += len(refs)
            for ref in refs:
                closed(ref, ("passage_id",))
                string(ref["passage_id"], "passage identifier", maximum=40)
                if ref["passage_id"] not in allowed or ref["passage_id"] not in inputs["passages"]:
                    raise StoreError("State evidence requires an immutable already supplied passage.")
                passage = inputs["passages"][ref["passage_id"]]
                original = inputs["aliases"][passage["message_alias"]]["message"]["text"]
                if not passage["quote"].strip() or len(passage["quote"]) > METHOD_CONFIG["passages"]["characters"] or original[passage["char_start"]:passage["char_end"]] != passage["quote"]:
                    raise StoreError("The immutable passage registry does not match supplied evidence.")
        if count > 500:
            raise StoreError("Too many state citations.")
        for contradiction in output["contradictions"]:
            closed(contradiction, ("assertion_keys", "text"))
            ids = contradiction["assertion_keys"]
            if not isinstance(ids, list) or not 2 <= len(ids) <= 4 or any(not isinstance(key, str) or key not in keys for key in ids) or len(set(ids)) != len(ids):
                raise StoreError("Contradictions need distinct known assertions.")
            string(contradiction["text"], "contradiction", maximum=240)
        canonical(output)
        if serialized_bytes(output) > PHASES[phase]["output_bytes"]:
            raise StoreError("The observed output exceeds its phase byte limit.")
        return output

    def _method(self, method):
        closed(method, ("adapter", "model", "elapsed_ms", "usage"), ("reasoning_effort",))
        if "reasoning_effort" in method and method["reasoning_effort"] not in EFFORTS:
            raise StoreError("Invalid acknowledged reasoning effort.")
        if method["adapter"] != "codex-native-v1":
            raise StoreError("Unsupported state model adapter.")
        string(method["model"], "model", maximum=200)
        if method["model"] in ("codex-default", "unknown", "default"):
            raise StoreError("Actual model identity is required.")
        natural(method["elapsed_ms"], "elapsed model time", maximum=300000)
        closed(method["usage"], ("input_tokens", "output_tokens", "total_tokens"))
        for count in method["usage"].values():
            natural(count, "token usage")
        if method["usage"]["total_tokens"] < method["usage"]["input_tokens"] + method["usage"]["output_tokens"]:
            raise StoreError("Invalid observed model usage.")

    def finish(self, payload):
        with self.store.lock, self.store.db:
            principal, pid = self._scope(payload, ("run_id", "lease_id", "input_hash", "call_id", "output", "method"))
            self._enabled(pid)
            run = self._run(principal, pid, payload["run_id"])
            if payload["lease_id"] != run["lease_id"] or payload["input_hash"] != run["input_hash"]:
                raise StoreError("Project-state lease or input does not match.", 409)
            event_uuid(payload["call_id"])
            call = self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=? AND call_id=?", (run["id"], payload["call_id"])).fetchone()
            if not call:
                raise StoreError("Model call not found.", 404)
            result_hash = digest({"output": payload["output"], "method": payload["method"]})
            if call["status"] in ("completed", "observed", "unknown"):
                if call["output_hash"] != result_hash:
                    raise StoreError("Model completion already describes different output.", 409)
                return self._completion_control(run) if call["status"] == "completed" else self._control(run)
            self._compatible(run)
            if self._status(run) != "running":
                raise StoreError("Execution expired; review before another model call.", 409)
            try:
                self._method(payload["method"])
            except StoreError:
                self.store.db.execute("UPDATE project_state_calls SET status='unknown',output_hash=? WHERE run_id=? AND call_id=?", (result_hash, run["id"], call["call_id"]))
                self.store.db.execute("UPDATE project_state_runs SET status='needs_review',failure_code='usage_unknown' WHERE id=?", (run["id"],))
                return self._control(self._run(principal, pid, run["id"]))
            total = self._job(run)["observed_tokens"] + payload["method"]["usage"]["total_tokens"]
            # Known spend is retained even when the result cannot be published.
            self.store.db.execute("UPDATE project_state_calls SET status='observed',output_hash=?,method=?,completed_at=? WHERE run_id=? AND call_id=?",
                (result_hash, encode(payload["method"]), now(), run["id"], call["call_id"]))
            if total > MAX_TOKENS:
                self.store.db.execute("UPDATE project_state_runs SET status='needs_review',failure_code='budget_exceeded' WHERE id=?", (run["id"],))
                return self._control(self._run(principal, pid, run["id"]))
            if self._unsupported_scope(principal, pid):
                return self._control(self._stop_scope(run))
            inputs = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (run["id"],)).fetchone()[0])
            source = json.loads(call["input"])
            allowed = allowed_passage_ids(source)
            try:
                if (payload["method"]["model"] != METHOD_CONFIG["model"]["name"] or
                        payload["method"].get("reasoning_effort") != PHASES[call["phase"]]["effort"]):
                    raise StoreError("Model identity or acknowledged phase effort differs from the frozen method.")
                output = self._validate_output(payload["output"], inputs, allowed, call["phase"])
            except StoreError:
                self.store.db.execute("UPDATE project_state_runs SET status='failed',failure_code='invalid_output' WHERE id=?", (run["id"],))
                return self._control(self._run(principal, pid, run["id"]))
            evidence_output = output
            if call["phase"] == "reconcile":
                records = {key: {"text": value["quote"], "message_id": inputs["aliases"][value["message_alias"]]["message_id"],
                    "char_start": value["char_start"], "char_end": value["char_end"]} for key, value in inputs["passages"].items()}
                evidence_output, _quality = filter_model_output(output, records)
            if not required_machine_aliases(source).issubset(referenced_machine_aliases(source, evidence_output)):
                self.store.db.execute("UPDATE project_state_calls SET output=? WHERE run_id=? AND call_id=?", (encode(output), run["id"], call["call_id"]))
                self.store.db.execute("UPDATE project_state_runs SET status='needs_review',failure_code='insufficient_device_evidence' WHERE id=?", (run["id"],))
                return self._control(self._run(principal, pid, run["id"]))
            manifest = json.loads(run["manifest"])
            if not all(self._eligible(origin, principal, pid) for origin in manifest["sources"]):
                self.store.db.execute("UPDATE project_state_runs SET status='failed',failure_code='input_ineligible' WHERE id=?", (run["id"],))
                return self._control(self._run(principal, pid, run["id"]))
            self.store.db.execute("UPDATE project_state_calls SET status='completed',output=?,output_hash=?,method=?,completed_at=? WHERE run_id=? AND call_id=?",
                (encode(output), result_hash, encode(payload["method"]), now(), run["id"], call["call_id"]))
            if call["phase"] == "reconcile":
                public = {"assertions": [], "uncertainties": [], "contradictions": output["contradictions"],
                    "generated_at": now(), "coverage": inputs["coverage"], "method": {"version": METHOD, "hash": METHOD_HASH,
                        "models": sorted({json.loads(row[0])["model"] for row in self.store.db.execute("SELECT method FROM project_state_calls WHERE run_id=? AND status='completed'", (run["id"],))}),
                        "reasoning_efforts": {phase: policy["effort"] for phase, policy in PHASES.items()},
                        "observed_tokens": total}}
                for section in ("assertions", "uncertainties"):
                    for index, item in enumerate(output[section]):
                        citation_ids = []
                        for ref in item["source_refs"]:
                            passage = inputs["passages"][ref["passage_id"]]
                            alias = inputs["aliases"][passage["message_alias"]]
                            citation_id = "citation:" + digest([run["id"], ref])
                            citation = {"citation_id": citation_id, "project_id": pid, "run_id": run["id"],
                                "passage_id": ref["passage_id"], "message_alias": passage["message_alias"],
                                "char_start": passage["char_start"], "char_end": passage["char_end"],
                                "quote": passage["quote"], "message_id": alias["message_id"],
                                "role": alias["message"]["role"], "line_start": alias["message"]["line_start"],
                                "line_end": alias["message"]["line_end"], "message_timestamp": alias["message"]["timestamp"],
                                "truncated": alias["truncated"], "origins": [inputs["sources"][sid] for sid in alias["origin_ids"]]}
                            self.store.db.execute("INSERT OR IGNORE INTO project_state_citations VALUES (?,?,?)", (run["id"], citation_id, encode(citation)))
                            citation_ids.append(citation_id)
                        assertion = {key: value for key, value in item.items() if key != "source_refs"}
                        assertion.update(id="assertion:" + digest([run["id"], section, index]), citation_ids=citation_ids)
                        self.store.db.execute("INSERT INTO project_state_assertions VALUES (?,?,?)", (run["id"], assertion["id"], encode(assertion)))
                        public[section].append(assertion)
                revision = digest(public)
                self.store.db.execute("UPDATE project_state_runs SET status='ready',output=?,state_revision=? WHERE id=?",
                    (encode(public), revision, run["id"]))
            return self._completion_control(self._run(principal, pid, run["id"]))

    def _completion_control(self, run):
        try:
            return self._control(run, include_plan=True)
        except StoreError:
            # A planning refusal must not undo a completed call or known usage.
            self.store.db.execute("UPDATE project_state_runs SET status='failed',failure_code='input_ineligible' WHERE id=?", (run["id"],))
            result = self._control(self._run({"account_id": run["account_id"], "workspace_id": run["workspace_id"]}, run["project_id"], run["id"]))
            result["next_plan"] = None
            return result

    def fail(self, payload):
        with self.store.lock, self.store.db:
            principal, pid = self._scope(payload, ("run_id", "lease_id", "input_hash", "code"), ("call_id", "method"))
            self._enabled(pid)
            run = self._run(principal, pid, payload["run_id"])
            if payload["lease_id"] != run["lease_id"] or payload["input_hash"] != run["input_hash"]:
                raise StoreError("Project-state lease or input does not match.", 409)
            if payload["code"] not in FAILURES:
                raise StoreError("Invalid safe failure code.")
            if ("call_id" in payload) != ("method" in payload):
                raise StoreError("Failure accounting requires a call and method together.")
            if "call_id" in payload:
                event_uuid(payload["call_id"])
                self._method(payload["method"])
                call = self.store.db.execute("SELECT * FROM project_state_calls WHERE run_id=? AND call_id=?", (run["id"], payload["call_id"])).fetchone()
                if not call:
                    raise StoreError("Dispatched model call not found.", 404)
                failure_hash = digest({key: payload[key] for key in ("code", "call_id", "method")})
                if call["failure_hash"]:
                    if call["failure_hash"] != failure_hash:
                        raise StoreError("Failure accounting already describes another outcome.", 409)
                    return self._control(run)
                if call["method"] and json.loads(call["method"]) != payload["method"]:
                    raise StoreError("Observed model accounting cannot be rewritten.", 409)
                # A late report adds accounting only, never a validated output.
                status = call["status"] if call["status"] == "completed" else "observed_failure" if payload["code"] in ("invalid_output", "insufficient_device_evidence") else "interrupted"
                self.store.db.execute("UPDATE project_state_calls SET method=?,failure_hash=?,status=? WHERE run_id=? AND call_id=?",
                    (encode(payload["method"]), failure_hash, status, run["id"], call["call_id"]))
            if run["status"] != "running":
                return self._control(run)
            status = "needs_review" if payload["code"] in ("usage_unknown", "budget_exceeded", "ambiguous_completion", "lease_expired", "deadline_exceeded", "insufficient_device_evidence", "unsupported_device_scope") else "failed"
            if self._status(run) == "needs_review" or self._job(run)["observed_tokens"] > MAX_TOKENS:
                status = "needs_review"
            self.store.db.execute("UPDATE project_state_runs SET status=?,failure_code=? WHERE id=?", (status, payload["code"], run["id"]))
            return self._control(self._run(principal, pid, run["id"]))

    def citation(self, payload):
        with self.store.lock:
            principal, pid = self._scope(payload, ("run_id", "citation_id"))
            self._run(principal, pid, payload["run_id"])
            row = self.store.db.execute("SELECT data FROM project_state_citations WHERE run_id=? AND id=?", (payload["run_id"], payload["citation_id"])).fetchone()
            if not row:
                raise StoreError("State citation not found.", 404)
            citation = json.loads(row[0])
            if not self._valid_citation(citation, principal, pid):
                raise StoreError("State citation is no longer eligible.", 404)
            inputs = json.loads(self.store.db.execute("SELECT data FROM project_state_inputs WHERE run_id=?", (payload["run_id"],)).fetchone()[0])
            alias = inputs["aliases"][citation["message_alias"]]
            raw = alias["message"]["text"].encode("utf-8")
            position = raw.find(citation["quote"].encode("utf-8"))
            start = max(0, position - 2048)
            excerpt = raw[start:start + 8192].decode("utf-8", errors="ignore")
            return {**citation, "excerpt": excerpt,
                    "excerpt_truncated": bool(start or start + 8192 < len(raw) or alias["truncated"])}
