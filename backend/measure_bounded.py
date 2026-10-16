"""Explicit real-history measurement on a private SQLite backup, never live state."""

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from hoomy.store import Store
from hoomy.live_discovery import context, publish, checkpoint
from hoomy.bounded_discovery import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', help='Isolated backup, not the live database')
    parser.add_argument('--container', help='Read-only consistent backup from the local pilot container')
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    # Always work on another private backup, including when invoked repeatedly.
    import sqlite3
    with tempfile.TemporaryDirectory(prefix='hoomy-benchmark-') as directory:
        with sqlite3.connect(str(Path(directory)/'hoomy.sqlite3')) as target:
            if args.container:
                import zlib
                code = ('import sqlite3,zlib,sys,json; a=sqlite3.connect("file:/data/hoomy.sqlite3?mode=ro",uri=True); a.execute("BEGIN"); '
                        'skip={"snapshots","sessions","analyses","reviews","recognition_excerpts","sqlite_sequence"}; '
                        'tables=[(n,s,a.execute("SELECT * FROM "+n).fetchall()) for n,s in a.execute("SELECT name,sql FROM sqlite_master WHERE type=?", ("table",)) if n not in skip]; '
                        'sys.stdout.buffer.write(zlib.compress(json.dumps(tables).encode()))')
                raw = subprocess.run(['docker', 'exec', args.container, 'python', '-c', code], check=True, capture_output=True).stdout
                for name, schema, rows in json.loads(zlib.decompress(raw)):
                    target.execute(schema)
                    if rows:
                        target.executemany('INSERT INTO '+name+' VALUES ('+','.join('?' for _ in rows[0])+')', rows)
            elif args.database:
                source = Path(args.database).resolve()
                original = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
                original.backup(target)
                original.close()
            else:
                parser.error('Provide --database or --container')
        store = Store(directory)
        with store.db:
            control = store._get('control'); control['running'] = False
            store._set('control', control)
            store.db.execute('DELETE FROM recognition_excerpts')
        store.overview.complete(dict(include_content=True, automatic_associations=True, bounded_discovery=True))
        work = store._get('overview')
        generation = store._get('control')['generation']
        env = lambda **p: dict(p, generation=generation, request_id=work['request_id'])
        ids = set(work['completion_ids'])
        records = [json.loads(r[0]) for r in store.db.execute('SELECT metadata FROM catalog')]
        records = [r for r in records if r['session_id'] in ids]
        before = store.state()['overview']
        print(json.dumps(dict(event='benchmark_start', at=time.time(), sources=len(records),
            linked=before['linked'], ambiguous=before['ambiguous'], no_project=before['no_project'],
            projects=len(store.state()['projects']))), flush=True)
        def save(ids, evidence, residual, **kwargs):
            return checkpoint(store, env(bounded=True, attempted_ids=ids, excerpt_evidence=evidence, unassigned=residual, **kwargs))
        def emit(proposal, evidence, snapshots):
            for item in snapshots: store.ingest(dict(item, generation=generation))
            return publish(store, env(proposal=proposal, excerpt_evidence=evidence))
        started = time.monotonic()
        result = run(records, context(store)['known_projects'], Path(args.root), control['exclusions'],
            lambda: False, lambda p: store.overview.agent_progress(env(**p)),
            ingest=lambda item: store.ingest(dict(item, generation=generation)),
            publish=emit, feedback=lambda: context(store), checkpoint=save)
        save([], {}, [], finish=True)
        after = store.state()['overview']
        print(json.dumps(dict(event='benchmark_result', at=time.time(), seconds=time.monotonic()-started,
            metrics=result, state={k:after[k] for k in ['linked','ambiguous','no_project','sampled','pending_sources','agent_remaining']},
            new_automatic=store.db.execute('SELECT count(*) FROM automatic_links WHERE created_at > ?',
                (time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time()-(time.monotonic()-started))),)).fetchone()[0])), flush=True)
        store.close()


if __name__ == '__main__':
    main()
