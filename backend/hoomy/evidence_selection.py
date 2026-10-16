"""Select short intent evidence without treating assistant verbosity as signal."""

import re
from .local_triage import signals
from .recognition import features


def select(messages, projects, *, fallback=False):
    terms = signals(projects)
    options = []
    for index, message in enumerate(messages):
        text = message['text']
        if text.lstrip().startswith(('<', '# AGENTS', '# Global Instructions', 'You are Codex')):
            continue
        role = message['role']
        # Explicit textual summary headings are hints, not verified conclusions.
        summary = role == 'assistant' and re.match(r'^\s*(?:#+\s*)?(?:summary|résumé|bilan|in summary)\s*[:\n]', text, re.I)
        if role != 'user' and not summary and not (fallback and role == 'assistant'):
            continue
        matches = [m.start() for m in re.finditer(r'[\w-]{3,}', text) if m.group().casefold() in terms]
        for position in (matches[:2] or [0]):
            start = max(0, position - 100)
            piece = {'message_id': message['id'], 'start': start, 'text': text[start:start + 300]}
            score = len(features(piece['text']) & terms)
            options.append((score, role == 'user', index, piece))
    options.sort(key=lambda x: x[:3], reverse=True)
    chosen = []
    for _, _, _, piece in options:
        if not piece['text'].strip() or any(p['text'] == piece['text'] or p['message_id'] == piece['message_id'] for p in chosen):
            continue
        chosen.append(piece)
        if len(chosen) == 2:
            break
    return chosen
