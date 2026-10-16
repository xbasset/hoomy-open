"""Reviewable system-service preparation and isolated privileged-helper behavior."""

import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from hoomy_agent import service
from hoomy_agent.client import AgentError
from hoomy_agent.storage import State
from hoomy_agent.__main__ import main


class SystemServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        base = self.root / "installed"
        self.release = base / "releases" / ("0.1.4-" + "a" * 64)
        (self.release / "agent/hoomy_agent").mkdir(parents=True)
        (self.release / "vendor").mkdir()
        (self.release / "agent/run.py").write_text("# Fixture executable entry.\n")
        self.python = base / "runtimes/python-fixture/python/bin/python3"
        self.python.parent.mkdir(parents=True)
        self.python.write_text("# Fixture executable path.\n")
        self.state = State(self.root / "state", "https://server.example", credential_backend=False)
        self.state.save_config({"device_id": "fixture-device", "policy_revision": 1})
        self.state.save_credentials({"access_token": "fixture-access", "refresh_token": "fixture-refresh", "state": "authorized"})
        identity = SimpleNamespace(pw_name="fixture-user", pw_gid=1001, pw_uid=1001, pw_dir=str(self.root / "home"))
        group = SimpleNamespace(gr_name="fixture-group")
        patches = [mock.patch.object(service.sys, "platform", "linux"),
                   mock.patch.object(service.sys, "executable", str(self.python)),
                   mock.patch.object(service, "__file__", str(self.release / "agent/hoomy_agent/service.py")),
                   mock.patch.object(service.pwd, "getpwuid", return_value=identity),
                   mock.patch.object(service.grp, "getgrgid", return_value=group)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_default_prepares_only_its_unit_without_os_calls_or_credential_changes(self):
        before_credentials = (self.state.path / "credentials.json").read_bytes()
        before_config = (self.state.path / "config.json").read_bytes()
        runner = mock.Mock()
        info = service.install_service(self.state, runner=runner)
        runner.assert_not_called()
        self.assertTrue(info["installation_required"])
        self.assertEqual(info["runtime_user"], "fixture-user")
        self.assertEqual(Path(info["prepared_unit"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(info["installation_helper"]).stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state.path / "credentials.json").read_bytes(), before_credentials)
        self.assertEqual((self.state.path / "config.json").read_bytes(), before_config)
        unit = Path(info["prepared_unit"]).read_text()
        self.assertIn("User=fixture-user\nGroup=fixture-group", unit)
        self.assertIn("Type=exec", unit)
        self.assertIn("ProtectHome=tmpfs", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=\nAmbientCapabilities=", unit)
        self.assertIn(service._bind_path(self.release), unit)
        self.assertIn(service._bind_path(self.python.parent.parent), unit)
        self.assertIn("BindPaths=" + service._bind_path(self.state.root), unit)
        self.assertNotIn("fixture-access", unit)
        self.assertNotIn("fixture-refresh", unit)
        helper = Path(info["installation_helper"]).read_text()
        self.assertIn("systemctl enable " + info["unit_name"], helper)
        self.assertEqual(helper.count("systemctl restart "), 1)
        for forbidden in ("--now", "loginctl", "disable ", "reset-failed", "reboot", "docker "):
            self.assertNotIn(forbidden, helper)

    def test_custom_paths_disable_exec_environment_expansion_without_changing_bind_paths(self):
        self.state = State(self.root / "space ${HOME} 50%", "https://server.example", credential_backend=False)
        unit = service.systemd_system_unit(self.state)
        self.assertIn('ExecStart=:"', unit)
        self.assertIn("${HOME}", unit)
        self.assertNotIn("$${HOME}", unit)
        self.assertIn("50%%", unit)
        self.assertIn("BindPaths=" + service._bind_path(self.state.root), unit)
        with self.assertRaises(AgentError):
            service._bind_path(self.root / "bad:destination")
        with self.assertRaises(AgentError):
            service._bind_path(self.root / "bad\npath")

    def test_history_bindings_require_ingestion_and_expose_only_approved_roots(self):
        history = self.root / "home/.codex/sessions"
        history.mkdir(parents=True)
        policy = {"content_tier": "conversation_visible_v1", "collection_enabled": True, "roots": [str(history)], "exclusions": [],
                  "project_bindings": [{"project_id": "project-alpha", "cwd_roots": [str(self.root / "work/alpha")]}]}
        config = self.state.config()
        config.update({"collection_enabled": True, "approved_policy": policy})
        self.state.save_config(config)
        with self.assertRaises(AgentError):
            service.systemd_system_unit(self.state)
        credentials = self.state.credentials()
        credentials["scope"] = "device:self evidence:ingest"
        self.state.save_credentials(credentials)
        unit = service.systemd_system_unit(self.state)
        bindings = next(line for line in unit.splitlines() if line.startswith("BindReadOnlyPaths="))
        self.assertIn(service._bind_path(history), bindings)
        self.assertNotIn(service._bind_path(history.parent) + " ", bindings)
        self.assertNotIn("auth.json", unit)
        history.rmdir()
        history.symlink_to(self.root / "work", target_is_directory=True)
        with self.assertRaises(AgentError):
            service.systemd_system_unit(self.state)

    def test_relative_state_paths_are_absolute_in_runtime_and_review_commands(self):
        previous = Path.cwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, previous)
        state = State("relative-state", "https://server.example", credential_backend=False)
        arguments = service.service_arguments(state)
        self.assertEqual(arguments[arguments.index("--state-dir") + 1], str(self.root / "relative-state"))
        info = service.prepare_system_service(state)
        self.assertTrue(Path(info["prepared_unit"]).is_absolute())
        self.assertTrue(Path(info["installation_helper"]).is_absolute())
        self.assertIn(str(self.root / "relative-state"), info["install_command"])
        self.assertIn(str(self.root / "relative-state"), info["verify_command"])
        self.assertIn("WorkingDirectory=/\n", Path(info["prepared_unit"]).read_text())
        self.assertIn(service._systemd_quote(str(self.root / "relative-state")), Path(info["prepared_unit"]).read_text())

    def test_system_service_refuses_source_checkout_and_root_identity(self):
        with mock.patch.object(service, "__file__", str(self.root / "source/agent/hoomy_agent/service.py")):
            with self.assertRaisesRegex(AgentError, "installed signed release"):
                service.prepare_system_service(self.state)
        with mock.patch.object(service.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="root", pw_uid=0, pw_gid=0)):
            with self.assertRaisesRegex(AgentError, "non-root"):
                service.systemd_system_unit(self.state)

    def test_linger_is_only_accepted_with_explicit_compatibility_mode(self):
        runner = mock.Mock()
        with self.assertRaisesRegex(AgentError, "explicit --user-service"):
            service.install_service(self.state, enable_linger=True, runner=runner)
        runner.assert_not_called()
        with mock.patch("hoomy_agent.__main__.State") as state, contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main(["service", "install", "--enable-linger"]), 1)
        state.assert_not_called()
        self.assertIn("explicit --user-service", output.getvalue())

    def test_existing_user_unit_requires_explicit_migration(self):
        compatibility = self.root / "home/.config/systemd/user" / ("hoomy-agent-" + self.state.profile_id + ".service")
        compatibility.parent.mkdir(parents=True)
        compatibility.write_text("Existing user supervisor\n")
        with self.assertRaisesRegex(AgentError, "explicitly migrated"):
            service.prepare_system_service(self.state)
        self.assertEqual(compatibility.read_text(), "Existing user supervisor\n")
        self.assertFalse((self.state.root / "system-service").exists())

    def test_cli_prepared_state_returns_two_without_heartbeat_or_active_claim(self):
        for operation in ("prepare", "install"):
            with self.subTest(operation=operation), mock.patch("hoomy_agent.__main__.State", return_value=self.state), mock.patch("hoomy_agent.__main__.HTTP"), mock.patch("hoomy_agent.__main__.Agent") as agent, contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["--server", "https://server.example", "service", operation]), 2)
                agent.return_value.heartbeat.assert_not_called()
                agent.return_value.login.assert_not_called()
                if operation == "prepare":
                    agent.return_value.status.assert_not_called()
                else:
                    agent.return_value.status.assert_called_once()
            self.assertIn("no privileged operation", output.getvalue())
            self.assertNotIn("Supervised service active", output.getvalue())
            self.assertEqual(self.state.config()["device_id"], "fixture-device")

    def fixture_helper(self, info):
        binaries = self.root / "binaries"
        binaries.mkdir()
        commands = self.root / "commands.jsonl"
        identity = binaries / "id"
        identity.write_text("#!/bin/sh\nprintf '0\\n'\n")
        identity.chmod(0o700)
        install = binaries / "install"
        install.write_text("#!" + str(Path(sys.__dict__.get('_base_executable', '/usr/bin/python3')).resolve()) + "\nimport pathlib,shutil,sys\nshutil.copyfile(sys.argv[-2],sys.argv[-1]);pathlib.Path(sys.argv[-1]).chmod(0o644)\n")
        install.chmod(0o700)
        digest = binaries / "sha256sum"
        digest.write_text("#!" + str(Path(sys.__dict__.get('_base_executable', '/usr/bin/python3')).resolve()) + "\nimport hashlib,pathlib,sys\nprint(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()+'  '+sys.argv[1])\n")
        digest.chmod(0o700)
        systemctl = binaries / "systemctl"
        systemctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> " + service.shlex.quote(str(commands)) + "\n")
        systemctl.chmod(0o700)
        destination = self.root / "etc-systemd-system"
        destination.mkdir()
        script = Path(info["installation_helper"]).read_text().replace("/etc/systemd/system", str(destination))
        for command, replacement in (("id", identity), ("install", install), ("sha256sum", digest), ("systemctl", systemctl), ("mktemp", shutil.which("mktemp")), ("mv", shutil.which("mv")), ("rm", shutil.which("rm"))):
            script = script.replace("/usr/bin/" + command, str(replacement))
        helper = self.root / "fixture-helper.sh"
        helper.write_text(script)
        return helper, destination, commands

    def test_helper_verifies_root_copy_and_changes_exactly_one_named_unit(self):
        info = service.prepare_system_service(self.state)
        helper, destination, commands = self.fixture_helper(info)
        unrelated = destination / "unrelated.service"
        unrelated.write_text("Unrelated existing service\n")
        result = subprocess.run(["/bin/sh", str(helper)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination / info["unit_name"]).read_text(), Path(info["prepared_unit"]).read_text())
        self.assertEqual(unrelated.read_text(), "Unrelated existing service\n")
        self.assertEqual(commands.read_text().splitlines(), ["daemon-reload", "enable " + info["unit_name"], "restart " + info["unit_name"], "is-active --quiet " + info["unit_name"]])

    def test_helper_rejects_changed_candidate_without_os_service_calls(self):
        info = service.prepare_system_service(self.state)
        helper, destination, commands = self.fixture_helper(info)
        with Path(info["prepared_unit"]).open("a") as stream:
            stream.write("User=root\n")
        result = subprocess.run(["/bin/sh", str(helper)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prepared unit changed", result.stderr)
        self.assertFalse((destination / info["unit_name"]).exists())
        self.assertFalse(commands.exists())

    def test_helper_refuses_symlinked_installed_unit(self):
        info = service.prepare_system_service(self.state)
        helper, destination, commands = self.fixture_helper(info)
        unrelated = self.root / "other-unit"
        unrelated.write_text("Protected unrelated file\n")
        (destination / info["unit_name"]).symlink_to(unrelated)
        result = subprocess.run(["/bin/sh", str(helper)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked system unit", result.stderr)
        self.assertEqual(unrelated.read_text(), "Protected unrelated file\n")
        self.assertFalse(commands.exists())


if __name__ == "__main__":
    unittest.main()
