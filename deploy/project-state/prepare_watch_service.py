"""Prepare one reviewable Mac LaunchAgent; do not install or start it."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import pwd
import uuid


LABEL_PREFIX = "dev.hoomy.state-executor"


def executable(value):
    path = Path(value).resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("invalid_executable")
    return str(path)


def prepare(repository, python, codex, output, *, project_id):
    if str(uuid.UUID(project_id)) != project_id:
        raise ValueError("invalid_project_id")
    label = LABEL_PREFIX + "." + project_id
    repository = Path(repository).resolve(strict=True)
    if not (repository / "backend" / "hoomy" / "state_executor.py").is_file():
        raise ValueError("invalid_repository")
    python, codex = executable(python), executable(codex)
    output = Path(output).absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("output_must_be_new")
    output.mkdir(mode=0o700)
    account = pwd.getpwuid(os.getuid())
    home = Path(account.pw_dir)
    logs = home / "Library" / "Logs" / "Hoomy" / "state-executor" / project_id
    target = output / (label + ".plist")
    plist = {"Label": label, "ProgramArguments": [python, "-m", "hoomy.state_executor", "watch",
                "--project-id", project_id], "WorkingDirectory": "/private/tmp",
        "EnvironmentVariables": {"PYTHONPATH": str(repository / "backend"), "HOME": str(home),
            "USER": account.pw_name, "LOGNAME": account.pw_name, "HOOMY_CODEX_BIN": codex,
            "PATH": ":".join([str(Path(python).parent), str(Path(codex).parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])},
        "RunAtLoad": True, "KeepAlive": False, "ThrottleInterval": 60,
        "ProcessType": "Background", "ExitTimeOut": 75,
        "StandardOutPath": str(logs / "status.log"), "StandardErrorPath": str(logs / "errors.log")}
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        plistlib.dump(plist, stream)
    return {"status": "prepared", "label": label, "plist": str(target), "log_directory": str(logs),
            "installed": False, "started": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.repository, args.python, args.codex, args.output_directory, project_id=args.project_id)))
        return 0
    except Exception:
        print(json.dumps({"status": "blocked", "failure_code": "watch_service_preparation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
