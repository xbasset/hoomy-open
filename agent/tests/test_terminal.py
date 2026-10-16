"""Real non-seekable terminal prompts with an installer pipe on standard input."""

import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class TerminalTests(unittest.TestCase):
    def fake_sudo(self, directory):
        executable = Path(directory) / "sudo"
        executable.write_text("#!" + sys.executable + "\n" + "\n".join([
            "import sys",
            "if '-n' in sys.argv:",
            "    raise SystemExit(1)",
            "print('Fixture OS authorization: ', end='', file=sys.stderr, flush=True)",
            "if sys.stdin.readline().strip() != 'confirm':",
            "    raise SystemExit(1)",
            "print('FIXTURE_AUTHORIZED', flush=True)",
        ]) + "\n")
        executable.chmod(0o700)

    def test_sudo_prompt_and_response_use_controlling_terminal_with_pipe(self):
        source = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory() as directory:
            self.fake_sudo(directory)
            script = "from hoomy_agent.service import _run; _run(['sudo', 'loginctl', 'enable-linger', 'fixture-user']); print('SERVICE_COMMAND_DONE', flush=True)"
            environment = dict(os.environ, PYTHONPATH=source, PATH=directory + os.pathsep + os.environ["PATH"])
            child, descriptor = pty.fork()
            if child == 0:
                incoming, outgoing = os.pipe()
                os.write(outgoing, b"installer-script-must-not-authorize\n")
                os.close(outgoing)
                os.dup2(incoming, 0)
                os.close(incoming)
                os.execvpe(sys.executable, [sys.executable, "-c", script], environment)
            output = bytearray()
            answered = False
            deadline = time.monotonic() + 10
            try:
                while time.monotonic() < deadline:
                    if select.select([descriptor], [], [], 0.1)[0]:
                        try:
                            chunk = os.read(descriptor, 65536)
                        except OSError:
                            break
                        if not chunk:
                            break
                        output.extend(chunk)
                        if not answered and b"Fixture OS authorization: " in output:
                            os.write(descriptor, b"confirm\n")
                            answered = True
                else:
                    os.kill(child, signal.SIGKILL)
                    self.fail("OS authorization terminal fixture timed out")
            finally:
                os.close(descriptor)
                _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
            self.assertTrue(answered)
            self.assertIn(b"FIXTURE_AUTHORIZED", output)
            self.assertIn(b"SERVICE_COMMAND_DONE", output)

    def test_sudo_without_terminal_fails_fast_with_manual_command(self):
        source = str(Path(__file__).resolve().parents[1])
        with tempfile.TemporaryDirectory() as directory:
            self.fake_sudo(directory)
            script = "\n".join([
                "from hoomy_agent.service import _run, AgentError",
                "try:",
                "    _run(['sudo', 'loginctl', 'enable-linger', 'fixture-user'])",
                "except AgentError as error:",
                "    print(error)",
                "    raise SystemExit(1)",
            ])
            result = subprocess.run([sys.executable, "-c", script], env=dict(os.environ, PYTHONPATH=source, PATH=directory + os.pathsep + os.environ["PATH"]), input="installer-script\n", capture_output=True, text=True, start_new_session=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("No interactive terminal", result.stdout)
            self.assertIn("sudo loginctl enable-linger fixture-user", result.stdout)
            self.assertNotIn("Fixture OS authorization", result.stdout + result.stderr)

    def test_prompts_use_controlling_terminal_with_piped_stdin(self):
        source = str(Path(__file__).resolve().parents[1])
        script = "\n".join([
            "from hoomy_agent.__main__ import terminal, prompt",
            "with terminal() as stream:",
            "    answer = prompt(stream, 'Fixture server', 'default')",
            "    print('ANSWER=' + answer, flush=True)",
        ])
        environment = dict(os.environ, PYTHONPATH=source)
        child, descriptor = pty.fork()
        if child == 0:
            incoming, outgoing = os.pipe()
            os.write(outgoing, b"installer-script-is-not-a-prompt-answer\n")
            os.close(outgoing)
            os.dup2(incoming, 0)
            os.close(incoming)
            os.execvpe(sys.executable, [sys.executable, "-c", script], environment)
        output = bytearray()
        answered = False
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([descriptor], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(descriptor, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                    if not answered and b"Fixture server [default]: " in output:
                        os.write(descriptor, b"chosen-server\n")
                        answered = True
            else:
                os.kill(child, signal.SIGKILL)
                self.fail("Terminal fixture timed out")
        finally:
            os.close(descriptor)
            _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
        self.assertIn(b"ANSWER=chosen-server", output)
        self.assertNotIn(b"ANSWER=installer-script", output)


if __name__ == "__main__":
    unittest.main()
