"""Closed pilot policy and canonical, content-addressed event encoding."""

import hashlib
import json
import os
from pathlib import Path

from .client import AgentError

CONTENT_TIER = "conversation_visible_v1"
INGEST_SCOPES = {"device:self", "evidence:ingest"}
MAX_SOURCE = 256 * 1024 * 1024
MAX_PROJECTION = 18 * 1024 * 1024
MAX_EVENT = 24 * 1024 * 1024
MAX_MESSAGE = 256 * 1024
MAX_MESSAGES = 10_000


def canonical(value):
    def check(item):
        if item is None or type(item) in (str, int, bool):
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(key, str) for key in item):
            for child in item.values():
                check(child)
            return
        raise AgentError("Unsupported canonical event value.")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def absolute_path(value):
    try:
        size = len(value.encode("utf-8")) if isinstance(value, str) else 4097
    except UnicodeError:
        raise AgentError("Collection paths require scalar Unicode.") from None
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//") or value.endswith("/") or size > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in value) or os.path.normpath(value) != value):
        raise AgentError("Collection paths must be normalized absolute paths.")
    return Path(value)


def within(value, root):
    return value == root or root in value.parents


def approved_policy(value):
    if (not isinstance(value, dict) or set(value) != {"content_tier", "collection_enabled", "roots", "exclusions", "project_bindings"}
            or value["content_tier"] != CONTENT_TIER or value["collection_enabled"] is not True):
        raise AgentError("A reviewed visible-conversation policy is required.")
    roots, exclusions, bindings = value["roots"], value["exclusions"], value["project_bindings"]
    if (not isinstance(roots, list) or not 1 <= len(roots) <= 8 or not isinstance(exclusions, list)
            or len(exclusions) > 32 or not isinstance(bindings, list) or len(bindings) != 1):
        raise AgentError("The pilot requires bounded history roots and exactly one project.")
    for root in roots:
        path = absolute_path(root)
        if path.name not in ("sessions", "archived_sessions") or path.parent.name != ".codex":
            raise AgentError("Only Codex sessions and archived_sessions roots can be approved.")
    if len(set(roots)) != len(roots):
        raise AgentError("Duplicate history roots are unsupported.")
    for exclusion in exclusions:
        absolute_path(exclusion)
    binding = bindings[0]
    if (not isinstance(binding, dict) or set(binding) != {"project_id", "cwd_roots"}
            or not isinstance(binding["project_id"], str) or not 1 <= len(binding["project_id"]) <= 128
            or not isinstance(binding["cwd_roots"], list) or not 1 <= len(binding["cwd_roots"]) <= 8):
        raise AgentError("The policy requires one canonical project and positive cwd roots.")
    for cwd in binding["cwd_roots"]:
        absolute_path(cwd)
    return json.loads(canonical(value))


def project_for_cwd(policy, value):
    try:
        cwd = absolute_path(value)
    except AgentError:
        return None
    if any(within(cwd, absolute_path(root)) for root in policy["exclusions"]):
        return None
    binding = policy["project_bindings"][0]
    if any(within(cwd, absolute_path(root)) for root in binding["cwd_roots"]):
        return binding["project_id"]
    return None
