"""Disposable UI preview: fictional history, isolated port, no model calls."""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hoomy.store import Store
from hoomy.server import make_handler
from hoomy.edge import Client, Edge


def fake_classify(payload, cancelled=lambda: False, progress=None):
    time.sleep(2)
    projects = []
    for row in payload['conversations']:
        projects.append(dict(name=row['title'], purpose='Prepare a small community garden.',
            rationale='The title and folder refer to the same community garden.',
            existing_project_id=None, session_ids=[row['id']]))
    return dict(projects=projects, unassigned=[], model='fixture-no-network', elapsed_seconds=2)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='hoomy-wizard-preview-') as temporary:
        root = Path(temporary)
        sessions = root/'history'/'sessions'
        sessions.mkdir(parents=True)
        names = ['Community garden', 'Neighborhood cookbook']
        for i, name in enumerate(names):
            raw = [dict(type='session_meta', payload=dict(id=f'fixture-{i}', cwd=f'/fictional/project-{i}')),
                   dict(type='event_msg', payload=dict(type='user_message', message=name+' planning and preparation.'))]
            (sessions/f'{i}.jsonl').write_text('\n'.join(json.dumps(r) for r in raw)+'\n')
        (root/'history'/'session_index.jsonl').write_text('\n'.join(json.dumps(dict(id=f'fixture-{i}', thread_name=name)) for i,name in enumerate(names)))
        store = Store(root/'store')
        server = ThreadingHTTPServer(('127.0.0.1', 8876), make_handler(store))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        edge = Edge(Client('http://127.0.0.1:8876'), root/'history')
        print('Isolated fictional preview ready on port 8876; no real history or inference.', flush=True)
        with patch('hoomy.classifier.classify', fake_classify), patch('hoomy.bounded_discovery.classify', fake_classify), patch('hoomy.exploration.explore', side_effect=RuntimeError('Live inference disabled in preview')):
            try:
                while True:
                    edge.tick()
                    time.sleep(.5)
            finally:
                server.shutdown()
                store.close()
