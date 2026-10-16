# Scoped conversation collection operations

Projected conversation evidence is uploaded under a separately approved policy
on each machine to one explicitly selected central project. Collection does not
run inference, import repositories or widen an existing grant. See the
[wire contract](../../docs/architecture/conversation-ingestion-contract.md),
[central configuration](README.md) and
[collection lessons](../../docs/research/collection-and-authentication-lessons.md).

## Rollout order

1. Preserve consistent online project-data backups on the VPS and Mac. Rehearse
   additive migration in a fresh directory outside Git, with the production
   volume read-only and networking disabled. Never deploy the rehearsal copy:
   it contains fictional fixtures. Confirm the original backup hash is unchanged.
2. Freeze reviewed sources and sign a new immutable agent release. Retain all
   existing archives and signatures. Publish the versioned archive/signature
   before replacing the public installer and manifest. The gateway checks the
   exact versioned archive/signature pair on each request, including regular
   files and stable non-symlink ancestors. Verify the newly added pair over
   HTTPS, then atomically switch the installer and manifest without restarting
   OAuth. Do not announce a version that is not yet reachable.
3. Preserve the old backend and gateway images for rollback. Pause enrolled
   agents before the coordinated backend/gateway restart when their OS
   supervisor can be resumed immediately. Presence-only agents may remain
   running for a short maintenance restart; verify their existing authorization
   and heartbeat afterward, and do not treat this as proof of ambiguous-refresh
   recovery. Always stop the old agent before replacing its grant. Preserve both Docker volumes,
   cookie keys, issuer, resource origins and GitHub configuration.
4. Set `HOOMY_INGEST_PROJECT_ID` to the reviewed canonical project ID in the protected
   gateway environment. Empty is the default and disables collection grants.
   Rebuild both images and recreate both services together: the gateway shares
   the backend network namespace. Use `restart-central.sh` with the protected
   environment/release paths set. Do not use parallel `compose restart backend
   gateway`: the gateway can retain the old namespace and remain unreachable
   until recreated after the backend is healthy. Keep Caddy and other virtual
   hosts unchanged.
5. Verify health, anonymous API challenges, signed artifacts, private backend
   isolation and existing native project access. Upgrade each agent without
   starting collection. Ordinary installation keeps `device:self` permission.
6. Review one policy per machine: existing session-history directories plus an
   exact approved project cwd binding. Stop its old daemon before replacing its grant.
   Use `collection approve --policy-file … --auth terminal`, then complete the
   entire fresh request on one phone browser. Do not advance its code in a
   desktop browser before giving it to the phone. Confirm token issuance and
   the same enrolled device ID, not merely an authorized registry badge.
7. Prepare and review the service definition. Linux exposes only approved
   session-history roots through read-only binds and keeps `.codex/auth.json`
   inaccessible. Its dedicated system-unit update requires the OS administrator
   to apply the prepared helper. No user lingering or SSH connection is needed.
8. Preserve central writer authority. Retired local stores stay archive-only;
   collection policies and signed collectors are independent of the old local
   worker. Any reviewed restoration of a local writer must first reconcile newer
   central context; never use an archive reset as a migration step.
9. Validate real delivery from both machines, durable receipts, restart/retry,
   exact replay, scoped cached evidence, projection exclusions and absence of
   automatic inference. Report excluded sources and coverage limitations.
   Broader collection stays off until pilot acceptance.
10. Install the persistent `plugins/hoomy-remote-acceptance` candidate through
    the native plugin installation API. Verify its declared server is present
    in the installed runtime, completes host-managed OAuth, and returns the
    central connection marker. An explicit temporary MCP alias or a parsed
    manifest alone is insufficient. The user must then verify the embedded
    panel's central-origin marker and read one cached source from each machine.
    The remote skill must never fall back to the local Hoomy server.

## Acceptance boundaries

The pilot is a single-account service with an explicit GitHub numeric-ID
allowlist. Account/project isolation for new evidence is enforced, but this
rollout does not convert the legacy database into a multi-tenant product.
Source SHA256 is declared agent provenance; the server verifies the projected
content and event digests. The supported message subset excludes hidden
reasoning, tools and injected context. Credential-pattern filtering is a
conservative heuristic, not a general secret-identification guarantee.

Agent 0.2.1 refuses a source containing any JSONL record above 1 MiB. The
0.2.2 collector streams strict classification of oversized records,
omitting only proven tool/reasoning records or assistant analysis messages.
Accepted omissions are reported as partial coverage; unknown, malformed or
scope-bearing records still reject the whole source without advancing its
checkpoint. Total source size is bounded at 256 MiB. Declared fork ancestry is
validated independently of filenames, with the first native ID kept canonical.
Version 0.2.3 also omits strictly typed compaction checkpoints with explicit
partial coverage. It distinguishes usage telemetry session IDs from native
thread ancestry, rejects malformed/unknown history entries and never exports
checkpoint text or reconstructs it as conversation evidence.

Projection adapters have a local checkpoint revision independent of the device
policy revision. A stricter adapter must recapture unchanged source bytes once
under the same source identity, with the next sequence number. Previously queued
events, receipt identities and historical versions remain immutable. Verify
the latest per-source projection separately from aggregate retained-history
counts: those counts include older projection blobs.

The collector reconciles periodically; it does not require or install a Codex
hook. macOS uses the user's LaunchAgent and still requires a graphical login
session. An ambiguous rotating-token renewal may require fresh authorization;
queued evidence remains private and durable while authentication is stopped.

## Rollback

Stop collection first and preserve central project edits and receipts. Restart
the retained compatible backend/gateway images with the same named volumes;
the migration is additive and no legacy tables were rewritten. Clear the pilot
allowlist only as a coordinated policy shutdown. Do not silently reuse a broad
grant under an older agent or restore stale authentication data.
After internal-source classifications exist, keep their cached-read denial
effective during rollback. An older backend without the classification guard
must not serve project evidence; retained blobs are audit history, not an
authorization to expose excluded conversations again. Do not downgrade an
agent that cannot write the migrated checkpoint schema.

The local authority map is operator-only and reversible per project. Before
removing it, reconcile any central edits with the local context; returning to
local writes does not copy central evidence or convert OAuth credentials.
Existing published agent versions stay immutable and available.
