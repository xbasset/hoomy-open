"""Metadata-only census. Conversation bodies are read only during deepening."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .collector import canonical_path, scope_status, session_paths

HEADER_BYTES = 64 * 1024
TITLE_CHARS = 240
INPUT_CHARS = 750_000


def clean_title(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip()
    if text.startswith(("<", "# AGENTS", "# Global Instructions", "The following is the Codex agent history", "You are Codex")) or re.search(r"(?:^\[\d+\]\s*(?:user|assistant):|\w+@[^ ]+[:#]|CONTAINER ID|Traceback \(most recent)", text):
        return ""
    return text[:TITLE_CHARS]


def title_index(root):
    """Read only Codex's title fields; never its previews or first-user text."""
    titles = {}
    try:
        if (root / "session_index.jsonl").is_symlink():
            raise OSError()
        with (root / "session_index.jsonl").open() as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get("id"), str):
                        title = clean_title(row.get("thread_name"))
                        if title:
                            titles[row["id"]] = title
                except (ValueError, TypeError):
                    continue
    except (OSError, UnicodeError):
        pass
    # The optional registry is read-only and schema-checked; JSONL remains primary.
    for database in sorted(root.glob("state_*.sqlite"), reverse=True):
        if database.is_symlink():
            continue
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
                fields = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                if not {"id", "title"}.issubset(fields):
                    continue
                for sid, title in connection.execute("SELECT id,title FROM threads"):
                    title = clean_title(title)
                    if title:
                        titles.setdefault(sid, title)
            break
        except sqlite3.Error:
            continue
    return titles


def inventory(root, exclusions, cancelled=lambda: False):
    root = Path(root).expanduser().resolve()
    titles = title_index(root)
    records, failures, excluded = {}, [], 0
    paths = session_paths(root)
    for path in paths:
        if cancelled():
            return None
        source_id = hashlib.sha256(str(path).encode()).hexdigest()
        try:
            # The first record contains session identity/cwd. Never read the body
            # to obtain a title, even if the index is missing or the file is huge.
            with path.open("rb") as source:
                header = source.readline(HEADER_BYTES + 1)
            if len(header) > HEADER_BYTES:
                raise ValueError()
            value = json.loads(header)
            metadata = value.get("payload")
            if value.get("type") != "session_meta" or not isinstance(metadata, dict):
                raise ValueError()
            sid = metadata.get("id")
            if not isinstance(sid, str) or not sid:
                raise ValueError()
            cwd = metadata.get("cwd", "")
            scope = scope_status(cwd, exclusions)
            if scope == "excluded":
                excluded += 1
                continue
            if scope != "included":
                failures.append({"source_id": source_id, "reason": "Périmètre inconnu : dossier de la conversation absent ou inutilisable."})
                continue
            info = path.stat()
            normalized = canonical_path(cwd)
            cwd = str(normalized) if normalized else ""
            record = {"session_id": sid, "title": titles.get(sid, "Conversation sans titre"),
                      "cwd": cwd, "source_path": str(path), "size": info.st_size,
                      "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
                      "title_source": "codex_index" if sid in titles else "unavailable"}
            # An archived copy is another location, not another conversation.
            if sid not in records or record["modified_at"] > records[sid]["modified_at"]:
                records[sid] = record
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            failures.append({"source_id": source_id, "reason": "En-tête Codex illisible ; contenu non lu."})
    return {"records": list(records.values()), "excluded": excluded, "failures": failures,
            "files_seen": len(paths)}


def classification_input(records, known_projects):
    """Include the whole eligible inventory or fail before spending inference."""
    selected, folders, folder_ids, chars = [], [], {}, 0
    for row in sorted(records, key=lambda r: (r["modified_at"], r["session_id"]), reverse=True):
        folder_id = folder_ids.get(row["cwd"], "f" + str(len(folders)))
        folder = {"id": folder_id, "name": Path(row["cwd"]).name if row["cwd"] else ""}
        item = {"id": row["session_id"], "title": clean_title(row["title"]) or "Conversation sans titre exploitable", "folder_id": folder_id}
        new_folder = row["cwd"] not in folder_ids
        size = len(json.dumps(item, ensure_ascii=False)) + (len(json.dumps(folder, ensure_ascii=False)) if new_folder else 0)
        chars += size
        selected.append(item)
        if new_folder:
            folders.append(folder)
            folder_ids[row["cwd"]] = folder_id
    payload = {"conversations": selected, "folders": folders, "known_projects": known_projects}
    chars = len(json.dumps(payload, ensure_ascii=False))
    if chars > INPUT_CHARS:
        from .inference import InferenceError
        raise InferenceError("L’inventaire dépasse le budget de cette passe ; aucune conversation n’a été omise. Un traitement par lots est nécessaire.")
    return payload, chars
