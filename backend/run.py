#!/usr/bin/env python3
"""Launch, stop, inspect, or reset only this private Hoomy runtime."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from hoomy.edge import BackendError, Client
from hoomy.inference import InferenceError, check_inference as live_inference_check

ROOT = Path(__file__).resolve().parent
RUNTIME = Path.home() / "Library/Application Support/HoomyPilot"
APP_ROOT = ROOT / "apps/HoomyPilot"
APP_BUNDLE = APP_ROOT / ".build/Hoomy Pilot.app"
API = Client("http://127.0.0.1:8765")


def call(*args, quiet=False):
    subprocess.run(args, cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL if quiet else None)


def bundle():
    call("swift", "build", "--package-path", str(APP_ROOT), "-c", "debug")
    result = subprocess.run(["swift", "build", "--package-path", str(APP_ROOT), "-c", "debug", "--show-bin-path"], check=True, capture_output=True, text=True)
    executable = Path(result.stdout.strip()) / "HoomyPilot"
    destination = APP_BUNDLE / "Contents/MacOS"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, destination / "HoomyPilot")
    with (APP_BUNDLE / "Contents/Info.plist").open("wb") as target:
        plistlib.dump({"CFBundleExecutable": "HoomyPilot", "CFBundleIdentifier": "dev.hoomy.private-pilot",
                      "CFBundleName": "Hoomy Pilot", "CFBundleDisplayName": "Hoomy Pilot",
                      "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "0.1.0",
                      "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
                      "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}, target)
    call("codesign", "--force", "--deep", "--sign", "-", str(APP_BUNDLE))


def edge_pid():
    try:
        pid = int((RUNTIME / "edge.pid").read_text().strip())
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
        if "-m hoomy.edge" in command and str(RUNTIME) in command:
            return pid
    except (OSError, ValueError):
        pass
    return None


def refuse_archive_start():
    try:
        authority = API.get("/api/health").get("authority")
    except BackendError:
        return
    if authority:
        raise BackendError("Hoomy local is a central read-only archive. Use Hoomy Remote Acceptance; the local app and discovery worker will not start.")


def start(no_open=False):
    refuse_archive_start()
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = edge_pid()
    if previous:
        try:
            API.post("/api/control", {"running": False})
        except BackendError:
            pass
        os.killpg(previous, signal.SIGTERM)
        for _ in range(20):
            if not edge_pid():
                break
            time.sleep(0.1)
    # A local alias avoids an unnecessary registry credential lookup once the
    # official base is present. Pulling remains explicit and easy to diagnose.
    present = subprocess.run(["docker", "image", "inspect", "python:3.12-slim"],
                             cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not present:
        call("docker", "pull", "python:3.12-slim")
    call("docker", "tag", "python:3.12-slim", "hoomy-pilot-base:local")
    call("docker", "build", "--pull=false", "--build-arg", "BASE_IMAGE=hoomy-pilot-base:local",
         "-t", "hoomy-private-pilot:local", ".")
    call("docker", "compose", "up", "--no-build", "-d", "--wait")
    # A previously stopped backend can reveal its persisted archive flag only now.
    refuse_archive_start()
    bundle()
    if not edge_pid():
        with (RUNTIME / "edge.log").open("ab") as log:
            agent_python = ROOT / ".venv-agent/bin/python"
            process = subprocess.Popen([str(agent_python) if agent_python.exists() else sys.executable, "-m", "hoomy.edge", "--state-dir", str(RUNTIME)],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       start_new_session=True)
        (RUNTIME / "edge.pid").write_text(str(process.pid))
    # Launching never grants discovery consent or resumes a paused run.
    for _ in range(20):
        state = API.get("/api/state")
        if state.get("worker", {}).get("last_seen"):
            break
        time.sleep(0.25)
    if not no_open:
        call("open", str(APP_BUNDLE))
    print("Hoomy is ready. Confirm scope in the app to start discovery.")
    print(APP_BUNDLE)


def stop():
    try:
        API.post("/api/control", {"running": False})
    except BackendError:
        pass
    pid = edge_pid()
    if pid:
        os.killpg(pid, signal.SIGTERM)
    call("docker", "compose", "stop")
    print("Hoomy stopped. Stored understanding is preserved.")


def reset():
    print("Deletes only Hoomy snapshots, briefs, projects, corrections and reviews.")
    print("Original Codex conversations are not changed. This cannot be undone.")
    if input("Type RESET HOOMY to continue: ").strip() != "RESET HOOMY":
        print("Cancelled.")
        return
    API.post("/api/reset", {"confirm": "DELETE_HOOMY_DATA"})
    print("Hoomy data cleared. Confirm scope again in the app to start fresh.")


def check_inference():
    print("This sends one fixed, content-free protocol check to OpenAI through Codex.")
    print("It reads no conversations or projects and consumes a small amount of your subscription allowance.")
    if input("Type CHECK CODEX INFERENCE to continue: ").strip() != "CHECK CODEX INFERENCE":
        print("Cancelled.")
        return
    result = live_inference_check()
    print(json.dumps(result, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status", "reset", "build-app", "check-inference"))
    parser.add_argument("--no-open", action="store_true", help="Prepare services and app without opening its window.")
    args = parser.parse_args()
    try:
        if args.command == "start":
            start(args.no_open)
        elif args.command == "stop":
            stop()
        elif args.command == "reset":
            reset()
        elif args.command == "build-app":
            bundle()
        elif args.command == "check-inference":
            check_inference()
        else:
            state = API.get("/api/state")
            print(json.dumps({key: state[key] for key in ("control", "coverage", "worker")}, indent=2))
    except (BackendError, InferenceError, subprocess.CalledProcessError, OSError) as exc:
        raise SystemExit(f"Hoomy could not complete this step: {exc}") from exc


if __name__ == "__main__":
    main()
