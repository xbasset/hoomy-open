# Target database model

Status: proposed design for review; not an applied migration. Updated 2026-09-07.

All examples below are fictional. IDs such as C1 are readable stand-ins for opaque production IDs; hashes are placeholders, not measured values. This document and the Excalidraw board are English-only. Original imported records are not translated or rewritten.

## Architecture and reading rules

Keep SQLite and the existing local backend. Separate immutable evidence, versioned interpretation, and a coherent current understanding. Metadata recognition must work before conversation bodies are captured. Extraction and reconciliation are separately repeatable stages. This specification extends the earlier ingestion proposal; it does not claim these tables exist today.

PK means primary key; FK means foreign key; a question mark means nullable. TEXT identifiers remain opaque. UTC means an ISO-8601 UTC timestamp stored as TEXT. JSON means validated JSON stored as TEXT, not a replacement for relational identity links. Fields shown on the board are the inspection-critical subset; table notes below add constraints and operational fields. N:N relationships use junction tables. Unless indicated otherwise, non-key fields are NOT NULL. A NULL example is a null value, not a row with ID “NULL”.

## Fictional walkthrough

R1 is a Codex installation. S1 authorizes its demo source root. C1 is a conversation found at L1. M1 captures its title, “Plan my vegetable garden”. Metadata-only run A0 proposes O1, “Family garden”, based on M1. A human acceptance correction creates P1 and confirms membership H1; no status is inferred from the title.

After the user chooses to deepen P1, snapshot V1 is normalized as N1. Event E1 says “I sowed tomatoes.” Run A1 reads E1 and produces brief B1 with claim K1v1 and evidence Q1. This is a reported outcome, not independent proof of completed work. Correction X1 renames P1 to “Kitchen garden”. U2 publishes that name while retaining the source and prior interpretation. X2 confirms membership H1. The illustrative conflict F1 compares a separate claim K2v1 with a previous correction X3; these labels describe additional hypothetical rows, not observed activity.

## Entity dictionary

### A — Evidence and identity

#### `source_realms`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'R1'` |
| `provider TEXT = 'codex'` |
| `installation_key TEXT = 'demo-mac'` |
| `created_at UTC = '2026-09-01T09:00:00Z'` |

UNIQUE(provider, installation_key). Native IDs are namespaced by realm.

#### `scope_revisions`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'S1'` |
| `FK realm_id = source_realms.R1` |
| `allowed_roots JSON = ["/demo/codex"]` |
| `excluded_roots JSON = ["/demo/client"]` |
| `destination TEXT = 'codex_subscription'` |
| `confirmed_at UTC = '2026-09-01T09:01:00Z'` |

Immutable authorization record. A new revision is not consent until confirmed.

#### `conversations`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'C1'` |
| `FK realm_id = source_realms.R1` |
| `native_id TEXT? = 'demo-thread-17'` |
| `FK parent_id? = conversations.NULL` |
| `identity_state TEXT = 'resolved'` |

UNIQUE(realm_id, native_id) where native_id IS NOT NULL. Paths and titles never define identity.

#### `source_locations`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'L1'` |
| `FK conversation_id = conversations.C1` |
| `FK realm_id = source_realms.R1` |
| `uri TEXT = '/demo/codex/sessions/17.jsonl'` |
| `first_seen_at UTC = '2026-09-01T09:02:00Z'` |
| `last_seen_at UTC = '2026-09-03T10:00:00Z'` |

Many locations may reference one conversation. Enforce realm consistency; moving a file does not create a conversation.

#### `metadata_observations`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'M1'` |
| `FK location_id = source_locations.L1` |
| `FK scope_id = scope_revisions.S1` |
| `title TEXT = 'Plan my vegetable garden'` |
| `cwd TEXT = '/demo/garden'` |
| `size_bytes INTEGER = 4096` |
| `modified_at UTC = '2026-09-03T09:59:00Z'` |
| `observed_at UTC = '2026-09-03T10:00:00Z'` |

Immutable metadata-only observation. This row exists before raw capture or message parsing.

#### `raw_snapshots`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'V1'` |
| `FK metadata_id = metadata_observations.M1` |
| `content_hash TEXT = 'sha256:<demo-64-hex>'` |
| `blob_ref TEXT = 'local://blobs/demo-1'` |
| `byte_start INTEGER = 0` |
| `byte_end INTEGER = 4096` |
| `captured_at UTC = '2026-09-03T10:02:00Z'` |

Immutable [start,end) byte coverage. Content-addressed bytes may be shared without collapsing conversation identity.

#### `normalizations`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'N1'` |
| `FK snapshot_id = raw_snapshots.V1` |
| `parser_version TEXT = 'codex-jsonl-v2'` |
| `coverage JSON = {"records":[1,18]}` |
| `gaps JSON = []` |
| `created_at UTC = '2026-09-03T10:02:01Z'` |

