"""Optional operator diagnostics for rejected public JSON, never model inputs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

CAP = 512 * 1024
SCHEMA_CAP = 64 * 1024
MANIFEST_RESERVE = 4096
FIELDS = {"schema_version", "assertions", "uncertainties", "contradictions", "key", "topic", "text", "kind",
    "reported_state", "limitations", "source_refs", "message_alias", "quote", "passage_id", "reason", "assertion_keys",
    "adapter", "model", "elapsed_ms", "usage", "input_tokens", "output_tokens", "total_tokens", "unknown_property"}
RULES = {"schema_depth", "value_type", "enum_value", "schema_object", "required_property", "unknown_property",
    "array_length", "string_length", "blank_string", "scalar_encoding", "reference_limit", "blank_quote",
    "unknown_alias", "quote_membership", "passage_membership", "required_machine_evidence", "duplicate_assertion_key", "duplicate_contradiction_key",
    "unknown_assertion_key", "method_metadata", "output_byte_limit"}


def safe_detail(value):
    if not isinstance(value, dict):
        raise ValueError("invalid_validation_diagnostic")
    stage, rule, pointer = (value.get(key) for key in ("substage", "rule", "pointer"))
    if type(stage) is not str or stage not in {"schema", "citations", "method", "bytecap"} or type(rule) is not str or rule not in RULES:
        raise ValueError("invalid_validation_diagnostic")
    if not isinstance(pointer, list) or len(pointer) > 16 or any(not (
            type(part) is int and 0 <= part <= 9999 or type(part) is str and part in FIELDS) for part in pointer):
        raise ValueError("invalid_validation_diagnostic")
    return {"substage": stage, "rule": rule, "pointer": list(pointer)}


def _encoded(value, keep=0):
    digest, size, prefix = hashlib.sha256(), 0, bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    try:
        for part in encoder.iterencode(value):
            raw = part.encode("utf-8")
            digest.update(raw)
            size += len(raw)
            prefix.extend(raw[:max(0, keep - len(prefix))])
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return {"bytes": None, "sha256": None, "encoding_valid": False}, b""
    return {"bytes": size, "sha256": digest.hexdigest(), "encoding_valid": True}, bytes(prefix)


def rejection_metadata(output, schema):
    result = {"output": _encoded(output)[0], "schema": _encoded(schema)[0]}
    counts, saturated = {}, False
    for field in ("assertions", "uncertainties", "contradictions"):
        value = output.get(field) if isinstance(output, dict) else None
        count = len(value) if isinstance(value, list) else None
        saturated |= count is not None and count > 100000
        counts[field] = min(count, 100000) if count is not None else None
    result.update(counts=counts, counts_saturated=saturated)
    return result


class RejectedOutputArchive:
    """Only an explicit operator can opt into this private, local callback."""

    @staticmethod
    def _open_directory(path):
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            os.close(descriptor)
            raise ValueError("symlink_capture_forbidden") from None

    def __init__(self, directory):
        path = Path(directory)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("private_directory_required")
        repository = Path(__file__).resolve().parents[2]
        if path == repository or repository in path.parents:
            raise ValueError("repository_capture_forbidden")
        parent = self._open_directory(path.parent)
        try:
            try:
                os.mkdir(path.name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            descriptor = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except OSError:
            raise ValueError("private_directory_required") from None
        finally:
            os.close(parent)
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(descriptor)
            raise ValueError("private_directory_required")
        self.directory, self.last_path = path, None
        self.directory_fd = descriptor
        self.identity = (info.st_dev, info.st_ino)

    def close(self):
        descriptor = getattr(self, "directory_fd", None)
        if descriptor is not None:
            self.directory_fd = None
            os.close(descriptor)

    def __del__(self):
        self.close()

    def _verify_directory(self):
        descriptor = self._open_directory(self.directory)
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != self.identity or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("private_directory_changed")
        finally:
            os.close(descriptor)

    def __call__(self, output, schema, diagnostic):
        # No input, native event, method or credential enters this interface.
        detail = safe_detail(diagnostic)
        schema_meta, schema_bytes = _encoded(schema, SCHEMA_CAP)
        output_meta, output_bytes = _encoded(output, CAP - MANIFEST_RESERVE - len(schema_bytes))
        if not schema_meta["encoding_valid"] or not output_meta["encoding_valid"]:
            raise ValueError("unsupported_json_encoding")
        self._verify_directory()
        candidate = "rejected-" + os.urandom(16).hex()
        os.mkdir(candidate, mode=0o700, dir_fd=self.directory_fd)
        path = self.directory / candidate
        descriptor = os.open(candidate, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.directory_fd)
        names = []
        try:
            manifest = {"format_version": 1, "output": {**output_meta,
                "captured_bytes": len(output_bytes), "truncated": len(output_bytes) != output_meta["bytes"]},
                "schema": {**schema_meta, "captured_bytes": len(schema_bytes),
                "truncated": len(schema_bytes) != schema_meta["bytes"]}, "diagnostic": detail}
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
            if len(manifest_bytes) > MANIFEST_RESERVE:
                raise ValueError("private_manifest_limit")
            for name, content in (("output.json", output_bytes), ("schema.json", schema_bytes), ("manifest.json", manifest_bytes)):
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
                names.append(name)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            os.fsync(descriptor)
            self._verify_directory()
            self.last_path = str(path)
        except Exception:
            for name in names:
                os.unlink(name, dir_fd=descriptor)
            os.rmdir(candidate, dir_fd=self.directory_fd)
            raise
        finally:
            os.close(descriptor)
