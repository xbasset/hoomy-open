# Hoomy device agent

The agent enrols a machine through Hoomy's OAuth Device Authorization flow and maintains a supervised heartbeat. Ordinary installation requests only `device:self` and preserves existing permissions. Version 0.2.0 additionally supports collection of one explicitly approved project's visible conversation text through a separate `evidence:ingest` grant and server-owned policy. **Collection never executes inference or ingests repositories.** Existing 0.1.x presence grants do not silently gain permission to read or upload conversation bodies.

A deployment may approve separate policies for the same canonical project on several machines. Fixture validation and read-only projection previews are distinct from durable upload acceptance. Live receipt and coverage checks belong to the operator; no private acceptance data is required to install the source candidate.

## Explicit collection approval

Prepare a small JSON policy outside Codex configuration directories. Its closed schema is documented in the [conversation ingestion contract](../docs/architecture/conversation-ingestion-contract.md). Approve only the selected canonical project, its working-directory roots, and existing `~/.codex/sessions` or `~/.codex/archived_sessions` directories. A history directory is a source allowlist; recorded cwd must also positively match the selected project by path components. Unknown or conflicting cwd fails closed.

```sh
~/.local/bin/hoomy-agent --server https://my.hoomy.dev collection approve --policy-file /path/to/reviewed-policy.json --auth terminal
~/.local/bin/hoomy-agent --server https://my.hoomy.dev collection status
```

The first command submits a device-bound policy proposal using the current presence authorization, prints the exact proposal and starts a fresh expiring phone approval for `device:self evidence:ingest`. It performs no history collection. Complete verification, GitHub sign-in when needed, final machine consent and the success redirect in one browser on the phone; starting the same code in another browser cannot resume an already confirmed request. Denial or expiry preserves the previous authorization. A successful approval replaces that machine's old grant, persists exactly the returned scopes, and retrieves the server-owned policy before activation.

Stop the old collector before replacing its grant. After approval, review/install the new service definition before starting collection. On Linux, service preparation exposes only the approved history directories through read-only binds; it never binds their enclosing `.codex` directory or `auth.json`. Existing signed releases and runtimes remain immutable.

The supervised `run` cycle checks the current authenticated device policy before reading sources. A disabled policy, missing ingestion scope, changed policy revision or changed approved roots stops new collection and delivery. Presence heartbeats remain available to grants that only permit presence. The collector does not depend on Codex hooks: periodic reconciliation recovers eligible source changes after missed events, API outages and restarts.

## Projection and local delivery

Read only regular, owner-held files reached through no-follow history-directory descriptors. The bounded first metadata line is read unbuffered exactly through its newline, avoiding body prefetch before native ID and positive cwd approval. Buffering starts only after that gate. Native IDs use the API's bounded ASCII identifier format; paths require scalar UTF-8, normalized absolute components and no control characters or DEL. Reject symlinked ancestors, source replacement, concurrent changes and an unterminated final JSONL record. Capture at most 256 MiB of original source through a 1 MiB record buffer; accepted sources hash every original byte without storing or uploading original JSONL.

From 0.2.2, an oversized record enters a strict streaming JSON validator using 64 KiB chunks. It verifies complete grammar, UTF-8, escapes and decoded duplicate keys through the final newline, regardless of field order. Limits are 32 nesting levels, 256 keys per object, 1,024 UTF-8 bytes per key, 1 MiB of retained key/capture bytes across the live stack, and 250,000 value nodes. It retains only bounded classification fields and duplicate-key sets. Only closed, proven `response_item` tool-call/output or reasoning records, and exact assistant `message` records with channel `analysis`, can be omitted. Unknown fields/types, structural scope keys, critical-field type changes, malformed tails and budget overflow reject the entire source without advancing its checkpoint. Earlier 0.2.0/0.2.1 releases conservatively rejected every oversized record.

Version 0.2.3 also accepts the rollout's optional `ordinal` only as null or an unsigned 64-bit integer, and the documented passthrough field on custom tool outputs. A closed oversized `compacted` checkpoint may be omitted in full after validation against the [official checkpoint wire fields](https://github.com/openai/codex/blob/main/codex-rs/history/src/rollout_payload.rs). Replacement and guardian histories contain supported, closed ResponseItem objects; their ordinary compaction and agent-message records remain private. This adapter never reconstructs conversation text from cached checkpoint history or summaries. Unknown variants, fields, scope paths and malformed typed metadata refuse the entire source.

