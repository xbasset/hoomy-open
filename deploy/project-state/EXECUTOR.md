# Explicit central state runner

The Mac runner calls the installed `hoomy_remote_acceptance` MCP server through
the native Codex app-server. Its controller never starts a model turn. A second
process uses the existing restricted Hoomy client for each extraction or
reconciliation call, with its default configured model. The executor uses an
explicit CLI runner or a separately reviewed watcher, with no new token store. Signed collectors
remain unchanged.

An optional Mac watch service is prepared below; installation remains gated on
the first explicit pilot's state and citation quality review.

## Deployment preparation

Before changing the authoritative backend, retain a consistent online backup of
the **project-data** database, check SQLite integrity and free space, and rehearse
the additive state-table migration on a separate copy. Authentication databases,
collector credentials and raw source histories are not migration inputs. The
local archive writer remains stopped and read-only. Never replace the central
database with the local archive.

Keep enough free space for the database backup, rehearsal copy, WAL growth and
both old/new Docker images. Measure current project-volume capacity before copying. Check existing business-table
row counts/digests before and after rehearsal. The new tables are additive;
approved context, old receipts, source versions and exclusions must remain intact.

`rehearse_migration.py --snapshot-db /private-backup/hoomy.sqlite3
--output-directory /private-backup/new-state-rehearsal` creates a new owner-private
copy from a closed, checkpointed snapshot, opens only that copy with `Store`,
and checks integrity plus exact existing-table fingerprints. It hashes schema,
row IDs, SQLite value types and exact text/blob bytes in bounded chunks; equal
row counts alone do not pass. Supply the already-created project snapshot, never
an auth database. `--snapshot-sha256` pins its recorded digest;
`--expected-new-table project_state_retry_links` restricts the retry migration to
that sole addition. All 37 existing tables, including the six populated state
tables and the original ambiguous dispatch, remain protected. A nonempty snapshot
WAL, source digest change or changed existing row fails the gate. The tool never
prints row contents or changes the original snapshot.

`compose.state.yaml` overlays the existing central compose file and passes the
same `HOOMY_STATE_PROJECT_ID` to both services. Its empty default disables state
generation. Select one approved canonical project UUID explicitly and verify
the installed remote connection's central profile and server origin. Local
Hoomy is never a fallback. Keep ingestion settings independent.
Recreate backend and gateway together in dependency order because they share a
network namespace. Preserve both named data volumes and the existing signed
release mount; do not copy any signing key or authentication store.

Example operator command after backup, rehearsal and review:

```sh
HOOMY_STATE_PROJECT_ID="<approved-project-uuid>" \
  docker compose -f deploy/central/compose.yaml \
  -f deploy/project-state/compose.state.yaml up -d --build --force-recreate backend gateway
```

The protected gateway environment/release paths, reviewed external data-volume
names and reverse-proxy network must already be set as described in the
[central runbook](../central/README.md). Do not print them or run `docker compose config` to export
their values. Verify trusted public HTTPS health and normal remote project reads
before creating a state job. To disable further execution, recreate with the
overlay and an empty `HOOMY_STATE_PROJECT_ID`; retain all persisted results.

## One native connection consent

Run from the repository on the Mac signed in to Codex with ChatGPT:

```sh
deploy/project-state/run-state.sh login
```

If needed, `HOOMY_CODEX_BIN` selects the existing installed Codex executable and
`HOOMY_PYTHON_BIN` selects an existing Python runtime. Neither is a credential.
The CLI prints only the public authorization URL, then waits for native OAuth
completion. Approve the actual Hoomy consent showing read, existing context edit,
state-refresh and state-execute permissions. Both state permissions use this
single connection. Existing grants cannot silently gain permissions; collector
device enrollment is unrelated and must not be repeated.

## Owner refresh, then explicit execution

Use the remote project panel to inspect current context/corpus revisions and
explicitly request a refresh. Reads do not create jobs. Pass the resulting UUIDs
to the runner:

```sh
deploy/project-state/run-state.sh run --project-id PROJECT_UUID --run-id RUN_UUID
```

The controller claims the frozen server input, heartbeats every 20 seconds and
acknowledges a central dispatch journal before calling the model. Each call gets
only the server-built payload/schema; leases, transport identities and tokens
never enter its prompt. Extraction results are validated/persisted before the
server builds reconciliation input. Final publication is server-authoritative.

