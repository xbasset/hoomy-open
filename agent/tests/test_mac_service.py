"""Bounded same-service recovery for a transient launchd upgrade handoff."""

from pathlib import Path
import plistlib
import tempfile
import unittest

from hoomy_agent.client import AgentError
from hoomy_agent.service import install_service
from hoomy_agent.storage import State


class MacServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.state = State(self.home / "state", "https://server.example", credential_backend=False)
        self.calls, self.delays = [], []

    def test_transient_bootstrap_retries_same_plist_then_starts_once(self):
        failures = [2]
        def runner(command):
            self.calls.append(command)
            if command[1] == "bootstrap" and failures[0]:
                failures[0] -= 1
                raise AgentError("Simulated transient handoff")
            return ""
        info = install_service(self.state, runner=runner, home=self.home, platform="darwin", sleep=self.delays.append)
        attempts = [command for command in self.calls if command[1] == "bootstrap"]
        self.assertEqual(len(attempts), 3)
        self.assertTrue(all(command == attempts[0] for command in attempts))
        self.assertEqual(self.delays, [1, 2])
        self.assertEqual([command[1] for command in self.calls], ["bootout", "enable", "bootstrap", "bootstrap", "bootstrap", "kickstart"])
        self.assertEqual(info["supervisor"], "launchd")
        self.assertEqual(plistlib.loads(Path(info["unit"]).read_bytes())["Label"], "dev.hoomy.agent." + self.state.profile_id)

    def test_permanent_bootstrap_failure_is_bounded_and_preserves_reviewed_plist(self):
        def runner(command):
            self.calls.append(command)
            if command[1] == "bootstrap":
                raise AgentError("Simulated permanent refusal")
            return ""
        with self.assertRaisesRegex(AgentError, "active state was not assumed") as failure:
            install_service(self.state, runner=runner, home=self.home, platform="darwin", sleep=self.delays.append)
        self.assertEqual(self.delays, [1, 2, 4, 8])
        self.assertEqual(len([command for command in self.calls if command[1] == "bootstrap"]), 5)
        self.assertFalse(any(command[1] == "kickstart" for command in self.calls))
        path = self.home / "Library/LaunchAgents" / ("dev.hoomy.agent." + self.state.profile_id + ".plist")
        self.assertTrue(path.is_file())
        self.assertIn("launchctl bootstrap gui/", str(failure.exception))
        self.assertNotIn("rollback", str(failure.exception))

    def test_enable_failure_prevents_bootstrap_or_false_active_result(self):
        def runner(command):
            self.calls.append(command)
            if command[1] == "enable":
                raise AgentError("Simulated OS refusal")
            return ""
        with self.assertRaises(AgentError):
            install_service(self.state, runner=runner, home=self.home, platform="darwin", sleep=self.delays.append)
        self.assertEqual([command[1] for command in self.calls], ["bootout", "enable"])
        self.assertEqual(self.delays, [])

    def test_missing_prior_service_still_bootstraps_without_delay(self):
        def runner(command):
            self.calls.append(command)
            if command[1] == "bootout":
                raise AgentError("Simulated service not found")
            return ""
        install_service(self.state, runner=runner, home=self.home, platform="darwin", sleep=self.delays.append)
        self.assertEqual(self.delays, [])
        self.assertEqual([command[1] for command in self.calls], ["bootout", "enable", "bootstrap", "kickstart"])


if __name__ == "__main__":
    unittest.main()
