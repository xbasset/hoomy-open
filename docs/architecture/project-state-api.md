# Project state authorization and transport

This contract separates explicit job creation from execution. It does not change
collector policies, collection scopes or the approved project context API.
The implementation supports an explicitly selected project and bounded cached
reads; the operator activates execution separately from collection.

## Permissions and activation

| Permission | Operations |
| --- | --- |
| `projects:read` | Cached state, job status and bounded version-pinned citations |
| `projects:state:refresh` | Create a bounded job against displayed revisions |
| `projects:state:execute` | Claim, heartbeat, dispatch intent, submit results, report failure |
| `projects:context:write` | Existing human-approved context edits only |

The two state permissions use one native Hoomy OAuth connection and one explicit
consent. Existing access/refresh tokens are not expanded. User discovery advertises
these user permissions and `offline_access`; it never advertises collector rights.
Collector tokens cannot target user resources. No collector enrollment is needed
for the Mac executor.

`HOOMY_STATE_PROJECT_ID` is an independent operator setting on gateway and backend.
Its empty default disables synthesis. The operator explicitly selects one canonical project UUID. Both layers must enable the same project. A cached
state read may return `enabled:false` elsewhere; reads never enqueue work.
`HOOMY_INGEST_PROJECT_ID` and existing device policies remain independent.

## Public facades and fixed private calls

All public paths below start with `/v1/projects/:project_id/state`. They require
user tokens for the project API resource. Native tools use the MCP resource and
the same backend operations through the actual installed remote plugin.

| Public suffix and method | Fixed private POST | Permission |
| --- | --- | --- |
| `GET /` | `/api/project-state/read` | Read |
| `POST /refresh` | `/api/project-state/refresh` | Refresh |
| `GET /jobs` | `/api/project-state/job` | Read; queued owner metadata only |
| `GET /jobs/:run_id` | `/api/project-state/job` | Read |
| `GET /jobs/:run_id/citations/:citation_id` | `/api/project-state/citation` | Read |
| `POST /jobs/:run_id/claim` | `/api/project-state/claim` | Execute |
| `POST /jobs/:run_id/heartbeat` | `/api/project-state/heartbeat` | Execute |
| `POST /jobs/:run_id/attempt` | `/api/project-state/attempt` | Execute |
| `POST /jobs/:run_id/finish` | `/api/project-state/finish` | Execute |
| `POST /jobs/:run_id/fail` | `/api/project-state/fail` | Execute |

The gateway injects `principal:{account_id,workspace_id}`, `project_id` and the
path's `run_id`. Clients cannot provide identities, backend paths or arbitrary
query parameters. Run, lease, call, worker and request IDs are UUIDs. Revision and
input hashes are lowercase SHA-256 strings. Citation IDs are `citation:` plus a
lowercase SHA-256 digest.

Mutation bodies are uncompressed UTF-8 JSON, at most 2 MiB, with unique keys,
Unicode scalar values and safe integers. Authentication and pilot gating precede
body parsing; current authorization is checked again before forwarding. Private
exceptions are replaced with constant public errors, preserving HTTP status.
No model prompts, source text, credentials or provider errors appear in failures.

## Closed request bodies

- Refresh: `{request_id,context_revision,corpus_revision,method_hash}`, optionally with
  `retry_of_run_id` (UUID) for an explicit reviewed retry. The HTTP
  `Idempotency-Key` must equal `request_id`. Success is `202` with the job.
  The client copies the current method hash from the displayed state. A missing
  or stale hash returns `409` and requires a reload; a malformed hash returns
  `400`. Neither the gateway nor the native tool injects a default.
- Claim: `{worker_id}`.
- Heartbeat: `{lease_id,input_hash}`.
- Attempt: `{lease_id,input_hash,call_id,phase,chunk_index}`. Phase is `extract`
  with a method-defined integer index (0–1 for v4), or `reconcile` with a null index.
- Finish: `{lease_id,input_hash,call_id,output,method}`.
- Fail: `{lease_id,input_hash,code}`, optionally with `call_id` and `method`
  together to record an already dispatched call's known usage without output.

All post-claim `input_hash` values identify the server-frozen run manifest. The
backend binds leases to the authenticated account, workspace, project and worker.
An attempt journals dispatch intent before returning the exact server-built
`input`, its canonical `payload_hash` and `output_schema`. The controller verifies
that digest before dispatch. Reconciliation inputs come exclusively from saved,
validated extraction results.

Claim and successful completion return a bounded `next_plan` beside the
phase/index-only `next`: `{phase,chunk_index,input,output_schema,phase_config,
payload_hash,method_version,method_hash}`. It is null when no next call is
available. Only the execution permission can receive this plan; heartbeats,
job status and queued discovery carry no model inputs. The client prepares a
restricted native thread without model turns, pins the plan and measures the
exact request before dispatch intent. `attempt` still accepts only identifiers,
phase and index. The backend rechecks eligibility, method, lease and reserve,
then journals the intent and returns the same pinned plan. The client compares
the acknowledgement and rechecks the exact request before writing it to the
already prepared native thread. A preparation refusal cannot create a model
call journal, and planning after completion cannot erase observed usage or a
validated extraction.

