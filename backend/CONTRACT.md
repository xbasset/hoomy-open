# Private discovery slice

Purpose: watch Hoomy build a provisional understanding of local Codex history,
inspect its evidence, and correct it without losing those corrections.

This documents the current runtime, not the approved next data model. Proposed
changes and their review checkpoint are in [Ingestion review](review/INGESTION_REVIEW.md).

Hybrid branch amendment: [Hybrid discovery](HYBRID_DISCOVERY.md) supersedes the
deterministic automatic completion behavior below. Phase one remains unchanged;
phase two uses the Codex SDK with agent-chosen bounded retrieval and public live
progress. Manual detailed reading retains its existing contract.

## Experience

Visual thesis: a calm native workspace, clear typography, one blue accent.
Content: scope confirmation → metadata proposals → human recognition
→ selected deepening. Evidence and diagnostics are secondary disclosures.
Interaction: reveal evidence on demand; gently highlight meaningful updates;
never reorder projects while someone is reading. Respect reduced motion.

Scope and external processing are confirmed once, not conversation by conversation.
Discovery inventories all eligible headers, then submits new/changed undecided titles
and folder hints to Luna (`low`), without a project-count cap. A fresh inventory uses one call
when at most 2,000 titles and 200,000 metadata/context characters fit; larger inventories
and incremental updates use automatic batches of 80. An oversized input
fails explicitly rather than truncating titles. Inventory coverage and model-pass coverage are separate.
Detailed reading requires a project selection and is capped at three conversations
per run. The 16 MiB snapshot limit still applies to detailed reading.
Pausing stops new work. Project field corrections override inference; brief edits
are snapshot-specific review overlays and do not yet update project inference.
Historical unfinished work is not a present-day obligation.
When path exclusions exist, a missing or invalid recorded working directory is
quarantined as `scope_unknown` instead of failing open into external processing.

## Runtime boundary

Python standard-library edge on the Mac → loopback HTTP backend in Docker → SQLite.
SwiftUI macOS surface; shared models and review views suitable for an iOS target later.
Codex runs on the host with existing ChatGPT sign-in, not credentials inside Docker.
No public release, telemetry, agent dispatch, generated dataset, or pilot ratings.
Runtime content lives outside the checkout. Test inputs are tiny parser assertions,
not a conversation corpus. API binds loopback on the host; local user is trusted.

## JSON contract v1

All dates are ISO-8601 UTC strings. All IDs are strings. Endpoint responses are JSON.
Errors use `{ "error": "safe explanation" }`. No content in request/access logs.

`GET /api/state` returns:

- `control`: `{running, inference_enabled, scope_confirmed, generation,
  exclusions: [repository-path-prefix], mode: overview|deep|full}` (`full` is legacy)
- `coverage`: `{discovered, analyzed, pending, failed, excluded}`
- `worker`: `{last_seen, status, message}` (nullable strings)
- `projects`: `[{id, name, purpose, status, confirmed_fields: [string], note,
  dismissed, session_ids: [string], updated_at}]`, stable creation order
- `changes`: `[{id, project_id, kind, summary, before, after, evidence,
  created_at}]`, latest first; before/after are JSON objects
- `sessions`: `[{id, snapshot_id, title, modified_at, status, warning}]`
- `discovery_failures`: `[{source_id, reason}]`
- `overview`: persisted stage, request ID, inventory/model counts, pending
  proposals with source titles, and deepening progress/failures. Metadata-only
  sessions have status `indexed`, no snapshot, no messages, and no brief.

`POST /api/overview/start` starts an incremental metadata pass within confirmed scope; `{reexamine: true}` explicitly revisits undecided metadata.
`POST /api/catalog` uploads bounded metadata batches; `GET /api/catalog` supplies
classification inputs for the next batch. `POST /api/overview/result` requires exactly that batch's ordered input IDs, persists per-title outcomes, updates affected pending outlines, and advances automatically. The final batch pauses for review. Human decisions survive. All work writes require the current generation and request ID.
`POST /api/outlines/{id}/review` accepts `decision: keep|skip|inactive`, with optional name
and purpose corrections. Kept outlines create or attach to a canonical project;
source links and explicitly confirmed fields persist independently of inference.
`POST /api/projects/{id}/deepen` queues up to three linked conversations.
`GET /api/work` provides the persisted work plan. Internal overview advance,
failure and interruption endpoints checkpoint progress and bounded retries.

