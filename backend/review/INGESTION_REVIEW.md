# Ingestion: first design checkpoint

Status: proposal for review. The running pilot is unchanged.

Historical checkpoint: the implementation facts below describe the reviewed
commit, not the later metadata-first flow. See the [target database model](TARGET_DATABASE_MODEL.md)
for the expanded relational proposal with fictional examples, and
[fast recognition](../FAST_DISCOVERY.md) for the newer implemented workflow.

## What we are trying to learn

Hoomy's mission is to help a person make meaningful progress with less attention
spent explaining, deciding, and reviewing. Our first use case is a chief of staff
for work with coding agents. This checkpoint concerns its prerequisite:
**can Hoomy build an understandable, correctable account of the work from the
conversations?** It does not yet test prioritization, satisfaction, or autonomy.

Discovery should cover the whole **authorized** history progressively. You see
useful understanding early, watch it change, and correct it as it develops.
There is no required conversation-by-conversation selection exercise. Excluded,
unreadable, and not-yet-understood material must remain distinguishable.

A brief describes the conversation, not your current obligations. An agent
reporting success is a reported outcome, not independently verified completion.
An unfinished next step is not permission to act. Importance, joy, desired
momentum, and motivation need their own user-grounded evidence later.

## What exists today

The private implementation was reviewed at `4340ac1` against the charter and
subsequent decisions in this conversation. These are implementation facts, not
research results:

| Part | Implemented | Important limit |
| --- | --- | --- |
| Local collector | Polls current and archived Codex histories; notices changed files; checks configured scope before upload. | Reads whole snapshots; rejects files above 16 MiB or containing an invalid record. A quiet file is not necessarily a finished conversation. |
| Local backend | Docker, loopback HTTP, SQLite; raw snapshots and normalized text; idempotent ingestion. | Raw and normalized versions are coupled. There is no independently versioned parser output. |
| Interpretation | Host Codex, existing subscription sign-in; chunking and consolidation; source-linked brief and project proposals. | Brief extraction and project reconciliation share one analysis operation. One saved analysis per snapshot; no durable chunk checkpoints. |
| Project understanding | Multiple project links per conversation; provisional projects; user rename, status correction, merge, and conversation-level split. | Latest file modification time governs inferred project fields. There is no explicit conflict resolution model or versioned understanding projection. |
| Review surface | Native macOS app, source inspection, changes, brief edits, pause, resume, feedback, and Hoomy-only reset. | Brief edits are snapshot-specific overlays; they do not yet revise project inference. A later review replaces the earlier review for that snapshot. |

The runtime slice is in place. Complete-history ingestion and useful project
understanding are **not demonstrated** by that fact. In particular, the size limit
and current scan accounting prevent an honest claim of complete coverage.

## The central design decision

Keep three layers separately addressable:

1. **Evidence:** what was recorded, where, by whom, and when.
2. **Interpretation:** what a particular Hoomy analysis inferred from that evidence.
3. **Current understanding:** what Hoomy currently presents after reconciliation
   with other evidence and your corrections.

A new model, an appended message, or a user correction can change the latter
layers without rewriting the first. These are logical boundaries inside the
existing small system, not a request for more services.

## Proposed data model

Names below are design terms, not an approved SQL migration. Keep SQLite and the
current host/backend arrangement while validating them. IDs are opaque; paths
and project names are hints, never identity. References must be enforced.

