import argparse
import contextlib
import json
import os
import signal
import socket
import sys
import time
import webbrowser

from . import __version__
from .client import Agent, AgentError, DEFAULT_SERVER, HTTP, origin
from .service import install_service, prepare_system_service
from .storage import State, StorageError, default_state_directory


@contextlib.contextmanager
def terminal():
    # Update-mode text streams require seeking, which a Linux TTY cannot do.
    with contextlib.ExitStack() as stack:
        try:
            reader = stack.enter_context(open("/dev/tty", "r"))
            writer = stack.enter_context(open("/dev/tty", "w"))
        except OSError:
            raise AgentError("Login requires a controlling terminal. Run from an interactive shell or SSH session.") from None
        class Terminal:
            def readline(self):
                return reader.readline()

            def write(self, value):
                return writer.write(value)

            def flush(self):
                return writer.flush()

            def isatty(self):
                return writer.isatty()
        yield Terminal()


def prompt(stream, message, default=None):
    stream.write(message + (" [" + str(default) + "]" if default is not None else "") + ": ")
    stream.flush()
    value = stream.readline()
    if not value:
        raise AgentError("Installation cancelled; no approval was assumed.")
    return value.strip() or default


def verification(stream, mode):
    def display(complete_uri, uri, code, duration):
        stream.write(f"\nOpen: {uri}\nConfirmation code: {code}\nExpires in: {duration // 60} minutes\n")
        try:
            import qrcode
            qr = qrcode.QRCode(border=4)
            qr.add_data(complete_uri)
            qr.make(fit=True)
            qr.print_ascii(out=stream, invert=True)
        except ImportError:
            stream.write("QR renderer is missing; open the URL and enter the confirmation code.\n")
        stream.flush()
        if mode == "browser" or (mode == "auto" and sys.platform == "darwin"):
            webbrowser.open(complete_uri)
        stream.write("Check this code on the approval page. Waiting for explicit machine approval…\n")
        stream.flush()
    return display


def service_preparation(stream, info):
    stream.write("Dedicated system-service files prepared; no privileged operation was executed.\n")
    stream.write("Review unit: " + info["prepared_unit"] + "\n")
    stream.write("Review helper: " + info["installation_helper"] + "\n")
    stream.write("After review, run: " + info["install_command"] + "\n")
    stream.write("Then verify service and authenticated heartbeat: " + info["verify_command"] + "\n")
    stream.flush()