The sole session-identity exception is the exact checkpoint path `latest_token_usage_record.session_id`: the [official protocol](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs) identifies it as a telemetry SessionId distinct from the native ThreadId. It must be a valid UUID within a closed, typed token-usage record. If usage `thread_id` is present, it must already belong to the canonical session or a declared ancestor admitted before that checkpoint; future metadata cannot authorize it. No checkpoint mutates lineage. All other nested session IDs and cwd/work-directory scope keys remain refused, as do native session/turn-context records hidden inside message content.

`excluded_oversized_records` counts only those proven excluded records. Their full bytes still contribute to the original-source hash, and later public messages retain original line indices. Such evidence explicitly has partial raw-record coverage; it does not claim original histories or skipped reasoning/tool contents are available centrally. Turn-context cwd transitions remain admissible only when both initial and current cwd positively map to the same approved canonical project, including exclusions.

Codex copied forks place the current session's canonical metadata first, followed by inherited ancestor metadata, as described by the [official session implementation](https://github.com/openai/codex/blob/main/codex-rs/core/src/session/session.rs). Version 0.2.2 preserves that first native ID and admits at most 64 identities reachable through explicitly declared, valid UUID `forked_from_id` references. Register another ancestor only from already-admitted metadata whose cwd matches the same approved project. Undeclared IDs, cycles, changed parent declarations, missing/external cwd and exclusions reject the source. Repeated canonical metadata must preserve its parent declaration. Filename resemblance never grants lineage or project access.

The projection allows supported `response_item` user messages and assistant final/absent-channel messages. It omits commentary, analysis/reasoning, system/developer records, tool calls/outputs, media, known injected instruction/configuration context and messages matching credential patterns. These pattern checks are conservative filters, not a guarantee that arbitrary prose contains no sensitive information. Excluded text never enters logs or the outbox. Preserve source language, nullable timestamps and original line indices. The projected hash covers only canonical visible-message JSON; the separate original-source hash is a provenance claim.

