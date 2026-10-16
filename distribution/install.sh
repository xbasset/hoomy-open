#!/usr/bin/env bash
# Release publishing fills the archive checksum and signing public key.

hoomy_install() {
  set -euo pipefail
  umask 077
  local distribution_origin="__DISTRIBUTION_ORIGIN__"
  local version="0.2.5"
  local agent_sha256="__AGENT_SHA256__"
  local base="${HOOMY_INSTALL_DIR:-$HOME/.local/lib/hoomy-agent}"
  local bin="${HOOMY_BIN_DIR:-$HOME/.local/bin}"
  local temporary os architecture target runtime_sha256 python_archive runtime python executable
  local -a globals=() options=()

  [ "$(id -u)" -ne 0 ] || { echo "Run as the OS user who runs Codex, not root." >&2; return 1; }
  { exec 3<>/dev/tty; } 2>/dev/null || { echo "Installation needs an interactive terminal or SSH session." >&2; return 1; }
  exec 3>&-
  case "$agent_sha256" in __*) echo "Build a signed release before running this installer template." >&2; return 1;; esac
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --server|--state-dir|--ca-file) [ "$#" -ge 2 ] || return 1; globals+=("$1" "$2"); shift 2;;
      --auth|--label) [ "$#" -ge 2 ] || return 1; options+=("$1" "$2"); shift 2;;
      --no-start|--enable-linger|--user-service) options+=("$1"); shift;;
      *) echo "Unknown installer option: $1" >&2; return 1;;
    esac
  done
  for executable in curl openssl tar; do
    command -v "$executable" >/dev/null || { echo "Missing bootstrap prerequisite: $executable" >&2; return 1; }
  done
  os="$(uname -s)"; architecture="$(uname -m)"
  case "$os:$architecture" in
    Darwin:arm64) target="aarch64-apple-darwin"; runtime_sha256="233f8e15255b3e1d1fd47ea5dd5230dfed803f371138e896e54d98c60ae19f6f";;
    Darwin:x86_64) target="x86_64-apple-darwin"; runtime_sha256="fb3187d95334813b88940db0f1ca57b84cedfd552739c3a5771c3ea0e49988c9";;
    Linux:aarch64|Linux:arm64) target="aarch64-unknown-linux-gnu"; runtime_sha256="2e1ba0e7f22e01c534caebf7f09e7820322bc0d055271d3434af671e8400f96f";;
    Linux:x86_64) target="x86_64-unknown-linux-gnu"; runtime_sha256="ffa8f85f1b56e88b687f08d743d817015524b86a9365fd921243132b80f1a36d";;
    *) echo "Supported: macOS/Linux, ARM64/x86_64. Linux needs glibc and systemd." >&2; return 1;;
  esac
  temporary="$(mktemp -d "${TMPDIR:-/tmp}/hoomy-install.XXXXXX")"
  trap "$(printf 'rm -rf -- %q' "$temporary")" EXIT
  cat > "$temporary/release-public.pem" <<'HOOMY_PUBLIC_KEY'
__PUBLIC_KEY__
HOOMY_PUBLIC_KEY
  curl --proto '=https' --proto-redir '=https' -fsSL "$distribution_origin/releases/$version/hoomy-agent-$version.tar.gz" -o "$temporary/agent.tar.gz"
  curl --proto '=https' --proto-redir '=https' -fsSL "$distribution_origin/releases/$version/hoomy-agent-$version.tar.gz.sig" -o "$temporary/agent.tar.gz.sig"
  local actual_sha256
  actual_sha256="$(openssl dgst -sha256 "$temporary/agent.tar.gz" | awk '{print $NF}')"
  [ "$actual_sha256" = "$agent_sha256" ] || { echo "Agent checksum mismatch; nothing executed." >&2; return 1; }
  openssl dgst -sha256 -verify "$temporary/release-public.pem" -signature "$temporary/agent.tar.gz.sig" "$temporary/agent.tar.gz" >/dev/null
  python_archive="cpython-3.12.15+20261009-$target-install_only_stripped.tar.gz"
  runtime="$base/runtimes/python-3.12.15-20261009-$target"
  python="$runtime/python/bin/python3"
  if [ ! -x "$python" ]; then
    curl --proto '=https' --proto-redir '=https' -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/20261009/$python_archive" -o "$temporary/python.tar.gz"
    actual_sha256="$(openssl dgst -sha256 "$temporary/python.tar.gz" | awk '{print $NF}')"
    [ "$actual_sha256" = "$runtime_sha256" ] || { echo "Python runtime checksum mismatch; nothing executed." >&2; return 1; }
    mkdir -p "$base/runtimes"
    mkdir "$runtime.pending.$$"
    tar -xzf "$temporary/python.tar.gz" -C "$runtime.pending.$$"
    [ -x "$runtime.pending.$$/python/bin/python3" ] || { echo "Python runtime is incomplete." >&2; return 1; }
    mv "$runtime.pending.$$" "$runtime"
  fi
  mkdir -p "$base/releases" "$bin"
  local release="$base/releases/$version-$agent_sha256"
  if [ ! -f "$release/agent/run.py" ]; then
    mkdir "$release.pending.$$"
    tar -xzf "$temporary/agent.tar.gz" -C "$release.pending.$$"
    [ -f "$release.pending.$$/agent/run.py" ] || return 1
    mv "$release.pending.$$" "$release"
  fi
  "$python" -I "$release/agent/run.py" --version
  local wrapper="$temporary/hoomy-agent"
  # Paths are data, serialized as shell strings rather than evaluated fragments.
  {
    printf '#!/usr/bin/env bash\nexec '
    printf '%q ' "$python" '-I' "$release/agent/run.py"
    printf '"$@"\n'
  } > "$wrapper"
  chmod 700 "$wrapper"
  mv "$wrapper" "$bin/hoomy-agent"
  echo "Agent installed: $bin/hoomy-agent"
  echo "Collection requires separate explicit approval of one project and its history roots."
  # Stock macOS Bash treats empty arrays as unset under nounset.
  "$bin/hoomy-agent" ${globals[@]+"${globals[@]}"} install ${options[@]+"${options[@]}"}
}

hoomy_install "$@"
