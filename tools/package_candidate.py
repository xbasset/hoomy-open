#!/usr/bin/env python3
"""Create a deterministic review archive from an approved public snapshot."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile


PLATFORM_VERSION = "0.0.1"
MAX_FILE_BYTES = 32 * 1024 * 1024
BLOCKED_PARTS = {".git", ".codex", ".aws", "secret", "secrets", "private", "private-data", "data", "runtime", "runs", "output", "backups", "artifacts", "node_modules", "__pycache__", "knowledge-base", "knowledge-base-pilot", "knowledge-base-pilot-v2"}
BLOCKED_SUFFIXES = (".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm", ".pem", ".key", ".p12", ".db", ".jsonl", ".log", "-wal", "-shm", ".env")


def approved_path(value):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid_archive_path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in (".", "..", "") for part in path.parts):
        raise ValueError("invalid_archive_path")
    if any(part in BLOCKED_PARTS or part.startswith(".env") for part in path.parts) or value.endswith(BLOCKED_SUFFIXES):
        raise ValueError("private_archive_path")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid_archive_path")
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        raise ValueError("invalid_archive_path") from None
    return value


def _directory(path):
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise ValueError("symlink_directory")
    if not path.is_dir():
        raise ValueError("missing_directory")
    return path


def _snapshot_files(root_descriptor):
    found = set()
    for directory, folders, files, descriptor in os.fwalk(".", dir_fd=root_descriptor, follow_symlinks=False):
        for name in folders:
            if stat.S_ISLNK(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                raise ValueError("symlink_snapshot")
        for name in files:
            if not stat.S_ISREG(os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode):
                raise ValueError("nonregular_snapshot")
            relative = PurePosixPath(directory, name).as_posix()
            found.add(approved_path(relative.removeprefix("./")))
    return found


def _open_snapshot_file(root_descriptor, name):
    descriptor = os.dup(root_descriptor)
    try:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
    finally:
        os.close(descriptor)


class _VerifiedReader:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size):
        data = self.stream.read(size)
        self.digest.update(data)
        self.size += len(data)
        return data


def package_snapshot(snapshot, file_digests, output, provenance):
    """Package exactly the export gate's files; never discover additional inputs."""
    snapshot = _directory(snapshot)
    if not isinstance(file_digests, dict) or not file_digests:
        raise ValueError("missing_approved_files")
    approved = {}
    for name, digest in file_digests.items():
        name = approved_path(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid_approved_digest")
        approved[name] = digest
    snapshot_descriptor = os.open(snapshot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        found = _snapshot_files(snapshot_descriptor)
    finally:
        os.close(snapshot_descriptor)
    if found != set(approved):
        raise ValueError("snapshot_file_set_changed")
    if not isinstance(provenance, dict):
        raise ValueError("invalid_provenance")
    source_commit = provenance.get("source_commit")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("invalid_source_commit")
    output = Path(output).absolute()
    _directory(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError("output_exists")
    output.mkdir(mode=0o700)
    output_descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output_identity = os.fstat(output_descriptor)
    archive = output / ("hoomy-" + PLATFORM_VERSION + "-source.tar.gz")
    total = 0
    snapshot_descriptor = None
    try:
        snapshot_descriptor = os.open(snapshot, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        archive_descriptor = os.open(archive.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output_descriptor)
        with os.fdopen(archive_descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(mode="w", fileobj=compressed, format=tarfile.PAX_FORMAT) as stream:
                    for name in sorted(approved):
                        descriptor = _open_snapshot_file(snapshot_descriptor, name)
                        with os.fdopen(descriptor, "rb") as content:
                            before = os.fstat(content.fileno())
                            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                                raise ValueError("invalid_snapshot_file")
                            info = tarfile.TarInfo(name)
                            info.size = before.st_size
                            info.mode = 0o755 if before.st_mode & 0o111 else 0o644
                            info.uid = info.gid = info.mtime = 0
                            info.uname = info.gname = ""
                            reader = _VerifiedReader(content)
                            stream.addfile(info, reader)
                            after = os.fstat(content.fileno())
                            current_descriptor = _open_snapshot_file(snapshot_descriptor, name)
                            try:
                                current = os.fstat(current_descriptor)
                            finally:
                                os.close(current_descriptor)
                            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
                                raise ValueError("snapshot_path_changed_during_package")
                            if reader.size != before.st_size or reader.digest.hexdigest() != approved[name]:
                                raise ValueError("snapshot_digest_changed")
                            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                                raise ValueError("snapshot_changed_during_package")
                            total += reader.size
        if _snapshot_files(snapshot_descriptor) != set(approved):
            raise ValueError("snapshot_file_set_changed")
        current_output = os.stat(output, follow_symlinks=False)
        if (current_output.st_dev, current_output.st_ino) != (output_identity.st_dev, output_identity.st_ino):
            raise ValueError("output_directory_changed")
        archive_descriptor = os.open(archive.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=output_descriptor)
        with os.fdopen(archive_descriptor, "rb") as content:
            digest = hashlib.file_digest(content, "sha256").hexdigest()
        report = {"schema_version": 1, "platform_version": PLATFORM_VERSION,
                  "source_commit": source_commit, "provenance": provenance,
                  "file_count": len(approved), "source_bytes": total,
                  "archive": {"name": archive.name, "sha256": digest, "bytes": os.stat(archive.name, dir_fd=output_descriptor, follow_symlinks=False).st_size}}
        metadata_descriptor = os.open("candidate.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output_descriptor)
        with os.fdopen(metadata_descriptor, "w", encoding="utf-8") as metadata:
            json.dump(report, metadata, ensure_ascii=False, indent=2, sort_keys=True)
            metadata.write("\n")
        current_output = os.stat(output, follow_symlinks=False)
        if (current_output.st_dev, current_output.st_ino) != (output_identity.st_dev, output_identity.st_ino):
            raise ValueError("output_directory_changed")
        return report
    except BaseException:
        try:
            current_output = os.stat(output, follow_symlinks=False)
        except FileNotFoundError:
            current_output = None
        if current_output and (current_output.st_dev, current_output.st_ino) == (output_identity.st_dev, output_identity.st_ino):
            shutil.rmtree(output)
        raise
    finally:
        if snapshot_descriptor is not None:
            os.close(snapshot_descriptor)
        os.close(output_descriptor)


def package_candidate(repository, manifest, output):
    from export_public import prepare_export
    output = Path(output).absolute()
    _directory(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError("output_exists")
    with tempfile.TemporaryDirectory(prefix=".hoomy-export-", dir=output.parent) as temporary:
        snapshot = Path(temporary) / "snapshot"
        provenance = prepare_export(repository, manifest, snapshot, require_clean=True)
        if provenance.get("source_dirty") is not False:
            raise ValueError("source_not_clean")
        files = {entry["path"]: entry["sha256"] for entry in provenance["files"]}
        proof = snapshot / "EXPORT-PROVENANCE.json"
        if proof.is_symlink() or not proof.is_file():
            raise ValueError("missing_export_provenance")
        if json.loads(proof.read_text(encoding="utf-8")) != provenance:
            raise ValueError("export_provenance_changed")
        files[proof.name] = hashlib.sha256(proof.read_bytes()).hexdigest()
        return package_snapshot(snapshot, files, output, provenance)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = package_candidate(args.repository, args.manifest, args.output)
    print(json.dumps({"platform_version": result["platform_version"],
                      "source_commit": result["source_commit"],
                      "file_count": result["file_count"], "archive": result["archive"]}, sort_keys=True))