Version 0.2.4 also excludes messages beginning with the exact full/delta review framing from [Codex's Guardian context composer](https://github.com/openai/codex/blob/main/codex-rs/guardian-context/src/composition.rs). Strict UTF-8 validation precedes this check; known injected framing is omitted before the public-message size limit. Ordinary prose quoting or discussing the framing remains evidence. Titles continue to use the first retained projected message, preserving its language; they never use an excluded message or raw metadata title. Whole Guardian sessions are not excluded by this targeted change.

Version 0.2.5 classifies a complete source as an internal approval review only when its first canonical native metadata has exactly `source: {subagent: {other: "guardian"}}` or `source: {internal: "guardian"}`, matching the [official source enum](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs). It first validates the owner-held regular file, exact unbuffered metadata line, native ID, positive approved cwd and stable file/path identity. It then returns a metadata-only exclusion without buffering or reading any body, including an incomplete or malformed body. `thread_source` alone, ordinary CLI/VSCode sources, delegated `thread_spawn` work and real conversation forks do not trigger this classification.

The closed `conversation.source_excluded` event contains only normal source-location identity, sequence, policy revision, observation time and a fixed exclusion reason/discriminator with the SHA-256 of the exact first raw header. It includes no projection, title, coverage or full-source/projected digest. The server appends an immutable classification and suppresses ordinary evidence/profile/cached reads for that exact device/project/session/path/archive location, including its older cached versions. Other paths/archive occurrences require their own proven header classification. Prior versions, blobs and receipts are preserved. Scanner status counts excluded sources separately from malformed sources.

Local checkpoints additionally record a deterministic projection-adapter revision. Legacy checkpoints migrate to revision 1; the 0.2.4 correction uses revision 2 and 0.2.5 uses revision 3. Each upgrade recaptures unchanged, still-approved sources once. The source identity stays fixed and the next sequence increases normally, producing a new immutable event. Ordinary projections retain their full original-source digest; exclusions use a digest explicitly limited to the first header. Prior queued bytes, central versions and receipts are preserved; capacity refusal leaves the adapter checkpoint unchanged. A later unchanged scan skips the corrected source. The checkpoint migration adds no scope and requires no new machine approval; older collector releases cannot write the migrated checkpoint schema.

Limits are 256 KiB UTF-8 per message, 10,000 messages, 18 MiB projection and 24 MiB event. Failures report constant categories and counters through `collection status`, never source bodies. The scanner visits up to 512 files per cycle with a durable rotating cursor; a declared 20,000-file inventory bound prevents unbounded traversal. A fingerprint checkpoint avoids re-reading unchanged bodies while checking approved metadata and stable file identity. Source versions and sequences remain separate for each session/path/archive occurrence.

Private SQLite stores immutable canonical event bytes and source checkpoints in the same durable transaction. The default queued-payload budget is 256 MiB with 4,096 event slots and 10,000 checkpoints; SQLite indexes and transaction journals add bounded storage overhead. Capacity refusal does not advance the checkpoint. The outbox contains only approved projected text and metadata, never tokens or raw source records. Its directory is owner-only and its database mode is 0600. Acknowledged payloads are deleted; unacknowledged or stale-policy captures remain private and count toward the bound.

Each cycle delivers at most eight eligible events with bounded retry delay. Retries preserve exact event bytes, UUID and per-location sequence; a receipt lookup can recover an acknowledgement lost after server commit. Dequeue only after the closed, type-specific receipt matches the project, receipt/event identity and event hash, with `stored` and `not_requested` states. Projection receipts additionally match their projected hash; exclusion receipts additionally require the exact exclusion event type/header hash and reject projected-hash fields. Mixed or wrong-type receipts retain the capture. Durable acceptance is distinct from inference or updated project knowledge. Revocation cannot be bypassed by retrying queued content.

## Login and service lifecycle

The published quick start is:

```sh
curl -fsSL https://my.hoomy.dev/install.sh | bash
```

The installer URL must serve a built release, not the template in `distribution/install.sh`. Publication and an actual GitHub account login are operational acceptance steps; local fixture tests cannot establish that the public one-liner is ready.

The bootstrap verifies the agent archive's SHA-256 and RSA signature, then installs a checksum-pinned standalone Python runtime for macOS/Linux on ARM64 or x86_64. It vendors the pinned QR renderer and CA bundle in the signed archive. No global Python packages or Node runtime are changed. Linux requires glibc and the systemd system manager; a user manager is required only for explicit `--user-service` compatibility mode. The OS user needs `curl`, `openssl` and `tar`; the bootstrap never guesses which account a root shell should enrol.

Prompts read `/dev/tty`, including when the shell script arrives on stdin. The default server is `https://my.hoomy.dev`. A Mac's ordinary automatic login opens the approval page and also shows the terminal QR; terminal mode shows QR, URL, public code and expiry without opening another browser. Explicit collection approval defaults to terminal mode for a single phone flow. The phone confirms identity through GitHub and explicitly approves this machine. Hoomy sends no authentication email. The QR contains only a verification URL and public code, never a device code or token.

Examples after installation:

```sh
~/.local/bin/hoomy-agent --server https://my.hoomy.dev status
~/.local/bin/hoomy-agent --server https://my.hoomy.dev login --auth terminal
~/.local/bin/hoomy-agent --server https://my.hoomy.dev service prepare
~/.local/bin/hoomy-agent --server https://my.hoomy.dev service install
~/.local/bin/hoomy-agent --server https://my.hoomy.dev logout
```

Installer options can follow `bash -s --`:

```sh
curl -fsSL https://my.hoomy.dev/install.sh | bash -s -- --server https://my.hoomy.dev --auth terminal --no-start
```

`--no-start` completes login without installing a service. A cancelled or denied login leaves the executable installed without an invented machine authorization. Re-running login or the verified installer reuses an accepted same-server device authorization and installation UUID, renewing its credentials when necessary instead of requesting another QR approval. After authorization, activation is retried idempotently if a prior attempt was interrupted. Explicit server selection isolates credentials and policy by origin; source data is never redirected to a different server.

macOS uses a user LaunchAgent, requiring a graphical login session and access to the user's default Keychain. It continues after Codex exits; it is not a system daemon that runs before the OS user signs in.

Version 0.2.1 handles a transient Mac upgrade handoff by retrying only the same named `launchctl bootstrap` up to five times, with delays of 1, 2, 4 and 8 seconds. Enable occurs before bootstrap; the service is kickstarted only after bootstrap succeeds. A permanent refusal leaves the reviewed plist installed and prints a precise manual recovery command without claiming active state or rolling back to an older permission model. Simulated transient/permanent/disabled-service fixtures validate this behavior. An observed 0.2.0 bootstrap failure followed by success with the unchanged plist supports the transient-handoff diagnosis, but does not establish the exact OS error cause.

From 0.1.4, Linux defaults to a dedicated systemd system unit running under the existing non-root OS user and primary group. It starts with the server's multi-user target and has no SSH-session or user-lingering dependency. `service prepare`, or `service install` before administrative installation, writes a reviewable unit and narrowly scoped shell helper under the private agent state. Exit status 2 explicitly means prepared but not active. It performs no sudo, system-unit installation or change to login settings. After review, the printed sudo command installs only that named Hoomy unit, reloads unit definitions, enables it and restarts it once. A subsequent `service install` verifies the exact installed unit, its active/enabled state and an authenticated heartbeat before reporting success.

The system unit has no capabilities or privilege escalation. Its home-directory view exposes only the signed release (including vendor/CA assets), standalone runtime and writable private agent state; Docker/control sockets are inaccessible. Filesystem hardening follows the documented [systemd namespace and bind-path semantics](https://github.com/systemd/systemd/blob/v255/man/systemd.exec.xml). Runtime/state/CA arguments are absolute, and command environment expansion is disabled. The reviewed user-owned helper is administrative code, not an OS-enforced sudo allowlist: its checksum validates the root-staged candidate unit, not later edits to the helper itself. No permanent sudo rule is created.

Existing user-service installations can explicitly retain compatibility with `--user-service`; `--enable-linger` is accepted only alongside that flag. This mode still requires user lingering and keeps its OS authorization on the controlling terminal. An existing same-profile user unit blocks system-service preparation/application until explicitly migrated, preventing two supervisors. Migration affects only the prior Hoomy user unit; it is not automatic. The default dedicated system mode never invokes loginctl.

The installer distinguishes authorized login, prepared service files and a verified active service. Closing SSH does not leave an unsupervised shell background process.

## Credential and TLS boundaries

macOS credentials are generic-password items in the default Keychain, accessed through Security.framework. Background access can fail when the Keychain is locked or OS permissions change; this is surfaced as storage unavailable. Linux stores credentials atomically in owner-only files under `${XDG_STATE_HOME:-~/.local/state}/hoomy/agent`. Mac installation/configuration metadata lives under `~/Library/Application Support/Hoomy/agent`; tokens are not stored there.

Local file locks serialize CLI and service access. Refresh rotation is marked durably before the request. An interrupted or ambiguous exchange requires login rather than replaying an old refresh token. Revocation, expiry and refusal stop authorized remote operations. `logout` deletes local credentials and reports whether central revocation was confirmed; revocation cannot be promised while offline. Stored state and service configuration never enter the repository, history outbox or model-visible tool output.

HTTPS certificate and hostname validation remain enabled. Release packages include certifi's pinned CA bundle to cover standalone Python and Macs without correctly configured Python roots. `--ca-file` deliberately selects a trusted CA bundle for a private/staging server and is retained for its service. The agent refuses authentication redirects and discovery endpoints outside the selected Hoomy origin; separate external authorization issuers are not supported by this first release. GitHub redirects belong to the Hoomy browser flow, not the agent's HTTP transport.

The downloaded bootstrap is the initial trust anchor. Signed agent archives do not protect against a compromised bootstrap origin. Releases pin third-party Python runtime digests obtained from [Python Build Standalone's immutable release](https://github.com/astral-sh/python-build-standalone/releases/tag/20261009); dependency URLs and hashes are recorded in `distribution/dependencies.json`. Dependency and runtime refreshes are explicit release changes.

The selected assets all use the filename `cpython-3.12.15+20261009-<target>-install_only_stripped.tar.gz`. Checksums were read from the publisher's [complete immutable asset list](https://github.com/astral-sh/python-build-standalone/releases/expanded_assets/20261009), rather than inferred from another architecture:

| Target | SHA-256 |
| --- | --- |
| `aarch64-apple-darwin` | `233f8e15255b3e1d1fd47ea5dd5230dfed803f371138e896e54d98c60ae19f6f` |
| `x86_64-apple-darwin` | `fb3187d95334813b88940db0f1ca57b84cedfd552739c3a5771c3ea0e49988c9` |
| `aarch64-unknown-linux-gnu` | `2e1ba0e7f22e01c534caebf7f09e7820322bc0d055271d3434af671e8400f96f` |
| `x86_64-unknown-linux-gnu` | `ffa8f85f1b56e88b687f08d743d817015524b86a9365fd921243132b80f1a36d` |

## Release build and staging

Keep the RSA release signing key outside the repository and public artifact directory. For production, retain a dedicated protected key; a temporary fixture key is only suitable for staging.

```sh
python3 distribution/build-release.py \
  --output /private/tmp/hoomy-agent-public \
  --signing-key-file /path/outside/repository/release-signing.pem \
  --distribution-origin https://my.hoomy.dev
```

The builder downloads pinned pure-Python wheels, verifies their hashes and includes the packages and licensing metadata. `--dependency-cache /path/to/cache` supports a prepopulated local cache. It emits:

- `install.sh`, with the archive hash, distribution origin and public verification key embedded.
- `manifest.json`, containing safe public artifact metadata and `collection_available: false`.
- `releases/0.2.5/hoomy-agent-0.2.5.tar.gz` and its detached `.sig` signature.

Serve only the output directory over HTTPS. A custom Hoomy server may use the official distribution origin; distribution origin and authenticated server origin are independent. Build a separate bootstrap with `--distribution-origin` for an isolated staging host. Signing keys never belong in the served directory. The gateway discovery contract advertises device-control endpoints and resources; endpoint names are not hardcoded to a particular OAuth implementation.

Published versioned archives and signatures are immutable. The builder includes archive timestamps, so rebuilding the same source can change the archive digest. Publish agent source changes under a new version. A bootstrap-only repair can replace mutable `install.sh` rendered with the existing published archive digest and public verification key, without rebuilding the archive or changing its manifest/signature.

## Validation

```sh
PYTHONPATH=agent python3 -m unittest discover -s agent/tests -v
bash -n distribution/install.sh
```

Tests use temporary directories, an isolated HTTPS server with a temporary trusted certificate, and pseudo-terminal installer fixtures. They cover approval polling/slow-down/expiry/denial, TLS and origin isolation, interruption and rotation, credential ownership, central revocation, retained installation identity, service prerequisites, signed publication and rejection of tampered artifacts. Additional fixtures cover explicit ingestion scope approval/refusal, streaming large records, public-text exclusion, stable reads, source limits, durable replay/restart, lost acknowledgements, receipt integrity and outbox backpressure. They never read user histories or modify the real user's Keychain/services.

`test_gateway_integration.py` additionally runs the Python client against the actual Node gateway and its OAuth provider over HTTPS. It stubs only GitHub identity and project evidence inside the dedicated fixture. A browser harness exercises explicit device confirmation, authorization, activation, heartbeat, refresh rotation and user-triggered machine revocation. Install the gateway dependencies and use Node 24+ (`HOOMY_TEST_NODE` can select the executable); this integration is explicitly skipped if those prerequisites are unavailable.

`acceptance-inspect.py`, `acceptance-renew.py` and `acceptance-revoke.py` are operator helpers kept outside the signed agent package. They select the installed signed release with `--release-directory` and emit only explicit safe fields. Renewal requires an enrolled device with its daemon stopped (and its Mac LaunchAgent unloaded). It advances the injected clock for the expiry comparison only, uses the real HTTPS refresh endpoint, and persists replacement credentials against real time. Revocation requires fresh approved Mac credentials and an unloaded daemon; it captures credentials in memory, verifies positive self/heartbeat responses, invokes the actual signed CLI logout, then checks old access/refresh refusal before access expiry. Neither helper prints tokens or response bodies or exports credential snapshots. Native plugin refresh must separately be observed through the actual Codex client after natural token expiry.

Operational checks verified the public signed bootstrap and standalone runtime startup on an ARM64 Mac and an x86_64 Linux VPS, including the real default-server prompt under stock macOS Bash 3.2. On 2026-10-12, explicit GitHub-backed machine consent authorized the Mac. Credentials were created in its default Keychain, a real HTTPS refresh exchange rotated the refresh token while preserving the device identity, and collector requests to project/device-administration/MCP resources each returned 401. The Mac LaunchAgent ran under launchd (parent PID 1), and central heartbeat timestamps advanced after its CLI exited. Collection remained unavailable throughout.

The VPS public one-liner also upgraded to 0.1.3 and completed explicit phone/browser device authorization. Its renewable credentials were persisted in an owner-only 0600 file. Real HTTPS refresh rotation preserved its device identity and persisted real-time expiry; project/device-administration/MCP resource requests each returned 401. A subsequent public 0.1.4 upgrade reused this approval without a new QR and preserved installation/enrolment/device metadata and credential mode. The actual dedicated system unit/helper were reviewed and administratively applied. Live verification confirmed enabled/active state, User/Group `xavier`, parent PID 1, zero restarts and `Linger=no`. Central HTTPS heartbeat timestamps advanced after SSH was closed; no SSH monitoring connection was used during that window. The 10:46:42.719 UTC heartbeat occurred beyond the last CLI operation bound (10:30:55 UTC) plus the 900-second access-token lifetime. No agent CLI call or forced renewal occurred in that interval, proving the daemon's own natural renewal and continued operation independently of Mac collector revocation.

The original Mac worker later stopped with `needs_login` after refused/interrupted rotating renewal; its stored access expiry was 07:27:38 UTC. Its LaunchAgent had no configured stdout/stderr files, so an exact network/provider error category could not be recovered. This is the documented conservative no-replay stop, and cannot be represented as uninterrupted daemon availability. A fresh explicit Mac authorization subsequently supplied the strict revocation baseline: self and heartbeat returned 200, CLI logout confirmed central revocation, its still-unexpired access token then returned 401 and its old refresh token returned 400 `invalid_grant`. Local credentials were removed, installation identity was preserved and signed CLI status/one-shot work stopped. The existing native plugin grant continued working independently.

After the gateway device-list status correction, an interrupted restoration flow produced an `authorized` registry record without a heartbeat, while its token poller remained pending and expired at 11:03:06 UTC. An actual-provider HTTPS fixture reproduced this boundary: initial code confirmation puts the request in flight; interrupting the consent redirect leaves it pending, and retrying the same public code in a different browser cannot resume it. The complete verification, GitHub authentication, machine consent and success redirect must remain in the same browser. A fresh phone-only restoration request also reached its normal deadline at 11:14:32 UTC without token/activation success. Neither expired attempt establishes Keychain storage or activation.

The final phone-only flow completed successfully at 11:52 UTC: the signed CLI received authorization and activation, preserving the Mac device and installation identity. Safe inspection confirmed renewable `authorized` credentials in the default Keychain and central device state `active`. The signed 0.1.3 LaunchAgent was restored, running as UID 501 with parent PID 1, and its first authenticated heartbeat was accepted at 11:52:08.719 UTC. Collection remained unavailable.

Default Keychain access after reboot, Mac application/OS logout and real machine reboots remain unverified. A graphical Mac login session remains a documented LaunchAgent requirement. The earlier ambiguous-renewal stop is a reliability limitation; current recovery through explicit re-enrollment does not remove that boundary. Those milestone 1 checks kept collection unavailable; the separately approved NoFlinch pilot now exercises projected conversation ingestion. Its privacy omissions and unsupported/empty sources must remain explicit in coverage reports. The fixture suite verifies service definitions and supervisor calls, not those live OS behaviors.
