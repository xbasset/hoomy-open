#!/bin/sh
# Use the installed remote plugin; no collector or token-store changes.
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
export PYTHONPATH="$repository/backend"
exec "${HOOMY_PYTHON_BIN:-python3}" -m hoomy.state_executor "$@"
