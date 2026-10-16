"""Read bounded history excerpts without loading oversized tool records."""
import hashlib
import json
from collections import deque
from datetime import datetime, timezone
from .collector import SnapshotError, text_content

MAX_LINE = 1024 * 1024
MAX_SCAN = 512 * 1024 * 1024


def excerpts(path, cancelled):
    before = path.stat()
    if before.st_size > MAX_SCAN:
        raise SnapshotError('source_too_large: above the 512 MiB scan budget')
    digest = hashlib.sha256()
    pools = {role:deque(maxlen=12) for role in ('user','assistant','tool')}
    metadata = None
    skipped = 0
    scanned = 0
    with path.open('rb') as handle:
        line_number = 0
        while True:
            if cancelled():
                from .inference import InferenceCancelled
                raise InferenceCancelled('Recherche arrêtée.')
            raw = handle.readline(MAX_LINE + 1)
            if not raw:
                break
            line_number += 1
            scanned += len(raw)
            if scanned > MAX_SCAN:
                raise SnapshotError('source_too_large: scan budget reached')
            digest.update(raw)
            if len(raw) > MAX_LINE:
                while raw and not raw.endswith(b'\n'):
                    raw = handle.readline(MAX_LINE + 1)
                    scanned += len(raw)
                    if cancelled():
                        from .inference import InferenceCancelled
                        raise InferenceCancelled('Recherche arrêtée.')
                    if scanned > MAX_SCAN:
                        raise SnapshotError('source_too_large: scan budget reached')
                    digest.update(raw)
                skipped += 1
                continue
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                skipped += 1
                continue
            if not isinstance(record,dict):
                continue
            payload = record.get('payload',{})
            if not isinstance(payload,dict):
                continue
            if record.get('type') == 'session_meta':
                metadata = payload
            role, text = None, ''
            if record.get('type') == 'response_item':
                if payload.get('type') == 'message' and payload.get('channel') != 'analysis':
                    role, text = payload.get('role'), text_content(payload.get('content'))
                elif payload.get('type') in ('function_call_output','custom_tool_call_output'):
                    role = 'tool'
                    output = payload.get('output','')
                    text = output if isinstance(output,str) else ''
            elif record.get('type') == 'event_msg':
                role = {'user_message':'user','agent_message':'assistant'}.get(payload.get('type'))
                text = text_content(payload.get('message',''))
            if role not in pools or not text.strip() or text.lstrip().startswith(('# AGENTS','<environment_context>','<system-reminder>')):
                continue
            # Prefer substantive exchanges; duplicate event/response records add no evidence.
            if any(m['full_hash']==hashlib.sha256(text.encode()).hexdigest() for m in pools[role]):
                continue
            budget = 1800 if role != 'tool' else 400
            piece = text if len(text)<=budget else text[:budget//2]+'\n[…]\n'+text[-budget//2:]
            pools[role].append({'line':line_number,'role':role,'text':piece,
                'timestamp':record.get('timestamp'),'truncated':piece!=text,
                'full_hash':hashlib.sha256(text.encode()).hexdigest()})
    after = path.stat()
    if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
        raise SnapshotError('source_changed: retry with a stable source')
    if not metadata:
        raise SnapshotError('missing_metadata: source identity cannot be verified')
    chosen=[]
    for role,budget in [('user',3200),('assistant',3200),('tool',800)]:
        for message in reversed(pools[role]):
            if budget<=0:break
            item=dict(message)
            item['text']=message['text'][:budget]
            item['truncated']=message['truncated'] or len(item['text'])<len(message['text'])
            budget-=len(item['text'])
            chosen.append(item)
    version=digest.hexdigest()
    messages=[{k:v for k,v in dict(m,id=f"{version}:l{m['line']}").items() if k not in ('full_hash','line')}
              for m in sorted(chosen,key=lambda m:m['line'])]
    return {'session_id':metadata.get('id'), 'cwd':metadata.get('cwd',''), 'snapshot_id':version,
            'modified_at':datetime.fromtimestamp(after.st_mtime,timezone.utc).isoformat(),
            'messages':messages,'skipped_records':skipped}
