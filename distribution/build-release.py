#!/usr/bin/env python3
"""Build a signed source release with checksum-pinned pure Python dependencies."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.5"


def build(output, signing_key, origin, cache=None):
    output = Path(output)
    dependencies = json.loads((ROOT / "distribution/dependencies.json").read_text())
    context = ssl.create_default_context()
    if Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations("/etc/ssl/cert.pem")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        package = directory / "package"
        shutil.copytree(ROOT / "agent/hoomy_agent", package / "agent/hoomy_agent", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy(ROOT / "agent/run.py", package / "agent/run.py")
        for name, dependency in dependencies.items():
            filename = dependency["url"].rsplit("/", 1)[1]
            cached = Path(cache) / filename if cache else None
            if cached and cached.is_file():
                payload = cached.read_bytes()
            else:
                with urlopen(dependency["url"], context=context, timeout=60) as response:
                    payload = response.read()
                if cached:
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(payload)
            if hashlib.sha256(payload).hexdigest() != dependency["sha256"]:
                raise ValueError("Dependency checksum mismatch: " + name)
            with zipfile.ZipFile(io.BytesIO(payload)) as wheel:
                for entry in wheel.infolist():
                    path = Path(entry.filename)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("Unsafe wheel archive")
                    if path.parts[0] == name or path.parts[0].endswith(".dist-info"):
                        wheel.extract(entry, package / "vendor")
        release_directory = output / "releases" / VERSION
        release_directory.mkdir(parents=True, exist_ok=True)
        archive = release_directory / ("hoomy-agent-" + VERSION + ".tar.gz")
        with tarfile.open(archive, "w:gz") as stream:
            for entry in sorted(package.iterdir()):
                stream.add(entry, arcname=entry.name)
        signature = archive.with_suffix(archive.suffix + ".sig")
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(signature), str(archive)], check=True)
        public_key = subprocess.run(["openssl", "pkey", "-in", str(signing_key), "-pubout"], check=True, capture_output=True, text=True).stdout.strip()
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        template = (ROOT / "distribution/install.sh").read_text()
        installer = template.replace("__AGENT_SHA256__", checksum).replace("__PUBLIC_KEY__", public_key).replace("__DISTRIBUTION_ORIGIN__", origin.rstrip("/"))
        (output / "install.sh").write_text(installer)
        (output / "install.sh").chmod(0o755)
        manifest = {"version": VERSION, "archive": "/releases/" + VERSION + "/" + archive.name, "signature": "/releases/" + VERSION + "/" + signature.name, "sha256": checksum, "collection_available": False}
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--signing-key-file", required=True, help="RSA private key outside the repository")
    parser.add_argument("--distribution-origin", default="https://my.hoomy.dev")
    parser.add_argument("--dependency-cache")
    args = parser.parse_args()
    from urllib.parse import urlsplit
    parsed = urlsplit(args.distribution_origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        parser.error("Distribution origin must be an HTTPS origin")
    print(json.dumps(build(args.output, args.signing_key_file, args.distribution_origin, args.dependency_cache), indent=2))