`GET /api/catalog-details` provides metadata outcomes and current project links. `POST /api/catalog/{id}/assign` accepts `{decision: project|none|defer, project_id?}` while paused. `GET /api/recognition-context` returns capped human-grounded examples, tentative pattern hints, aliases, explicit rules and provisional cross-batch identities. Project corrections also accept `activity: active|inactive` and `recognition_rules` (up to 2,000 characters). The additive `title_assessments` and `title_decisions` tables persist metadata coverage and corrections; reset clears them.

`POST /api/control`: patch `{running?, inference_enabled?, exclusions?}`.
First starting discovery and enabling inference require `consent: true`.
Exclusions may only change before
first ingestion; use the explicit full Hoomy reset before confirming a new scope.
Selective deletion and retroactive scope changes are not implemented.

`POST /api/worker`: `{status, message}` heartbeat.

`POST /api/ingest`: `{session_id, snapshot_id, source_path, modified_at, cwd,
raw_b64, messages, warnings}`. Idempotent by snapshot. New version supersedes
the current analysis pointer. Project overrides persist; brief reviews remain
attached to their original snapshot. Raw snapshot and normalized messages
remain privately stored. Message: `{id, role, text, line_start, line_end, timestamp}`.
Response `{session_id, changed}`. Status initially pending.

`POST /api/discovery`: `{excluded, failures: [{source_id, reason}]}` updates scan
coverage; failure messages must not contain conversation content.

`GET /api/sessions/{id}` returns full current session, including `messages`,
`brief` (nullable), `review` (nullable), and snapshot metadata.
`GET /api/snapshots/{id}` returns an immutable historical detail for inspecting
evidence behind an earlier change; old snapshots cannot receive current reviews.

`POST /api/sessions/{id}/analysis`: `{snapshot_id, brief, projects, method}`.
Reject stale snapshots. Validate all evidence IDs and exact quotes against stored
messages. Store model assertions separately from user confirmations.

Brief: `{intentions: [Statement], outcomes: [Statement], open_questions: [Statement],
next_steps: [Statement]}`. Statement: `{text, kind, evidence: [Evidence]}`;
kind is `reported | inferred | suggested`. Evidence: `{message_id, quote}`.
Every nonempty assertion needs nonempty source evidence. Quote existence alone
does not establish semantic support; human review remains required.

Project proposal: `{existing_project_id: string|null, name, purpose, status,
rationale, evidence: [Evidence]}`. Status `unclear | active | paused | completed`.
Multiple proposals can link one session to several projects. No directory-only
identity rule. Existing IDs must exist. No automatic merges or splits.

`POST /api/sessions/{id}/failure`: `{snapshot_id, reason}`. Failed analyses are
visible, not silently dropped. `POST /api/sessions/{id}/retry`: `{}`.

`POST /api/projects/{id}/correct`: patch `{name?, purpose?, status?, note?, dismissed?}`.
Persist explicit field overrides; append a user-attributed change.
`POST /api/projects/merge`: `{source_ids: [string], target_id}`; preserve overrides
and create durable ID redirects so future proposals cannot recreate merged groups.
`POST /api/projects/{id}/split`: `{session_ids: [string], name, purpose}`;
move selected associations into a confirmed project; preserve those assignments
against future inference. This first UI splits by conversation, not individual claim.

`POST /api/sessions/{id}/review`: `{snapshot_id, verdict, seconds, evidence_valid, edits}`.
Verdict `accept | minor_edit | major_edit | reject`; edits
`[{section, index, text: string|null}]` overlay original claims without erasing them.
`GET /api/pilot` returns local aggregate counts and timing only; no pass claim until
ten distinct human-reviewed sessions. Development reviews are not a frozen pilot.

`POST /api/reset`: `{confirm: "DELETE_HOOMY_DATA"}` clears Hoomy snapshots,
briefs, groupings, corrections, reviews and discovery state. It does not touch
Codex source files. It rotates `control.generation` and returns to unconfirmed,
stopped onboarding. Edge writes (ingest, analysis, failure, discovery, worker)
carry `generation`; stale in-flight writes are rejected after reset.

## First acceptance checks