Both app-server processes use distinct empty temporary directories outside
approved project roots and ephemeral sessions with empty environment selection.
Generated analysis must not become a collected project conversation. The model
process disables tools, MCP, plugins, hooks, memory and workspace access using
the existing client restrictions. Its instructions treat source/context text as
untrusted evidence and select immutable passage IDs; the server resolves exact
quotes and provenance without rewriting imported language.

The run deadline is 300 seconds and lease is 90 seconds. The current method
allows two device-scoped 24 KiB extraction slots plus one reconciliation, using
6,144-byte accepted extraction outputs and 112-scalar immutable passages. Usage comes from native Codex notifications:
`thread/tokenUsage/updated` → `tokenUsage.last.inputTokens`, `outputTokens`,
`totalTokens`, bound to the current ephemeral analysis thread. Hoomy records
Codex-reported usage, not independently metered billing. Missing/invalid usage
causes `needs_review`. The 200,000 observed-token ceiling is checked during usage
notifications and at completion, including previously persisted call usage.
Notifications may lag actual generation; this is not a hard monetary cap.

If actual usage/model identity is known for an interrupted, invalid or overbudget
call, failure reporting includes that call's immutable method metadata. The
backend accounts it once without inventing output or publishing a result, even
when the lease has expired. Unknown usage keeps the prior known subtotal and
remains explicitly unknown; generic model labels are not actual model identity.

## Interrupted runs

A lost dispatch ACK never authorizes a model call. A model call with unknown
outcome stops for review and is never automatically repeated. A lost result ACK
retries the exact same call ID/output/method once, without calling the model
again. SIGINT/SIGTERM cancels the active call, reports a safe failure and releases
the subprocesses. If the native connection is gone, lease expiry supplies the
server-side backstop; inspect the central job before requesting another run.

Failures may also include a closed local diagnostic: native stage/category,
turn-start requested, valid native turn ACK received, matching terminal event
received, and valid usage known. These facts belong only to the current call.
Categories use local guard/parser facts or native `CodexErrorInfo` discriminators;
unknown provider errors remain unknown. No provider prose, payload or identifier
is included. These diagnostics do not alter the central journal or authorize a
retry. An older run without these facts cannot be retroactively classified.

The CLI prints bounded status, completed-call count, observed token count or a
constant failure code. It does not print source messages, assertions, provider
errors, leases or credentials. Historical reports are not current verified
observations; use the resulting citation controls to inspect version-pinned
evidence and coverage before relying on a claim.

## Keep explicit UI refreshes usable after the pilot

After the first controlled run and citation review pass, run:

```sh
deploy/project-state/run-state.sh watch --project-id "$HOOMY_STATE_PROJECT_ID"
```

Watch polls `hoomy_project_state_job` with only `project_id` every 15 seconds.
The server returns metadata for an owner's explicitly **queued** job, or null;
it does not build the corpus or return messages while idle. The watcher never
calls refresh, creates jobs or reacts to ingestion. Idle polls construct no
model client and make no model call. Running, ready, failed and `needs_review`
jobs are ineligible. Each observed queued UUID is consumed once per process;
the central claim/dispatch journal prevents redispatch across service restarts.
Ambiguous calls still stop for review. A bounded memory set stops the watcher
after 1,024 distinct jobs; restart it after inspecting central job status.

To prepare a reviewable LaunchAgent without installation, use existing verified
Python and Codex executable paths:

```sh
python3 deploy/project-state/prepare_watch_service.py \
  --project-id "$HOOMY_STATE_PROJECT_ID" \
  --python /absolute/path/to/python3 --codex /absolute/path/to/codex \
  --output-directory /private/tmp/hoomy-state-watch-review
```

The preparation writes only a new private candidate directory and plist. Review
its exact paths and project-specific label
`dev.hoomy.state-executor.<approved-project-uuid>`. It contains no run
UUID, credential, model override, sudo call or installer. Its process runs as the
logged-in Mac user; the existing Hoomy native OAuth connection is reused. The
model's temporary cwd remains outside all approved project bindings.

Only **after pilot quality approval**, install/start that one reviewed unit:

```sh
watch_label="dev.hoomy.state-executor.$HOOMY_STATE_PROJECT_ID"
watch_logs="$HOME/Library/Logs/Hoomy/state-executor/$HOOMY_STATE_PROJECT_ID"
mkdir -p "$HOME/Library/LaunchAgents" "$watch_logs"
chmod 700 "$watch_logs"
install -m 600 /private/tmp/hoomy-state-watch-review/$watch_label.plist \
  "$HOME/Library/LaunchAgents/$watch_label.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$watch_label.plist"
launchctl print "gui/$(id -u)/$watch_label"
```

