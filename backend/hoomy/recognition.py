"""Durable metadata coverage and compact, human-grounded recognition memory."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .catalog import clean_title
from .store import StoreError, encode, identifier, now, require_object, string

BATCH_SIZE = 80
FIRST_PASS_CHARS = 200_000


def first_pass_size(records, known_projects):
    """Use one overview call when the initial metadata fits a conservative budget."""
    from .catalog import classification_input
    from .inference import InferenceError
    if not records or len(records) > 2000:
        return BATCH_SIZE
    try:
        _, chars = classification_input(records, known_projects)
    except InferenceError:
        return BATCH_SIZE
    return len(records) if chars <= FIRST_PASS_CHARS else BATCH_SIZE


def fingerprint(row):
    # Body appends do not invalidate a title-only decision.
    return hashlib.sha256(encode([row["title"], row["cwd"]]).encode()).hexdigest()


def excerpt_fingerprint(row):
    # Unlike title coverage, excerpt freshness includes the source version hints.
    return hashlib.sha256(encode([row["title"], row["cwd"], row["modified_at"], row.get("size")]).encode()).hexdigest()


def initialize(store):
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS title_assessments (
            session_id TEXT PRIMARY KEY REFERENCES catalog(session_id),
            fingerprint TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
            request_id TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS title_decisions (
            session_id TEXT PRIMARY KEY REFERENCES catalog(session_id),
            decision TEXT NOT NULL, project_id TEXT REFERENCES projects(id),
            rejected_projects TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    with store.db:
        tracked = store.db.execute("SELECT 1 FROM settings WHERE key='recognition_tracking_started'").fetchone()
        if not tracked:
            legacy = store.db.execute("SELECT 1 FROM catalog LIMIT 1").fetchone() is not None
            store.db.execute("INSERT INTO settings VALUES (?,?)", ("recognition_tracking_started", encode(now())))
            store.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("legacy_coverage_note", encode("Les anciennes passes ne conservaient pas le suivi titre par titre. Les titres sans résultat suivi seront examinés une fois ; vos validations sont conservées." if legacy else "")))
        # Legacy proposals prove only their own sources were examined.
        for row in store.db.execute("SELECT DISTINCT c.session_id,c.metadata FROM catalog c JOIN outline_members m ON m.session_id=c.session_id"):
            store.db.execute("INSERT OR IGNORE INTO title_assessments VALUES (?,?,?,?,?,?)",
                             (row[0], fingerprint(json.loads(row[1])), "proposed", "Proposition d’une ancienne passe.", None, now()))
        for row in store.db.execute("SELECT m.session_id,o.data FROM outline_members m JOIN outlines o ON o.id=m.outline_id WHERE o.decision='skip' AND NOT EXISTS (SELECT 1 FROM catalog_links l WHERE l.session_id=m.session_id)"):
            target = json.loads(row[1]).get("existing_project_id")
            store.db.execute("INSERT OR IGNORE INTO title_decisions VALUES (?,?,?,?,?)",
                             (row[0], "none", None, encode([target] if target else []), now()))


def decided_ids(store):
    ids = {r[0] for r in store.db.execute("SELECT session_id FROM title_decisions")}
    ids.update(r[0] for r in store.db.execute("SELECT session_id FROM catalog_links"))
    ids.update(r[0] for r in store.db.execute("SELECT m.session_id FROM outline_members m JOIN outlines o ON o.id=m.outline_id WHERE o.decision!='pending'"))
    return ids


def eligible(store, request_id, reexamine=False):
    decided = decided_ids(store)
    assessed = {r["session_id"]: r["fingerprint"] for r in store.db.execute("SELECT * FROM title_assessments")}
    records = [json.loads(r[0]) for r in store.db.execute("SELECT metadata FROM catalog WHERE scan_id=?", (request_id,))]
    records.sort(key=lambda r: (r["modified_at"], r["session_id"]), reverse=True)
    return [r for r in records if r["session_id"] not in decided and
            (reexamine or assessed.get(r["session_id"]) != fingerprint(r))]


def remove_pending_sources(store, source_ids):
    ids = set(source_ids)
    for row in store.db.execute("SELECT id,data FROM outlines WHERE decision='pending'").fetchall():
        data = json.loads(row["data"])
        keep = [sid for sid in data["session_ids"] if sid not in ids]
        if keep == data["session_ids"]:
            continue
        for sid in ids:
            store.db.execute("DELETE FROM outline_members WHERE outline_id=? AND session_id=?", (row["id"], sid))
        if keep:
            data["session_ids"] = keep
            store.db.execute("UPDATE outlines SET data=? WHERE id=?", (encode(data), row["id"]))
        else:
            store.db.execute("DELETE FROM outlines WHERE id=?", (row["id"],))


def assign(store, sid, payload):
    require_object(payload)
    sid = identifier(sid)
    decision = payload.get("decision")
    if decision not in ("project", "none", "defer"):
        raise StoreError("Choisissez un projet, aucun projet ou à décider.")
    with store.lock, store.db:
        if store._get("control")["running"] and not (store._get("overview").get("completion_phase") == "agent" and store._get("overview").get("agent_continuous")):
            raise StoreError("Mettez la lecture en pause avant de corriger un rattachement.", 409)
        source = store.db.execute("SELECT metadata FROM catalog WHERE session_id=?", (sid,)).fetchone()
        if source is None:
            raise StoreError("Conversation absente de l’inventaire.", 404)
        pid = store._resolve(identifier(payload.get("project_id")))["id"] if decision == "project" else None
        previous = {r[0] for r in store.db.execute("SELECT project_id FROM catalog_links WHERE session_id=?", (sid,))}
        previous.update(r[0] for r in store.db.execute("SELECT project_id FROM associations WHERE session_id=?", (sid,)))
        for row in store.db.execute("SELECT o.data FROM outlines o JOIN outline_members m ON m.outline_id=o.id WHERE o.decision='pending' AND m.session_id=?", (sid,)):
            target = json.loads(row[0]).get("existing_project_id")
            if target:
                previous.add(target)
        old = store.db.execute("SELECT rejected_projects FROM title_decisions WHERE session_id=?", (sid,)).fetchone()
        for project_id in previous | ({pid} if pid else set()):
            store.require_project_writable(project_id)
        rejected = set(json.loads(old[0])) if old else set()
        rejected.update(previous - {pid})
        rejected.discard(pid)
        store.db.execute("DELETE FROM catalog_links WHERE session_id=?", (sid,))
        store.db.execute("DELETE FROM associations WHERE session_id=?", (sid,))
        if pid:
            store.db.execute("INSERT INTO catalog_links VALUES (?,?)", (pid, sid))
            if store.db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                store.db.execute("INSERT INTO associations VALUES (?,?)", (sid, pid))
        store.db.execute("UPDATE sessions SET assignment_locked=1 WHERE id=?", (sid,))
        store.db.execute("INSERT INTO title_decisions VALUES (?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET decision=excluded.decision,project_id=excluded.project_id,rejected_projects=excluded.rejected_projects,updated_at=excluded.updated_at",
                         (sid, decision, pid, encode(sorted(rejected)), now()))
        store.db.execute("INSERT INTO title_assessments VALUES (?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET fingerprint=excluded.fingerprint,outcome=excluded.outcome,reason=excluded.reason,updated_at=excluded.updated_at",
                         (sid, fingerprint(json.loads(source[0])), "reviewed", "Décision explicite.", None, now()))
        remove_pending_sources(store, [sid])
        prune_queue(store)
        store.db.execute("UPDATE automatic_links SET corrected=1 WHERE session_id=?", (sid,))
        from .live_discovery import feedback_changed
        feedback_changed(store)
        store._change(pid, "conversation_assignment", "Vous avez corrigé le projet de cette conversation.",
                      {"project_ids": sorted(previous)}, {"decision": decision, "project_id": pid}, [{"catalog_session_id": sid}])
        return {"decision": decision, "project_id": pid}


def prune_queue(store):
    state = store._get("overview")
    if state.get("stage") not in ("inventory", "classifying", "error") or "queue" not in state:
        return
    decided = decided_ids(store)
    state["queue"] = [sid for sid in state["queue"] if sid not in decided]
    state["pass_total"] = state.get("pass_done", 0) + len(state["queue"])
    size = state.get("batch_size", BATCH_SIZE)
    state["batches_total"] = state.get("batches_done", 0) + (len(state["queue"]) + size - 1) // size
    store._set("overview", state)


def coverage(store):
    rows = {r[0]: json.loads(r[1]) for r in store.db.execute("SELECT session_id,metadata FROM catalog")}
    assessed = {r["session_id"]: dict(r) for r in store.db.execute("SELECT * FROM title_assessments")}
    decided = decided_ids(store)
    pending = {r[0] for r in store.db.execute("SELECT m.session_id FROM outline_members m JOIN outlines o ON o.id=m.outline_id WHERE o.decision='pending'")}
    linked = {r[0] for r in store.db.execute("SELECT session_id FROM catalog_links")}
    examined = {sid for sid, row in rows.items() if sid in decided or
                (sid in assessed and assessed[sid]["fingerprint"] == fingerprint(row))}
    unknown = set(rows) - examined
    unassigned = examined - decided - pending
    explicit = Counter(r[0] for r in store.db.execute("SELECT decision FROM title_decisions"))
    return {"examined": len(examined), "unexamined": len(unknown), "decided": len(decided & rows.keys()),
            "linked": len(linked), "pending_sources": len(pending),
            "human_no_project": explicit["none"], "deferred": explicit["defer"],
            "no_project": sum(assessed.get(s, {}).get("outcome") == "no_project" for s in unassigned),
            "ambiguous": sum(assessed.get(s, {}).get("outcome") != "no_project" for s in unassigned)}


def catalog_details(store):
    with store.lock:
        result = []
        decisions = {r["session_id"]: dict(r) for r in store.db.execute("SELECT * FROM title_decisions")}
        assessments = {r["session_id"]: dict(r) for r in store.db.execute("SELECT * FROM title_assessments")}
        for row in store.db.execute("SELECT session_id,metadata FROM catalog ORDER BY rowid"):
            sid, metadata = row[0], json.loads(row[1])
            links = [r[0] for r in store.db.execute("SELECT project_id FROM catalog_links WHERE session_id=?", (sid,))]
            assessment = assessments.get(sid, {})
            decision = decisions.get(sid, {}).get("decision")
            current = assessment.get("fingerprint") == fingerprint(metadata)
            sample = store.db.execute("SELECT data,fingerprint FROM recognition_excerpts WHERE session_id=?", (sid,)).fetchone()
            excerpt_text = "\n[…]\n".join(piece["text"] for piece in json.loads(sample["data"])["excerpts"]) if sample and sample["fingerprint"] == excerpt_fingerprint(metadata) else None
            result.append({"id": sid, "title": clean_title(metadata["title"]) or "Conversation sans titre exploitable",
                           "excerpt_text": excerpt_text,
                           "directory": metadata["cwd"], "project_ids": links,
                           "outcome": decision or ("project" if links else assessment.get("outcome", "unexamined") if current else "unexamined"),
                           "reason": "Décision explicite conservée." if decision else assessment.get("reason", "Ce titre n’a pas encore de résultat suivi.")})
        return result


STOP = set("the and for with from this that une des les pour avec dans sur projet project review update add fix faire développer development build implementation".split())


def features(title):
    return {t for t in re.findall(r"[\w-]{3,}", title.casefold()) if not t.isdecimal()} - STOP


def memory_context(store):
    """Select diverse reviewed metadata; never train on unvalidated predictions."""
    with store.lock:
        projects = [store._project(r) for r in store.db.execute("SELECT * FROM projects WHERE merged_into IS NULL")]
        all_terms = {}
        for project in projects:
            samples = []
            for row in store.db.execute("SELECT c.session_id,c.metadata FROM catalog c JOIN catalog_links l ON l.session_id=c.session_id WHERE l.project_id=? ORDER BY c.session_id", (project["id"],)):
                if store.db.execute("SELECT 1 FROM automatic_links WHERE session_id=?", (row[0],)).fetchone() and not store.db.execute("SELECT 1 FROM title_decisions WHERE session_id=?", (row[0],)).fetchone():
                    continue
                data = json.loads(row[1])
                title = clean_title(data["title"])
                if title and title not in ("Conversation sans titre", "Conversation sans titre exploitable"):
                    samples.append({"title": title, "folder": Path(data["cwd"]).name, "belongs": True})
            for row in store.db.execute("SELECT c.metadata,d.rejected_projects,d.project_id FROM title_decisions d JOIN catalog c ON c.session_id=d.session_id ORDER BY d.updated_at DESC"):
                if project["id"] in json.loads(row[1]) and row[2] != project["id"]:
                    data = json.loads(row[0]); title = clean_title(data["title"])
                    if title:
                        samples.insert(0, {"title": title, "folder": Path(data["cwd"]).name, "belongs": False})
            chosen, seen = [], []
            pool = [(sample, features(sample["title"]) | {"folder:" + sample["folder"], "belongs:" + str(sample["belongs"])}) for sample in samples]
            while pool and len(chosen) < 10:
                def novelty(pair):
                    return 1 - max((len(pair[1] & prior) / max(1, len(pair[1] | prior)) for prior in seen), default=0)
                pair = max(pool, key=novelty)
                if novelty(pair) <= 0.35:
                    break
                pool.remove(pair)
                if not pair[0]["belongs"] and sum(not s["belongs"] for s in chosen) >= 3:
                    continue
                chosen.append(pair[0]); seen.append(pair[1])
            project["examples"] = chosen
            all_terms[project["id"]] = Counter(t for s in samples if s["belongs"] for t in features(s["title"]))
        output = []
        for project in projects:
            others = set().union(*(set(v) for k, v in all_terms.items() if k != project["id"])) if len(projects) > 1 else set()
            patterns = [term for term, count in all_terms[project["id"]].most_common() if count >= 2 and term not in others][:8]
            output.append({"id": project["id"], "name": project["name"], "purpose": project["purpose"],
                           "aliases": [r[0] for r in store.db.execute("SELECT name FROM aliases WHERE project_id=? ORDER BY rowid DESC LIMIT 10", (project["id"],))],
                           "activity": project["activity"], "user_rules": project.get("recognition_rules", ""),
                           "pattern_hints": patterns, "examples": project["examples"],
                           "guidance": "Repeated distinctive terms are tentative hints, not requirements. Generalize by shared purpose; respect negative examples and explicit user rules. A shared folder alone is not proof."})
        for row in store.db.execute("SELECT id,data FROM outlines WHERE decision='pending'"):
            data = json.loads(row[1])
            if not data.get("existing_project_id"):
                output.append({"id": "outline:" + row[0], "name": data["name"], "purpose": data["purpose"],
                               "provisional": True, "examples": [], "pattern_hints": []})
        return output