`output` has exactly `schema_version:1`, `assertions`, `uncertainties` and
`contradictions`. V4 model `source_refs` contain exactly `{passage_id}`, matching
`^p[0-9]{4}_[0-9]{2}$`. Freeform `quote` and `message_alias` fields are forbidden
in model references. Extraction messages contain immutable contiguous passages
of at most 112 Unicode scalar characters from the provided clipped prefix,
without a duplicate message body. References must identify passages actually
supplied to that extraction slot; reconciliation can use only IDs retained by
validated extraction results. The private registry resolves exact quote,
message, version, offsets and machine provenance. Public citation responses keep
their existing exact quote/provenance contract. Models never supply or rewrite
those quotes. The backend validates the full model schema, selected passages
and eligibility. Model assertion kinds are limited
to `record_fact`, `reported_status` and `interpretation`. Models cannot create
owner confirmations or verified observations.

V4 partitions extraction inputs by device, with at most two slots and no spill
from one device's allocation into the other's. Model input contains
`control:{selection_partial:true,required_machine_aliases:[...]}` instead of
numeric coverage counters. Each extraction must support its assigned machine
through actual selected passage references. Empty output remains valid JSON
but cannot satisfy that evidence requirement. Reconciliation receives grouped
extractions `{machine_alias,shared_passage_ids,result}` and must retain supported
references for every required machine. Shared passage IDs are derived from
immutable registry origins; an identical projection is assigned once and remains
one witness with multiple recorded origins, not independent corroboration.
Missing device proof stops the job for review and retains known accounting.

Cached state reads add a derived `state.device_coverage` qualifier containing
`provided_devices`, `cited_devices`, `uncited_provided_devices` and
`cross_machine_complete`. It uses surviving visible citations and never rewrites
the stored historical state, corpus counts or call ledger. Numeric coverage and
coverage gaps remain server metadata; the current model uncertainty taxonomy
excludes `coverage_gap`, while historical reads preserve existing records.

The independent `project-state-reader-quality-v1` policy has hash
`19f1e574d5b609cc7c9a6c82fac730e3095ddac848e901fdf96d6ebd6de57d0c`.
It conservatively excludes nonterminal prose, unclosed code delimiters and
version literals absent from that item's own cited quotes. Only touching scalar
passages from the same frozen message can complete a split version literal.
It never repairs text or establishes general semantic entailment.

`state.state_revision` identifies the immutable stored snapshot.
`state.visible_revision` separately hashes that revision, the reader policy hash,
the surviving assertions, uncertainties and contradictions, and their sorted
unique citation IDs. `state.quality` has exactly `policy_version`, `policy_hash`,
`raw_assertions`, `raw_uncertainties`, `source_eligible_assertions`,
`source_eligible_uncertainties`, `visible_assertions`, `visible_uncertainties`
and `excluded_items`. Each exclusion contains exactly `id`, `section`
(`assertions` or `uncertainties`) and closed `reasons`: `nonterminal_prose`,
`unclosed_code` or `unsupported_version`. It contains neither rejected text nor
an array index. Raw counts describe stored output, source-eligible counts follow
source withdrawal, and visible counts follow both filters. Existing
`suppressed_assertions` remains the source-withdrawal count. Device coverage and
dependent contradictions are computed after both filters.

The public facade validates the pinned policy, bounded counts and exclusions,
the visible digest and device-set consistency. Legacy stored counts up to 100
assertions and 20 uncertainties remain readable. For future reconciliation,
the same pure policy is used only to test surviving machine evidence. Model
inputs, extraction results and the raw finish argument remain unchanged.
Loss of required device proof stops for review with known usage retained.
This reader policy changes no OAuth scope, model schema or method hash.

`method` has exactly:

```json
{
  "adapter": "codex-native-v1",
  "model": "configured-model",
    "reasoning_effort": "low",
  "elapsed_ms": 1200,
  "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
}
```

The example is fictional. A generic `codex-default` label is rejected because it
does not identify the actual model. All usage counters must be known nonnegative safe
integers; total usage must be at least input plus output. Unknown usage is a
`usage_unknown` failure, never an invented zero. Model identity is recorded from
the configured adapter, not substituted by the orchestrator. `reasoning_effort`
is optional only for historical accounting; when present it is one of `low`,
`medium`, `high`, `xhigh`, `max` or `ultra`. V4 result publication requires the
reported model and requested, acknowledged effort to match the frozen phase configuration. Known usage
from a misconfigured call can still be recorded without publishing its output.
The effort records the supported request accepted by the native turn RPC; it
does not attest which effort the provider actually used or billed.
Counters describe the latest correlated native observation, not an independent
measurement or a guarantee of final billing.

Safe failure codes are `cancelled`, `deadline_exceeded`, `lease_expired`,
`model_unavailable`, `model_failed`, `invalid_output`, `usage_unknown`,
`budget_exceeded`, `ambiguous_completion`, `input_ineligible` and
`insufficient_device_evidence` and `unsupported_device_scope`. Insufficient
device evidence requires owner review and retains known native-observed usage;
it never authorizes fabricated evidence or a retry. More than two distinct
eligible devices are outside this pilot and must stop before dispatch, without
silently dropping a device or claiming zero usage for an unknown call.

