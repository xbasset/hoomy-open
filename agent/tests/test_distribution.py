import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock
import zipfile


REPOSITORY = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("release_builder", REPOSITORY / "distribution/build-release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source"
        (self.source / "distribution").mkdir(parents=True)
        shutil.copytree(REPOSITORY / "agent/hoomy_agent", self.source / "agent/hoomy_agent", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(REPOSITORY / "agent/run.py", self.source / "agent/run.py")
        shutil.copy(REPOSITORY / "distribution/install.sh", self.source / "distribution/install.sh")
        wheel = self.root / "fixture.whl"
        with zipfile.ZipFile(wheel, "w") as stream:
            stream.writestr("qrcode/__init__.py", "# Fixture package; no network or image processing.\n")
        (self.source / "distribution/dependencies.json").write_text(json.dumps({"qrcode": {"version": "test", "url": wheel.as_uri(), "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}}))
        key = self.root / "signing.pem"
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.public = self.root / "public"
        with mock.patch.object(builder, "ROOT", self.source):
            self.manifest = builder.build(self.public, key, "https://distribution.example")

    def run_installer(self, installer_arguments=None):
        binaries = self.root / "fixture-bin"
        binaries.mkdir()
        curl = binaries / "curl"
        curl.write_text("#!/usr/bin/env python3\nimport os, pathlib, shutil, sys\na=sys.argv[1:]; out=a[a.index('-o')+1]; url=a[a.index('-o')-1]; src=pathlib.Path(os.environ['FIXTURE_PUBLIC'])/url.split('https://distribution.example/')[1]; shutil.copyfile(src,out)\n")
        curl.chmod(0o700)
        installation = self.root / "installed"
        target = "aarch64-apple-darwin" if os.uname().sysname == "Darwin" and os.uname().machine == "arm64" else "x86_64-apple-darwin" if os.uname().sysname == "Darwin" else "aarch64-unknown-linux-gnu" if os.uname().machine in ("aarch64", "arm64") else "x86_64-unknown-linux-gnu"
        runtime = installation / "runtimes" / ("python-3.12.15-20261009-" + target) / "python/bin/python3"
        runtime.parent.mkdir(parents=True)
        arguments = self.root / "arguments"
        runtime.write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "' + str(arguments) + '"\n')
        runtime.chmod(0o700)
        environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"], HOOMY_INSTALL_DIR=str(installation), HOOMY_BIN_DIR=str(self.root / "installed-bin"), FIXTURE_PUBLIC=str(self.public))
        child, descriptor = pty.fork()
        if child == 0:
            arguments_to_forward = installer_arguments if installer_arguments is not None else ["--server", "https://server.example", "--auth", "terminal", "--no-start"]
            os.execvpe("/bin/bash", ["/bin/bash", str(self.public / "install.sh"), *arguments_to_forward], environment)
        output = bytearray()
        deadline = time.monotonic() + 15
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
            else:
                os.kill(child, signal.SIGKILL)
                self.fail("Installer fixture timed out")
        finally:
            os.close(descriptor)
            _, status = os.waitpid(child, 0)
        return os.waitstatus_to_exitcode(status), output.decode(), arguments

    def test_signed_release_and_terminal_option_forwarding(self):
        code, output, arguments = self.run_installer()
        self.assertEqual(code, 0, output)
        forwarded = arguments.read_text().splitlines()
        self.assertIn("--server", forwarded)
        self.assertIn("https://server.example", forwarded)
        self.assertIn("install", forwarded)
        self.assertIn("--no-start", forwarded)
        self.assertNotIn("__PUBLIC_KEY__", (self.public / "install.sh").read_text())
        self.assertFalse(list(self.public.rglob("*signing*")))

    def test_stock_bash_accepts_default_server_without_empty_argument(self):
        code, output, arguments = self.run_installer(["--auth", "terminal", "--label", "Fixture Mac", "--no-start"])
        self.assertEqual(code, 0, output)
        forwarded = arguments.read_text().splitlines()
        self.assertNotIn("", forwarded)
        self.assertNotIn("--server", forwarded)
        self.assertEqual(forwarded[-6:], ["install", "--auth", "terminal", "--label", "Fixture Mac", "--no-start"])

    def test_stock_bash_accepts_no_installer_options(self):
        code, output, arguments = self.run_installer([])
        self.assertEqual(code, 0, output)
        forwarded = arguments.read_text().splitlines()
        self.assertNotIn("", forwarded)
        self.assertEqual(forwarded[-1], "install")

    def test_tampered_download_is_never_executed(self):
        archive = self.public / self.manifest["archive"].lstrip("/")
        with archive.open("ab") as stream:
            stream.write(b"tampered")
        code, output, arguments = self.run_installer()
        self.assertNotEqual(code, 0)
        self.assertIn("checksum mismatch", output)
        self.assertFalse(arguments.exists())

    def test_installer_without_terminal_fails_before_network(self):
        result = subprocess.run(["bash", str(self.public / "install.sh")], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("interactive terminal", result.stderr)


if __name__ == "__main__":
    unittest.main()
