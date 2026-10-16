"""Private local state with one credential owner across CLI and service."""

import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import uuid


class StorageError(Exception):
    pass


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise StorageError("State directory must belong to the current OS user.")
    path.chmod(0o700)
    return path


def read_private(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError:
        raise StorageError("Cannot open private state safely.") from None
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise StorageError("Private state requires owner-only permissions.")
        try:
            return json.load(stream)
        except (ValueError, UnicodeError):
            raise StorageError("Private state is damaged; restore it or enrol again.") from None


def atomic_write(path, value):
    path = Path(path)
    private_directory(path.parent)
    if path.is_symlink():
        raise StorageError("Refusing a symlink for private state.")
    descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Keychain:
    """Use the default macOS Keychain without passing secrets to subprocesses."""

    def __init__(self, account):
        self.account = account.encode()
        self.service = b"dev.hoomy.collector"
        self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.foundation = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer = ctypes.c_void_p
        integer = ctypes.c_uint32
        self.security.SecKeychainFindGenericPassword.argtypes = [pointer, integer, pointer, integer, pointer, ctypes.POINTER(integer), ctypes.POINTER(pointer), ctypes.POINTER(pointer)]
        self.security.SecKeychainAddGenericPassword.argtypes = [pointer, integer, pointer, integer, pointer, integer, pointer, ctypes.POINTER(pointer)]
        self.security.SecKeychainItemModifyAttributesAndData.argtypes = [pointer, pointer, integer, pointer]
        self.security.SecKeychainItemDelete.argtypes = [pointer]
        self.security.SecKeychainItemFreeContent.argtypes = [pointer, pointer]
        self.foundation.CFRelease.argtypes = [pointer]
        self.foundation.CFRelease.restype = None

    def _find(self):
        length, content, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(None, len(self.service), self.service, len(self.account), self.account, ctypes.byref(length), ctypes.byref(content), ctypes.byref(item))
        if status == -25300:
            return None, None
        if status:
            raise StorageError("macOS Keychain is unavailable; unlock it for this OS user.")
        try:
            value = json.loads(ctypes.string_at(content, length.value))
        except ValueError:
            self.foundation.CFRelease(item)
            raise StorageError("The Hoomy Keychain item is damaged.") from None
        finally:
            self.security.SecKeychainItemFreeContent(None, content)
        return value, item

    def read(self):
        value, item = self._find()
        if item:
            self.foundation.CFRelease(item)
        return value

    def write(self, value):
        payload = json.dumps(value, separators=(",", ":")).encode()
        _, item = self._find()
        if item:
            try:
                status = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(payload), payload)
            finally:
                self.foundation.CFRelease(item)
        else:
            item = ctypes.c_void_p()
            status = self.security.SecKeychainAddGenericPassword(None, len(self.service), self.service, len(self.account), self.account, len(payload), payload, ctypes.byref(item))
            if item:
                self.foundation.CFRelease(item)
        if status:
            raise StorageError("Cannot persist credentials in macOS Keychain.")

    def delete(self):
        _, item = self._find()
        if item:
            try:
                status = self.security.SecKeychainItemDelete(item)
            finally:
                self.foundation.CFRelease(item)
            if status:
                raise StorageError("Cannot remove the Hoomy Keychain item.")


class State:
    def __init__(self, root, server, credential_backend=None):
        self.root = private_directory(root)
        self.profile_id = hashlib.sha256(server.encode()).hexdigest()[:32]
        self.path = private_directory(self.root / "profiles" / self.profile_id)
        self.server = server
        self.backend = credential_backend
        if self.backend is None and sys.platform == "darwin":
            identity = hashlib.sha256((str(self.root.resolve()) + "\n" + server).encode()).hexdigest()
            self.backend = Keychain("hoomy-" + identity + "-" + str(os.getuid()))

    @contextlib.contextmanager
    def locked(self):
        descriptor = os.open(self.path / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise StorageError("Unsafe credential lock permissions.")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def config(self):
        return read_private(self.path / "config.json") or {}

    def save_config(self, value):
        atomic_write(self.path / "config.json", value)

    def installation_id(self):
        path = self.root / "installation.json"
        descriptor = os.open(self.root / "installation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise StorageError("Unsafe installation lock permissions.")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            value = read_private(path)
            if value is None:
                value = {"installation_id": str(uuid.uuid4())}
                atomic_write(path, value)
            return value["installation_id"]
        finally:
            os.close(descriptor)

    def credentials(self):
        return self.backend.read() if self.backend else read_private(self.path / "credentials.json")

    def save_credentials(self, value):
        if self.backend:
            self.backend.write(value)
        else:
            atomic_write(self.path / "credentials.json", value)

    def clear_credentials(self):
        if self.backend:
            self.backend.delete()
        else:
            (self.path / "credentials.json").unlink(missing_ok=True)


def default_state_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hoomy" / "agent"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "hoomy" / "agent"