## Explicit retry after a terminal failure

Ordinary refresh preserves its existing deduplication behavior. A fresh request
UUID alone does not authorize retrying an ambiguous model call. Only an explicit
`retry_of_run_id` invokes the retry branch, under `projects:state:refresh` and the
same operator pilot gate. The watcher never creates retry jobs.

The referenced stored run must be `failed` or `needs_review`, belong to the same
authenticated account/workspace/project and have the unchanged current manifest,
context revision, corpus revision and method hash. Queued, running, ready, cross-owner,
cross-project and stale parents are rejected. An expired but persisted running
record is not a retryable parent. No other project run may be active.

A successful retry creates a new queued child with exact copies of the parent's
immutable manifest and selected inputs. The additive `project_state_retry_links`
relation records lineage, with at most one child per parent. The previous run,
call ledger, unknown usage and output records are unchanged. Full request-payload
idempotency returns the same child for an identical request UUID replay; changed
payloads or a second child request conflict. This separately authorized retry can
consume additional quota and never pretends the previous unknown usage was zero.

## Worker lifecycle and limits

Job states are `queued`, `running`, `ready`, `failed` and `needs_review`. A lease
lasts 90 seconds, heartbeats occur at most 30 seconds apart, and the absolute run
deadline is 300 seconds. Only one run per project may execute.

V4 permits two extraction calls and one reconciliation call with the configured
`gpt-6.1-sol` model, using `low` then `medium` reasoning effort. Exact final native
RPC envelopes are bounded at 32 KiB for extraction and 64 KiB for reconciliation,
using UTF-8 serialization with `ensure_ascii=False`; raw string sizes or a fixed
escaping multiplier cannot substitute for this check. Accepted extraction output
is limited to 6 KiB and reconciliation output to 48 KiB. Extraction permits up
to six assertions, two uncertainties and no contradictions; reconciliation
permits twelve assertions, four uncertainties and three contradictions. Each
item has at most two source references. Quotes are resolved from exact nonblank
passages of at most 112 characters. Observed total usage retains its 200,000-token
ceiling. Extraction calls are capped at 75 seconds each and reconciliation at
90 seconds, including phase preparation. Initialization is capped at 40 seconds
within the same 300-second deadline; each extraction preserves 100 seconds for
reconciliation. This accounting cannot promise a hard monetary
cap or prevent the current call exceeding the remaining token budget.

The current read exposes `method_version`, `method_hash` and a closed
`execution_policy` descriptor. The canonical descriptor covers phase schemas,
selector, context, limits, model/provider, phase efforts, native serialization
and deadlines; its hash is part of the immutable run input hash. Job and pending
metadata expose their own stored `method_version`, `method_hash` and
`execution_compatible`. Historical missing descriptors remain null and
incompatible; any genuinely stored version label is preserved even when its
descriptor hash is unknown. Configuration is never reconstructed or invented for old jobs.
V1/v2/v3 records and usage remain immutable and readable, with historical state
marked stale under v4. Claim, attempt and reviewed retry cannot execute a v1/v2/v3
job under the v4 configuration. Bound late accounting remains available and
cannot publish or revive a job. Reads and idle watchers never upgrade or retry
historical jobs.

Current v4 requires evidence from each device assigned an extraction slot. The
reader-quality projection can mask incomplete or unsupported raw items with
explicit exclusions while preserving raw results, usage and context unchanged.
This is a conservative reading policy, not independent factual verification.
Comparative trial outcomes and startup failures are retained in
[method research](../research/project-state-bounded-method.md).

Watch startup shares one absolute 40-second budget across native initialization
and thread setup, the latter also capped at 25 seconds. A failed startup stops
for operator inspection; it does not automatically relaunch, retry a model call
or widen the environment. An idle successful watcher establishes no new
synthesis quality or current factual observation.

Extraction completion stores validated partial results. Only reconciliation
completion can publish the final state. Replaying the same completed call with
identical output returns its existing completion; differing output conflicts.
A failed call may record valid known method metadata without accepting or
publishing output. Accounting is bound to its recorded dispatch and lease; a late
accounting acknowledgement can never revive or redispatch a terminal job. Exact
replay does not count usage twice. Unknown usage remains explicitly unknown.

An interrupted dispatched call with unknown outcome becomes `needs_review` and
is never automatically redispatched. SQLite idempotency does not imply exactly
once provider execution.

The deterministic Mac controller starts a native Codex app-server without model
turns and calls only the installed `hoomy_remote_acceptance` tools. A separate,
restricted Hoomy `_Client` performs each model call once. Host-managed OAuth and
Codex subscription credentials remain in their existing stores. The controller
never places credentials, leases or transport identities inside model input.

See [project state design](project-state.md) for eligibility, separate
context/corpus/state revisions, provenance and acceptance requirements.
