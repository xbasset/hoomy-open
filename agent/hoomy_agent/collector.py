"""Stable, allowlisted Codex snapshots projected to public conversation text."""

from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat

from .client import AgentError
from .policy import (MAX_MESSAGE, MAX_MESSAGES, MAX_PROJECTION, MAX_SOURCE,
                     absolute_path, canonical, project_for_cwd, sha256, within)
from .stream_json import StreamJSONError, proven_excluded

MAX_RECORD = 1024 * 1024
MAX_HEADER = MAX_RECORD
EXCLUDED_OVERSIZED = object()
PROJECTION_ADAPTER_REVISION = 3
CONTEXT = re.compile(r"<(?:environment_context|permissions[ _]instructions|INSTRUCTIONS|app-context|skills_instructions|collaboration_mode|send_user_message_question_reply)(?:\s|>)|^#\s*AGENTS\.md instructions|\A\s*The following is the Codex agent history (?:whose request action you are assessing|added since your last approval assessment)\.", re.I | re.M)
SENSITIVE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}|\b(?:access[_-]?token|refresh[_-]?token|client[_-]?secret|api[_-]?key|password)[\"']?\s*[:=]\s*[\"']?[^\s\"']+", re.I)


class SourceError(AgentError):
    def __init__(self, category):
        self.category = category
        super().__init__("Source capture deferred (" + category + ").")


@dataclass(frozen=True)
class SourceExclusion:
    discriminator: str
    header_sha256: str
    session_id: str
    cwd: str
    project_id: str
    modified_at: str
    fingerprint: tuple
    read_bytes: int
    unchanged: bool


def timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)", value):
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise SourceError("duplicate_json_key")
        result[key] = value
    return result


