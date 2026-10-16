# Fast project recognition before detailed summaries

On `xbt-agent/experiment-hybrid-discovery`, the automatic second phase described
below is superseded by [agent-led exploration](HYBRID_DISCOVERY.md). The title
pass and human review remain unchanged. The deterministic second phase is
preserved on `xbt-agent/experiment-deterministic-discovery`.

## Intended experience

- Visual thesis: a calm native surface, a readable list, one action color.
- Content: recognize projects without a proposal-count cap, keep or correct them, then deepen a selected project. Sources and diagnostics appear on demand.
- Interaction: gently remove reviewed proposals, reveal justifications in place, and show progress without reordering what the user is reading. Respect reduced motion.

## Two separate readings

1. Inventory all authorized local Codex headers. Read titles from Codex indexes and folders from headers, without reading message bodies. Large files remain discoverable even when detailed reading cannot accept them.
2. For a fresh inventory with no prior assessments or human decisions, use one GPT-5.6 Luna request (low reasoning) when all eligible metadata plus recognition context fits 200,000 characters and at most 2,000 titles. This is a conservative application budget, not a measured model context limit. Larger initial inventories and incremental updates use automatically chained batches of 80. The persisted plan survives pause/restart. Every submitted title receives a proposed project, no-project result, or ambiguous result. Missing/invalid model decisions become explicit ambiguous results, never silent omissions or endless retries. Completed calls persist and resume without replay. The 750,000-character safety budget includes recognition memory and fails explicitly if exceeded.

The model sees short, call-local references (`c0`, `c1`, etc.), mapped back to original IDs only after validation. Unassigned outcomes are compact ID arrays, without repeated individual explanations. The UI explicitly labels the resulting generic reason as not individually requested. Classification instructions are supplied once at thread level, with the developer override empty. The model, reasoning level, metadata-only scope and consent boundary are unchanged.

During an active call, the UI reports the submitted title/folder counts, elapsed waiting time, response reception, and complete source-valid project previews when available in the streamed JSON. Previews are provisional; the full result is still validated before persistence. No per-title reading progress is inferred from elapsed time or streamed character counts. Pausing an unfinished single call cancels it; resuming repeats that unfinished call, not completed results. Initial calls may take up to 600 seconds before timeout; this is a safety timeout, not an expected latency or speed claim.

Refreshing normally examines only new or changed title/folder metadata without a human decision. Explicit reexamination revisits undecided titles. Body appends do not invalidate title-only coverage. Pending groups from earlier batches are supplied as provisional identities for continuity, never as approved examples. Counters distinguish cumulative examined metadata, linked sources, pending review, no project, ambiguity, current batch progress and detailed content reading.

Legacy passes did not persist their complete input IDs. Migration seeds tracking only from actual proposal provenance and human decisions, displays this limitation, and preserves all existing corrections. It does not fabricate cumulative coverage.

Keeping a proposal confirms its name, broad purpose, and conversation links, never its progress, importance, or permission to act. Proposals and their provenance remain separate from existing briefs. Existing summaries and corrections are preserved.

Project activity is an explicit active/inactive preference, separate from inferred status. Inactive projects retain history and remain recognizable, but detailed reading requires reactivation. Each catalog conversation can be reassigned, marked as belonging to no project, or deferred. Explicit assignments override future classification and detailed reading; corrections are recorded in the change history.

Recognition memory derives at most ten diverse examples per project from current human-approved metadata links and explicit negative corrections (at most three negatives). Greedy novelty selection avoids near-duplicate examples. Repeated distinctive title terms become tentative pattern hints; user-authored rules and aliases complement examples. Raw conversation bodies, terminal-like titles, and full source paths never enter this memory. Memory is recomputed from current decisions rather than append-only prompt growth. This is a deterministic lexical diversity baseline, not a claim of measured semantic accuracy improvement.

Deepening reads up to three conversations linked to a selected project. Exact quotation checks and the current 16 MiB snapshot limit still apply. Invalid evidence blocks one conversation, not the remaining queue. Authentication and safety failures pause processing; sources with changed identity or scope are not admitted.

## Reviewing overlap and moving beyond titles

### Automatic completion with bounded local retrieval

