# Discovery usage review and guided-flow proposal

Date: 2026-09-21. Status: observed usage and design proposal; no runtime changes.

## Preserved outcome

The user considers the active/inactive project map a useful foundation for the
next experiment. Preserve it rather than resetting discovery to test interface
changes. Current state: 15 projects (10 active, five inactive), 719 cataloged
conversations, 540 linked, 37 explicitly marked no-project, 75 automatically
labeled no-project, and 67 ambiguous. There are 514 sources with partial excerpts.

A private, Git-ignored project-map export was saved under
`pilot/runtime/checkpoints/20260921T121927Z-project-map.json`. It preserves project
identities, purposes, activity and conversation IDs, without conversation bodies.
It is a seed for the next experiment, not a full database recovery backup or an
implemented import workflow. The live database and original source files remain
unchanged. Private project names and source identifiers are omitted here.

## Observed journey

Evidence: host worker metrics, persisted review/change records, read-only source
headers and the user's screenshots. Times below are Europe/Paris. The screenshots'
UTC capture names differ by two hours. There is no complete clickstream: opening
a screen, expanding a disclosure or abandoning a dialog cannot be reconstructed.

| Time / stage | Evidence |
| --- | --- |
| 13:41:58 | Initial title classification completed in 65.75 s; 28,939 tokens |
| 13:42:18–13:42:38 | First six project-recognition events |
| 13:42:44–13:46:30 | First complement: 564 candidates prepared, 510 classified on excerpts, 225.83 s, 238,146 tokens |
| 13:47:10–13:48:57 | Further project recognition and attachment to existing projects |
| 13:52:13–13:54:17 | Second complement: 163 candidates prepared, 112 classified, 124.06 s, 314,813 tokens |
| 13:53:40 | A project-recognition event during the second complement |
| 13:58:30 | Incremental title update completed: one title, 11.09 s, 8,353 tokens |
| 14:02:16–14:03:41 | Nine explicit conversation decisions |
| 14:06:05–14:06:40 | Three project edits |

The first scan plus first complement consumed about 4 min 52 s of instrumented
processing, excluding human decisions and inter-stage overhead. This is stronger
evidence for the five-minute processing target than the earlier projected sum,
but still does not establish classification accuracy or universal latency.

Persisted interactions across the current dataset:

- 22 recognition events: 15 created projects and seven attached proposals to an
  existing project. These support the user's grouping workflow; they are not
  necessarily seven merges of two already-created project entities.
- Final outline decisions: 17 kept, five kept inactive and 15 rejected proposals.
  Rejection counts are proposal counts, not conversation counts or click counts.
- Nine explicit conversation decisions: four project assignments and five
  no-project decisions.
- Three project edits changed three names and one purpose.
- No detailed conversation summaries are present. This supports non-completion
  of detailed analysis, not a claim that its button was never clicked.

The second complement used more tokens than the first despite a smaller candidate
set. Its agent portion alone reported 280,069 tokens. A general relaunch is not
predictably cheap or beneficial. A next action should explain what new evidence
or context it will use, not merely offer another run.

## Untitled conversations: metadata findings

The inspection read only the first source record (bounded to 64 KiB), not message
bodies. It found:

| Untitled population | Count |
| --- | ---: |
| All untitled catalog records | 373 |
| Marked with a subagent source | 343 |
| Untitled records without a project link | 107 |
| Unlinked untitled records marked as subagents | 85 |
| Untitled records with an explicit parent ID | 243 |
| Explicit parents found in the catalog | 243 |
| Untitled records whose explicit parent is linked | 209 |
| Unlinked untitled records whose parent is linked | 43 |

Parent IDs occur both at the top level and in nested subagent-spawn metadata;
looking only at one representation misses many relationships. Subagent markers
do not prove a record is useless or safe to discard. Fork ancestry is also not
automatically a delegation relationship.

Of 166 untitled records with both child and parent currently linked, 102 share a
project and 64 do not. This is not an error count: conversations may span projects
or use shared context. It rules out unconditional parent-project inheritance.