| Record | Minimum content | Relationships and version rule |
| --- | --- | --- |
| Scope revision | Allowed source roots, exclusions, processing destination, user confirmation, recorded time. | Each collection and inference run references the scope it used. Unknown scope can be held back. A changed scope is not inferred consent. |
| Conversation | Hoomy ID, agent kind, source installation/realm ID, native conversation ID if present, parent/fork link when available. | One conversation has many source locations and snapshots. Namespace native IDs; copying or archiving a file should not create another conversation. Ambiguous identity stays unresolved. |
| Raw snapshot | Content hash, immutable bytes or manifest, source reference, byte coverage, observation time, file modification time. | One snapshot can have many normalization revisions. Identical bytes may be shared in storage without collapsing distinct conversation identity. |
| Normalization revision | Snapshot ID, parser version, event IDs, covered record ranges, skipped/unsupported records with reasons. | Immutable output of one parser version. Raw storage coverage and interpretable coverage are different. |
| Event | Conversation ID, source-native ID when present, kind, actor, content blocks, source timestamp, optional turn/call/parent IDs. | Evidence references the normalization revision plus an exact raw span. For append-only input, unchanged events retain identity; rewritten records create new versions, never silently reuse old evidence. |
| Analysis run | Input normalization/chunk IDs, stage, model and adapter versions, prompt/config fingerprint, permitted project/correction revision, status, timing, available usage. | Many runs per input. Preserve completed chunk outputs; retry records another attempt. Record unknown usage as unknown. A fingerprint records configuration; it does not promise deterministic model output. |
| Conversation brief | Conversation ID, covered events/turns, analysis run ID, claim IDs, coverage/limitations. | A versioned reading of a defined conversation range. A displayed whole-conversation brief names the ranges it covers. It is not the canonical representation of the raw conversation. |
| Claim + evidence | Claim ID/version, type, text, attribution, evidence links, optional owner and time, support/review state. | A claim is one reviewable assertion. Evidence links point to precise event text/raw spans. Reinterpretation creates a new claim version with a supersession link, not an index-based overwrite. |
| Project + membership hypothesis | Stable project ID; conversation or claim link; supporting evidence; rationale; proposed/confirmed/rejected state; creating run or correction. | Many-to-many. Repository and conversation are not synonyms for project. A conversation may remain unassigned. Keep merge redirects and explicit distinctions. |
| Correction | Author, target ID/version, operation, replacement or constraint, reason if supplied, recorded time, applicability, superseded-by reference. | Append-only. Replacing a correction is another correction. A correction can fix a claim, a membership, a project field, or a mistaken distinction/merge. |
| Understanding revision | Parent revision, effective project fields and memberships, supporting claim/correction IDs, unresolved conflicts, change reasons. | The app reads a coherent revision. Each visible change can explain what evidence or correction caused it. Old revisions remain inspectable until deliberate deletion. |
| Discovery work item | Source/snapshot ID, stage, input/config identity, attempt, checkpoint, state, safe failure reason. | Durable inventory and queue. Distinguish waiting for append, excluded, blocked, queued, running, complete, and failed. Completed means complete for the stated coverage, not forever. |

### A claim needs more than a quotation

Suggested initial types: `intention`, `decision`, `reported_outcome`,
`open_question`, `candidate_next_step`. Preserve these distinctions even if the
first brief UI keeps its familiar four sections. A decision currently has no
dedicated field. Owners and timing are optional, source-linked claims; unknown
is valid. Mentioning a date never creates a reminder automatically.

Do not combine different questions in one confidence flag:

- **Attribution:** user stated, agent reported, tool observed, or Hoomy inferred.
- **Support:** source locator valid; exact excerpt verified; semantic support
  unchecked, accepted, disputed, or contradicted. Mechanical verification cannot
  answer the semantic question.
- **User review:** unreviewed, accepted, edited, or rejected; references the
  correction rather than replacing the original assertion.
- **Time:** recorded/observed time separately from the asserted event or effective
  time. A recently copied old file is still old evidence.

Start without numeric confidence scores. Every extra field must help inspection,
reconciliation, or evaluation. Suggested actions remain candidates. Personal
priorities and desired momentum are outside this ingestion model for now.

## Proposed process and visible states

1. **Confirm scope.** Explain local storage and external Codex processing. Record
   a scope revision. Local metadata inspection may be needed to determine scope;
   no content goes to inference before admission.
2. **Inventory.** Record distinct sources and current eligibility. Show a growing
   census and whether enumeration is still underway. Deduplicate source locations
   before claiming a conversation count.
3. **Capture and normalize.** Read a stable prefix, preserve raw provenance, parse
   complete records, and checkpoint. A trailing partial record waits for append;
   an invalid middle record is a visible gap, not a silent skip. Keep unsupported
   media visible as un-interpreted coverage.
4. **Extract.** Produce source-linked claims for covered ranges. Save chunk
   outputs and run metadata. Validate quotations against the deterministic parse
   of the raw snapshot, not merely a separately submitted text field.
5. **Reconcile.** Compare claims with relevant projects, earlier claims, and
   corrections. Propose membership and field changes. Record contradictions and
   stale intentions. Extraction and reconciliation become separately repeatable
   stages; they need not become separate processes.
6. **Publish a provisional revision locally.** Commit a coherent view and its
   change explanation. Show projects immediately as provisional; human approval
   is not required for every discovery. Keep list positions stable while reading.
7. **Correct and revisit.** Your correction takes effect in a new revision.
   Re-evaluate affected claims/memberships within the authorized scope and budget.
   No whole-history rerun just because you renamed a project. Expose pending
   reconciliation; never silently erase a correction.