UNIQUE(snapshot_id, parser_version, config_hash); config_hash TEXT = 'demo-parser-config'. Gaps include unsupported and partial records.

#### `event_versions`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'E1'` |
| `FK normalization_id = normalizations.N1` |
| `event_key TEXT = 'native-message-7'` |
| `kind TEXT = 'message'; actor TEXT = 'user'` |
| `blocks JSON = [{"text":"I sowed tomatoes."}]` |
| `raw_span JSON = {"start":880,"end":1040}` |
| `source_at UTC? = '2026-09-03T09:50:00Z'` |
| `FK supersedes_id? = event_versions.NULL` |

UNIQUE(normalization_id,event_key). Retain event_key across unchanged append-only records; rewritten records get a new version. Namespace event_key by conversation.

### B — Versioned interpretation

#### `analysis_runs`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'A1'` |
| `FK scope_id = scope_revisions.S1` |
| `stage TEXT = 'extract'` |
| `model TEXT = 'gpt-5.6-luna'` |
| `adapter_version TEXT = 'demo-v2'` |
| `config_hash TEXT = 'demo-prompt-1'` |
| `FK input_revision_id? = understanding_revisions.U1` |
| `status TEXT = "complete"; usage JSON? = NULL` |

Also started_at UTC, finished_at UTC?, correction_cutoff_id FK? to corrections. Each retry is a separate run; unknown usage stays NULL. Model is illustrative, not a benchmark recommendation.

#### `analysis_inputs`

| Field, type, and example value |
| --- |
| `PK/FK run_id = analysis_runs.A1` |
| `PK/FK event_version_id = event_versions.E1` |

Exact event-level input membership. Metadata recognition uses analysis_metadata_inputs instead.

#### `analysis_metadata_inputs`

| Field, type, and example value |
| --- |
| `PK/FK run_id = analysis_runs.A0` |
| `PK/FK metadata_id = metadata_observations.M1` |

A0 is a fictional recognize-stage run. Persist exactly which titles were submitted, including the scope revision.

#### `briefs`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'B1'` |
| `FK conversation_id = conversations.C1` |
| `FK run_id = analysis_runs.A1` |
| `coverage JSON = {"events":["E1"]}` |
| `limitations JSON = ["text_only"]` |
| `FK supersedes_id? = briefs.NULL` |

Immutable interpretation of an explicit range; covered events must be admitted inputs belonging to this conversation.

#### `claim_versions`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'K1v1'` |
| `lineage_id TEXT = 'K1'; version INTEGER = 1` |
| `FK run_id = analysis_runs.A1` |
| `type TEXT = 'reported_outcome'` |
| `text TEXT = 'The user reports sowing tomatoes.'` |
| `attribution TEXT = 'user_stated'` |
| `effective_at UTC? = NULL` |
| `FK supersedes_id? = claim_versions.NULL` |

UNIQUE(lineage_id,version). Types also include intention, decision, open_question, candidate_next_step. Reinterpretation creates a version, not an overwrite.

#### `brief_claims`

| Field, type, and example value |
| --- |
| `PK/FK brief_id = briefs.B1` |
| `PK/FK claim_version_id = claim_versions.K1v1` |
| `position INTEGER = 0` |

A claim can be reused in several briefs; stable IDs replace section/index addresses.

#### `claim_evidence`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'Q1'` |
| `FK claim_version_id = claim_versions.K1v1` |
| `FK event_version_id = event_versions.E1` |
| `text_span JSON = {"block":0,"start":0,"end":16}` |
| `quote TEXT = 'I sowed tomatoes.'` |
| `locator_valid BOOLEAN = true` |
| `exact_match BOOLEAN = true` |
| `semantic_support TEXT = 'unchecked'` |

Text offsets use Unicode code-point [start,end) ranges. Mechanical quotation checks do not establish semantic support. User assessment is recorded as an append-only correction.

### C — Projects and human reconciliation

#### `projects`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'P1'` |
| `created_at UTC = '2026-09-03T10:01:00Z'` |

Stable identity only. Displayed fields and merge redirects belong to project_states at a named understanding revision.

#### `recognition_proposals`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'O1'` |
| `FK run_id = analysis_runs.A0` |
| `name TEXT = 'Family garden'` |
| `purpose TEXT = 'Grow vegetables at home'` |
| `rationale TEXT = 'Related garden titles'` |
| `FK existing_project_id? = projects.NULL` |

Immutable metadata hypothesis. Accept/reject is a correction; accepting creates a project or attaches to an existing one. Up to three proposals per recognition run.

#### `proposal_sources`