The current catalog retains ID, title, folder, path and modification metadata,
but discards parent/source relationships. The interface therefore asks the user
to decide from a placeholder and folder, even when more useful structural
evidence exists. A folder alone remains insufficient for a definitive association.

Proposed treatment: group recognizable child work under its parent in the user
view, preserve separate provenance, and propose or automatically apply a parent
association only when supported, unambiguous and not contrary to human decisions.
Unresolved or conflicting ancestry should explain the uncertainty, not present
hundreds of anonymous records as equal user decisions. No inheritance was applied
during this audit.

## What the interface should optimize

The successful user work was recognizing projects, deciding activity, grouping
related proposals and correcting a few meaningful exceptions. The interface
instead foregrounds the internal pipeline: title refresh, title reclassification,
excerpt completion, detailed reading, logs and multiple overlapping counters.

The product goal is a usable project map, not forcing every historical technical
record into a final binary decision. Undecided conversations need not block the
next experiment once the user recognizes their active projects.

### Design theses

- Visual: a calm, mostly unboxed workspace with restrained typography and one
  action accent; logs and metrics are secondary inspection, not the main surface.
- Content: current step and scope, one decision with its evidence, a stable result
  summary, and one primary next action. Project navigation remains accessible.
- Interaction: preserve focus as results arrive, animate only meaningful state
  changes, and use a stable activity indicator while waiting. Respect reduced
  motion; never auto-scroll away from a decision being edited.

### Guided flow, not an irreversible linear wizard

| Step | Autonomous work | Human handoff | Primary action |
| --- | --- | --- | --- |
| Discover | Inventory titles, identify lineage, propose distinct projects | Explain scope and obtain processing consent | Start discovery |
| Recognize | Group similar proposals and surface likely existing matches | Confirm identity, merge, choose active/inactive | Confirm these projects |
| Complete | Search minimal evidence, apply supported existing-project links, investigate useful exceptions within budget | Only new projects and meaningful ambiguity | Review remaining decisions |
| Ready | Summarize gains, unresolved scope and why processing stopped | Confirm the map is useful; unfinished history is optional | Continue with my projects |

The flow can revisit an earlier decision without resetting the map. Incoming
conversations are an incremental maintenance concern, not another equally
prominent button in the initial onboarding.

### Stable layout contract

1. Fixed header: current step, short purpose and scope.
2. Scrollable central workspace: only the evidence and objects relevant to this
   step. Technical children are grouped, not expanded by default.
3. Fixed feedback region: what just happened, what is running, or why Hoomy needs
   input. Keep dimensions stable; let long detail open separately.
4. Fixed action region: one primary action, a secondary defer/back action when
   useful, and pause during processing. Keep keyboard focus and accessibility
   order predictable. Avoid hiding actions below logs or long lists.

Every request should answer: what happened previously, why this question matters
now, which evidence supports the suggestion, and what the action will change.
An untitled child should show its source type, parent title/project when known,
and the reason for any uncertainty. A decision must remain possible to defer.

Automatic associations stay reversible and auditable. Existing human decisions
remain authoritative. Never promote automatic links to reviewed examples merely
because the user did not correct them. New-project approval remains explicit.

### Stopping and next-action rules

- If projects are usable, offer continuation even with unresolved history.
- Recommend another analysis only when it has a stated new input: changed project
  boundaries, new evidence, or previously unattempted sources.
- Distinguish budget exhaustion, unavailable sources, insufficient evidence and
  no new useful lead. Do not summarize all of them as discovery completed.
- Put refresh, manual detailed reading and diagnostic logs under secondary
  controls; do not ask users to choose inference mechanisms.
- At each pass end, show incremental gain and unresolved scope, not just a total
  count. Coverage is not confidence.

## Next experiment boundary

Use the preserved reviewed map as input to the next product experiment. Its goal
still needs definition; this audit does not infer or launch project work. Improve
discovery ergonomics separately, without resetting, silently relabeling or forcing
resolution of the existing map. No interface or inference code changed here.