No administrator privilege, collector restart or new device consent is needed.
If native OAuth or OS Keychain requires interaction, perform it visibly in the
user session; never bypass that gate. The Mac must remain logged in/available.
The unit starts once at load and does not automatically restart after a failure.
Watch startup shares one absolute 40-second readiness budget across native
initialize and thread setup; thread setup is also capped at the normal 25 seconds.
The deadline is checked before writing and after receiving an ACK. Other native
RPC timeouts, model limits, OAuth settings and execution restrictions are unchanged.
Its closed exit status remains visible; inspect the prerequisite failure before
an explicit operator restart. Pending discovery still excludes dispatched jobs. Logs contain safe status metadata.

Stop it reversibly with
`launchctl bootout "gui/$(id -u)/$watch_label"`.
Removing only its plist prevents automatic loading at the next login. Preserve
native credentials and central results. Stop the watcher before a manual pilot
runner to avoid competing claims; only one worker may own a run.

## Acceptance gates

Fixtures cover native subprocess RPC with zero model turns, journal ordering,
payload digest rejection, heartbeat cancellation, ambiguous dispatch/model
outcomes, exact completion replay, native usage validation and actual backend
state/citation persistence, idle watch behavior, explicit-job-only consumption,
duplicate suppression and prepare-only Mac service generation. Existing
inference tests must still pass.

Before the live pilot, independently review the runner and public worker gates,
rehearse the migration, complete the explicit native permission upgrade and
verify cached reads. Then execute one explicitly approved owner-requested project job through this
runner, inspect its real usage/status and open Mac/VPS citations in the actual
remote UI. No other project or automatic background refresh is enabled by this
procedure.

## Optional operator capture of rejected public output

Validation failures expose only a fixed substage (`schema`, `citations`,
`method`, or `bytecap`), a fixed rule and a pointer containing known schema
fields and bounded array indices. Unknown submitted property names become
`unknown_property`. Counts, canonical output/schema sizes and hashes contain
no submitted strings. The existing validators and dispatch/usage journal are
unchanged. A failure does not authorize another model call.

An operator may explicitly pass a `RejectedOutputArchive` callback to
`Executor(rejected_output=...)` before an authorized controlled run. Capture
is disabled by default and never enabled by watch mode or the UI. Use a
private directory outside this repository. The callback receives only the
parsed public model JSON and the exact server-provided output schema; it does
not receive the input, source messages, native events, reasoning, method,
leases or credentials.

Each rejection gets a new directory with mode 0700 and exclusive files with
mode 0600. Directory traversal and writes use verified, pinned directory
file descriptors; replacing a base or ancestor with a symlink cannot redirect
capture. No files are overwritten. The complete bundle is at most 512 KiB.
`manifest.json` records complete canonical hashes and byte sizes, captured
sizes and explicit truncation for both `output.json` and `schema.json`.
A truncated file is a byte prefix, not a valid complete JSON document. Invalid
Unicode cannot be captured. A partial write is cleaned up, and callback failure
preserves the original rejection and its observed-usage accounting.

The callback's `last_path` is available only to the explicitly configured
operator helper. It is never sent to the server, printed by the executor or
opened automatically. Preserve a captured artifact for the agreed review;
do not add it to Git, copy it into the repository or expose it in the UI.
The previous failed live output was not captured and cannot be reconstructed
from its closed diagnostic or usage receipt.

Heartbeat exceptions are reported as uncertain `ambiguous_completion`, not as
proof that the server's 90-second lease expired. A confirmed expiry requires a
matching project/run, a known terminal server response and its explicit safe
expiry code. Local metadata records response duration and success count without
response bodies, error prose or credentials. Controller RPC remains 25 seconds,
heartbeat cadence 20 seconds, lease 90 seconds and absolute run deadline
300 seconds. Control-plane probes without a model passed both before and after
a dispatched intent; they do not establish the cause of an earlier live
heartbeat failure or reclassify its immutable ledger.

The installed pilot watcher uses a verified private release under
`~/Library/Application Support/Hoomy/state-executor/releases/`, rather than the
mutable repository. Its plist is 0600 and logs are owner-private. Startup/poll
failures expose only closed stage/RPC metadata, validated poll count, native
liveness/exit status and reader terminal category, captured before cleanup.
Operator restart is explicit after fixing a prerequisite; no automatic retry
of a dispatched model call is introduced by a service restart.
