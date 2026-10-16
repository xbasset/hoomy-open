#!/bin/sh
# Recreate the shared namespace in dependency order; retain both data volumes.
set -eu
cd "$(dirname "$0")/../.."
: "${HOOMY_GATEWAY_ENV_FILE:?Set the protected gateway environment path}"
: "${HOOMY_RELEASE_DIR:?Set the public signed release directory}"
: "${HOOMY_PROJECT_DATA_VOLUME:?Set the reviewed project-data volume name}"
: "${HOOMY_AUTH_DATA_VOLUME:?Set the reviewed authentication volume name}"
: "${HOOMY_PROXY_NETWORK:?Set the reviewed external reverse-proxy network}"
docker compose -f deploy/central/compose.yaml -f deploy/project-state/compose.state.yaml up -d --force-recreate backend gateway