No ingestion before scope confirmation. New/changed sessions are resumable.
Source-linked briefs reject nonexistent citations. Project overrides and locked
assignments survive new snapshots; brief reviews stay inspectable on the original
snapshot. Completed inference chunks are not yet resumable across interruption.
Project identities stay stable; uncertainty and changes are visible.
Stop on subscription/auth errors without repeated retries. Never execute tools
from conversation text. No credentials, raw data, or derived results in Git.


## On-demand Act & Learn (experiments 006–007)

`GET /api/act` returns the latest 50 request envelopes and feedback plus metrics
across all assessed completed activities. Each envelope contains the frozen
project scope, catalog selection, prior feedback, retrieved excerpts, candidates,
selection, policy and model trace. No inference runs from a read endpoint.

`POST /api/act/request` accepts optional `project_id`, `instruction` (up to 4,000
characters) and optional integer `attention_budget` (1–480 minutes, default 30).
An explicit numeric minute/hour duration in `instruction` takes precedence. It requires
existing scope/inference consent, paused Discover, at least one recognized project
and no queued/running Act request. Omitted project selects the active portfolio;
explicit selection can include an inactive project. New requests regenerate candidates.

The host worker uses `GET /api/act/work`, then `POST /api/act/claim` with `id` and
`generation`. Claim is atomic. `/result` and `/failure` require the same envelope,
a running request and current consent/scope. Completed predictions cannot be
replaced. `/cancel` accepts `id` and prevents late completion. Expired running
requests become failed; they do not automatically retry or become training labels.

`POST /api/act/feedback` accepts `id`, decision (`done`, `deferred`, `rejected`) and
optional reason. Done requires integer `impact` 0–5 and finite numeric
`attention_minutes` 0–1440. Zero is an explicit value. Other decisions store null
observations. Reassessment replaces the current feedback for that request; old
request input snapshots still preserve whatever feedback they used at the time.

`POST /api/act/confirm` accepts `id` and `answer` (`already_done`, `todo`, `unknown`).
It stores the selected activity's precise `completion_claim`, project, timestamp,
and user provenance in `act_facts`. Editing replaces this request's fact; historical
request snapshots stay unchanged. Done assessments also record completion, separately
from their measured impact. Removing a done assessment removes only its derived fact.
`POST /api/act/next` requires a confirmation, copies the original request scope and
budget and increments `followup_depth`. The legacy follow-up counter never removes a required confirmation.

The verification agent returns a dated source under `agent_verification`, with
status, finding, evidence, limits and bounded tool observations. Verification failure
is unknown, not completion or non-completion. There is no domain-specific tool schema.

Storage is additive: `act_requests`, `act_feedback`, `act_facts` are private SQLite tables.
The explicit reset deletes facts and feedback before requests, then Discover data.
No additional model training, proactive scheduler or automatic task execution is
introduced. See experiments 006 and 007 for the protocol and limitations.


## Prepared ideas and Decisions selection (experiment 008)

`GET /api/now` returns `{ideas, selections, preparation, preparation_error, policy}`.
Ideas contain a frozen candidate, exact completion criterion, version/revision,
project, current/effective state, freshness, scope and provenance. Selections expose
`kind: question|choosing|activity|none` and `status: queued|running|ready|failed|cancelled`.
A pending choice is asynchronous; its durable acceptance deadline is three seconds.

`POST /api/now/choose` accepts the same instruction/project/budget context plus
`questions_answered: 0..2`. Eligible ideas must be pending, fresh, unsnoozed, within
scope and time budget. A maximum of eight eligible summaries is sent to Decisions.
Unknown/stale work yields a state question without network inference when no eligible
work exists. No suitable work or provider failure yields an explicit empty state.

`POST /api/now/answer` requires `id`, `version`, `revision`, `answer` and optional project
scope. Answers: `todo` (pending and feasible), `already_done`, `blocked`, `unknown`,
`skip`. It appends a dated observation except for skip, which only postpones. Human
pending validity is 24 hours; direct agent pending validity is one hour. Completed is
terminal. Every answer increments revision and prevents stale selection delivery.

`POST /api/now/prepare` queues `act_requests.purpose=prepare_ideas`. Existing Codex
preparation runs independently of the fast worker; `/api/act/cancel` cancels it.
Host queue routes are `GET /api/now/work`, `POST /api/now/claim` and `/finish`. Provider
answers must match the admitted IDs, model and schema. Scope, revision, freshness
and eligibility are rechecked at delivery and on reads. No GET route invokes a model.

