"""Conservative presentation checks; these do not establish semantic entailment."""

import hashlib
import json
import re

POLICY = {
    "version": "project-state-reader-quality-v1",
    "prose_fields": ["assertion.text", "assertion.limitations", "uncertainty.text"],
    "terminal_punctuation": ".?!…。！？",
    "code": "matching-unescaped-backtick-runs",
    "trailing_closers": "\"'”’»)]}`",
    "version_pattern": r"(?<![A-Za-z0-9_])(?:[vV])?\d+\.\d+(?:\.\d+)*(?:[-+][A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)?",
    "versions": "numeric-dotted-own-quotes-optional-v",
    "adjacent_quotes": "same-frozen-message-touching-scalar-offsets-only",
    "repair": False,
    "semantic_guarantee": False,
}
POLICY_HASH = hashlib.sha256(json.dumps(POLICY, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
REASONS = ("nonterminal_prose", "unclosed_code", "unsupported_version")
VERSION = re.compile(POLICY["version_pattern"])
PASSAGE = re.compile(r"^(p[0-9]{4})_([0-9]{2})$")


def _versions(text):
    return {match.group().lstrip("vV") for match in VERSION.finditer(text)}


def _open_code(text):
    delimiter = None
    for match in re.finditer(r"`+", text):
        index, escapes = match.start() - 1, 0
        while index >= 0 and text[index] == "\\":
            escapes += 1
            index -= 1
        if escapes % 2:
            continue
        run = match.group()
        if delimiter is None:
            delimiter = run
        elif run == delimiter:
            delimiter = None
    return delimiter is not None


def _joined_quotes(records):
    # Only an item's own touching passages can complete a split version token.
    joined, previous = [], None
    for record in sorted(records, key=lambda r: (r["message_id"], r["char_start"], r["char_end"])):
        if previous and previous["message_id"] == record["message_id"] and previous["char_end"] == record["char_start"]:
            joined[-1] += record["text"]
        else:
            joined.append(record["text"])
        previous = record
    return joined


def item_reasons(item, records):
    fields = [item["text"], *item.get("limitations", [])]
    reasons = []
    if any(not text.rstrip().rstrip(POLICY["trailing_closers"]).endswith(tuple(POLICY["terminal_punctuation"])) for text in fields):
        reasons.append("nonterminal_prose")
    if any(_open_code(text) for text in fields):
        reasons.append("unclosed_code")
    cited = set().union(*(_versions(text) for text in _joined_quotes(records))) if records else set()
    if set().union(*(_versions(text) for text in fields)) - cited:
        reasons.append("unsupported_version")
    return reasons


def filter_output(output, evidence):
    """Return references to unmodified surviving items plus closed exclusion metadata."""
    visible = {**output, "assertions": [], "uncertainties": []}
    excluded = []
    for section in ("assertions", "uncertainties"):
        for index, item in enumerate(output[section]):
            reasons = item_reasons(item, evidence(item))
            if reasons:
                excluded.append({"section": section, "index": index, "reasons": reasons})
            else:
                visible[section].append(item)
    keys = {item["key"] for item in visible["assertions"]}
    visible["contradictions"] = [item for item in output["contradictions"] if set(item["assertion_keys"]).issubset(keys)]
    return visible, {"policy_version": POLICY["version"], "policy_hash": POLICY_HASH,
        "raw_assertions": len(output["assertions"]), "raw_uncertainties": len(output["uncertainties"]),
        "visible_assertions": len(visible["assertions"]), "visible_uncertainties": len(visible["uncertainties"]),
        "excluded_items": excluded}


def model_quote_records(payload):
    """The native client uses only the frozen, already supplied passage registry."""
    from .project_state_contract import METHOD_CONFIG
    records = {}
    for passage in payload["passages"]:
        match = PASSAGE.fullmatch(passage["passage_id"])
        if not match or int(match[2]) < 1:
            raise ValueError("Invalid frozen passage identifier.")
        start = (int(match[2]) - 1) * METHOD_CONFIG["passages"]["characters"]
        records[passage["passage_id"]] = {"text": passage["text"], "message_id": match[1],
            "char_start": start, "char_end": start + len(passage["text"])}
    return records


def filter_model_output(output, records):
    return filter_output(output, lambda item: [records[ref["passage_id"]] for ref in item["source_refs"]])
