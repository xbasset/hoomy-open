"""Loopback-only JSON API. No source-file access, credentials, or content logging."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .store import Store, StoreError


MAX_BODY = 64 * 1024 * 1024
LOCAL_NAMES = {"localhost", "127.0.0.1", "::1"}


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HoomyPilot"
        sys_version = ""

        def log_message(self, *_args):
            pass

        def _reply(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def _boundary(self):
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1:
                raise StoreError("A local Host header is required.", 403)
            try:
                host = urlsplit("http://" + hosts[0])
                if host.hostname not in LOCAL_NAMES or host.username or host.password or host.path or host.query or host.fragment:
                    raise ValueError()
                host.port
            except ValueError:
                raise StoreError("Only local requests are accepted.", 403) from None
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1:
                raise StoreError("Only local requests are accepted.", 403)
            if origins:
                try:
                    origin = urlsplit(origins[0])
                    same_host = origin.hostname == host.hostname and origin.port == host.port
                    if origin.scheme != "http" or not same_host or origin.path or origin.query or origin.fragment or origin.username or origin.password:
                        raise ValueError()
                except ValueError:
                    raise StoreError("Cross-origin browser requests are not accepted.", 403) from None
            if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site") and not origins:
                raise StoreError("Cross-origin browser requests are not accepted.", 403)

        def _body(self, ingestion=False, project_state=False):
            if self.headers.get_content_type() != "application/json":
                raise StoreError("Writes require application/json.", 415)
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                raise StoreError("A bounded Content-Length is required.", 411)
            try:
                length = int(lengths[0])
            except ValueError:
                raise StoreError("Invalid Content-Length.") from None
            maximum = 2 * 1024 * 1024 if project_state else 24 * 1024 * 1024 + 64 * 1024 if ingestion else MAX_BODY
            if not 0 < length <= maximum:
                raise StoreError("Request body is empty or too large.", 413)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise StoreError("Incomplete JSON body.")
            try:
                def unique_pairs(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                value = json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
                                   object_pairs_hook=unique_pairs if ingestion or project_state else dict)
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise StoreError("Invalid JSON.") from None
            if not isinstance(value, dict):
                raise StoreError("Expected a JSON object.")
            return value

        def _run(self, write=False):
            try:
                self.connection.settimeout(20)
                self._boundary()
                if write:
                    # Reject archive writes before parsing bodies or routing new endpoints.
                    store.require_instance_writable()
                path = urlsplit(self.path)
                if path.query or path.fragment:
                    raise StoreError("Unknown endpoint.", 404)
                parts = [unquote(part) for part in path.path.strip("/").split("/")]
                if any("/" in part or not part for part in parts):
                    raise StoreError("Unknown endpoint.", 404)
                if not write and (tuple(parts) in (("api", "act"), ("api", "act", "work"), ("api", "now"), ("api", "now", "work"), ("api", "now", "question-work"), ("api", "catalog"))
                                  or len(parts) == 4 and parts[:3] == ["api", "act", "explore"]):
                    # Legacy selection reads can expire leases or expose runnable jobs.
                    store.require_instance_writable()
                payload = self._body(ingestion=parts[:2] == ["api", "ingestion"], project_state=parts[:2] == ["api", "project-state"]) if write else None
                if not write:
                    if parts == ["api", "health"]:
                        result = store.health()
                    elif parts == ["api", "projects"]:
                        result = store.project_list()
                    elif parts == ["api", "state"]:
                        result = store.state()
                    elif len(parts)==4 and parts[:3]==['api','projects','context']:
                        result = store.dialogue.profile(parts[3])
                    elif parts == ["api", "now"]:
                        result = store.ideas.state()
                    elif parts == ["api", "now", "work"]:
                        result = store.ideas.work('activity')
                    elif parts == ["api", "now", "question-work"]:
                        result = store.ideas.work('question_batch')
                    elif parts == ["api", "act"]:
                        result = store.act.state()
                    elif parts == ["api", "act", "work"]:
                        result = store.act.work()
                    elif len(parts)==4 and parts[:3]==["api","act","explore"]:
                        result = store.explorer.graph(parts[3])
                    elif parts == ["api", "pilot"]:
                        result = store.pilot()
                    elif parts == ["api", "catalog"]:
                        result = store.overview.inputs()
                    elif parts == ["api", "recognition-context"]:
                        from .recognition import memory_context
                        result = memory_context(store)
                    elif parts == ["api", "agent-context"]:
                        from .live_discovery import context
                        result = context(store)
                    elif parts == ["api", "catalog-details"]:
                        from .recognition import catalog_details
                        result = catalog_details(store)
                    elif parts == ["api", "clarification-inputs"]:
                        result = store.overview.clarification_inputs()
                    elif parts == ["api", "work"]:
                        with store.lock:
                            result = store._get("overview")
                    elif len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                        result = store.session(parts[2])
                    elif len(parts) == 3 and parts[:2] == ["api", "snapshots"]:
                        result = store.snapshot(parts[2])
                    else:
                        raise StoreError("Unknown endpoint.", 404)
                elif len(parts) == 3 and parts[:2] == ["api", "project-state"] and parts[2] in ("read", "refresh", "job", "claim", "heartbeat", "attempt", "finish", "fail", "citation"):
                    result = getattr(store.project_state, parts[2])(payload)
                elif parts == ["api", "ingestion", "events"]:
                    result = store.ingestion.accept(payload)
                elif parts == ["api", "ingestion", "receipts", "query"]:
                    result = store.ingestion.receipt(payload)
                elif parts == ["api", "ingestion", "evidence", "query"]:
                    result = store.ingestion.evidence(payload)
                elif len(parts) == 2 and parts[0] == "api" and parts[1] in ("control", "worker", "ingest", "discovery", "reset"):
                    result = getattr(store, parts[1])(payload)
                elif parts == ['api','projects','context']:
                    result = store.dialogue.update_profile(payload)
                elif parts == ["api","act","research-feedback"]:
                    result = store.explorer.feedback(payload)
                elif len(parts) == 3 and parts[:2] == ["api", "now"] and parts[2] in ("choose", "answer", "prepare", "claim", "finish", "check-request", "context-answer"):
                    result = getattr(store.ideas, parts[2].replace('-','_'))(payload)
                elif len(parts) == 3 and parts[:2] == ["api", "act"] and parts[2] in ("request", "claim", "cancel", "result", "failure", "feedback", "confirm", "next", "progress"):
                    if parts[2] in ("result", "failure"):
                        result = store.act.finish(payload, failed=parts[2] == "failure")
                    else:
                        result = getattr(store.act, parts[2])(payload)
                elif parts == ["api", "projects", "merge"]:
                    result = store.merge(payload)
                elif parts == ["api", "catalog"]:
                    result = store.overview.catalog(payload)
                elif len(parts) == 3 and parts[:2] == ["api", "agent"] and parts[2] in ("publish", "checkpoint"):
                    from . import live_discovery
                    result = getattr(live_discovery, parts[2])(store, payload)
                elif len(parts) == 4 and parts[:2] == ["api", "catalog"] and parts[3] == "assign":
                    from .recognition import assign
                    result = assign(store, parts[2], payload)
                elif len(parts) == 3 and parts[:2] == ["api", "overview"] and parts[2] in ("start", "result", "advance", "failure", "interruption", "clarify", "complete", "agent_progress"):
                    result = getattr(store.overview, parts[2])(payload)
                elif len(parts) == 4 and parts[:2] == ["api", "outlines"] and parts[3] == "review":
                    result = store.overview.review(parts[2], payload)
                elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "deepen":
                    result = store.overview.deepen(parts[2], payload)
                elif len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] in ("analysis", "failure", "retry", "review"):
                    result = store.retry(parts[2]) if parts[3] == "retry" else getattr(store, parts[3])(parts[2], payload)
                elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] in ("correct", "split"):
                    result = getattr(store, parts[3])(parts[2], payload)
                else:
                    raise StoreError("Unknown endpoint.", 404)
                self._reply(200, result)
            except StoreError as exc:
                self._reply(exc.status, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                self.close_connection = True
            except Exception:
                self._reply(500, {"error": "The local service could not complete this request."})

        def do_GET(self):
            self._run()

        def do_POST(self):
            self._run(write=True)

        def _unsupported(self):
            self._reply(405, {"error": "Use GET or POST."})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _unsupported

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Library/Application Support/HoomyPilot/backend")
    args = parser.parse_args(argv)
    destination = args.data_dir.expanduser().resolve()
    # Runtime conversation content must never be created inside any checkout.
    if any((parent / ".git").exists() for parent in (destination, *destination.parents)):
        parser.error("Runtime data must be outside a Git checkout.")
    store = Store(destination)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    server.daemon_threads = True
    print("Hoomy private backend ready. Request content is not logged.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