def parser():
    result = argparse.ArgumentParser(description="Hoomy device connection and explicitly approved single-project collection.")
    result.add_argument("--server", help="HTTPS Hoomy origin (default https://my.hoomy.dev)")
    result.add_argument("--state-dir", default=str(default_state_directory()))
    result.add_argument("--ca-file", help="Additional trusted CA bundle for a private/staging HTTPS server")
    result.add_argument("--version", action="version", version=__version__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("install", "login"):
        action = sub.add_parser(name)
        action.add_argument("--auth", choices=("auto", "browser", "terminal"), default="auto")
        action.add_argument("--label")
        if name == "install":
            action.add_argument("--no-start", action="store_true")
            action.add_argument("--enable-linger", action="store_true")
            action.add_argument("--user-service", action="store_true", help="Linux compatibility mode using the OS user manager")
    sub.add_parser("status")
    sub.add_parser("logout")
    collection = sub.add_parser("collection")
    collection.add_argument("operation", choices=("approve", "status"))
    collection.add_argument("--policy-file", help="Reviewed collection policy JSON; never an authentication file")
    collection.add_argument("--auth", choices=("auto", "browser", "terminal"), default="terminal")
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    service = sub.add_parser("service")
    service.add_argument("operation", choices=("install", "prepare"))
    service.add_argument("--enable-linger", action="store_true")
    service.add_argument("--user-service", action="store_true", help="Linux compatibility mode using the OS user manager")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if (sys.platform.startswith("linux") and args.command in ("install", "service")
                and args.enable_linger and not args.user_service):
            raise AgentError("--enable-linger requires explicit --user-service compatibility mode.")
        if args.command in ("install", "login"):
            if os.getuid() == 0:
                raise AgentError("Run the installer as the OS user who runs Codex, not root.")
            with terminal() as stream:
                server = origin(args.server or prompt(stream, "Hoomy server", DEFAULT_SERVER))
                state = State(args.state_dir, server)
                if args.ca_file:
                    config = state.config()
                    config["ca_file"] = os.path.abspath(args.ca_file)
                    state.save_config(config)
                agent = Agent(state, HTTP(server, args.ca_file))
                stream.write(f"Server: {server}\nConnection preserves existing permissions; collection requires separate explicit policy approval.\n")
                label = args.label or prompt(stream, "Machine name", socket.gethostname())
                device = agent.login(verification(stream, args.auth), label)
                stream.write("Machine authorized: " + str(device.get("device_id", "registered")) + "\n")
                if args.command == "install" and not args.no_start:
                    enable_linger = args.enable_linger
                    if sys.platform.startswith("linux") and args.user_service and not enable_linger:
                        enable_linger = str(prompt(stream, "Allow enabling Linux lingering if needed (sudo may prompt)?", "no")).lower() in ("yes", "y")
                    info = install_service(state, enable_linger=enable_linger, user_service=args.user_service)
                    config = state.config()
                    config["service"] = info
                    state.save_config(config)
                    if info.get("installation_required"):
                        service_preparation(stream, info)
                        return 2
                    agent.heartbeat()
                    stream.write("Supervised service active; first heartbeat accepted. Collection follows the separately approved policy.\n")
                else:
                    stream.write("Connected without starting a service. Use hoomy-agent service install when ready.\n")
            return 0
        server = origin(args.server or DEFAULT_SERVER)
        state = State(args.state_dir, server)
        agent = Agent(state, HTTP(server, args.ca_file or state.config().get("ca_file")))
        if args.command == "status":
            device = agent.status()
            # Only explicit safe fields can reach terminal output.
            print(json.dumps({name: device.get(name) for name in ("device_id", "status", "workspace_id", "policy_revision")}, indent=2))
        elif args.command == "logout":
            confirmed = agent.logout()
            print("Local credentials removed. Central revocation " + ("confirmed." if confirmed else "unconfirmed; revoke this machine in Hoomy when reachable."))
        elif args.command == "collection":
            if args.operation == "approve":
                from .collector import _json
                from .policy import approved_policy
                if not args.policy_file:
                    raise AgentError("Choose a reviewed --policy-file before requesting collection approval.")
                from pathlib import Path
                policy_path = Path(args.policy_file).absolute()
                if ".codex" in policy_path.parts or policy_path.name in ("auth.json", "credentials.json"):
                    raise AgentError("Choose a policy file outside Codex configuration and credential paths.")
                descriptor = os.open(args.policy_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    import stat
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        raise AgentError("Collection policy must be a regular JSON file.")
                    content = os.read(descriptor, 65537)
                finally:
                    os.close(descriptor)
                if len(content) > 65536:
                    raise AgentError("Collection policy exceeds the supported size.")
                policy = approved_policy(_json(content))
                with terminal() as stream:
                    stream.write("Proposed collection policy (conversation text only; inference is not requested):\n")
                    stream.write(json.dumps(policy, indent=2, ensure_ascii=False) + "\n")
                    device = agent.login(verification(stream, args.auth), collection_policy=policy)
                    stream.write("Collection policy authorized for machine: " + str(device.get("device_id")) + "\n")
                    stream.write("Review/install the service to expose only approved history roots. No collection ran in this command.\n")
            else:
                device = agent.status()
                config = state.config()
                print(json.dumps({"device_id": device.get("device_id"), "collection_available": device.get("collection_available") is True,
                                  "policy_revision": device.get("policy_revision"), "coverage": config.get("collection_coverage", {})}, indent=2))
        elif args.command == "service":
            if args.operation == "prepare":
                if args.user_service:
                    raise AgentError("Service preparation is for the dedicated Linux system service.")
                info = prepare_system_service(state)
                config = state.config()
                config["service"] = info
                state.save_config(config)
                service_preparation(sys.stdout, info)
                return 2
            agent.status()
            info = install_service(state, enable_linger=args.enable_linger, user_service=args.user_service)
            config = state.config()
            config["service"] = info
            state.save_config(config)
            if info.get("installation_required"):
                service_preparation(sys.stdout, info)
                return 2
            agent.heartbeat()
            print("Supervised service active; first heartbeat accepted. Collection follows the separately approved policy.")
        elif args.command == "run":
            running = True
            def stop(signum, frame):
                nonlocal running
                running = False
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            delay = 60
            while running:
                try:
                    agent.cycle()
                    delay = 60
                except AgentError:
                    with state.locked():
                        credentials = state.credentials()
                    if not credentials or credentials.get("state") != "authorized":
                        print("Hoomy authorization requires attention; service stopped.", file=sys.stderr)
                        if args.once:
                            return 1
                        return 0 if sys.platform == "darwin" else 3
                    print("Hoomy cycle deferred; queued captures retained for bounded retry.", file=sys.stderr)
                    if args.once:
                        return 1
                    delay = min(delay * 2, 300)
                if args.once:
                    return 0
                for _ in range(delay):
                    if not running:
                        break
                    time.sleep(1)
        return 0
    except (AgentError, StorageError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled. No machine approval was assumed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
