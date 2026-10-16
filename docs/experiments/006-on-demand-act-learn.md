# Experiment 006 — On-demand Act & Learn baseline

Date: 2026-09-26. Status: implemented baseline; daily-use learning unmeasured.
Predecessor: [data design review](005-data-design-review.md).
Successor: [verification and project facts](007-verification-and-project-facts.md).

## Question and scope

Given the preserved Discover project map, existing Codex-backed LLM infrastructure
and fresh reads of linked conversations, can Hoomy suggest a useful next activity
and improve its estimates using explicit human feedback? Skip a dedicated Understand
phase. Missing or stale information observed in this baseline will determine what
Understand must eventually provide. Do not reset or relabel Discover to run this test.

The agent waits for a human request. Proactivity is a later change to this trigger,
not another decision engine. No Jev, reinforcement learning, autonomous execution,
attention sensors or background inference are part of this baseline.

## Minimal persistent model

- Existing `projects`: identity, purpose, user corrections, active/inactive stance,
  linked conversations. These remain authoritative inputs, not new understanding.
- `act_requests`: a durable request/suggestion envelope. Stores request ID, project
  scope (one project or active portfolio), current instruction, attention budget,
  timestamps, generation, execution status, immutable input/evidence snapshot,
  candidate predictions, selected candidate, rationale and policy/model provenance.
  Candidates are nested JSON, not separate entities. Pending/failed requests are
  operational state, not positive/negative examples.
- `act_feedback`: one explicitly editable assessment per request, plus revision
  timestamp. Decision is done/deferred/rejected; only done requires observed impact
  (integer 0–5) and active-attention minutes (including zero). A reason is optional.
  Feedback edits affect future requests, never historical prediction snapshots.

Use additive SQLite tables in the existing private database. Preserve the same
project IDs. Retain requests and feedback until the existing explicit reset.
No private sources or experiment results belong in Git.

## Decision and learning flow

1. Human chooses a project or active portfolio, gives optional current context and
   maximum available minutes, then requests a suggestion.
2. Snapshot project metadata, the bounded linked-conversation inventory and recent
   explicit feedback. A first LLM call selects conversations to inspect.
3. Read up to eight current linked sources locally, within existing exclusions and
   Codex history roots. Send bounded recent user/assistant/tool excerpts with stable
   source hashes and message IDs. Surface truncation and failed reads. No arbitrary
   shell access or external app tools are enabled in this first experiment.
4. A second LLM call proposes up to four activities and selects one, or abstains.
   Predict impact (0–5) and active-attention minutes. Prefer sufficient impact (2+)
   then economical attention within the budget, rather than a fragile impact/time
   ratio. Require source citations or clearly label the current request as evidence.
5. Show the suggestion, why now, expected values, evidence limits and alternatives.
   The user does the task outside Hoomy and supplies observed values afterward.
6. Future calls receive recent feedback with the original predictions. This is
   contextual adaptation, not weight training or proof of causal improvement.

No-response is unknown. Deferral and refusal are preference/context signals, not
zero impact. Repeated completed/rejected tasks should not be suggested without a
specific changed circumstance. Old conversations do not prove current state.

## Measurement

Show completed sample count, mean observed impact, mean observed attention and
mean absolute prediction errors for impact and attention. These are descriptive,
self-reported measures on selected activities, not estimates for unseen candidates.
Preserve prompt/policy version, actual model, source coverage, latency and token
telemetry for each request. Compare early vs later daily use only with task mix and
small sample limitations stated. First target: ten assessed real suggestions;
inspect repeated mistakes before adding model or schema complexity.

## Manual execution plan

1. Relaunch the matching app and backend with the preserved Discover database.
2. Open **Agir & apprendre**; choose a known project and a realistic time budget.
3. Add today's constraints and request one activity. Check why, source dates,
   expected impact/attention, and whether stale assumptions are explicit.
4. Do it, defer it or reject it. For done, enter both actual values; never use zero
   for unknown. Save, reopen the app and verify the assessment survived.
5. Request another activity. Inspect the saved context to verify the previous
   feedback was included. Judge whether the recommendation actually used it.
6. Repeat across days/projects. Review errors and usefulness, not just acceptance.

## Implementation validation

- 174 Python tests passed (one environment-gated test skipped). Act & Learn fixtures exercise the source → suggestion
  → explicit feedback → next-context loop, predictions remaining immutable,
  metrics, edited feedback, consent, cancellation, duplicate claims, expiry, reset,
  inactive-project scope and unread-evidence rejection. Fixtures use synthetic data.
- 15 Swift tests passed, covering the existing Discover UI contracts and new queued/completed
  request decoding, including explicit zero observations.
- A real two-call Codex smoke test with fictional input succeeded using
  `gpt-5.6-luna` (medium), returning one selected activity in 25.61 seconds. It
  verifies adapter integration, not usefulness on private projects or improvement.
- The macOS debug app was built and ad-hoc signed. A synthetic HTTP service on a
  separate port verified launch plumbing; native visual QA could not proceed while
  the Mac was locked.
- At handoff preparation, Docker was waiting for a macOS administrator dialog.
  The existing Discover volume was neither migrated nor reset. Its final live
  startup and the first real-project suggestion remain to be verified after Docker
  starts. Do not confuse the synthetic smoke with that real usage test.

Sources are limited to currently linked, cataloged Codex conversations. No live
repository inspection, external connector calls or newly unlinked conversations
are available to this baseline. Add those capabilities based on observed misses,
not as a prerequisite for this experiment.


## Live startup checkpoint — 2026-09-27

Docker became available and `python3 run.py start` rebuilt the matching backend,
host worker and signed app. The additive tables initialized on the existing volume.
All 15 project objects match the pre-launch state exactly; Discover remains paused.
Scope and inference consent were already enabled. Act reports zero requests and
the host worker is ready. Native UI inspection confirmed the new workspace, project
selector and enabled request button. No real suggestion was triggered on behalf
of the user; the next step is the first human-requested activity.

A private pre-launch state export and full SQLite backup are stored under ignored
`pilot/runtime/checkpoints/20260927-before-act-learn/`. The first backup attempt
exceeded the container's 64 MiB temporary filesystem; the successful full backup
was taken after additive migration and is explicitly named
`after-additive-migration.sqlite3`. Its SQLite quick check passed. It must not be
described as a pre-migration database backup.
