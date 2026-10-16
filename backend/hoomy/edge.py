"""A consent-gated local collector and sequential Codex inference worker."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlparse

from .collector import (SCOPE_UNKNOWN, SCOPE_UNKNOWN_FAILURE, SnapshotError,
                        canonical_path, scope_status, session_paths, snapshot)


class BackendError(RuntimeError):
    pass


class Client:
    def __init__(self, base: str):
        parsed = urlparse(base)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("The unauthenticated pilot requires a loopback HTTP backend.")
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        return self.request(path)

    def post(self, path, payload):
        return self.request(path, payload)

    def request(self, path, payload=None):
        raw = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base + path, data=raw, headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # Do not echo arbitrary server bodies or source text into logs.
            raise BackendError(f"Backend rejected operation (HTTP {exc.code}).") from exc
        except (OSError, ValueError) as exc:
            raise BackendError("Local backend unavailable; waiting to reconnect.") from exc


class Edge:
    def __init__(self, client: Client, root: Path, model=None):
        self.client, self.root, self.model = client, root, model
        self.generation = None
        self.seen = {}
        self.scan_at = 0.0
        self.discovery_failures = {}
        self.last_cancel_check = 0.0
        self.cancelled_cache = False
        self.request_id = None

    def post(self, path, payload):
        return self.client.post(path, dict(payload, generation=self.generation))

    def cancelled(self):
        if time.monotonic() - self.last_cancel_check < 1.0:
            return self.cancelled_cache
        self.last_cancel_check = time.monotonic()
        try:
            state = self.client.get("/api/state")
            control = state["control"]
            self.cancelled_cache = not control["running"] or control["generation"] != self.generation
            if self.request_id and state.get("overview", {}).get("request_id") != self.request_id:
                self.cancelled_cache = True
        except BackendError:
            self.cancelled_cache = True
        return self.cancelled_cache

    def scan(self, state):
        excluded_count = 0
        imported = 0
        self.discovery_failures = {}
        paths = session_paths(self.root)
        for path in paths:
            if self.cancelled():
                break
            source_id = hashlib.sha256(str(path).encode()).hexdigest()
            try:
                info = path.stat()
                signature = (info.st_size, info.st_mtime_ns)
                cached = self.seen.get(str(path))
                if cached and cached[0] == signature:
                    if cached[1] == "excluded":
                        excluded_count += 1
                    elif cached[1] == SCOPE_UNKNOWN:
                        self.discovery_failures[source_id] = SCOPE_UNKNOWN_FAILURE
                    continue
                # Let a live append settle; the next pass will retry it.
                if time.time() - info.st_mtime < 3:
                    self.discovery_failures[source_id] = "Session is still being written; waiting for a stable snapshot."
                    continue
                item = snapshot(path)
                scope = scope_status(item["cwd"], state["control"]["exclusions"])
                if scope == SCOPE_UNKNOWN:
                    self.discovery_failures[source_id] = SCOPE_UNKNOWN_FAILURE
                    self.seen[str(path)] = (signature, SCOPE_UNKNOWN)
                    continue
                if scope == "excluded":
                    excluded_count += 1
                    self.seen[str(path)] = (signature, "excluded")
                    continue
                normalized_cwd = canonical_path(item["cwd"])
                item["cwd"] = str(normalized_cwd) if normalized_cwd is not None else ""
                if self.cancelled():
                    break
                self.post("/api/ingest", item)
                self.seen[str(path)] = (signature, "included")
                imported += 1
                if imported >= 10:
                    break
            except (SnapshotError, OSError) as exc:
                reason = str(exc) if isinstance(exc, SnapshotError) else "Unable to read this source file."
                self.discovery_failures[source_id] = reason
        self.post("/api/discovery", {"excluded": excluded_count,
                  "failures": [{"source_id": key, "reason": reason} for key, reason in self.discovery_failures.items()]})
        if not paths:
            self.post("/api/worker", {"status": "waiting", "message": "No Codex history found in the configured sessions or archived_sessions folders."})
        self.scan_at = 0 if imported >= 10 else time.monotonic()

    def tick(self):
        state = self.client.get("/api/state")
        if state.get("act_pending") or (state.get("idea_checks_pending") and not state["control"]["running"]
                                       and state["control"]["inference_enabled"] and state["control"]["scope_confirmed"]):
            from .act_inference import tick as act_tick
            if act_tick(self):
                return
        control = state["control"]
        if self.generation != control["generation"]:
            self.generation = control["generation"]
            self.seen.clear()
            self.scan_at = 0
        self.cancelled_cache = not control["running"]
        self.request_id = None
        if not control["running"] or not control.get("scope_confirmed"):
            self.post("/api/worker", {"status": "ready", "message": "Ready. Discovery starts only after scope confirmation." if not control.get("scope_confirmed") else "Paused. No new analysis is running."})
            return
        if control.get("mode", "full") in ("overview", "deep"):
            self.focused_tick(state)
            return
        if time.monotonic() - self.scan_at >= 15:
            self.post("/api/worker", {"status": "scanning", "message": "Discovering local Codex history, newest first."})
            self.scan(state)
            state = self.client.get("/api/state")
            control = state["control"]
        if not control["running"] or control["generation"] != self.generation:
            return
        pending = sorted((s for s in state["sessions"] if s["status"] == "pending"),
                         key=lambda s: s["modified_at"], reverse=True)
        if not control["inference_enabled"] or not pending:
            self.post("/api/worker", {"status": "watching", "message": "Watching for new or changed conversations." if not pending else "Conversations collected. Codex analysis is not enabled."})
            return
        selected = pending[0]
        identifier = quote(selected["id"], safe="")
        session = self.client.get("/api/sessions/" + identifier)
        if not session.get("messages"):
            self.post(f"/api/sessions/{identifier}/failure", {"snapshot_id": selected["snapshot_id"], "reason": "No supported text for analysis; inspect collection warnings."})
            return
        self.post("/api/worker", {"status": "analyzing", "message": "Reading one conversation with Codex. Other conversations remain queued."})
        try:
            from .inference import analyze
            result = analyze(session["messages"], state["projects"], session.get("cwd", ""), self.cancelled, self.model)
            if not self.cancelled():
                self.post(f"/api/sessions/{identifier}/analysis", dict(result, snapshot_id=selected["snapshot_id"]))
        except BackendError:
            raise
        except Exception as exc:
            # Detailed model output and process errors can contain private content.
            if not self.cancelled():
                from .inference import InferenceError
                reason = str(exc) if isinstance(exc, InferenceError) else "Codex analysis did not complete safely. Check sign-in, usage availability, and adapter compatibility, then retry."
                self.post(f"/api/sessions/{identifier}/failure", {"snapshot_id": selected["snapshot_id"], "reason": reason})
                from .inference import EvidenceError
                if not isinstance(exc, EvidenceError):
                    self.client.post("/api/control", {"running": False})
                    self.post("/api/worker", {"status": "error", "message": "Analysis paused after an error. No automatic retry or extra subscription usage."})

    def work_post(self, endpoint, payload):
        return self.post(endpoint, dict(payload, request_id=self.request_id))

    def focused_tick(self, state):
        from .inference import InferenceCancelled, InferenceError, _TransientError
        work = self.client.get("/api/work")
        if state["control"]["mode"] == "overview" and work["stage"] in ("idle", "review"):
            work = self.client.post("/api/overview/start", {})
        self.request_id = work["request_id"]
        if time.time() < work.get("retry_at", 0):
            self.post("/api/worker", {"status": "reconnecting", "message": "Connexion interrompue. La passe reprendra automatiquement."})
            return
        try:
            if state["control"]["mode"] == "overview":
                self.overview_tick(state, work)
            else:
                self.deep_tick(state, work)
        except InferenceCancelled:
            print(f"{time.time():.0f} overview cancelled generation={self.generation} request={self.request_id}", flush=True)
            return
        except _TransientError:
            print(f"{time.time():.0f} overview transient interruption generation={self.generation} request={self.request_id}", flush=True)
            if not self.cancelled():
                self.work_post("/api/overview/interruption", {})
        except InferenceError as exc:
            print(f"{time.time():.0f} overview failed type={type(exc).__name__} generation={self.generation} request={self.request_id}", flush=True)
            if not self.cancelled():
                self.work_post("/api/overview/failure", {"reason": str(exc)})

    def overview_tick(self, state, work):
        from .catalog import classification_input, inventory
        from .classifier import classify
        if work.get("completion_phase") == "agent":
            from .exploration import explore
            records = self.client.get("/api/catalog")
            continuous = work.get("agent_continuous", False)
            if continuous and (not records or time.time() >= work["agent_deadline"]):
                self.work_post("/api/agent/checkpoint", {"attempted_ids": [], "excerpt_evidence": {}})
                return
            def agent_progress(event):
                if not self.cancelled():
                    self.work_post("/api/overview/agent_progress", event)
            def publish_finding(proposal, evidence, snapshots):
                if self.cancelled():
                    from .inference import InferenceCancelled
                    raise InferenceCancelled("Exploration interrompue.")
                for item in snapshots:
                    self.post("/api/ingest", item)
                return self.work_post("/api/agent/publish", {"proposal": proposal, "excerpt_evidence": evidence})
            options = {"publish": publish_finding, "feedback": lambda: self.client.get("/api/agent-context"),
                       "max_seconds": min(600, max(1, work["agent_deadline"] - time.time()))} if continuous else {}
            known = self.client.get("/api/agent-context")["known_projects"] if continuous else work["completion_context_data"]
            if work.get('bounded_discovery'):
                from .bounded_discovery import run
                def save(ids, evidence, unassigned, **kwargs):
                    return self.work_post('/api/agent/checkpoint', dict(bounded=True,
                        attempted_ids=ids, excerpt_evidence=evidence, unassigned=unassigned, **kwargs))
                run(records, known, self.root, state['control']['exclusions'], self.cancelled,
                    agent_progress, ingest=lambda item: self.post('/api/ingest', item),
                    publish=publish_finding, feedback=lambda: self.client.get('/api/agent-context'),
                    checkpoint=save, max_seconds=max(1, work['agent_deadline']-time.time()))
                if not self.cancelled():
                    save([], {}, [], finish=True)
                return
            result = explore(records, known, self.root,
                             state["control"]["exclusions"], self.cancelled, agent_progress, **options)
            for item in result.pop("snapshots", []):
                if self.cancelled():
                    return
                self.post("/api/ingest", item)
            if not self.cancelled():
                if continuous:
                    self.work_post("/api/agent/checkpoint", {"attempted_ids": result.get("attempted_ids", []), "excerpt_evidence": result["excerpt_evidence"]})
                else:
                    self.work_post("/api/overview/result", {**result, "input_ids": [r["session_id"] for r in records]})
            return
        if not work["inventory_complete"]:
            self.post("/api/worker", {"status": "indexing", "message": "Repérage des titres et dossiers Codex. Les échanges ne sont pas lus."})
            census = inventory(self.root, state["control"]["exclusions"], self.cancelled)
            if census is None:
                return
            records = census.pop("records")
            for offset in range(0, max(1, len(records)), 500):
                if self.cancelled():
                    return
                self.work_post("/api/catalog", {**census, "records": records[offset:offset + 500],
                    "complete": offset + 500 >= len(records)})
        if not state["control"]["inference_enabled"] or self.cancelled():
            return
        records = self.client.get("/api/catalog")
        latest = self.client.get("/api/state")
        if (not latest["control"]["running"] or latest["control"]["generation"] != self.generation
                or latest["overview"]["request_id"] != self.request_id):
            return
        known = self.client.get("/api/recognition-context")
        if work.get("completion_phase") in ("titles", "excerpts"):
            known = work["completion_context_data"] + [p for p in known if p.get("provisional")]
        if len(json.dumps(known, ensure_ascii=False)) > 400_000:
            from .inference import InferenceError
            raise InferenceError("Le contexte des projets dépasse le budget de cette passe ; aucun projet n’a été omis silencieusement.")
        sampling = latest["overview"].get("completion_phase") == "excerpts"
        evidence = {}
        eligible_records = records
        if sampling:
            from .local_triage import excerpts
            scan_started = time.monotonic()
            eligible_records = []
            for index, record in enumerate(records):
                if self.cancelled():
                    return
                self.post("/api/worker", {"status": "classifying", "message": f"Tri local du contenu · {index + 1} / {len(records)} conversations · aucun extrait envoyé pour le moment."})
                try:
                    path = Path(record["source_path"])
                    roots = [self.root.resolve() / "sessions", self.root.resolve() / "archived_sessions"]
                    if path.is_symlink() or not any(path.resolve().is_relative_to(root) for root in roots):
                        raise SnapshotError("Source hors du périmètre.")
                    item = snapshot(path)
                    if item["session_id"] != record["session_id"] or scope_status(item["cwd"], latest["control"]["exclusions"]) != "included":
                        raise SnapshotError("Identité ou périmètre modifié.")
                    pieces = excerpts(item["messages"], known)
                    if not pieces:
                        continue
                    item["title"] = record["title"]
                    if self.cancelled():
                        return
                    self.post("/api/ingest", item)
                    evidence[record["session_id"]] = {"snapshot_id": item["snapshot_id"], "excerpts": pieces}
                    eligible_records.append(record)
                except (SnapshotError, OSError):
                    # A failed local read is not evidence that no project exists.
                    continue
        payload, chars = classification_input(eligible_records, known)
        if sampling:
            for row in payload["conversations"]:
                row["excerpts"] = [piece["text"] for piece in evidence[row["id"]]["excerpts"]]
            chars = len(json.dumps(payload, ensure_ascii=False))
            print(json.dumps({"event": "overview_local_triage", "request_id": self.request_id,
                              "selected": len(records), "with_excerpts": len(evidence),
                              "local_scan_seconds": round(time.monotonic() - scan_started, 3),
                              "excerpt_chars": sum(len(p["text"]) for e in evidence.values() for p in e["excerpts"])}), flush=True)
        total, folders = len(payload["conversations"]), len(payload["folders"])
        scope = "extraits partiels" if sampling else "titres et dossiers uniquement"
        self.post("/api/worker", {"status": "classifying", "message": f"Préparation de l’envoi · {total} conversations · {scope}."})
        print(f"{time.time():.0f} overview classify request={self.request_id} titles={len(records)} model=gpt-5.6-luna effort=low", flush=True)
        phase = None
        def progress(event):
            nonlocal phase
            seconds = int(event["elapsed_seconds"])
            message = (f"Réception du classement · {event['response_chars']} caractères reçus · {seconds} s"
                       if event["phase"] == "receiving" else
                       f"{total} conversations · {scope} envoyés · attente du premier retour de Luna · {seconds} s")
            names = event.get("preview_names", [])
            if names:
                message = f"{len(names)} pistes reçues · {', '.join(names[-3:])} · classement provisoire en cours de réception · {seconds} s"
            self.post("/api/worker", {"status": "classifying", "message": message})
            if event["phase"] != phase:
                phase = event["phase"]
                print(json.dumps({"event": "overview_progress", "request_id": self.request_id,
                                  "at": time.time(), **{key: value for key, value in event.items()
                                                         if key != "preview_names"}}), flush=True)
        result = classify(payload, self.cancelled, progress=progress)
        print(json.dumps({"event": "overview_metrics", "request_id": self.request_id,
                          "evidence_scope": scope,
                          "at": time.time(), "elapsed_seconds": result["elapsed_seconds"],
                          **result.get("metrics", {})}), flush=True)
        if not self.cancelled():
            self.post("/api/worker", {"status": "classifying", "message": "Réponse reçue · vérification des rattachements et enregistrement du classement."})
            self.work_post("/api/overview/result", {**result, "input_ids": [r["session_id"] for r in records], "input_chars": chars,
                                                   "excerpt_evidence": evidence})
            print(f"{time.time():.0f} overview complete request={self.request_id} projects={len(result['projects'])}", flush=True)

    def deep_tick(self, state, work):
        from .inference import EvidenceError, analyze
        if not work["deep_queue"]:
            self.client.post("/api/control", {"running": False})
            return
        selected = work["deep_queue"][0]
        sid = selected["session_id"]
        endpoint = "/api/sessions/" + quote(sid, safe="")
        self.post("/api/worker", {"status": "analyzing", "message": f"Lecture détaillée : {work['deep_done'] + 1} sur {work['deep_total']} conversations."})
        item = None
        try:
            path = Path(selected["source_path"])
            allowed = [self.root.resolve() / "sessions", self.root.resolve() / "archived_sessions"]
            if path.is_symlink() or not any(path.resolve().is_relative_to(root) for root in allowed):
                raise SnapshotError("La source n’est plus dans les dossiers Codex autorisés.")
            item = snapshot(path)
            if item["session_id"] != sid or scope_status(item["cwd"], state["control"]["exclusions"]) != "included":
                raise SnapshotError("L’identité ou le périmètre de cette source a changé.")
            normalized = canonical_path(item["cwd"])
            item["cwd"] = str(normalized) if normalized else ""
            item["title"] = selected.get("title", "")
            if self.cancelled():
                return
            self.post("/api/ingest", item)
            session = self.client.get(endpoint)
            if session["status"] != "analyzed":
                if not session["messages"]:
                    raise SnapshotError("Cette conversation ne contient pas de texte pris en charge.")
                result = analyze(session["messages"], state["projects"], item["cwd"], self.cancelled, self.model)
                if self.cancelled():
                    return
                self.post(endpoint + "/analysis", dict(result, snapshot_id=item["snapshot_id"]))
        except (SnapshotError, EvidenceError, OSError) as exc:
            reason = str(exc) if isinstance(exc, (SnapshotError, EvidenceError)) else "Source temporairement illisible."
            if self.cancelled():
                return
            if item and item["session_id"] == sid:
                # Only records already admitted to the backend can receive failure.
                try:
                    self.post(endpoint + "/failure", {"snapshot_id": item["snapshot_id"], "reason": reason})
                except BackendError:
                    pass
            self.work_post("/api/overview/advance", {"session_id": sid, "reason": reason})
            return
        if not self.cancelled():
            self.work_post("/api/overview/advance", {"session_id": sid})


def main():
    def stop(_signum, _frame):
        # Unwind inference's cleanup so its isolated child process cannot outlive us.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8765")
    parser.add_argument("--codex-root", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    parser.add_argument("--model", default=None, help="Explicit optional Codex model; otherwise the adapter default.")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / "Library/Application Support/HoomyPilot")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (args.state_dir / "edge.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Hoomy edge is already running for this runtime directory.")
    edge = Edge(Client(args.backend), args.codex_root.expanduser(), args.model)
    from .idea_worker import start as start_decisions
    decisions_stop = start_decisions(Client(args.backend), args.state_dir)
    print("Hoomy edge ready. No session contents are written to this log.", flush=True)
    try:
        while True:
            try:
                edge.tick()
            except BackendError as exc:
                print(str(exc), flush=True)
            except OSError:
                print("Local history temporarily unavailable; retrying.", flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        decisions_stop.set()


if __name__ == "__main__":
    main()
