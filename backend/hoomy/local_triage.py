"""Deterministic retrieval hints, never project decisions or executable searches."""

import re
from collections import Counter
from .recognition import features

MAX_CONVERSATIONS = 12
EXCERPT_CHARS = 3000


def signals(projects):
    trusted = [p for p in projects if not p.get("provisional")]
    names = set().union(*(features(p["name"]) for p in trusted)) if trusted else set()
    groups = [set().union(*(features(x.get("title", "")) for x in p.get("examples", []) if x.get("belongs", True))) for p in trusted]
    counts = Counter(word for group in groups for word in group)
    generic = set("which where about needed complete steps building informations because notes compile review update".split())
    return names | {word for word, count in counts.items() if count == 1 and word not in generic}


def rank(records, projects):
    terms = signals(projects)
    ordered = sorted(records, key=lambda r: (len(features(r["title"] + " " + r["cwd"]) & terms), r["modified_at"], r["session_id"]), reverse=True)
    # Leave room for conversations with no known-project vocabulary.
    familiar = [row for row in ordered if features(row["title"] + " " + row["cwd"]) & terms]
    exploratory = [row for row in ordered if row not in familiar]
    chosen = familiar[:9] + exploratory[:3]
    chosen_ids = {row["session_id"] for row in chosen}
    chosen.extend(row for row in ordered if row["session_id"] not in chosen_ids)
    return chosen[:MAX_CONVERSATIONS]


def excerpts(messages, projects):
    terms = signals(projects)
    options = []
    seen = set()
    for index, message in enumerate(messages):
        text = message["text"]
        if message["role"] not in ("user", "assistant") or text.lstrip().startswith(("<", "# AGENTS", "# Global Instructions", "You are Codex")):
            continue
        matches = [match for match in re.finditer(r"[\w-]{3,}", text) if match.group().casefold() in terms]
        positions = [match.start() for match in matches[:3]] or [0]
        spans = []
        for position in positions:
            start = max(0, position - 200)
            excerpt = text[start:start + 750]
            if any(start < end and start + len(excerpt) > begin for begin, end in spans):
                continue
            if not excerpt.strip() or excerpt in seen:
                continue
            seen.add(excerpt)
            spans.append((start, start + len(excerpt)))
            options.append((len(features(excerpt) & terms), message["role"] == "user", index,
                            {"message_id": message["id"], "start": start, "text": excerpt}))
    options.sort(key=lambda row: row[:3], reverse=True)
    return [row[3] for row in options[:EXCERPT_CHARS // 750]]
