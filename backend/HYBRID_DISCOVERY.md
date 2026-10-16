# Hybrid discovery experiment

Branch: `xbt-agent/experiment-hybrid-discovery`.

Measured checkpoint and approved next iteration:
[Experiment 002 retrospective](../docs/experiments/002-hybrid-discovery-checkpoint.md).
That note preserves the previous measured implementation. The new app requests
`bounded_discovery: true`: local user-input-first evidence preparation (two
300-character snippets per source), classification packets of at most 100 sources
with three concurrent calls, then one agent investigation of residual cases.
The phase-two deadline is 235 seconds from launch, including pauses. The grouped
call stage uses at most 72% of that budget; the agent receives the remaining time.
Unreadable or evidence-free sources remain unresolved. Explicit summary headings
are selection hints, not verified summary semantics. The agent can inspect other
user-visible assistant passages when investigating insufficient evidence.

Source access, consent, live publication and human-decision protection remain the
same. Older clients without the new flag keep the original continuous workflow
described below. Bounded checkpoints retain ambiguous sources for investigation;
the final queue is not cleared to claim completion. Public phase events and an
elapsed-time heartbeat remain visible during parallel calls.

The [first bounded measurement](../docs/experiments/003-bounded-hybrid-measurement.md)
records the result and comparison limits. It used an isolated logical backup of
the current reviewed context, not the original first-pass feedback.

The first pass is unchanged: one bounded title/folder classification, followed by
human review. “Complete discovery” now hands unresolved metadata, its first-pass
outcomes, and validated project context to a GPT-5.6 Luna (`low`) Codex
agent. It does not run the old contextual title call or deterministic ranking.

The agent chooses conversation IDs, literal search terms and follow-up searches
through `inspect_conversation`. This first prototype uses a narrowly scoped
retrieval tool rather than unrestricted shell access. There is no hard-coded
classification sequence inside phase two. The objective is editable in
[`hoomy/exploration.md`](hoomy/exploration.md).

## Continuous findings and live review

The current confirmation explicitly grants `automatic_associations: true`.
Older clients sending content consent alone retain the previous single-turn,
human-reviewed behavior; updating the backend does not silently expand consent.

The agent calls `publish_finding` as soon as evidence supports a grouping. For
an already validated project, Hoomy creates the association immediately, with a
rationale and an optional alternative project and ambiguity explanation. New
projects remain pending human approval. A source cannot be linked without exact,
snapshot-verified excerpts. Repeated publication is idempotent for associations;
pending groups can be extended and approved while exploration continues.

Ambiguous associations appear in a correction list. The alternative is
preselected: reassign in one click, or choose another project and confirm.
Other automatic associations remain accessible in a collapsible history.
Explicit corrections create human decisions and disappear from the ambiguity
queue. Automatic links alone are excluded from reviewed few-shot examples.

The worker polls the feedback revision every two seconds and steers the active
SDK turn with new human decisions and project context. If the turn has already
ended, the following checkpoint reloads this context. A later model finding
cannot overwrite a human decision, even if it was computed before the correction.

Completed or time-limited checkpoints remove inspected/attempted IDs from the remaining work and
continue automatically. Published results are committed before checkpoint end,
so pause or failure does not discard them. An attempt making no progress stops
instead of looping. Unreadable sources stay unresolved but are not retried
indefinitely in the same launch. The global limit is 60 minutes from launch,
including pauses; per-checkpoint limits remain below. Each checkpoint uses a new
ephemeral thread with current context, not a persisted internal agent session.

## Runtime

The host worker uses the pinned Python SDK, with the installed host Codex binary
selected explicitly to retain the environment-isolation protocol used by phase
one. ChatGPT sign-in is required; no API-key fallback or model substitution.

```sh
cd backend
python3 -m venv .venv-agent
.venv-agent/bin/python -m pip install -r requirements-agent.txt
python3 run.py start --no-open
```

`run.py` selects that environment for the worker when present. An absent SDK is
reported when phase two starts; phase one continues to use the original adapter.
The SDK's typed low-level client carries experimental dynamic-tool fields as
documented in the [App Server reference](https://learn.chatgpt.com/docs/app-server).
See also the [SDK documentation](https://learn.chatgpt.com/docs/codex-sdk).

## Boundaries and lifecycle

- Only the unresolved, consented catalog IDs can be opened. Host paths and source
  identity are rechecked; exclusions and the 16 MiB source-file limit still apply.
- At most 24 different files and 48 retrieval calls per attempt. A source returns
  no more than four 750-character excerpts cumulatively. Repeated results can be
  sent again, so this is a unique-evidence budget, not a total token ceiling.
- A ten-minute watchdog terminates the SDK process on expiry or cancellation.
  No shell, arbitrary filesystem access, external integrations or source writes
  are exposed to the model. SDK process environment inheritance is stripped.
- The thread is ephemeral. Pause preserves the visible journal and validated
  context, not the agent's internal session. Resume starts a new attempt with
  fresh budgets; this limitation is stated in the confirmation interface.
- A pause changes the request identity, so late events/results cannot become
  valid after a rapid resume. Reset and generation checks continue to apply.
- Only findings citing actually returned excerpts can be recorded as automatic
  associations or pending new projects. Excerpts are verified against private immutable snapshots.
  Human decisions are never automatically replaced. Uninspected conversations
  retain their first-pass outcomes, including any existing pending proposals.

## Live progress

The worker consumes SDK notifications continuously. It forwards a bounded journal
of public commentary, public reasoning summaries when provided, plan updates,
retrieval activity and token usage. Raw reasoning deltas, arbitrary tool output
and final structured JSON are not rendered as progress.

The backend persists the latest 40 journal entries and 12 plan steps, guarded by
generation/request identity. The app polls this projection once per second while
the agent is active; this is SDK-stream-derived progress, not a direct socket
stream into SwiftUI. An elapsed-time heartbeat continues during silent periods.
Event times measure actual received activity, not inferred reading percentages.

Logs contain event kinds/timing and aggregate completion metrics, never the
progress text or source excerpts. Metrics include first visible feedback, total
duration, tool calls, opened sources, excerpt characters and SDK token usage.
Stored progress summaries can mention private projects; the journal stays local.

## Verification

The normal suite uses fixtures and fake inference. The explicitly enabled live
test uses only one fictional irrigation conversation in a temporary directory:

```sh
HOOMY_AGENT_SMOKE=1 .venv-agent/bin/python -m unittest discover -s tests -p test_exploration.py
```

A development smoke run completed in 21.0 seconds with two retrieval calls,
3,000 unique excerpt characters and a source-supported proposal. This verifies
SDK/tool integration, not real-history classification quality or speed.

A later fictional-data smoke test exercised live publication and active-turn
feedback in 26.9 seconds. This is an integration check, not a quality benchmark.

Compare against the [preserved baseline retrospective](../docs/experiments/001-deterministic-project-discovery.md)
using a fixed first-pass inventory and identical human feedback. The user starts
real discovery; development tests must not silently consume their history.
