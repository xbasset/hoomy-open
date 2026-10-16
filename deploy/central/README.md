# Central service operations

The central Python backend stores approved project context, scoped immutable
conversation projections and cited project state. The authenticated Node gateway
provides GitHub user identity, remote MCP, phone-approved device enrolment,
renewable device credentials, presence, revocation and explicit public facades.
The legacy local writer remains an archive; collectors and the remote Codex
plugin use the central service. Fact verification and Act/Learn remain separate
experimental modules.

The backend listens only on loopback in the namespace shared with the gateway.
Caddy forwards HTTPS to gateway port 8780; backend port 8765 is not published.
The public gateway never proxies reset, arbitrary backend administration or
legacy worker routes. Health probes use metadata-only readiness, avoiding legacy
full-state discovery. Project/context reads remain separate from model execution.

## Explicit operator configuration

Provide DNS and a GitHub App for the selected canonical HTTPS origin. Register
that origin's `/auth/github/callback` with the App. User login needs the OAuth
client ID and client secret, not the App's repository private key. Configure
Node 24 for development, Docker Compose on the server and a reviewed external
Caddy network. Copy this Caddy fragment into the operator's existing configuration
with `HOOMY_PUBLIC_HOST` set in Caddy's environment; preserve other virtual hosts.

Keep the gateway environment outside Git and the Docker build context, in an
operator-private file. Example values below are placeholders:

```dotenv
HOOMY_PUBLIC_ORIGIN=https://hoomy.example
HOOMY_GITHUB_CLIENT_ID=<operator GitHub App OAuth client ID>
HOOMY_GITHUB_CLIENT_SECRET=<operator GitHub App OAuth client secret>
HOOMY_ALLOWED_GITHUB_IDS=<explicit allowed numeric user IDs, comma-separated>
HOOMY_COOKIE_KEYS=["<persistent random signing key, at least 32 characters>"]
HOOMY_TRUSTED_PROXY_IPS=<actual reviewed Caddy peer IPs, comma-separated>
HOOMY_INGEST_PROJECT_ID=
```

Empty ingestion configuration refuses collection grants. A reviewed nonempty
project ID does not replace per-machine explicit phone consent. State execution
is independently configured through the
[project-state overlay](../project-state/README.md). The release source cannot
expand an installed grant. Use one canonical origin consistently for discovery,
OAuth resources, signed distribution and the installed remote plugin; changing
it requires explicit connection selection and reauthentication.

Compose requires these operator inputs:

| Variable | Required choice |
| --- | --- |
| `HOOMY_GATEWAY_ENV_FILE` | Absolute path to the protected gateway configuration |
| `HOOMY_RELEASE_DIR` | Absolute directory containing the existing signed public distribution |
| `HOOMY_PROXY_NETWORK` | Reviewed existing external reverse-proxy network |
| `HOOMY_PROJECT_DATA_VOLUME` | Exact reviewed project-data volume name |
| `HOOMY_AUTH_DATA_VOLUME` | Exact reviewed authentication-data volume name |
| `HOOMY_STATE_PROJECT_ID` | Empty by default; separately approved project UUID when the state overlay is used |

The Compose project is `hoomy-central`. Both data volumes are **external** and
must already exist: a renamed checkout must not silently initialize empty stores.
For an existing deployment, retain its exact volume identities, cookie keys,
issuer and release mount; do not infer volume names from the new project name.
For a fresh installation only, choose unused names and create those two empty
volumes as an explicit operator preparation step. Initialize project data only
from a consistent reviewed project snapshot or the fresh empty store. Never copy
an authentication store with project data or replace current central edits from
a stale archive. This release preparation runs no volume or service commands.

## Immutable signed collector distribution

`distribution/build-release.py` accepts `--output`, `--signing-key-file`,
`--distribution-origin` and optional `--dependency-cache`. Its archive, detached
signature, pinned dependencies and installer are a separate versioned component
from platform v0.0.1. Use the operator's chosen origin; keep already-published
archives and signatures immutable. Publish only generated public output, with
signing keys retained outside the checkout and release directory.

The installer asks for a server, installs a portable checksum-pinned Python
runtime and starts device login. Phone approval shows the machine, code and
requested policy. A same-server valid credential can be reused for an ordinary
upgrade; collection approval remains separate. macOS uses the signed-in user's
LaunchAgent. Linux prepares a dedicated system service running as the enrolling
OS user, with a reviewed administrative installation step; preparation alone
does not activate a service. Verify the named unit and authenticated heartbeat.

See [collection operations](collection.md) for scoped upload, adapter revisions
and rollback invariants, and [agent documentation](../../agent/README.md) for
installation commands and service preparation.

## Migration, recreation and rollback

1. Take a consistent SQLite online backup or a closed checkpointed snapshot and
   verify integrity. Preserve the original volume; copying a live main file can
   omit WAL state. Measure space for backup, rehearsal, WAL growth and old images.
2. Rehearse additive migration on a new private copy and compare existing table
   fingerprints. Fictional fixture-bearing rehearsal databases are never deployed.
3. Confirm explicit volume, origin, release and proxy selections. Recreate backend
   and gateway together in dependency order: the gateway shares the backend's
   network namespace. `restart-central.sh` includes the state overlay and requires
   the state UUID to be explicitly carried forward; an empty value disables it.
4. Validate Caddy configuration and public HTTPS, discovery, anonymous challenges,
   signed artifacts and native project access. Retain prior images and exact volumes
   for rollback. Reads and health checks must not create jobs or invoke a model.
5. Preserve central edits, immutable receipts, exclusions, policy revisions and
   credentials across rollback. An older backend that cannot enforce sticky source
   exclusions must not serve cached evidence. Never resume a competing archive
   writer or restore stale authentication state as a routine rollback.

`smoke-image.mjs` is a fixture smoke inside the built gateway image with a
fresh temporary auth database and a fictional HTTPS identity. Mount it read-only
at `/app/deploy/central/smoke-image.mjs` plus the public signed release, and give
it writable `/tmp`. It checks guards, anonymous challenges, release checksum and
private project metadata through a separately approved backend snapshot. It does
not exercise real OAuth, inspect auth stores or establish live device acceptance.

## Current limits and preserved lessons

The current installation is a single-account pilot with an explicit GitHub
numeric-ID allowlist, not a generally accepted multi-tenant migration of legacy
records. New evidence/state operations enforce account/workspace/project scope.
Historical authorship remains unknown where the original record lacks it.

Mac graphical logout, machine reboot and Keychain access after reboot remain
unverified. Rotating-token ambiguity can stop a collector for fresh enrolment;
its private queue is retained. HTTP success or a registry badge alone cannot
establish active service delivery. Header-only internal-session classification,
strict oversized-record handling and durable receipt replay are summarized in
[collection/authentication research](../../docs/research/collection-and-authentication-lessons.md).
Method failures, device coverage and watcher startup limits are preserved in
[project-state research](../../docs/research/project-state-bounded-method.md).