`POST /api/overview/complete` requires `include_content: true`, existing scope consent,
paused processing and at least one human-confirmed project. It first reclassifies unresolved
metadata against the validated context, using one call when the usual overview budget fits.
New proposals remain pending. It then ranks the remaining uncertain metadata locally and
selects at most twelve conversations: up to nine matching known vocabulary and up to three
exploratory records, filling unused slots within that cap. Ranking is a deterministic hint,
not a project assignment or an exhaustive search of all remaining bodies.

Only selected, authorized history files are read locally (16 MiB per-file limit). The
existing snapshot parser excludes reasoning from normalized messages. Local regular-expression
matching then excludes tool messages and obvious instruction scaffolding, removes duplicate
and overlapping windows, and selects at most four 750-character excerpts per conversation.
No shell command is constructed from user text. No match does not mean no project: a bounded
context excerpt remains eligible. Full selected snapshots stay in private local storage;
only excerpts, metadata and recognition context are sent for one grouped Luna classification.
No full-conversation summary is generated by this path.

The backend verifies excerpt offsets and text against the stored immutable snapshot before
accepting results. `recognition_excerpts` retains provenance, metadata fingerprint and validated
context hash; reset clears it. Already sampled unchanged records are not repeated with the same
context. A changed metadata/context fingerprint permits revisiting them. Pause/restart preserves
the phase and queue; an interrupted unfinished model call may need repeating. Failed/oversized
sources remain uncertain, never classified as no-project because retrieval failed. Remaining
records outside the budget are not silently marked as content-read.

The UI confirms this bounded workflow once, exposes excerpt evidence in proposals and the
catalog review, and keeps manual detailed reading as a secondary option. Logs record selected
source count, usable excerpt count, excerpt character count and the existing per-call timing/
usage metrics without storing source text. Latency and classification quality still require a
user-triggered evaluation; local retrieval does not itself establish improved accuracy.

Each proposal shows its recorded grouping rationale. Shared significant name tokens flag nearby existing projects and show the existing purpose for comparison. This is a lexical overlap hint, not a model-generated proof of separation. A pending outline can be attached to any chosen existing project with explicit confirmation; canonical name, purpose and activity are retained. Already-created projects have the same merge entry point in their detail view. Merges require paused processing.

The donut describes metadata classification categories, not confidence. Detailed reading is a separate evidence level. `GET /api/clarification-inputs` lists current ambiguous/no-project records without a human decision or current successful content analysis. `POST /api/overview/clarify` requires an explicit selection of one to three eligible IDs, existing scope and Codex consent, and no running work. It reuses the scoped snapshot, evidence-validation, pause and retry pipeline. It does not read other conversations automatically. The confirmation explicitly names content transmission to OpenAI through Codex. Completed content reviews are accessible from the selection sheet, including when no project was found. Metadata categories remain metadata outcomes; a completed content reading is not silently promoted to human recognition or guaranteed confidence.

## Background work and interruptions

A proposal citing an unknown conversation is discarded as a whole, without blocking other proposals or retrying inference. The completed pass displays a warning count, including when no valid proposal remains. Source IDs are never guessed or repaired; backend provenance validation remains strict.

The collector runs independently of the app window. The review timer is intentionally suspended when the app loses focus. Sleep naturally suspends computation. Transient connection failures have bounded retries after 5, 15, and 60 seconds, with the queue preserved; further failure requires explicit resume. No Mac sleep setting is changed. Completed conversations are retained, but interrupted summary chunks do not yet have durable checkpoints.

## What to measure

Measure time to the first useful proposal, submitted titles, grouping corrections, and review attention. A model change alone establishes neither a quality improvement nor a speed improvement. Data stays private; no results from this iteration are published automatically.

The private `edge.log` includes `overview_progress` phase timestamps and `overview_metrics` after each successful classification. Metrics include total classification duration (setup included), turn duration (from just before `turn/start`), first streamed-response delay, response character count, compact payload/instruction/schema character counts, and reported input/cached/output/reasoning token usage when supplied by Codex. Missing usage is `null`, never an estimate or zero. Interrupted calls log available timing/usage without saving the response. Preview names, titles, folder paths, prompts and response bodies are never written to these metrics. Compare successive `overview complete` timestamps for return-to-return intervals. These are client-observed timings, not server-side model-compute time. A real user-launched run is still required to measure whether the new path improves latency and grouping quality.

## Target model

The [target database proposal](review/TARGET_DATABASE_MODEL.md) extends this flow with versioned metadata, durable work, precise claim/evidence references, append-only corrections, and coherent understanding revisions. It is a design for review, not an applied migration.