| Field, type, and example value |
| --- |
| `PK/FK proposal_id = recognition_proposals.O1` |
| `PK/FK metadata_id = metadata_observations.M1` |

N:N provenance using exact title observations, not mutable current titles. Must be an input to the proposal's recognition run.

#### `membership_hypotheses`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'H1'` |
| `FK project_id = projects.P1` |
| `FK conversation_id = conversations.C1` |
| `FK run_id? = analysis_runs.A0` |
| `FK proposal_id? = recognition_proposals.O1` |
| `FK supporting_claim_id? = claim_versions.NULL` |
| `rationale TEXT = 'Garden planning thread'` |

Immutable proposed conversation-to-project relationship. Claim support is optional. Effective accept/reject state belongs to revision_memberships; corrections can constrain it.

#### `corrections`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'X1'; author TEXT = 'user'` |
| `FK project_id? = projects.P1` |
| `FK claim_version_id? = claim_versions.NULL` |
| `FK membership_id? = membership_hypotheses.NULL` |
| `FK proposal_id? = recognition_proposals.NULL` |
| `operation TEXT = 'set_name'` |
| `value JSON = "Kitchen garden"` |
| `FK supersedes_id? = corrections.NULL` |

Exactly one target FK is non-NULL. Also reason TEXT?, recorded_at UTC, effective_from UTC?, effective_to UTC?, applicability TEXT = 'until_superseded'. Append-only; NULL interval bounds mean open bounds, not guessed dates.

#### `understanding_revisions`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'U2'` |
| `FK parent_id? = understanding_revisions.U1` |
| `FK run_id? = analysis_runs.NULL` |
| `created_at UTC = '2026-09-03T10:05:00Z'` |
| `reason TEXT = 'User renamed project'` |

An immutable coherent published view. U1 is the preceding fictional revision. One local head pointer is atomically advanced after all projection rows commit.

#### `project_states`

| Field, type, and example value |
| --- |
| `PK/FK revision_id = understanding_revisions.U2` |
| `PK/FK project_id = projects.P1` |
| `name TEXT = 'Kitchen garden'` |
| `purpose TEXT = 'Grow vegetables at home'` |
| `status TEXT = 'unclear'` |
| `confirmed_fields JSON = ["name","purpose"]` |
| `FK merged_into_id? = projects.NULL` |

Effective fields at a revision. Merge targets must exist in the same revision, be acyclic, and never redirect to self. Raw evidence and earlier revisions stay unchanged.

#### `revision_memberships`

| Field, type, and example value |
| --- |
| `PK/FK revision_id = understanding_revisions.U2` |
| `PK/FK membership_id = membership_hypotheses.H1` |
| `state TEXT = 'confirmed'` |
| `FK correction_id? = corrections.X2` |

X2 is a fictional confirm_membership correction targeting H1. Valid states: proposed, confirmed, rejected. Explicit rejection is retained, not represented as absence.

### D — Explanations and durable work

#### `revision_causes`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'RC1'` |
| `FK revision_id = understanding_revisions.U2` |
| `FK project_id = projects.P1` |
| `field TEXT = 'name'` |
| `FK claim_version_id? = claim_versions.NULL` |
| `FK correction_id? = corrections.X1` |

Exactly one cause FK is set. Composite FK (revision_id,project_id) → project_states. Multiple causes are separate rows, not comma-separated IDs.

#### `conflicts`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'F1'` |
| `FK revision_id = understanding_revisions.U2` |
| `FK project_id = projects.P1` |
| `field TEXT = 'status'` |
| `FK claim_version_id = claim_versions.K2v1` |
| `FK correction_id = corrections.X3` |
| `state TEXT = 'unresolved'` |

Example K2v1 claims active work; X3 is a prior paused-status correction. Conflicting evidence is visible, not silently allowed to erase a correction. Resolution appears in a later revision.

#### `work_items`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'W1'` |
| `FK scope_id = scope_revisions.S1` |
| `FK metadata_id? = metadata_observations.M1` |
| `FK snapshot_id? = raw_snapshots.NULL` |
| `FK normalization_id? = normalizations.NULL` |
| `FK input_revision_id? = understanding_revisions.NULL` |
| `stage TEXT = 'capture'; state TEXT = 'queued'` |
| `input_config_key TEXT = 'demo-capture-M1'` |

One typed primary input appropriate to stage. Also generation TEXT, priority INTEGER, byte/range checkpoint JSON, next_attempt_at UTC?, created_at UTC. UNIQUE(input_config_key) inside one generation.

#### `work_attempts`

| Field, type, and example value |
| --- |
| `PK id TEXT = 'WA1'` |
| `FK work_item_id = work_items.W1` |
| `attempt INTEGER = 1; state TEXT = 'failed'` |
| `FK run_id? = analysis_runs.NULL` |
| `checkpoint JSON = {"byte_end":2048}` |
| `failure_code TEXT? = 'transport_timeout'` |
| `started_at UTC = '2026-09-03T10:02:00Z'` |
| `finished_at UTC? = '2026-09-03T10:02:30Z'` |

