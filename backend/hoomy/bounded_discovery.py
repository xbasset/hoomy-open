"""Prepare minimal evidence once, classify concurrently, investigate exceptions."""

import base64
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .catalog import classification_input
from .classifier import classify
from .evidence_selection import select
from .exploration import EvidenceTools, LiveProgress, explore
from .inference import InferenceCancelled, InferenceError


def run(records, known, root, exclusions, cancelled, progress, *, ingest,
        publish, feedback, checkpoint, max_seconds=235, classify_fn=classify):
    started = time.monotonic()
    deadline = started + max_seconds
    run_id = str(uuid.uuid4())
    live = LiveProgress(progress)
    live.plan = [{'step': name, 'status': 'inProgress' if i == 0 else 'pending'} for i, name in enumerate(
        ['Lecture ciblée · demandes et résumés', 'Classement parallèle · résultats en direct', 'Investigation · cas difficiles'])]
    evidence, available = {}, []
    totals = {'raw_bytes': 0, 'excerpt_chars': 0, 'excerpt_bytes': 0, 'prepared': 0,
              'classified': 0, 'failed_calls': 0}
    def log(event, **values):
        print(json.dumps(dict(event=event, run_id=run_id, at=time.time(),
                              elapsed_seconds=round(time.monotonic()-started, 3), **values)), flush=True)
    def expired():
        return cancelled() or time.monotonic() >= deadline
    live.add('status', 'Lecture ciblée · demandes utilisateur et résumés explicites')
    for record in records:
        if expired():
            break
        # Reuse the established source identity, symlink and scope checks.
        reader = EvidenceTools([record], root, exclusions, expired, lambda *args: None)
        sid = record['session_id']
        try:
            reader.inspect(sid, '')
        except InferenceCancelled:
            if cancelled():
                raise
            break
        item = reader.snapshots.get(sid)
        if item:
            totals['raw_bytes'] += len(base64.b64decode(item['raw_b64']))
            pieces = select(item['messages'], known)
            if pieces:
                ingest(item)
                evidence[sid] = {'snapshot_id': item['snapshot_id'], 'excerpts': pieces}
                available.append(record)
                totals['excerpt_chars'] += sum(len(p['text']) for p in pieces)
                totals['excerpt_bytes'] += sum(len(p['text'].encode()) for p in pieces)
        totals['prepared'] += 1
        if totals['prepared'] % 20 == 0:
            live.add('status', f"Lecture ciblée · {totals['prepared']} / {len(records)} sources parcourues")
    log('bounded_preparation_metrics', **totals)
    # Keep the context small and send each conversation to only one classifier.
    packets = [available[i:i+100] for i in range(0, len(available), 100)]
    batch_deadline = min(deadline, started + max_seconds * 0.72)
    completed = set()
    usage = {}
    def classify_packet(index, rows):
        stop = lambda: cancelled() or time.monotonic() >= batch_deadline
        if stop():
            raise InferenceCancelled('Budget de classement atteint.')
        payload, _ = classification_input(rows, feedback()['known_projects'])
        for row in payload['conversations']:
            row['excerpts'] = [p['text'] for p in evidence[row['id']]['excerpts']]
        result = classify_fn(payload, stop)
        log('bounded_classification_metrics', call=index, sources=len(rows), **result.get('metrics', {}))
        return rows, result
    live.add('status', f'Classement · {len(available)} conversations avec extraits · 3 appels simultanés maximum')
    live.plan[0]['status'], live.plan[1]['status'] = 'completed', 'inProgress'
    def ready(futures):
        pending = set(futures)
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            live.flush()
            yield from done
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(classify_packet, i, rows) for i, rows in enumerate(packets)]
        for future in ready(futures):
            if cancelled():
                raise InferenceCancelled('Exploration interrompue.')
            try:
                rows, result = future.result()
            except (InferenceError, InferenceCancelled):
                totals['failed_calls'] += 1
                continue
            for key, value in (result.get('metrics', {}).get('token_usage') or {}).items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
            for proposal in result['projects']:
                ids = proposal['session_ids']
                for offset in range(0, len(ids), 24):
                    part = ids[offset:offset+24]
                    publish(dict(proposal, session_ids=part), {sid: evidence[sid] for sid in part}, [])
            ids = [r['session_id'] for r in rows]
            # Persist outcomes, but leave ambiguous sources eligible for investigation.
            checkpoint(ids, {sid: evidence[sid] for sid in ids}, result['unassigned'])
            completed.update(ids)
            totals['classified'] = len(completed)
            live.usage = usage
            live.add('status', f"Classement · {len(completed)} / {len(available)} conversations examinées")
    remaining_ids = set(feedback().get('remaining_ids', []))
    residual = [r for r in records if r['session_id'] in remaining_ids]
    if residual and not expired():
        live.plan[1]['status'], live.plan[2]['status'] = 'completed', 'inProgress'
        live.add('status', f'Investigation ciblée · {len(residual)} conversations encore incertaines')
        offset = time.monotonic() - started
        def agent_progress(frame):
            events = [dict(e, elapsed_seconds=e['elapsed_seconds']+offset) for e in frame['events']]
            progress(dict(frame, events=(live.events+events)[-40:], plan=live.plan,
                elapsed_seconds=time.monotonic()-started,
                usage={k:usage.get(k, 0)+(frame.get('usage') or {}).get(k, 0)
                       for k in set(usage) | set(frame.get('usage') or {})}))
        result = explore(residual, feedback()['known_projects'], root, exclusions, cancelled,
                         agent_progress, publish=publish, feedback=feedback,
                         max_seconds=max(1, deadline-time.monotonic()))
        for item in result.pop('snapshots', []):
            ingest(item)
        checkpoint(result.get('attempted_ids', []), result['excerpt_evidence'], result.get('unassigned', []), investigation=True)
        for key, value in (result.get('metrics', {}).get('token_usage') or {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
    live.usage = usage
    for step in live.plan:
        if step['status'] == 'inProgress':
            step['status'] = 'completed'
    live.add('status', f"Passe terminée · {totals['classified']} classements sur extraits · {len(feedback().get('remaining_ids', []))} conversations à investiguer")
    live.flush(force=True)
    log('bounded_discovery_metrics', **totals, token_usage=usage)
    return totals
