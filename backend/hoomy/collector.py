"""Read immutable snapshots; never modify the original Codex history."""

from __future__ import annotations

import base64
import bisect
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
PARSER_VERSION = "codex-jsonl-v1"
SCOPE_UNKNOWN = "scope_unknown"
SCOPE_UNKNOWN_FAILURE = (
    "scope_unknown: Hoomy could not verify this conversation against the configured exclusions."
)


class SnapshotError(ValueError):
    pass


def text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"] for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
            and part.get("type") in ("input_text", "output_text", "text")
        )
    return ""


def normalize(raw: bytes, source_path: str, modified_at: str) -> dict:
    """Line references address the raw hash, not a moving source file."""
    if not raw or len(raw) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("Empty file or snapshot exceeds the 16 MiB pilot limit.")
    digest = hashlib.sha256(raw).hexdigest()
    records = []
    warnings = []
    try:
        lines = raw.decode("utf-8").splitlines()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SnapshotError(f"Incomplete or invalid JSON at line {line_number}; retry after writing finishes.") from exc
            if not isinstance(value, dict):
                raise SnapshotError(f"Unsupported record at line {line_number}.")
            records.append((line_number, value))
    except UnicodeDecodeError as exc:
        raise SnapshotError("Session is not UTF-8 JSONL.") from exc

    metadata = next((v.get("payload", {}) for _, v in records if v.get("type") == "session_meta"), {})
    if not isinstance(metadata, dict):
        metadata = {}
    canonical_lines = {}
    for number, record in records:
        payload = record.get("payload", {})
        if (record.get("type") == "response_item" and isinstance(payload, dict)
                and payload.get("type") == "message"
                and payload.get("role") in ("user", "assistant")
                and payload.get("channel") != "analysis"):
            text = text_content(payload.get("content"))
            if text.strip():
                canonical_lines.setdefault((payload["role"], text), []).append(number)
    messages = []
    unsupported_media = 0
    for number, record in records:
        payload = record.get("payload", {})
        if not isinstance(payload, dict):
            continue
        role, text = None, ""
        if record.get("type") == "response_item":
            kind = payload.get("type")
            if kind == "message" and payload.get("role") in ("user", "assistant"):
                if payload.get("channel") == "analysis":
                    continue
                role = payload["role"]
                text = text_content(payload.get("content"))
                content = payload.get("content")
                if isinstance(content, list):
                    unsupported_media += sum(isinstance(p, dict) and p.get("type") not in ("input_text", "output_text", "text") for p in content)
            elif kind in ("function_call_output", "custom_tool_call_output"):
                role = "tool"
                output = payload.get("output", "")
                text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            elif kind in ("function_call", "custom_tool_call"):
                role = "tool"
                arguments = payload.get("arguments", payload.get("input", ""))
                text = str(payload.get("name", "tool")) + ": " + (arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False))
        elif record.get("type") == "event_msg":
            role = {"user_message": "user", "agent_message": "assistant"}.get(payload.get("type"))
            text = text_content(payload.get("message", ""))
            candidates = canonical_lines.get((role, text), [])
            nearby = bisect.bisect_left(candidates, number - 5)
            if nearby < len(candidates) and candidates[nearby] <= number + 5:
                continue
        if role and text.strip():
            messages.append({"id": f"{digest}:l{number}", "role": role, "text": text,
                             "line_start": number, "line_end": number,
                             "timestamp": record.get("timestamp") if isinstance(record.get("timestamp"), str) else None})
    if unsupported_media:
        warnings.append(f"{unsupported_media} non-text content block(s) retained in raw storage but not analyzed.")
    if not messages:
        warnings.append("No supported conversation text; not eligible for inference.")
    return {"session_id": str(metadata.get("id") or hashlib.sha256(source_path.encode()).hexdigest()),
            "snapshot_id": digest, "source_path": source_path, "modified_at": modified_at,
            "cwd": metadata.get("cwd") if isinstance(metadata.get("cwd"), str) else "",
            "raw_b64": base64.b64encode(raw).decode("ascii"), "messages": messages, "warnings": warnings}


def snapshot(path: Path) -> dict:
    if path.is_symlink():
        raise SnapshotError("Symbolic-link sessions are not followed.")
    before = path.stat()
    if before.st_size > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("Snapshot exceeds the 16 MiB pilot limit; not silently truncated.")
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise SnapshotError("Session changed during collection; retrying on the next scan.")
    return normalize(raw, str(path), datetime.fromtimestamp(after.st_mtime, timezone.utc).isoformat())


def session_paths(codex_root: Path) -> list[Path]:
    """Only known history roots, not config, credentials, or arbitrary home files."""
    paths = []
    for name in ("sessions", "archived_sessions"):
        root = codex_root / name
        if root.is_dir() and not root.is_symlink():
            paths.extend(p for p in root.rglob("*.jsonl") if not p.is_symlink()
                         and not any(parent.is_symlink() for parent in p.parents if root in parent.parents))
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def canonical_path(value: str) -> Path | None:
    """Resolve host paths without requiring the target to still exist."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return None
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def excluded(cwd: str, prefixes: list[str]) -> bool:
    candidate = canonical_path(cwd)
    if candidate is None:
        return False
    for prefix in prefixes:
        root = canonical_path(prefix)
        if root is not None and (candidate == root or root in candidate.parents):
            return True
    return False


def scope_status(cwd: str, prefixes: list[str]) -> str:
    """Fail closed only when exclusions make recorded scope material."""
    if not prefixes:
        return "included"
    if canonical_path(cwd) is None or any(canonical_path(prefix) is None for prefix in prefixes):
        return SCOPE_UNKNOWN
    return "excluded" if excluded(cwd, prefixes) else "included"