UNIQUE(work_item_id,attempt). Completed chunk outputs persist; retry appends an attempt. Lease expiry and generation checks prevent abandoned or stale attempts from publishing.

#### `local_state`

| Field, type, and example value |
| --- |
| `PK key TEXT = 'understanding_head'` |
| `FK revision_id? = understanding_revisions.U2` |
| `value JSON? = NULL` |

Singleton head with a real FK; a separate generation key stores JSON text. CHECK constraints distinguish allowed key/value shapes. Operational pointers are mutable; historical records are not.

## Referential integrity and publication rules

- Enable foreign keys on every connection. Use explicit FK indexes and composite FKs where ownership must match (realm, conversation, revision). A cross-conversation evidence link is rejected even when each independent ID exists.
- Production DDL must add NOT NULL, CHECK enums, JSON validity, nonnegative range checks, and the uniqueness rules described above. Every conditional target has an exactly-one rule; no generic unvalidated target_id field.
- Validate evidence offsets against the immutable normalized block and its mapping to raw bytes. A valid quotation does not prove semantic support. Preserve source time separately from observation and processing time.
- A run may consume only admitted inputs under its scope. Save exact event or metadata input links. Reconciliation also pins its input understanding revision and correction cutoff; it cannot silently use later corrections.
- Raw snapshots, normalization results, event versions, completed runs, briefs, claims, proposals, hypotheses, corrections, and published projections are append-only. Updates to run/attempt status are allowed while incomplete; final outputs are immutable. Work state and the local head pointer are operational exceptions.
- Claim lineages and supersession chains must be acyclic. A replacement correction supersedes another correction; it never edits the original record. Time-scoped corrections retain their effective interval.
- Commit a complete revision with project states, effective memberships, causes, and conflicts in one transaction, then advance the head using compare-and-swap on parent_id. Carry unchanged projection rows into the new revision; do not publish a partially updated project list.
- Empty or unknown status remains unclear. No claim, date, or candidate_next_step authorizes an agent action or automatically creates a reminder.
- A completed work item means complete for its recorded input and coverage. Partial trailing input waits for append; unsupported and invalid middle records remain visible gaps. Store leases, bounded retries, safe failure codes, and generation guards; never put source content in error logs.
- Deletion is an explicit scope-aware workflow, not an inference side effect. Retain referential integrity while deleting a permitted subgraph; shared blobs require reference counting. Changing authorization does not imply retroactive erasure at the inference provider.

## Indexes and boundaries

Index every FK used for lookups. Prioritize conversations(realm_id,native_id), metadata_observations(location_id,observed_at), raw_snapshots(metadata_id), normalizations(snapshot_id,parser_version,config_hash), event_versions(normalization_id,event_key), analysis input joins in both directions, claim_evidence(claim_version_id), membership_hypotheses(project_id,conversation_id), corrections(target FK,recorded_at), project_states(revision_id,project_id), and work_items(state,next_attempt_at,priority). Deduplication is by input identity plus configuration, not by a model's output text.

The diagram is a reviewable relational proposal, not executable SQL. Stage-specific work-input checks, complete enum lists, event identity for providers without native IDs, conflict resolution beyond claim-versus-correction, and selective-deletion rules must be finalized before generating a migration. Provider-specific content blocks remain JSON; identities, provenance, and many-to-many links remain relational.

## From current to target

| Current pilot | Proposed boundary |
| --- | --- |
| catalog | conversations + source_locations + metadata_observations |
| outlines / outline_members | recognition_proposals + proposal_sources + explicit corrections |
| catalog_links / associations | membership_hypotheses + revision_memberships |
| sessions / snapshots | conversations + immutable raw_snapshots + independent normalizations / event_versions |
| analyses with embedded brief | analysis_runs + exact input joins + briefs + claim_versions + claim_evidence |
| reviews overwriting one snapshot review | append-only corrections; a future evaluation record may separately hold aggregate verdict/time |
| projects.inferred / overrides | stable projects + revisioned project_states + correction causes |
| changes | understanding_revisions + revision_causes + explicit conflicts |
| overview queue in settings | work_items + work_attempts + constrained local_state pointers |

Retain the existing UI flow: metadata census → up to three recognition proposals → human recognition → selected detailed reading. Introduce these boundaries incrementally; no new service or automatic whole-history inference is required.

## Language convention

Project-authored technical documentation, database names, field names, diagrams, and fictional examples use English. Preserve original imported source documents verbatim. Runtime UI localization is a separate concern and is not changed by this schema-design task.