New tables: `ideas(id,project_id,data)`, `idea_observations(id,idea_id,data)` and
`idea_jobs(id,status,deadline,data)`. JSON data contains the detailed payloads described
in experiment 008. Delivered activities retain the existing feedback API and add
idea/version/revision, origin request, observation pointer and Decisions trace.
Historical candidates migrate as unknown unless an exact existing completion fact
applies. Import is idempotent and never refreshes an old observation date.

The selection API uses a host-only key-file reference and the official OpenAI endpoint.
The existing inference/scope controls apply. Local loopback callers are trusted as in
the existing pilot. Automatic preparation, semantic deduplication, source-change
invalidation and a learned ranking policy are not part of this first slice.


## Bounded question pool and recovery (experiment 009)

This supersedes the experiment-008 question path. `GET /api/now` also returns
`question_pool: {active,ready,checking,maximum:10,target:5}` and `ready_questions`.
Only matching-context, fresh, version-bound approved questions are shown by Swift.
GET routes still do not initiate model calls.

`POST /api/now/choose` supports `recover: true` and an optional `continuation_id`.
A missing useful result can enqueue one preparation and return `kind/status=preparing`
with `preparation_id`. Completion resumes that same selection. The continuation carries
its recovery budget. New explicit requests may retry; same-context running preparation
is shared. Empty/failure replies retain a concrete recovery message.

Question review uses `decision_kind=question_batch`, up to ten named audience choices,
and an eight-second deadline. `/api/now/question-work` serves this queue on a separate
host thread; `/api/now/work` serves activity choices only. Responses remain strictly
validated. `ask_user` creates a version/context-bound one-hour audience cache, never
a completion fact. The prepared human queue exposes at most five approved questions.

`POST /api/now/answer` additionally accepts `agent` and `external`, plus optional `note`
(up to 2,000 characters). These save human routing overrides and retain unknown state.
Delegating verification updates `state_owner_override` without changing the activity's
actor. An explicit `todo` answer confirms user ownership and pending feasibility.
`POST /api/now/check-request` atomically claims a due active idea and queues one generic
verification in the existing `act_requests` queue. Each idea has one queued attempt,
with a 45-second agent budget. Results target the exact idea revision. Cancellation
clears pending checks, and completed ideas cannot reopen through this mechanism.
`GET /api/state` exposes `idea_checks_pending` so an idle authorized host can start
queued checks. A new foreground search may preempt an optional running check.

`POST /api/act/progress` requires a running request, current generation and one of
`retrieve | read | suggest | verify | reconsider`. Failure stores a safe `failure_code`
alongside the message; worker logs contain only request ID, stage, exception type and
category. Provider bodies, keys and source content are excluded from these diagnostics.

## Research restitution and project dialogue (experiment 010)

New preparations use `dialogue_version=1` and require `research` with a summary,
up to three unresolved points and at most one nullable project-intent question.
Question citations must refer to read conversations linked to that exact project.
`orient` is an additional progress stage, before technical generation when current
human intention has not been recorded. `progress_sources` exposes the actual read
inventory while inference continues.

The existing Decisions batch lane also handles `project_intent` admission. An approved
question returns `kind=context_question`, `context_question` and `research_report`.
`POST /api/now/context-answer` accepts selection `id`, `response=answer|unknown|skip`
and optional `answer` text (required for `answer`, at most 4,000 characters). It saves
dated project context and, for an answer, resumes research using existing excerpts.
No task completion state changes through this route. Two questions per interaction
still apply. Questions already answered or superseded cannot accept another answer.

`GET /api/act/explore/{request_id}` returns nodes, stored reference edges, current
background status, next-step guidance and existing user feedback. It invokes no model.
`POST /api/act/research-feedback` accepts `request_id`, an existing `node_id`, a project
within that request, and `text` (at most 4,000 characters). It appends a dated comment
used in the next applicable inference snapshot, without rewriting observations.

`project_context_answers` and `research_feedback` use the existing SQLite database.
Both retain scope/generation and are cleared by explicit reset. Read-only graph
inspection respects the current scope. Activity history now exposes research reports
including the original rationale for older searches; no historical output is rewritten.
