"""A bounded project-recognition pass, separate from conversation briefs."""

import json
import time
import uuid

from .store import StoreError, boolean, encode, identifier, lexical_absolute, now, require_object, string, timestamp, within
from . import recognition


def initial_overview():
    return {"request_id": None, "stage": "idle", "inventory_complete": False,
            "files_seen": 0, "excluded": 0, "failures": [], "model": None,
            "considered": 0, "elapsed_seconds": None, "input_chars": 0,
            "message": None, "deep_queue": [], "deep_done": 0, "deep_total": 0}


class Overview:
    def __init__(self, store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS recognition_excerpts (
                session_id TEXT PRIMARY KEY REFERENCES catalog(session_id),
                fingerprint TEXT NOT NULL, context_hash TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS catalog (
                session_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, scan_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outlines (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, decision TEXT NOT NULL,
                project_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS outline_members (
                outline_id TEXT NOT NULL REFERENCES outlines(id), session_id TEXT NOT NULL REFERENCES catalog(session_id),
                PRIMARY KEY(outline_id,session_id)
            );
            CREATE TABLE IF NOT EXISTS catalog_links (
                project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL REFERENCES catalog(session_id),
                PRIMARY KEY(project_id,session_id)
            );
        """)
        with store.db:
            store.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", ("overview", encode(initial_overview())))
        recognition.initialize(store)
        from .live_discovery import initialize
        initialize(store)

    def _active(self, payload):
        store = self.store
        store._generation(payload)
        control = store._get("control")
        state = store._get("overview")
        if not control["scope_confirmed"] or not control["running"] or payload.get("request_id") != state["request_id"]:
            raise StoreError("Cette passe a été arrêtée ou remplacée.", 409)
        return state, control

    def start(self, payload):
        store = self.store
        reexamine = boolean(payload.get("reexamine", False), "reexamine")
        local_only = boolean(payload.get('local_only', False), 'local inventory')
        with store.lock, store.db:
            control = store._get("control")
            if not control["scope_confirmed"] or (not local_only and not control["inference_enabled"]):
                raise StoreError("Confirmez le périmètre et le traitement Codex avant la découverte.", 409)
            state = store._get("overview")
            state.update(request_id=str(uuid.uuid4()), stage="inventory", inventory_complete=False,
                         message=None, deep_queue=[], retry_count=0, retry_at=0,
                         reexamine=reexamine, queue=[], pass_done=0, pass_total=0,
                         pass_new=0, pass_reexamined=0, batches_done=0, batches_total=0,
                         pass_elapsed=0, discarded_total=0, batch_size=recognition.BATCH_SIZE,
                         completion_phase=None, agent_progress=None, agent_continuous=False, local_only=local_only)
            if local_only:
                control['inference_enabled'] = False
            control.update(running=True, mode="overview")
            store._set("overview", state)
            store._set("control", control)
            return state

    def catalog(self, payload):
        store = self.store
        require_object(payload)
        records = payload.get("records", [])
        if not isinstance(records, list) or len(records) > 500:
            raise StoreError("Lot de métadonnées invalide.")
        complete = boolean(payload.get("complete", False), "catalog completion")
        validated = []
        for row in records:
            require_object(row)
            item = {"session_id": identifier(row.get("session_id")),
                    "title": string(row.get("title"), "title", maximum=240),
                    "cwd": string(row.get("cwd", ""), "directory", empty=True, maximum=4096),
                    "source_path": string(row.get("source_path"), "source path", maximum=4096),
                    "modified_at": timestamp(row.get("modified_at")),
                    "title_source": string(row.get("title_source"), "title source", maximum=100)}
            if type(row.get("size")) is not int or row["size"] < 0:
                raise StoreError("Taille invalide.")
            item["size"] = row["size"]
            validated.append(item)
        failures = payload.get("failures", [])
        if not isinstance(failures, list) or len(failures) > 100000:
            raise StoreError("Inventaire invalide.")
        failures = [{"source_id": identifier(f.get("source_id")),
                     "reason": string(f.get("reason"), "inventory failure", maximum=1000)} for f in failures]
        for key in ("excluded", "files_seen"):
            if type(payload.get(key, 0)) is not int or payload.get(key, 0) < 0:
                raise StoreError("Compte d’inventaire invalide.")
        with store.lock, store.db:
            state, control = self._active(payload)
            for row in validated:
                cwd = lexical_absolute(row["cwd"])
                if (control["exclusions"] and cwd is None) or any(within(cwd or "", p) for p in control["exclusions"]):
                    raise StoreError("Métadonnées hors du périmètre autorisé.", 403)
                if lexical_absolute(row["source_path"]) is None:
                    raise StoreError("Chemin de source invalide.")
                row["cwd"] = cwd or ""
                store.db.execute("INSERT INTO catalog VALUES (?,?,?) ON CONFLICT(session_id) DO UPDATE SET metadata=excluded.metadata,scan_id=excluded.scan_id",
                                 (row["session_id"], encode(row), state["request_id"]))
            state.update(inventory_complete=complete, stage="classifying" if complete else "inventory",
                         excluded=payload.get("excluded", 0), failures=failures, files_seen=payload.get("files_seen", 0))
            if complete:
                records = recognition.eligible(store, state["request_id"], state.get("reexamine", False))
                prior = {r[0] for r in store.db.execute("SELECT session_id FROM title_assessments")}
                if not prior and not recognition.decided_ids(store):
                    state["batch_size"] = recognition.first_pass_size(records, recognition.memory_context(store))
                size = state["batch_size"]
                state.update(queue=[r["session_id"] for r in records], pass_total=len(records),
                             pass_new=sum(r["session_id"] not in prior for r in records),
                             pass_reexamined=sum(r["session_id"] in prior for r in records),
                             batches_total=(len(records) + size - 1) // size)
                if not records:
                    state.update(stage="review", message="Inventaire à jour. Aucun nouveau titre à examiner.")
                    control["running"] = False
                    store._set("control", control)
                if state.get('local_only'):
                    state.update(stage='awaiting_consent', message='Inventaire local prêt. Aucun titre envoyé à OpenAI.')
                    control['running'] = False
                    store._set('control', control)
            store._set("overview", state)
            return {"received": len(validated)}

    def inputs(self):
        store = self.store
        with store.lock:
            state = store._get("overview")
            if "queue" not in state:
                # A paused legacy pass can resume through the new tracked workflow.
                records = recognition.eligible(store, state["request_id"])
                prior = {r[0] for r in store.db.execute("SELECT session_id FROM title_assessments")}
                state.update(queue=[r["session_id"] for r in records], pass_done=0, pass_total=len(records),
                             pass_new=sum(r["session_id"] not in prior for r in records),
                             pass_reexamined=sum(r["session_id"] in prior for r in records),
                             batches_done=0, batches_total=(len(records) + recognition.BATCH_SIZE - 1) // recognition.BATCH_SIZE)
                with store.db:
                    store._set("overview", state)
            records = [json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
                       for sid in state["queue"][:state.get("batch_size", recognition.BATCH_SIZE)]]
            if state.get("completion_phase") == "agent":
                for record in records:
                    assessment = store.db.execute("SELECT outcome,reason FROM title_assessments WHERE session_id=?", (record["session_id"],)).fetchone()
                    record["first_pass"] = {"outcome": assessment[0], "reason": assessment[1]} if assessment else {"outcome": "unexamined"}
            return records

    def complete(self, payload):
        import hashlib
        store = self.store
        with store.lock, store.db:
            control = store._get("control")
            if control["running"] or not control["scope_confirmed"] or not control["inference_enabled"]:
                raise StoreError("Mettez la lecture en pause et confirmez le traitement Codex.", 409)
            if payload.get("include_content") is not True:
                raise StoreError("Confirmez la lecture locale et l’envoi d’extraits ciblés à OpenAI.", 409)
            continuous = payload.get("automatic_associations") is True
            bounded = continuous and payload.get('bounded_discovery') is True
            approved = {row["id"] for row in store.db.execute("SELECT * FROM projects WHERE merged_into IS NULL")
                        if {"name", "purpose"}.issubset(store._project(row)["confirmed_fields"])}
            context = [p for p in recognition.memory_context(store) if p["id"] in approved]
            if not context:
                raise StoreError("Validez au moins un projet pour guider cette seconde passe.", 409)
            context_hash = hashlib.sha256(encode(context).encode()).hexdigest()
            candidates = {row["id"] for row in self.clarification_inputs()}
            records = [json.loads(row[0]) for row in store.db.execute("SELECT metadata FROM catalog")]
            sampled = {row["session_id"]: row for row in store.db.execute("SELECT * FROM recognition_excerpts WHERE context_hash=?", (context_hash,))}
            records = [row for row in records if row["session_id"] in candidates and
                       (bounded or row["session_id"] not in sampled or sampled[row["session_id"]]["fingerprint"] != recognition.excerpt_fingerprint(row))]
            records.sort(key=lambda row: (row["modified_at"], row["session_id"]), reverse=True)
            if not records:
                raise StoreError("Aucune conversation restante à compléter avec ce contexte.", 409)
            state = self.start({"reexamine": True})
            size = len(records)
            state.update(inventory_complete=True, stage="classifying", completion_phase="agent",
                         agent_continuous=continuous, agent_deadline=time.time() + (235 if bounded else 3600), agent_checked=0,
                         bounded_discovery=bounded, bounded_classified=[],
                         completion_context=context_hash, completion_context_data=context, completion_ids=[r["session_id"] for r in records],
                         queue=[r["session_id"] for r in records], batch_size=size,
                         pass_total=len(records), pass_reexamined=len(records),
                         batches_total=(len(records) + size - 1) // size)
            for row in records:
                store.db.execute("UPDATE catalog SET scan_id=? WHERE session_id=?", (state["request_id"], row["session_id"]))
            store._set("overview", state)
            return {"titles": len(records), "checkpoint_conversations": 24, "max_excerpt_chars": 3000, "global_minutes": 235 / 60 if bounded else 60}

    def agent_progress(self, payload):
        store = self.store
        events, plan = payload.get("events", []), payload.get("plan", [])
        elapsed = payload.get("elapsed_seconds")
        if not isinstance(events, list) or len(events) > 40 or not isinstance(plan, list) or len(plan) > 12:
            raise StoreError("Progression de l’agent invalide.")
        if type(elapsed) not in (float, int) or not 0 <= elapsed <= 86400:
            raise StoreError("Durée de l’agent invalide.")
        clean = []
        for event in events:
            require_object(event)
            if event.get("kind") not in ("status", "tool", "message", "summary"):
                raise StoreError("Événement de l’agent non autorisé.")
            at = event.get("elapsed_seconds")
            if type(at) not in (int, float) or not 0 <= at <= 86400:
                raise StoreError("Horodatage invalide.")
            clean.append({"kind": event["kind"], "text": string(event.get("text"), "progress", empty=True, maximum=4000), "elapsed_seconds": at})
        steps = []
        for step in plan:
            require_object(step)
            if step.get("status") not in ("pending", "inProgress", "in_progress", "completed"):
                raise StoreError("Étape invalide.")
            steps.append({"step": string(step.get("step"), "step", empty=True, maximum=300), "status": step["status"]})
        usage = payload.get("usage")
        if usage is not None:
            require_object(usage)
            usage = {k: usage[k] for k in ("inputTokens", "outputTokens", "totalTokens", "cachedInputTokens") if k in usage}
            if any(type(v) is not int or v < 0 for v in usage.values()):
                raise StoreError("Consommation invalide.")
        with store.lock, store.db:
            state, control = self._active(payload)
            if state.get("completion_phase") != "agent":
                raise StoreError("Aucune exploration active.", 409)
            state["agent_progress"] = {"events": clean, "plan": steps, "elapsed_seconds": elapsed, "usage": usage}
            store._set("overview", state)
            return {"received": True}

    def result(self, payload):
        store = self.store
        proposals = payload.get("projects")
        if not isinstance(proposals, list):
            raise StoreError("La liste des propositions est invalide.")
        input_ids = payload.get("input_ids")
        if not isinstance(input_ids, list) or len(input_ids) > 100000 or any(not isinstance(sid, str) for sid in input_ids) or len(set(input_ids)) != len(input_ids):
            raise StoreError("Références d’entrée invalides.")
        model = string(payload.get("model"), "model", maximum=100)
        discarded = payload.get("discarded_proposals", 0)
        if type(discarded) is not int or discarded < 0:
            raise StoreError("Compte de propositions écartées invalide.")
        elapsed = payload.get("elapsed_seconds")
        if type(elapsed) not in (int, float) or not 0 <= elapsed <= 86400:
            raise StoreError("Durée invalide.")
        if type(payload.get("input_chars")) is not int or payload["input_chars"] < 0:
            raise StoreError("Taille d’entrée invalide.")
        with store.lock, store.db:
            state, control = self._active(payload)
            agent = state.get("completion_phase") == "agent"
            sampling = agent or state.get("completion_phase") == "excerpts"
            if state["stage"] != "classifying":
                raise StoreError("Cette passe a déjà produit un résultat.", 409)
            if input_ids != state.get("queue", [])[:state.get("batch_size", recognition.BATCH_SIZE)] or set(input_ids) & recognition.decided_ids(store):
                raise StoreError("Ce lot n’est plus celui attendu.", 409)
            valid_ids = {r[0] for r in store.db.execute("SELECT session_id FROM catalog WHERE scan_id=?", (state["request_id"],))}
            if not set(input_ids).issubset(valid_ids):
                raise StoreError("Source absente de cet inventaire.")
            if sampling:
                evidence = require_object(payload.get("excerpt_evidence", {}))
                if set(evidence) - set(input_ids):
                    raise StoreError("Extrait hors de la sélection.")
                if agent and len(evidence) > 24:
                    raise StoreError("Budget d’extraits dépassé.")
                for sid, sample in evidence.items():
                    session = store.session(sid)
                    if sample.get("snapshot_id") != session["snapshot_id"]:
                        raise StoreError("La source de l’extrait a changé.", 409)
                    messages = {m["id"]: m["text"] for m in session["messages"]}
                    pieces = sample.get("excerpts")
                    if not isinstance(pieces, list) or not 1 <= len(pieces) <= 4:
                        raise StoreError("Extraits invalides.")
                    for piece in pieces:
                        text = string(piece.get("text"), "excerpt", maximum=750)
                        start = piece.get("start")
                        if type(start) is not int or start < 0 or messages.get(piece.get("message_id"), "")[start:start + len(text)] != text:
                            raise StoreError("L’extrait ne correspond pas à sa source.")
                    metadata = json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
                    store.db.execute("INSERT OR REPLACE INTO recognition_excerpts VALUES (?,?,?,?)",
                                     (sid, recognition.excerpt_fingerprint(metadata), state["completion_context"], encode(sample)))
            proposed_ids = set()
            for proposal in proposals:
                require_object(proposal)
                for key in ("name", "purpose", "rationale"):
                    string(proposal.get(key), key, maximum=2000)
                ids = proposal.get("session_ids")
                if not isinstance(ids, list) or not ids or any(not isinstance(sid, str) for sid in ids) or not set(ids).issubset(input_ids):
                    raise StoreError("Projet sans métadonnées justificatives valides.")
                if sampling and not set(ids).issubset(evidence):
                    raise StoreError("Rattachement sans extrait justificatif.")
                if proposed_ids.intersection(ids):
                    raise StoreError("Une conversation a plusieurs décisions dans ce lot.")
                proposed_ids.update(ids)
                existing = proposal.get("existing_project_id")
                if existing is not None:
                    if isinstance(existing, str) and existing.startswith("outline:"):
                        if not store.db.execute("SELECT 1 FROM outlines WHERE id=? AND decision='pending'", (existing[8:],)).fetchone():
                            raise StoreError("Proposition cible remplacée.", 409)
                    else:
                        store._resolve(existing)
            unassigned = payload.get("unassigned", [])
            if not isinstance(unassigned, list):
                raise StoreError("Résultats de classement invalides.")
            residual = {}
            for item in unassigned:
                require_object(item)
                sid = identifier(item.get("session_id"))
                if sid not in input_ids or sid in proposed_ids or sid in residual or item.get("outcome") not in ("no_project", "ambiguous"):
                    raise StoreError("Résultat sans source valide.")
                residual[sid] = (item["outcome"], string(item.get("reason"), "reason", maximum=1000))
            recognition.remove_pending_sources(store, list(evidence) if agent else input_ids)
            for proposal in proposals:
                proposal = dict(proposal, session_ids=list(dict.fromkeys(proposal["session_ids"])))
                existing = proposal.get("existing_project_id")
                target = None
                if existing and existing.startswith("outline:"):
                    target = store.db.execute("SELECT * FROM outlines WHERE id=?", (existing[8:],)).fetchone()
                elif existing:
                    target = next((r for r in store.db.execute("SELECT * FROM outlines WHERE decision='pending'")
                                   if json.loads(r["data"]).get("existing_project_id") == existing), None)
                if target:
                    oid = target["id"]
                    data = json.loads(target["data"])
                    data["session_ids"] = list(dict.fromkeys(data["session_ids"] + proposal["session_ids"]))
                    store.db.execute("UPDATE outlines SET data=? WHERE id=?", (encode(data), oid))
                else:
                    oid = str(uuid.uuid4())
                    data = {**proposal, "model": model}
                    if existing and existing.startswith("outline:"):
                        data["existing_project_id"] = None
                    store.db.execute("INSERT INTO outlines VALUES (?,?,?,?,?)", (oid, encode(data), "pending", None, now()))
                for sid in set(proposal["session_ids"]):
                    store.db.execute("INSERT OR IGNORE INTO outline_members VALUES (?,?)", (oid, sid))
                    residual[sid] = ("proposed", "Rattachement proposé, à valider.")
            for sid in input_ids:
                if agent and sid not in evidence:
                    continue
                metadata = json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
                outcome, reason = residual.get(sid, ("ambiguous", "Le modèle n’a pas fourni de décision exploitable pour ce titre."))
                if sampling:
                    if sid not in evidence:
                        outcome, reason = "ambiguous", "Aucun extrait exploitable : source illisible, hors limite ou texte filtré. Aucune conclusion sur le projet."
                    else:
                        reason = "Classement sur extraits partiels, pas sur l’intégralité des échanges. " + reason
                store.db.execute("INSERT INTO title_assessments VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET fingerprint=excluded.fingerprint,outcome=excluded.outcome,reason=excluded.reason,request_id=excluded.request_id,updated_at=excluded.updated_at",
                                 (sid, recognition.fingerprint(metadata), outcome, reason, state["request_id"], now()))
            state["queue"] = state["queue"][len(input_ids):]
            state.update(pass_done=state.get("pass_done", 0) + (0 if sampling else len(input_ids)),
                         batches_done=state.get("batches_done", 0) + 1,
                         pass_elapsed=state.get("pass_elapsed", 0) + elapsed,
                         discarded_total=state.get("discarded_total", 0) + discarded)
            state.update(stage="classifying" if state["queue"] else "review", model=model,
                         input_chars=payload["input_chars"], retry_count=0, retry_at=0,
                         message=None)
            if not state["queue"]:
                state.update(considered=state["pass_done"], elapsed_seconds=state["pass_elapsed"],
                             message=(f"{state['discarded_total']} proposition(s) écartée(s). Les titres concernés restent à clarifier ; les résultats valides sont disponibles." if state["discarded_total"] else None))
            control["running"] = bool(state["queue"])
            if not state["queue"] and state.get("completion_phase") == "titles":
                from .local_triage import rank
                eligible = {row["id"] for row in self.clarification_inputs()}
                records = [json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
                           for sid in state["completion_ids"] if sid in eligible]
                selected = rank(records, state["completion_context_data"])
                state.update(completion_phase="excerpts" if selected else "done",
                             queue=[row["session_id"] for row in selected], batch_size=12,
                             batches_total=state["batches_done"] + bool(selected),
                             stage="classifying" if selected else "review",
                             message=None if selected else "Les titres restants ont été reclassés. Aucun contenu supplémentaire à lire dans cette passe.", content_selected=len(selected))
                control["running"] = bool(selected)
            elif sampling and not state["queue"]:
                state.update(completion_phase="done", content_sampled=len(payload.get("excerpt_evidence", {})),
                             message=f"Seconde passe terminée : {len(evidence)} conversations examinées par extraits, {len(input_ids) - len(evidence)} sans nouvelle conclusion. Les rattachements restent à valider ; le classement des autres conversations est conservé.")
            store._set("overview", state)
            store._set("control", control)
            return {"ready": len(proposals)}

    def review(self, outline_id, payload):
        store = self.store
        decision = payload.get("decision")
        if decision not in ("keep", "skip", "inactive"):
            raise StoreError("Choisissez garder ou écarter.")
        with store.lock, store.db:
            if store._get("control")["running"] and not (store._get("overview").get("completion_phase") == "agent" and store._get("overview").get("agent_continuous")):
                raise StoreError("Mettez la lecture en pause avant de valider.", 409)
            row = store.db.execute("SELECT * FROM outlines WHERE id=?", (identifier(outline_id),)).fetchone()
            if row is None:
                raise StoreError("Proposition introuvable.", 404)
            if row["decision"] != "pending":
                raise StoreError("Cette proposition a déjà été revue.", 409)
            data = json.loads(row["data"])
            protected_projects = {value for value in (data.get("existing_project_id"), payload.get("project_id")) if value}
            for sid in data["session_ids"]:
                protected_projects.update(r[0] for r in store.db.execute("SELECT project_id FROM associations WHERE session_id=? UNION SELECT project_id FROM catalog_links WHERE session_id=?", (sid, sid)))
            for project_id in protected_projects:
                store.require_project_writable(project_id)
            pid = None
            if decision in ("keep", "inactive"):
                name = string(payload.get("name", data["name"]), "name", maximum=2000)
                purpose = string(payload.get("purpose", data["purpose"]), "purpose", empty=True, maximum=2000)
                existing = payload.get("project_id", data.get("existing_project_id"))
                if existing:
                    project = store._resolve(existing)
                    pid = project["id"]
                    before = store._project(project)
                    edits = {key: value for key, value in (("name", name), ("purpose", purpose)) if key in payload}
                    if edits:
                        overrides = json.loads(project["overrides"])
                        overrides.update(edits)
                        store.db.execute("UPDATE projects SET overrides=?,updated_at=? WHERE id=?", (encode(overrides), now(), pid))
                else:
                    pid = str(uuid.uuid4())
                    created = now()
                    store.db.execute("INSERT INTO projects VALUES (?,?,?,NULL,?,?)", (pid,
                        encode({"name": name, "purpose": purpose, "status": "unclear"}),
                        encode({"name": name, "purpose": purpose}), created, created))
                    before = {}
                for sid in data["session_ids"]:
                    store.db.execute("INSERT OR IGNORE INTO catalog_links VALUES (?,?)", (pid, sid))
                    if store.db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                        store.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (sid, pid))
                        store.db.execute("UPDATE sessions SET assignment_locked=1 WHERE id=?", (sid,))
                if decision == "inactive":
                    project = store._resolve(pid)
                    overrides = json.loads(project["overrides"])
                    overrides["activity"] = "inactive"
                    store.db.execute("UPDATE projects SET overrides=?,updated_at=? WHERE id=?", (encode(overrides), now(), pid))
                store._change(pid, "user_recognition", "Vous avez reconnu ce projet à partir de ses titres de conversations.", before,
                              store._project(store._resolve(pid)), [{"catalog_session_id": sid} for sid in data["session_ids"]])
            store.db.execute("UPDATE outlines SET decision=?,project_id=? WHERE id=?", (decision, pid, outline_id))
            for sid in data["session_ids"]:
                rejected = [data["existing_project_id"]] if decision == "skip" and data.get("existing_project_id") else []
                store.db.execute("INSERT OR IGNORE INTO title_decisions VALUES (?,?,?,?,?)",
                                 (sid, "project" if pid else "none", pid, encode(rejected), now()))
            recognition.prune_queue(store)
            from .live_discovery import feedback_changed
            feedback_changed(store)
            return {"decision": decision, "project_id": pid}

    def clarification_inputs(self):
        store = self.store
        with store.lock:
            details = {row["id"]: row for row in recognition.catalog_details(store)}
            result = []
            for row in store.db.execute("SELECT metadata FROM catalog"):
                metadata = json.loads(row[0])
                sid = metadata["session_id"]
                detail = details[sid]
                if detail["outcome"] not in ("ambiguous", "no_project") or detail["project_ids"]:
                    continue
                raw = store.db.execute("SELECT s.status,p.modified_at FROM sessions s JOIN snapshots p ON p.id=s.snapshot_id WHERE s.id=?", (sid,)).fetchone()
                if raw and raw["status"] == "analyzed" and timestamp(raw["modified_at"]) >= timestamp(metadata["modified_at"]):
                    continue
                result.append((metadata["modified_at"], detail))
            return [row for _, row in sorted(result, key=lambda x: (x[0], x[1]["id"]), reverse=True)]

    def clarify(self, payload):
        store = self.store
        with store.lock, store.db:
            control = store._get("control")
            if control["running"] or not control["scope_confirmed"] or not control["inference_enabled"]:
                raise StoreError("Arrêtez la lecture en cours et confirmez le traitement Codex.", 409)
            ids = payload.get("session_ids")
            if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids):
                raise StoreError("Choisissez de une à trois conversations.")
            eligible = {row["id"] for row in self.clarification_inputs()}
            if not set(ids).issubset(eligible):
                raise StoreError("La sélection a changé : actualisez les conversations à clarifier.", 409)
            todo = [json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0]) for sid in ids]
            state = store._get("overview")
            state.update(request_id=str(uuid.uuid4()), stage="deepening", deep_queue=todo,
                         deep_project_id=None, completion_phase=None, deep_done=0, deep_total=len(todo), message=None,
                         deep_failures=[], retry_count=0, retry_at=0)
            control.update(mode="deep", running=True)
            store._set("overview", state)
            store._set("control", control)
            return {"queued": len(todo)}

    def deepen(self, project_id, payload):
        store = self.store
        with store.lock, store.db:
            control = store._get("control")
            if control["running"]:
                raise StoreError("Mettez la passe en cours en pause avant de changer de projet.", 409)
            if not control["scope_confirmed"] or not control["inference_enabled"]:
                raise StoreError("Le traitement Codex doit être autorisé.", 409)
            project = store._project(store._resolve(project_id))
            if project["activity"] == "inactive":
                raise StoreError("Réactivez ce projet avant de lancer une lecture détaillée.", 409)
            todo = []
            for sid in project["session_ids"]:
                raw = store.db.execute("SELECT s.status,p.modified_at FROM sessions s JOIN snapshots p ON p.id=s.snapshot_id WHERE s.id=?", (sid,)).fetchone()
                source = store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()
                if source:
                    metadata = json.loads(source[0])
                    if raw and raw["status"] == "analyzed" and timestamp(raw["modified_at"]) >= timestamp(metadata["modified_at"]):
                        continue
                else:
                    if raw and raw["status"] == "analyzed":
                        continue
                    source = store.db.execute("SELECT p.source_path,p.modified_at FROM sessions s JOIN snapshots p ON p.id=s.snapshot_id WHERE s.id=?", (sid,)).fetchone()
                    if not source:
                        continue
                    metadata = dict(source)
                    metadata["session_id"] = sid
                todo.append(metadata)
            todo.sort(key=lambda x: x["modified_at"], reverse=True)
            todo = todo[:3]
            if not todo:
                raise StoreError("Les conversations liées ont déjà un résumé.", 409)
            state = store._get("overview")
            state.update(request_id=str(uuid.uuid4()), stage="deepening", deep_queue=todo,
                         deep_project_id=project["id"], completion_phase=None,
                         deep_done=0, deep_total=len(todo), message=None, deep_failures=[], retry_count=0, retry_at=0)
            control.update(mode="deep", running=True)
            store._set("overview", state)
            store._set("control", control)
            return {"queued": len(todo)}

    def advance(self, payload):
        store = self.store
        with store.lock, store.db:
            state, control = self._active(payload)
            sid = identifier(payload.get("session_id"))
            if not state["deep_queue"] or state["deep_queue"][0]["session_id"] != sid:
                raise StoreError("Cette étape n’est plus en cours.", 409)
            state["deep_queue"].pop(0)
            state["deep_done"] += 1
            state.update(retry_count=0, retry_at=0)
            if payload.get("reason"):
                state.setdefault("deep_failures", []).append({"session_id": sid,
                    "reason": string(payload["reason"], "failure", maximum=1000)})
            if not state["deep_queue"]:
                state["stage"] = "review"
                control["running"] = False
            store._set("overview", state)
            store._set("control", control)
            return {"remaining": len(state["deep_queue"])}

    def interruption(self, payload):
        store = self.store
        with store.lock, store.db:
            state, control = self._active(payload)
            count = state.get("retry_count", 0) + 1
            state.update(retry_count=count, message="Connexion interrompue. Reprise automatique dès que Codex répond.")
            if count <= 3:
                state["retry_at"] = time.time() + (5, 15, 60)[count - 1]
            else:
                state.update(stage="error", message="Codex ne répond toujours pas. La passe est conservée ; reprenez quand la connexion est rétablie.")
                control["running"] = False
            store._set("overview", state)
            store._set("control", control)
            return {"retry_count": count}

    def failure(self, payload):
        store = self.store
        with store.lock, store.db:
            state, control = self._active(payload)
            state.update(stage="error", message=string(payload.get("reason"), "failure", maximum=1000))
            control["running"] = False
            store._set("overview", state)
            store._set("control", control)
            return {"paused": True}

    def summary(self):
        store = self.store
        state = store._get("overview")
        result = {key: value for key, value in state.items() if key not in ("deep_queue", "queue", "completion_context_data", "completion_ids")}
        result.update(recognition.coverage(store))
        result["sampled"] = store.db.execute("SELECT COUNT(*) FROM recognition_excerpts").fetchone()[0]
        from .live_discovery import associations
        result["automatic_associations"] = associations(store)
        result["agent_remaining"] = len(state.get("queue", []))
        result["coverage_note"] = store._get("legacy_coverage_note") if result["unexamined"] else ""
        result["indexed"] = store.db.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        result["remaining"] = store.db.execute("SELECT COUNT(*) FROM catalog c WHERE NOT EXISTS (SELECT 1 FROM outline_members m WHERE m.session_id=c.session_id)").fetchone()[0]
        result["candidates"] = []
        for row in store.db.execute("SELECT * FROM outlines WHERE decision='pending' ORDER BY rowid"):
            data = json.loads(row["data"])
            sources = []
            for sid in data["session_ids"]:
                metadata = json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
                from .catalog import clean_title
                sample = store.db.execute("SELECT data,fingerprint FROM recognition_excerpts WHERE session_id=?", (sid,)).fetchone()
                excerpt_text = "\n[…]\n".join(piece["text"] for piece in json.loads(sample[0])["excerpts"]) if sample and sample[1] == recognition.excerpt_fingerprint(metadata) else None
                sources.append({"id": sid, "title": clean_title(metadata["title"]) or "Conversation sans titre exploitable", "directory": metadata["cwd"], "excerpt_text": excerpt_text})
            result["candidates"].append({"id": row["id"], **data, "sources": sources})
        return result
