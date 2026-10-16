#!/usr/bin/env python3
"""Copy only explicitly reviewed source files into a fresh public snapshot."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess

MAX_FILE_BYTES = 32 * 1024 * 1024
PRIVATE_PARTS = {'secret', 'secrets', 'private', 'private-data', 'data', 'runtime', 'runs', 'output', 'backups', '.codex', '.aws', 'node_modules', '__pycache__', 'artifacts', 'knowledge-base', 'knowledge-base-pilot', 'knowledge-base-pilot-v2'}
PRIVATE_SUFFIXES = ('.pem', '.key', '.p12', '.sqlite', '.sqlite3', '.db', '.jsonl', '.log', '.env', '-wal', '-shm')
GENERATED_PARTS = {'.git', '__pycache__', 'node_modules', '.pytest_cache', '.cache', '.venv', 'artifacts'}
KINDS = {'code', 'documentation', 'research', 'configuration', 'license'}
LICENSES = {'Apache-2.0', 'CC-BY-4.0', 'LicenseRef-ThirdParty'}


def path_name(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('invalid_path')
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(part in {'.', '..', ''} for part in p.parts):
        raise ValueError('invalid_path')
    try:
        value.encode('utf-8', 'strict')
    except UnicodeError:
        raise ValueError('invalid_path') from None
    return value


def public_path(value):
    value = path_name(value)
    parts = PurePosixPath(value).parts
    if any(part in PRIVATE_PARTS or part == '.git' or part.startswith('.env') for part in parts) or value.endswith(PRIVATE_SUFFIXES) or parts[-1] in {'auth.json', 'credentials.json', 'id_rsa', 'id_ed25519'}:
        raise ValueError('private_path')
    # This one repository-owned marketplace file is not host/editor state.
    hidden = [part for part in parts if part.startswith('.')]
    if hidden and value != 'plugins/hoomy-remote-acceptance/.agents/plugins/marketplace.json' and not (len(hidden) == 1 and parts[-1] in {'.gitignore', '.dockerignore'}):
        raise ValueError('hidden_path')
    return value


def directory(value):
    value = Path(value).absolute()
    for p in (value, *value.parents):
        if p.is_symlink():
            raise ValueError('symlink_directory')
    if not value.is_dir():
        raise ValueError('missing_directory')
    return value


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_manifest_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def load_manifest(path):
    path = Path(path).absolute()
    directory(path.parent)
    if path.is_symlink() or not path.is_file():
        raise ValueError('invalid_manifest')
    with path.open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('manifest_too_large')
    manifest = _json(raw)
    keys = {'schema_version', 'release', 'base_source_commit', 'files', 'excluded_prefixes', 'excluded_files'}
    if not isinstance(manifest, dict) or set(manifest) != keys or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise ValueError('invalid_manifest')
    release = manifest['release']
    if not isinstance(release, dict) or set(release) != {'name', 'version', 'status'} or release != {'name': 'Hoomy', 'version': '0.0.1', 'status': 'review-candidate'}:
        raise ValueError('invalid_release')
    if not isinstance(manifest['base_source_commit'], str) or not re.fullmatch('[0-9a-f]{40}', manifest['base_source_commit']):
        raise ValueError('invalid_base_commit')
    approved = {}
    if not isinstance(manifest['files'], list) or not manifest['files']:
        raise ValueError('missing_approved_files')
    for entry in manifest['files']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'kind', 'license'} or entry['kind'] not in KINDS or entry['license'] not in LICENSES:
            raise ValueError('invalid_approved_file')
        name = public_path(entry['path'])
        if name in approved or name == 'EXPORT-PROVENANCE.json':
            raise ValueError('duplicate_approved_file')
        approved[name] = entry
    excluded = {}
    for key in ('excluded_prefixes', 'excluded_files'):
        if not isinstance(manifest[key], list):
            raise ValueError('invalid_exclusions')
        entries = {}
        for entry in manifest[key]:
            if not isinstance(entry, dict) or set(entry) != {'path', 'reason'} or not isinstance(entry['reason'], str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', entry['reason']):
                raise ValueError('invalid_exclusion')
            name = path_name(entry['path'])
            if name in entries:
                raise ValueError('duplicate_exclusion')
            entries[name] = entry['reason']
        excluded[key] = entries
    for name in approved:
        if name in excluded['excluded_files']:
            raise ValueError('overlapping_classification')
    return manifest, approved, excluded, hashlib.sha256(raw).hexdigest()


def _reason(name, excluded):
    if name in excluded['excluded_files']:
        return excluded['excluded_files'][name]
    for prefix, reason in excluded['excluded_prefixes'].items():
        if name == prefix or name.startswith(prefix + '/'):
            return reason
    if any(p in GENERATED_PARTS for p in PurePosixPath(name).parts) or PurePosixPath(name).name in {'.DS_Store'} or name.endswith('.pyc'):
        return 'generated_or_git_state'
    return None


def _excluded_files(path):
    count = 0
    for parent, folders, files in os.walk(path, followlinks=False):
        for name in folders:
            if (Path(parent) / name).is_symlink():
                raise ValueError('symlink_source')
        for name in files:
            if not stat.S_ISREG((Path(parent) / name).lstat().st_mode):
                raise ValueError('nonregular_source')
            count += 1
    return count


def classify(repo, approved, excluded):
    found, counts = set(), Counter()
    for parent, folders, files in os.walk(repo, followlinks=False):
        relative = Path(parent).relative_to(repo)
        for name in list(folders):
            path = Path(parent) / name
            rel = (relative / name).as_posix()
            if path.is_symlink():
                raise ValueError('symlink_source')
            reason = _reason(rel, excluded)
            if reason and not any(n.startswith(rel + '/') for n in approved):
                folders.remove(name)
                counts[reason] += 1 if reason == 'generated_or_git_state' else _excluded_files(path)
        for name in files:
            path = Path(parent) / name
            rel = (relative / name).as_posix()
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise ValueError('nonregular_source')
            if rel in approved:
                found.add(rel)
            else:
                reason = _reason(rel, excluded)
                if not reason:
                    raise ValueError('unclassified_source_path')
                counts[reason] += 1
    if found != set(approved):
        raise ValueError('missing_approved_file')
    return dict(sorted(counts.items()))


def _git(repo):
    try:
        commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain', '--untracked-files=all'], stderr=subprocess.DEVNULL))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, None
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('invalid_source_commit')
    return (None if dirty else commit), dirty


def _proof_exclusions(repo, approved, excluded, fallback):
    try:
        names = subprocess.check_output(['git', '-C', str(repo), 'ls-tree', '-rz', '--name-only', 'HEAD'], stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {r: n for r, n in fallback.items() if r != 'generated_or_git_state'}
    counts = Counter()
    for raw in names.split(b'\0'):
        if not raw:
            continue
        name = raw.decode('utf-8', 'surrogateescape')
        if name not in approved:
            reason = _reason(name, excluded)
            if reason and reason != 'generated_or_git_state':
                counts[reason] += 1
    return dict(sorted(counts.items()))


def _open(root, name):
    fd = os.dup(root)
    try:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    finally:
        os.close(fd)


def _create(root, name):
    fd = os.dup(root)
    try:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
    finally:
        os.close(fd)


def _verify_destination(path, expected):
    directory(path)
    current = path.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('export_destination_changed')


def prepare_export(repo, manifest, output, *, require_clean=False):
    repo = directory(repo)
    manifest, approved, excluded, digest = load_manifest(manifest)
    counts = classify(repo, approved, excluded)
    commit, dirty = _git(repo)
    if require_clean and (dirty is not False or commit is None):
        raise ValueError('source_not_clean')
    output = Path(output).absolute()
    directory(output.parent)
    if output.exists() or output.is_symlink() or output == repo or repo in output.parents and not (repo / 'artifacts') in output.parents:
        raise ValueError('unsafe_export_output')
    output.mkdir(mode=0o700)
    root = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    records, fingerprints = [], {}
    destination = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination_identity = os.fstat(destination)
    try:
        for name, entry in sorted(approved.items()):
            fd = _open(root, name)
            with os.fdopen(fd, 'rb') as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                    raise ValueError('invalid_source_file')
                target_fd = _create(destination, name)
                mode = 0o755 if before.st_mode & 0o111 else 0o644
                h, size = hashlib.sha256(), 0
                with os.fdopen(target_fd, 'wb') as copied:
                    os.fchmod(copied.fileno(), mode)
                    while chunk := source.read(1024 * 1024):
                        h.update(chunk)
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise ValueError('source_file_too_large')
                        copied.write(chunk)
                after = os.fstat(source.fileno())
                current_fd = _open(root, name)
                try:
                    current = os.fstat(current_fd)
                finally:
                    os.close(current_fd)
                fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if fields(before) != fields(after) or fields(before) != fields(current) or size != before.st_size:
                    raise ValueError('source_changed_during_export')
                records.append({**entry, 'sha256': h.hexdigest(), 'bytes': size, 'mode': mode})
                fingerprints[name] = fields(before)
        if classify(repo, approved, excluded) != counts or _git(repo) != (commit, dirty):
            raise ValueError('source_changed_during_export')
        for name, expected in fingerprints.items():
            fd = _open(root, name)
            try:
                if fields(os.fstat(fd)) != expected:
                    raise ValueError('source_changed_during_export')
            finally:
                os.close(fd)
        _verify_destination(output, destination_identity)
        report = {'schema_version': 1, 'release': manifest['release'], 'base_source_commit': manifest['base_source_commit'], 'source_commit': commit, 'source_dirty': dirty, 'manifest_sha256': digest, 'files': records, 'excluded_summary': _proof_exclusions(repo, approved, excluded, counts)}
        with os.fdopen(_create(destination, 'EXPORT-PROVENANCE.json'), 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write('\n')
        _verify_destination(output, destination_identity)
        return report
    except BaseException:
        # Never clean a swapped path or an unrelated replacement directory.
        try:
            _verify_destination(output, destination_identity)
            shutil.rmtree(output)
        except (ValueError, OSError):
            pass
        raise
    finally:
        os.close(root)
        os.close(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--require-clean', action='store_true')
    args = parser.parse_args()
    report = prepare_export(args.repository, args.manifest, args.output, require_clean=args.require_clean)
    print(json.dumps({'source_commit': report['source_commit'], 'source_dirty': report['source_dirty'], 'file_count': len(report['files']), 'excluded_summary': report['excluded_summary']}, sort_keys=True))
