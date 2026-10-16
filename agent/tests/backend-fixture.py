"""Isolated actual backend with invented project evidence and no inference."""

import base64
import hashlib
from http.server import ThreadingHTTPServer
import json
import sys
import threading

from hoomy.server import make_handler
from hoomy.store import Store, SECTIONS

store = Store(sys.argv[1])
generation = store.state()["control"]["generation"]
store.control({"consent": True, "running": False})
text = "Invented collection integration project."
raw = json.dumps({"type": "message", "role": "user", "text": text}).encode()
snapshot = hashlib.sha256(raw).hexdigest()
store.ingest({"generation": generation, "session_id": "fixture-source", "snapshot_id": snapshot,
              "source_path": "/fictional/source.jsonl", "cwd": "/fictional/project",
              "modified_at": "2026-10-12T12:00:00Z", "raw_b64": base64.b64encode(raw).decode(),
              "warnings": [], "messages": [{"id": "fixture-message", "role": "user", "text": text,
                                             "line_start": 1, "line_end": 1, "timestamp": None}]})
evidence = [{"message_id": "fixture-message", "quote": text}]
store.analysis("fixture-source", {"generation": generation, "snapshot_id": snapshot,
    "brief": {section: [] for section in SECTIONS}, "method": {"adapter": "fixture", "schema": 1},
    "projects": [{"name": "Fixture project", "purpose": text, "status": "active", "rationale": text, "evidence": evidence}]})
project_id = store.project_list()["projects"][0]["id"]
server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
server.daemon_threads = True
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(json.dumps({"url": "http://127.0.0.1:" + str(server.server_port), "project_id": project_id}), flush=True)
try:
    for line in sys.stdin:
        if line.strip() == "stop":
            break
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
    store.close()
