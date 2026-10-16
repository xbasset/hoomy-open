"""OS supervisors; installation is separate from authenticated login."""

import os
import grp
import hashlib
from pathlib import Path
import plistlib
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
import time

from .client import AgentError
from .storage import private_directory


def _systemd_quote(value):
    if "\n" in value or "\r" in value or "\x00" in value:
        raise AgentError("Invalid service argument.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_arguments(state):
    entry = Path(__file__).resolve().parents[1] / "run.py"
    arguments = [sys.executable, "-I", str(entry), "--server", state.server, "--state-dir", str(state.root.resolve())]
    if state.config().get("ca_file"):
        arguments += ["--ca-file", str(Path(state.config()["ca_file"]).resolve())]
    return arguments + ["run"]


def systemd_unit(state):
    arguments = " ".join(_systemd_quote(value) for value in service_arguments(state))
    return f"""[Unit]
Description=Hoomy device connection (heartbeat only)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=:{arguments}
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=3
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def _bind_path(path):
    value = str(Path(path).resolve())
    if ":" in value:
        raise AgentError("System-service bind paths cannot contain colons.")
    return _systemd_quote(value)


def _system_identity():
    record = pwd.getpwuid(os.getuid())
    group = grp.getgrgid(record.pw_gid).gr_name
    for value in (record.pw_name, group):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", value):
            raise AgentError("Unsupported OS identity for a system service.")
    if record.pw_uid == 0 or record.pw_gid == 0:
        raise AgentError("The Hoomy system service must use a non-root OS identity.")
    return record.pw_name, group


def system_service_name(state):
    username, _ = _system_identity()
    return "hoomy-agent-" + username + "-" + state.profile_id + ".service"


def systemd_system_unit(state):
    username, group = _system_identity()
    entry = Path(__file__).resolve().parents[1] / "run.py"
    release = entry.parent.parent
    runtime = Path(sys.executable).resolve().parent.parent
    if (release.parent.name != "releases" or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-[a-f0-9]{64}", release.name)
            or not (release / "vendor").is_dir() or not runtime.is_relative_to(release.parent.parent / "runtimes")):
        raise AgentError("Prepare the system service using the installed signed release and standalone runtime.")
    command = service_arguments(state)
    command[0] = str(Path(sys.executable).resolve())
    arguments = " ".join(_systemd_quote(value) for value in command)
    bindings = [_bind_path(runtime), _bind_path(release)]
    config = state.config()
    credentials = state.credentials() or {}
    if config.get("collection_enabled"):
        from .collector import open_directory
        from .policy import INGEST_SCOPES, approved_policy
        if credentials.get("state") != "authorized" or set(credentials.get("scope", "device:self").split()) != INGEST_SCOPES:
            raise AgentError("Approved ingestion credentials are required for history bindings.")
        policy = approved_policy(config.get("approved_policy"))
        for root in policy["roots"]:
            descriptor = open_directory(root)
            os.close(descriptor)
            bindings.append(_bind_path(root))
    if state.config().get("ca_file"):
        bindings.append(_bind_path(state.config()["ca_file"]))
    writable = _bind_path(state.root)
    return f"""[Unit]
Description=Hoomy device connection and approved collection
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User={username}
Group={group}
WorkingDirectory=/
ExecStart=:{arguments}
Restart=on-failure
RestartSec=30
RestartPreventExitStatus=3
UMask=0077
NoNewPrivileges=true
CapabilityBoundingSet=
AmbientCapabilities=
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=tmpfs
BindReadOnlyPaths={" ".join(bindings)}
BindPaths={writable}
ReadWritePaths={writable}
InaccessiblePaths=-/run/docker.sock -/run/containerd/containerd.sock -/run/crio/crio.sock -/run/podman/podman.sock
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictNamespaces=true
LockPersonality=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _write_prepared(path, content, mode=0o600):
    if path.is_symlink():
        raise AgentError("Refusing a symlinked service preparation path.")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_system_service(state):
    if os.getuid() == 0 or not sys.platform.startswith("linux"):
        raise AgentError("Prepare the Linux system service as its non-root OS user.")
    username, _ = _system_identity()
    name = system_service_name(state)
    compatibility_unit = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".config/systemd/user" / ("hoomy-agent-" + state.profile_id + ".service")
    if compatibility_unit.exists() or compatibility_unit.is_symlink():
        raise AgentError("An existing Hoomy user unit must be explicitly migrated before preparing its system service.")
    directory = private_directory(state.root.resolve() / "system-service")
    unit = directory / name
    content = systemd_system_unit(state)
    checksum = hashlib.sha256(content.encode()).hexdigest()
    helper = directory / ("install-" + name + ".sh")
    target = "/etc/systemd/system/" + name
    quoted_unit, quoted_name, quoted_target = (shlex.quote(str(value)) for value in (unit, name, target))
    script = f"""#!/bin/sh
set -eu
[ "$(/usr/bin/id -u)" -eq 0 ] || {{ echo 'Run this reviewed helper with sudo.' >&2; exit 1; }}
[ ! -e {shlex.quote(str(compatibility_unit))} ] && [ ! -L {shlex.quote(str(compatibility_unit))} ] || {{ echo 'Migrate the existing Hoomy user unit first.' >&2; exit 1; }}
temporary=$(/usr/bin/mktemp -d /etc/systemd/system/.hoomy-agent-install.XXXXXX)
trap '/usr/bin/rm -rf -- "$temporary"' EXIT HUP INT TERM
# Verify the root-owned copy before replacing the one named unit.
/usr/bin/install -o root -g root -m 0644 -- {quoted_unit} "$temporary/unit"
actual=$(/usr/bin/sha256sum "$temporary/unit")
[ "${{actual%% *}}" = '{checksum}' ] || {{ echo 'Prepared unit changed; no service was installed.' >&2; exit 1; }}
[ ! -L {quoted_target} ] || {{ echo 'Refusing a symlinked system unit.' >&2; exit 1; }}
/usr/bin/mv -f -- "$temporary/unit" {quoted_target}
/usr/bin/systemctl daemon-reload
/usr/bin/systemctl enable {quoted_name}
/usr/bin/systemctl restart {quoted_name}
/usr/bin/systemctl is-active --quiet {quoted_name}
echo 'Dedicated Hoomy system service is active; verify its authenticated heartbeat.'
"""
    _write_prepared(unit, content)
    _write_prepared(helper, script, 0o700)
    return {"supervisor": "systemd-system", "unit": target, "unit_name": name,
            "runtime_user": username, "prepared_unit": str(unit), "installation_helper": str(helper),
            "install_command": shlex.join(["sudo", "/bin/sh", str(helper)]),
            "verify_command": shlex.join(service_arguments(state)[:-1] + ["service", "install"]),
            "installation_required": True, "survives_ssh_logout": True}


def launchagent_definition(state):
    return {
        "Label": "dev.hoomy.agent." + state.profile_id,
        "ProgramArguments": service_arguments(state),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "Umask": 0o077,
    }


def _run(arguments):
    if arguments[0] == "sudo":
        manual_command = shlex.join(arguments)
        try:
            incoming = open("/dev/tty", "r")
        except OSError:
            incoming = None
        if incoming is not None:
            with incoming:
                try:
                    outgoing = open("/dev/tty", "w")
                except OSError:
                    outgoing = None
                if outgoing is not None:
                    with outgoing:
                        # OS authorization stays on the terminal, outside captured logs.
                        result = subprocess.run(arguments, stdin=incoming, stdout=outgoing, stderr=outgoing)
                    if result.returncode == 0:
                        return ""
                    raise AgentError("OS authorization was not completed. Run this command yourself, then retry service install: " + manual_command)
        result = subprocess.run([arguments[0], "-n", *arguments[1:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode:
            raise AgentError("No interactive terminal is available for OS authorization. Run this command yourself, then retry service install: " + manual_command)
        return result.stdout.strip()
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise AgentError("OS service command failed: " + arguments[0] + ". Check this user's service manager.")
    return result.stdout.strip()


def _bootstrap_mac(runner, domain, path, sleep):
    command = ["launchctl", "bootstrap", domain, str(path)]
    for delay in (0, 1, 2, 4, 8):
        if delay:
            sleep(delay)
        try:
            runner(command)
            return
        except AgentError:
            continue
    raise AgentError("Mac service bootstrap failed after bounded retries; active state was not assumed. "
                     "The reviewed plist remains installed. Run this command in the logged-in Mac session, then retry service install: "
                     + shlex.join(command))


def install_service(state, enable_linger=False, runner=_run, home=None, platform=None, user_service=False, sleep=time.sleep):
    if os.getuid() == 0:
        raise AgentError("Install as the OS user who runs Codex, not root.")
    home = Path(home or Path.home())
    platform = platform or sys.platform
    if platform == "darwin":
        directory = home / "Library" / "LaunchAgents"
        directory.mkdir(parents=True, exist_ok=True)
        definition = launchagent_definition(state)
        path = directory / (definition["Label"] + ".plist")
        if path.is_symlink():
            raise AgentError("Refusing to replace a symlinked service definition.")
        # launchctl reads public configuration, never credentials.
        temporary = path.with_suffix(".plist.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            plistlib.dump(definition, stream)
        os.replace(temporary, path)
        domain = "gui/" + str(os.getuid())
        try:
            runner(["launchctl", "bootout", domain + "/" + definition["Label"]])
        except AgentError:
            pass
        runner(["launchctl", "enable", domain + "/" + definition["Label"]])
        # launchd may still be releasing the old label immediately after bootout.
        _bootstrap_mac(runner, domain, path, sleep)
        runner(["launchctl", "kickstart", domain + "/" + definition["Label"]])
        return {"supervisor": "launchd", "unit": str(path), "survives_ssh_logout": True, "requires_logged_in_os_user": True}
    if platform.startswith("linux"):
        if enable_linger and not user_service:
            raise AgentError("--enable-linger requires explicit --user-service compatibility mode.")
        if not user_service:
            info = prepare_system_service(state)
            installed = Path(info["unit"])
            if not installed.is_symlink() and installed.is_file() and installed.read_text() == Path(info["prepared_unit"]).read_text():
                try:
                    runner(["systemctl", "is-enabled", "--quiet", info["unit_name"]])
                    runner(["systemctl", "is-active", "--quiet", info["unit_name"]])
                    info["installation_required"] = False
                except AgentError:
                    pass
            return info
        uid = str(os.getuid())
        lingering = runner(["loginctl", "show-user", uid, "--property=Linger", "--value"])
        if lingering != "yes":
            if not enable_linger:
                raise AgentError("Linux requires user lingering to survive SSH logout. Re-run service install --enable-linger; sudo may ask for OS authorization.")
            username = pwd.getpwuid(os.getuid()).pw_name
            runner(["sudo", "loginctl", "enable-linger", username])
            if runner(["loginctl", "show-user", uid, "--property=Linger", "--value"]) != "yes":
                raise AgentError("User lingering is still disabled; service was not reported as installed.")
        directory = home / ".config" / "systemd" / "user"
        directory.mkdir(parents=True, exist_ok=True)
        name = "hoomy-agent-" + state.profile_id + ".service"
        path = directory / name
        if path.is_symlink():
            raise AgentError("Refusing to replace a symlinked service definition.")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(systemd_unit(state))
        runner(["systemctl", "--user", "daemon-reload"])
        runner(["systemctl", "--user", "enable", name])
        runner(["systemctl", "--user", "restart", name])
        runner(["systemctl", "--user", "is-active", "--quiet", name])
        return {"supervisor": "systemd", "unit": str(path), "survives_ssh_logout": True}
    raise AgentError("Automatic service installation supports macOS and Linux with systemd.")