def _json(value):
    try:
        return json.loads(value, object_pairs_hook=_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise SourceError("incomplete_or_invalid_json") from None


def open_directory(path):
    """Anchor each path component; no symlinked ancestor is traversed."""
    path = absolute_path(str(path))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if os.fstat(descriptor).st_uid != os.getuid():
            raise SourceError("history_root_owner")
        return descriptor
    except (OSError, AgentError):
        os.close(descriptor)
        raise SourceError("unsafe_or_missing_history_root") from None


def source_paths(root, exclusions):
    descriptor = open_directory(root)
    def walk(directory, prefix, depth):
        if depth > 8:
            raise SourceError("history_depth_limit")
        for name in sorted(os.listdir(directory)):
            path = Path(root) / prefix / name
            if any(within(path, Path(exclusion)) for exclusion in exclusions):
                continue
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        yield from walk(child, prefix / name, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode) and name.endswith(".jsonl") and not re.search(r"(?:auth|credentials|secrets|config)\.jsonl$", name, re.I):
                    yield path
            except OSError:
                continue
    try:
        yield from walk(descriptor, Path(), 0)
    finally:
        os.close(descriptor)


def _fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_source(path, root, policy, unchanged=None):
    try:
        path, root = absolute_path(str(path)), absolute_path(str(root))
        relative = path.relative_to(root)
    except (AgentError, ValueError):
        raise SourceError("invalid_source_path") from None
    directory = open_directory(root)
    descriptor = None
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid():
            raise SourceError("unsafe_source")
        # Read only bounded metadata until recorded cwd positively matches policy.
        with os.fdopen(descriptor, "rb", buffering=0, closefd=False) as raw_stream:
            # Raw readline stops exactly at LF; buffering here would prefetch another project's body.
            header = raw_stream.readline(MAX_HEADER + 1)
            if not header.endswith(b"\n") or len(header) > MAX_HEADER:
                raise SourceError("unsupported_session_header")
            record = _json(header)
            metadata = record.get("payload") if isinstance(record, dict) and record.get("type") == "session_meta" else None
            if not isinstance(metadata, dict) or not isinstance(metadata.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", metadata["id"]):
                raise SourceError("unsupported_session_header")
            project = project_for_cwd(policy, metadata.get("cwd"))
            if project is None:
                raise SourceError("cwd_not_approved")
            source = metadata.get("source")
            discriminator = ("legacy_subagent_guardian" if source == {"subagent": {"other": "guardian"}}
                             else "internal_guardian" if source == {"internal": "guardian"} else None)
            if before.st_size > MAX_SOURCE and not discriminator:
                raise SourceError("source_oversized")
            is_unchanged = bool(unchanged and unchanged(metadata, _fingerprint(before)))
            if is_unchanged or discriminator:
                after = os.fstat(descriptor)
                current = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
                if _fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current):
                    raise SourceError("source_changed")
                if discriminator:
                    return SourceExclusion(discriminator, sha256(header), metadata["id"], metadata["cwd"], project,
                                           utc(after.st_mtime), _fingerprint(after), len(header), is_unchanged)
                raise SourceError("unchanged")
            stream = io.BufferedReader(raw_stream)
            hasher, read_bytes = hashlib.sha256(), 0
            lineage = SessionLineage(metadata["id"], metadata["cwd"], policy)
            def records():
                nonlocal read_bytes
                hasher.update(header)
                read_bytes += len(header)
                yield 1, record
                number = 1
                while True:
                    chunk = stream.readline(MAX_RECORD + 1)
                    if not chunk:
                        break
                    number += 1
                    oversized = len(chunk) > MAX_RECORD
                    hasher.update(chunk)
                    read_bytes += len(chunk)
                    if read_bytes > MAX_SOURCE:
                        raise SourceError("source_oversized")
                    if oversized:
                        def chunks(first):
                            nonlocal read_bytes
                            for offset in range(0, len(first), 65536):
                                yield first[offset:offset + 65536]
                            part = first
                            while not part.endswith(b"\n"):
                                part = stream.readline(65536)
                                if not part:
                                    raise SourceError("incomplete_or_invalid_json")
                                hasher.update(part)
                                read_bytes += len(part)
                                if read_bytes > MAX_SOURCE:
                                    raise SourceError("source_oversized")
                                yield part
                        try:
                            proven_excluded(chunks(chunk), known_session_ids=lineage.allowed)
                        except StreamJSONError as exc:
                            raise SourceError(exc.category) from None
                        yield number, EXCLUDED_OVERSIZED
                        continue
                    if not chunk.endswith(b"\n"):
                        raise SourceError("incomplete_or_invalid_json")
                    if chunk.strip():
                        yield number, _json(chunk)
            try:
                projection, digest, coverage = _project_records(records(), metadata["cwd"], metadata["id"], policy, lineage)
            finally:
                stream.close()
        after = os.fstat(descriptor)
        current = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
        if read_bytes != before.st_size or _fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(current):
            raise SourceError("source_changed")
        return projection, digest, coverage, hasher.hexdigest(), metadata["id"], metadata["cwd"], project, utc(after.st_mtime), read_bytes, list(_fingerprint(after))
    except OSError:
        raise SourceError("unsafe_or_changed_source") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def project(raw, cwd, session_id, policy=None):
    if not raw.endswith(b"\n"):
        raise SourceError("incomplete_or_invalid_json")
    return _project_records(((number, _json(line)) for number, line in enumerate(raw.splitlines(), 1) if line.strip()), cwd, session_id, policy)


class SessionLineage:
    """Keep canonical-first identity; admit only explicitly declared copied ancestors."""

    def __init__(self, session_id, cwd, policy):
        self.canonical_id, self.cwd, self.policy = session_id, cwd, policy
        self.project_id = project_for_cwd(policy, cwd) if policy else None
        self.allowed, self.parents = {session_id}, {}

    def validate(self, metadata):
        identity, cwd = metadata.get("id"), metadata.get("cwd")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", identity):
            raise SourceError("conflicting_session_scope")
        if ((self.policy and project_for_cwd(self.policy, cwd) != self.project_id)
                or (not self.policy and cwd != self.cwd)):
            raise SourceError("conflicting_session_scope")
        if identity not in self.allowed:
            raise SourceError("conflicting_session_scope")
        parent = metadata.get("forked_from_id")
        if parent is not None and (not isinstance(parent, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", parent)):
            raise SourceError("malformed_fork_lineage")
        if identity in self.parents:
            if self.parents[identity] != parent:
                raise SourceError("conflicting_fork_lineage")
            return
        seen, cursor = {identity}, parent
        while cursor is not None:
            if cursor in seen:
                raise SourceError("cyclic_fork_lineage")
            seen.add(cursor)
            cursor = self.parents.get(cursor)
        if parent is not None and parent not in self.allowed and len(self.allowed) >= 64:
            raise SourceError("fork_lineage_limit")
        self.parents[identity] = parent
        if parent is not None:
            self.allowed.add(parent)


def _project_records(records, cwd, session_id, policy=None, lineage=None):
    messages = []
    coverage = {"excluded_records": 0, "excluded_sensitive_messages": 0, "excluded_context_messages": 0, "excluded_oversized_records": 0}
    projection_bytes = len(canonical({"version": 1, "messages": []}))
    expected_project = project_for_cwd(policy, cwd) if policy else None
    if policy and expected_project is None:
        raise SourceError("cwd_not_approved")
    lineage = lineage or SessionLineage(session_id, cwd, policy)
    for number, record in records:
        if record is EXCLUDED_OVERSIZED:
            coverage["excluded_records"] += 1
            coverage["excluded_oversized_records"] += 1
            continue
        if not isinstance(record, dict):
            raise SourceError("unsupported_record")
        payload = record.get("payload")
        if not isinstance(payload, dict):
            if record.get("type") in ("session_meta", "turn_context"):
                raise SourceError("unknown_context_scope")
            coverage["excluded_records"] += 1
            continue
        if record.get("type") == "session_meta":
            lineage.validate(payload)
        if record.get("type") == "turn_context":
            if not isinstance(payload.get("cwd"), str):
                raise SourceError("unknown_context_scope")
            if ((policy and project_for_cwd(policy, payload["cwd"]) != expected_project)
                    or (not policy and payload["cwd"] != cwd)):
                raise SourceError("conflicting_session_scope")
        if (record.get("type") != "response_item" or payload.get("type") != "message"
                or payload.get("role") not in ("user", "assistant")
                or payload.get("channel") not in (None, "final")
                or payload.get("recipient") not in (None, "all")):
            coverage["excluded_records"] += 1
            continue
        content = payload.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            allowed = {"input_text", "text"} if payload["role"] == "user" else {"output_text", "text"}
            parts = [part["text"] for part in content if isinstance(part, dict) and part.get("type") in allowed and isinstance(part.get("text"), str)]
            text = "\n".join(parts)
        else:
            text = ""
        if not text.strip():
            coverage["excluded_records"] += 1
            continue
        try:
            text_bytes = text.encode("utf-8")
        except UnicodeError:
            raise SourceError("invalid_utf8") from None
        if CONTEXT.search(text):
            coverage["excluded_context_messages"] += 1
            continue
        if len(text_bytes) > MAX_MESSAGE:
            raise SourceError("message_oversized")
        if SENSITIVE.search(text):
            coverage["excluded_sensitive_messages"] += 1
            continue
        item = {"role": payload["role"], "text": text, "line_start": number, "line_end": number, "timestamp": timestamp(record.get("timestamp"))}
        projection_bytes += len(canonical(item)) + (1 if messages else 0)
        if projection_bytes > MAX_PROJECTION:
            raise SourceError("projection_oversized")
        messages.append(item)
        if len(messages) > MAX_MESSAGES:
            raise SourceError("message_count_limit")
    projection = {"version": 1, "messages": messages}
    encoded = canonical(projection)
    if len(encoded) > MAX_PROJECTION:
        raise SourceError("projection_oversized")
    return projection, sha256(encoded), coverage


def reconcile(outbox, policy, revision, now, max_files=512, max_read_bytes=64 * 1024 * 1024):
    stats = {"captured": 0, "unchanged": 0, "examined": 0, "eligible": 0, "excluded": 0, "failures": {}}
    cursor = outbox.cursor()
    last, exhausted, read_bytes = cursor, True, 0
    candidates = []
    for root in policy["roots"]:
        try:
            for path in source_paths(root, policy["exclusions"]):
                candidates.append((str(path), root))
                if len(candidates) > 20000:
                    raise SourceError("history_inventory_limit")
        except SourceError as exc:
            stats["failures"][exc.category] = stats["failures"].get(exc.category, 0) + 1
    for path_string, root in sorted(candidates[:20000]):
        path = Path(path_string)
        if path_string <= cursor:
            continue
        if stats["examined"] >= max_files or read_bytes >= max_read_bytes:
            exhausted = False
            break
        stats["examined"] += 1
        last = path_string
        try:
            archive = Path(root).name == "archived_sessions"
            def known(metadata, fingerprint):
                key = sha256(canonical([project_for_cwd(policy, metadata["cwd"]), "codex", metadata["id"], path_string, archive]))
                return outbox.unchanged(key, fingerprint, revision, PROJECTION_ADAPTER_REVISION)
            result = read_source(path, root, policy, known)
            if isinstance(result, SourceExclusion):
                stats["eligible"] += 1
                stats["excluded"] += 1
                read_bytes += result.read_bytes
                if result.unchanged:
                    stats["unchanged"] += 1
                    continue
                location = {"path": str(path), "cwd": result.cwd, "archive_occurrence": archive, "modified_at": result.modified_at}
                source_key = sha256(canonical([result.project_id, "codex", result.session_id, str(path), archive]))
                def build_exclusion(sequence, event_id):
                    return {"schema_version": 1, "event_id": event_id, "type": "conversation.source_excluded",
                        "project_id": result.project_id, "policy_revision": revision, "connector": "codex", "session_id": result.session_id,
                        "source_location": location, "sequence": sequence, "observed_at": utc(now), "exclusion": {
                            "reason": "internal_approval_review", "discriminator": result.discriminator, "header_sha256": result.header_sha256}}
                captured = outbox.capture(source_key, result.header_sha256, revision, build_exclusion, list(result.fingerprint), PROJECTION_ADAPTER_REVISION)
                stats["captured" if captured else "unchanged"] += 1
                continue
            projection, digest, coverage, original_digest, session_id, cwd, project_id, modified_at, size, fingerprint = result
            stats["eligible"] += 1
            read_bytes += size
            if not projection["messages"]:
                raise SourceError("no_public_messages")
            location = {"path": str(path), "cwd": cwd, "archive_occurrence": Path(root).name == "archived_sessions", "modified_at": modified_at}
            source_key = sha256(canonical([project_id, "codex", session_id, str(path), location["archive_occurrence"]]))
            def build(sequence, event_id):
                return {"schema_version": 1, "event_id": event_id, "type": "conversation.projected", "project_id": project_id,
                        "policy_revision": revision, "connector": "codex", "session_id": session_id, "source_location": location,
                        "sequence": sequence, "source_sha256": original_digest, "projected_sha256": digest,
                        "observed_at": utc(now), "title": projection["messages"][0]["text"].splitlines()[0][:160],
                        "coverage": coverage, "projection": projection}
            captured = outbox.capture(source_key, original_digest, revision, build, fingerprint, PROJECTION_ADAPTER_REVISION)
            stats["captured" if captured else "unchanged"] += 1
        except SourceError as exc:
            if exc.category == "unchanged":
                stats["unchanged"] += 1
                stats["eligible"] += 1
            else:
                stats["failures"][exc.category] = stats["failures"].get(exc.category, 0) + 1
    outbox.set_cursor("" if exhausted else last)
    stats["scan_complete"] = exhausted and not any(key in stats["failures"] for key in ("history_inventory_limit", "history_depth_limit", "unsafe_or_missing_history_root"))
    return stats
