"""Transactional live findings and human feedback for agent exploration."""

import json
import time
import uuid

from . import recognition
from .store import StoreError, encode, identifier, now, require_object, string


def initialize(store):
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS automatic_links (
            session_id TEXT PRIMARY KEY REFERENCES catalog(session_id),
            project_id TEXT NOT NULL REFERENCES projects(id),
            alternative_id TEXT REFERENCES projects(id),
            rationale TEXT NOT NULL, ambiguity_reason TEXT NOT NULL,
            corrected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
    """)
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", ("agent_feedback_revision", "0"))


def feedback_changed(store):
    row = store.db.execute("SELECT value FROM settings WHERE key='agent_feedback_revision'").fetchone()
    store._set("agent_feedback_revision", (json.loads(row[0]) if row else 0) + 1)


def context(store):
    with store.lock:
        row = store.db.execute("SELECT value FROM settings WHERE key='agent_feedback_revision'").fetchone()
        approved = {r["id"] for r in store.db.execute("SELECT * FROM projects WHERE merged_into IS NULL")
                    if {"name", "purpose"}.issubset(store._project(r)["confirmed_fields"])}
        return {"revision": json.loads(row[0]) if row else 0,
                "known_projects": [p for p in recognition.memory_context(store) if p["id"] in approved or p.get("provisional")],
                "human_decisions": [dict(r) for r in store.db.execute("SELECT session_id,decision,project_id FROM title_decisions")],
                "remaining": len(store._get("overview").get("queue", [])),
                "remaining_ids": store._get("overview").get("queue", [])}


def _evidence(store, state, evidence):
    require_object(evidence)
    if len(evidence) > 24 or not set(evidence).issubset(state.get("completion_ids", [])):
        raise StoreError("Extraits hors du périmètre autorisé.")
    for sid, sample in evidence.items():
        require_object(sample)
        session = store.session(sid)
        if sample.get("snapshot_id") != session["snapshot_id"]:
            raise StoreError("La source a changé.", 409)
        messages = {m["id"]: m["text"] for m in session["messages"]}
        pieces = sample.get("excerpts")
        if not isinstance(pieces, list) or not 1 <= len(pieces) <= 4:
            raise StoreError("Extraits invalides.")
        for piece in pieces:
            require_object(piece)
            text = string(piece.get("text"), "excerpt", maximum=750)
            start = piece.get("start")
            if type(start) is not int or start < 0 or messages.get(piece.get("message_id"), "")[start:start + len(text)] != text:
                raise StoreError("Extrait sans preuve correspondante.")
        metadata = json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
        store.db.execute("INSERT OR REPLACE INTO recognition_excerpts VALUES (?,?,?,?)",
                         (sid, recognition.excerpt_fingerprint(metadata), state["completion_context"], encode(sample)))


def publish(store, payload):
    proposal = require_object(payload.get("proposal"))
    for key in ("name", "purpose", "rationale"):
        string(proposal.get(key), key, maximum=2000)
    ids = proposal.get("session_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 24 or any(not isinstance(sid, str) for sid in ids) or len(set(ids)) != len(ids):
        raise StoreError("Sources de proposition invalides.")
    with store.lock, store.db:
        state, control = store.overview._active(payload)
        if state.get("completion_phase") != "agent" or not state.get("agent_continuous"):
            raise StoreError("Aucune exploration continue active.", 409)
        evidence = require_object(payload.get("excerpt_evidence", {}))
        if not set(ids).issubset(evidence):
            raise StoreError("Rattachement sans extrait lu.")
        _evidence(store, state, evidence)
        pid = proposal.get("existing_project_id")
        if pid is not None and not isinstance(pid, str):
            raise StoreError("Projet cible invalide.")
        target_outline = None
        if isinstance(pid, str) and pid.startswith("outline:"):
            target_outline = store.db.execute("SELECT * FROM outlines WHERE id=?", (pid[8:],)).fetchone()
            if target_outline is None or target_outline["decision"] == "skip":
                return {"ignored": "Proposal rejected or replaced by the user", **context(store)}
            pid = target_outline["project_id"] if target_outline["decision"] != "pending" else None
        if pid:
            project = store._project(store._resolve(identifier(pid)))
            if not {"name", "purpose"}.issubset(project["confirmed_fields"]):
                raise StoreError("Le projet cible doit être validé.")
            pid = project["id"]
        alternative = proposal.get("alternative_project_id")
        if alternative is not None and not isinstance(alternative, str):
            raise StoreError("Alternative invalide.")
        ambiguity = string(proposal.get("ambiguity_reason", ""), "ambiguity", empty=True, maximum=1000)
        if alternative:
            alt = store._project(store._resolve(identifier(alternative)))
            if not pid or alt["id"] == pid or not {"name", "purpose"}.issubset(alt["confirmed_fields"]) or not ambiguity.strip():
                raise StoreError("Alternative ambiguë invalide.")
            alternative = alt["id"]
        # Human decisions and existing links win over late or repeated model output.
        protected = recognition.decided_ids(store)
        eligible = [sid for sid in ids if sid not in protected]
        if pid:
            for sid in eligible:
                recognition.remove_pending_sources(store, [sid])
                store.db.execute("INSERT INTO catalog_links VALUES (?,?)", (pid, sid))
                store.db.execute("INSERT OR IGNORE INTO associations VALUES (?,?)", (sid, pid))
                store.db.execute("INSERT INTO automatic_links VALUES (?,?,?,?,?,0,?)",
                                 (sid, pid, alternative, proposal["rationale"], ambiguity, now()))
        elif eligible:
            if target_outline is None:
                target_outline = next((r for r in store.db.execute("SELECT * FROM outlines WHERE decision='pending'")
                    if json.loads(r["data"])["name"].strip().casefold() == proposal["name"].strip().casefold() and
                       json.loads(r["data"]).get("existing_project_id") is None), None)
            oid = target_outline["id"] if target_outline else str(uuid.uuid4())
            # One pending home per source. Repeated publication extends the same group.
            recognition.remove_pending_sources(store, eligible)
            data = {k: proposal[k] for k in ("name", "purpose", "rationale")}
            old_ids = json.loads(target_outline["data"])["session_ids"] if target_outline else []
            data.update(session_ids=list(dict.fromkeys(old_ids + eligible)), existing_project_id=None, model="gpt-5.6-luna")
            if store.db.execute("SELECT 1 FROM outlines WHERE id=?", (oid,)).fetchone():
                store.db.execute("UPDATE outlines SET data=? WHERE id=?", (encode(data), oid))
            else:
                store.db.execute("INSERT INTO outlines VALUES (?,?,?,?,?)", (oid, encode(data), "pending", None, now()))
            for sid in data["session_ids"]:
                store.db.execute("INSERT OR IGNORE INTO outline_members VALUES (?,?)", (oid, sid))
        for sid in eligible:
            metadata = json.loads(store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()[0])
            store.db.execute("INSERT OR REPLACE INTO title_assessments VALUES (?,?,?,?,?,?)",
                (sid, recognition.fingerprint(metadata), "automatic" if pid else "proposed", proposal["rationale"], state["request_id"], now()))
        state["queue"] = [sid for sid in state.get("queue", []) if sid not in ids]
        store._set("overview", state)
        return {"published": len(eligible), "kind": "association" if pid else "new_project"}


def checkpoint(store, payload):
    if payload.get('bounded'):
        return bounded_checkpoint(store, payload)
    attempted = payload.get("attempted_ids", [])
    if not isinstance(attempted, list) or len(attempted) > 24 or any(not isinstance(s, str) for s in attempted):
        raise StoreError("Point de sauvegarde invalide.")
    with store.lock, store.db:
        state, control = store.overview._active(payload)
        if state.get("completion_phase") != "agent" or not state.get("agent_continuous") or not set(attempted).issubset(state.get("completion_ids", [])):
            raise StoreError("Point de sauvegarde hors périmètre.")
        evidence = payload.get("excerpt_evidence", {})
        if not set(evidence).issubset(attempted):
            raise StoreError("Extraits absents du point de sauvegarde.")
        _evidence(store, state, evidence)
        state["queue"] = [s for s in state.get("queue", []) if s not in attempted and s not in recognition.decided_ids(store)]
        state["agent_checked"] = state.get("agent_checked", 0) + len(attempted)
        state["batches_done"] = state.get("batches_done", 0) + 1
        stop = not state["queue"] or not attempted or time.time() >= state.get("agent_deadline", 0)
        control["running"] = not stop
        if stop:
            reason = "Périmètre parcouru" if not state["queue"] else "Limite globale atteinte" if time.time() >= state.get("agent_deadline", 0) else "L’agent n’a pas trouvé de nouvelle piste exploitable"
            state.update(stage="review", completion_phase="done", message=f"{reason} · {len(state['queue'])} conversations restantes. Les résultats en direct sont conservés.")
        else:
            state.update(message=None, completion_context_data=context(store)["known_projects"])
        store._set("overview", state)
        store._set("control", control)
        return {"continuing": not stop, "remaining": len(state["queue"])}


def bounded_checkpoint(store, payload):
    with store.lock, store.db:
        state, control = store.overview._active(payload)
        if not state.get('bounded_discovery') or state.get('completion_phase') != 'agent':
            raise StoreError('Aucun parcours borné actif.', 409)
        ids = payload.get('attempted_ids', [])
        if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(s, str) for s in ids) or not set(ids).issubset(state['completion_ids']):
            raise StoreError('Sources hors périmètre.')
        evidence = require_object(payload.get('excerpt_evidence', {}))
        if not set(evidence).issubset(ids):
            raise StoreError('Extraits hors périmètre.')
        for offset in range(0, len(ids), 24):
            _evidence(store, state, {sid: evidence[sid] for sid in ids[offset:offset+24] if sid in evidence})
        decided = recognition.decided_ids(store)
        resolved = set(decided)
        for item in payload.get('unassigned', []):
            require_object(item)
            sid, outcome = item.get('session_id'), item.get('outcome')
            if sid not in evidence or outcome not in ('ambiguous', 'no_project'):
                raise StoreError('Résultat hors périmètre.')
            if sid in decided or store.db.execute('SELECT 1 FROM catalog_links WHERE session_id=?', (sid,)).fetchone():
                continue
            metadata = json.loads(store.db.execute('SELECT metadata FROM catalog WHERE session_id=?', (sid,)).fetchone()[0])
            store.db.execute('INSERT OR REPLACE INTO title_assessments VALUES (?,?,?,?,?,?)',
                (sid, recognition.fingerprint(metadata), outcome, 'Classement sur extraits partiels ; pas une lecture intégrale.', state['request_id'], now()))
            if outcome == 'no_project':
                resolved.add(sid)
        state['queue'] = [sid for sid in state['queue'] if sid not in resolved]
        state['bounded_classified'] = sorted(set(state.get('bounded_classified', [])) | set(evidence))
        state['agent_checked'] = len(state['bounded_classified'])
        if payload.get('finish'):
            control['running'] = False
            state.update(stage='review', completion_phase='done',
                message=f"Passe bornée terminée · {len(state['bounded_classified'])} conversations examinées sur extraits · {len(state['queue'])} à investiguer. Aucune lecture intégrale annoncée.")
        store._set('overview', state)
        store._set('control', control)
        return {'continuing': control['running'], 'remaining': len(state['queue'])}


def associations(store):
    result = []
    for row in store.db.execute("SELECT a.*,c.metadata FROM automatic_links a JOIN catalog c ON c.session_id=a.session_id ORDER BY a.created_at,a.session_id"):
        data = dict(row)
        metadata = json.loads(data.pop("metadata"))
        data.update(id=data.pop("session_id"), title=metadata["title"])
        current = store.db.execute("SELECT project_id FROM catalog_links WHERE session_id=?", (data["id"],)).fetchone()
        data["current_project_id"] = current[0] if current else None
        result.append(data)
    return result