New appends resume at capture; a parser change resumes at normalization; a model
or prompt change resumes at extraction; a grouping correction resumes at
reconciliation. Refreshing a project does not require rereading identical raw
bytes. Historical output remains comparable.

Use recent-first discovery with a guaranteed share for the historical backlog.
Show coverage against a named inventory revision: admitted, held/excluded,
captured, normalized, interpreted, and unresolved. Do not present one reassuring
percentage while the denominator or unsupported coverage is hidden.

Pause prevents new work and cancels active inference; saved completed chunks can
be resumed. Reset stops work, invalidates old writes, and removes only Hoomy's
local state. The existing generation guard is worth preserving.

## What the interactive review shows

`ingestion-explorer.html` has three views:

- **Model:** select a record in the evidence-to-understanding chain and compare
  the implementation with the proposed boundary and references.
- **Process:** inspect each stage, what becomes visible, and its restart point.
- **Evolution:** step through schematic new evidence, reassignment, correction,
  and conflicting older evidence. Compare current limitations with proposed
  behavior. Project positions stay stable and changed relationships are explicit.

The symbols represent schema relationships, not generated conversations or real
user data. The explorer does not connect to the backend, run inference, or
measure performance. Its source can support future project communication after
review; nothing has been published.

The next implementation should feed a **local** instance of this same inspection
pattern from the database: select a project → supporting claims → exact source;
select a change → before/after plus cause. Real traces remain local. Any later
community results export requires a separate, explicit preview of content-free
outcomes; this explorer is not an export mechanism.

## Review together before changing storage

Three decisions deserve your time:

1. **What is the reviewable unit?** Recommendation: individual claims with stable
   references, grouped into a readable brief. Does this let you correct Hoomy's
   account precisely without creating review homework?
2. **What makes one project?** Recommendation: an ongoing purpose, with supporting
   conversations and claims; allow multiple memberships and an unresolved bucket.
   Review cases of one conversation spanning projects and one project spanning
   repositories before locking the membership rules.
3. **What should a correction change, and for how long?** Recommendation: a rename
   persists until changed; a membership correction constrains that relationship;
   a status correction carries an effective time. Conflicting new evidence raises
   a visible review need without silently overriding your correction or freezing
   a time-sensitive state forever. Confirm these semantics together.

## Small next implementation, after this checkpoint

1. Version provenance and analysis runs; add stable claim/evidence references and
   append-only corrections. Demonstrate two interpretations of identical source
   bytes without destroying the earlier one.
2. Separate extraction from reconciliation; add revisioned project membership and
   visible conflict handling. Demonstrate a correction surviving a later append.
3. Make coverage and work durable; replace the whole-file size barrier with
   resumable records/chunks. Demonstrate partial writes, restart, large histories,
   historical backlog progress, and scope/reset races.
4. Connect the inspector to your authorized local data. Review interpretation,
   grouping, correction behavior, and the attention needed to understand changes.

Each step keeps the current app launch and reset procedures usable. No new service,
mobile distribution, autonomous action engine, or public data pipeline is needed
for this checkpoint.

## How we will assess this phase

First verify ingestion invariants: no duplicate logical conversations from file
moves; append-stable evidence; explicit gaps; independently repeatable runs;
correction preservation; coherent revisions; complete accounted coverage.

Then review actual authorized conversations locally: semantic support, omitted
intentions/decisions, grouping corrections, and review time. Record method and
coverage so differences can be explained. Use development feedback to improve the
method, then freeze a separate evaluation. Runtime checks are not user ratings.

The public `EXP-0001` remains a selected-session brief experiment. This private
progressive-discovery design is broader. Agree the evaluation/public description
before presenting discovery results as evidence for that experiment. Neither
brief accuracy nor grouping accuracy alone demonstrates better attention use.

## Implementation anchors

Review checks: all 58 Python tests and 5 Swift model tests pass. The explorer's
model/process controls and relationship changes were checked in the browser,
including narrow layouts and light/dark appearance. No discovery, real inference,
or reset was started for this review. No runtime source code was changed.

- [Collector](../hoomy/collector.py): `normalize`, `snapshot`, `session_paths`.
- [Edge](../hoomy/edge.py): `scan`, `tick` and pending-work selection.
- [Inference](../hoomy/inference.py): `analyze`, chunk reduction, method metadata.
- [Store](../hoomy/store.py): table definitions, `ingest`, `analysis`, `correct`,
  `split`, `review`, and `reset`.
- [Current API contract](../CONTRACT.md) and [operator procedures](../README.md).
