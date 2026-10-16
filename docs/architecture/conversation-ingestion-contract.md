# Scoped conversation ingestion contract

The operator selects one canonical Hoomy project ID. The owner reviews separate
Mac and VPS history roots and working-directory bindings for that project. Existing presence-only grants do not enable collection. No broad collection
or inference starts implicitly.

## Policy and authorization

Keep the collector resource audience and add the explicitly approved scope
`evidence:ingest` alongside `device:self`. Collection enrollment proposes:

```json
{
  "content_tier": "conversation_visible_v1",
  "collection_enabled": true,
  "roots": ["/home/alex/.codex/sessions"],
  "exclusions": [],
  "project_bindings": [
    {"project_id": "project-alpha", "cwd_roots": ["/home/alex/work/alpha"]}
  ]
}
```

Paths are absolute, normalized and positive allowlists. Match a working root or
its descendants by path components, never a string prefix or directory name.
Only approved `sessions`/`archived_sessions` history directories are readable.
Unknown cwd, symlinks, changing sources and stale policy revisions fail closed.
The first pilot permits one project per device. Server-owned approved policy and
authenticated principal decide admission; event-supplied IDs do not grant access.

Existing enrolled devices initiate `POST /v1/devices/self/policy-proposals`
under `device:self`. The server creates an expiring account/device-bound ticket
and next revision without applying it. The agent starts a new device
authorization requesting `device:self evidence:ingest` with that ticket. Explicit
phone consent replaces only that device's policy/grant; denial preserves its
existing presence authorization. Ordinary upgrades do not request ingestion.

`conversation_visible_v1` contains user messages and assistant final messages
(including absent-channel final records) from supported `response_item` records.
Exclude reasoning/analysis, commentary, tool calls/outputs, system/developer
records, injected configuration/instruction context and credential-bearing
messages. Preserve source language and line references. Report omissions as
bounded counters, never log excluded text. Do not upload original JSONL bytes.

Internal permission-review sessions are not project conversation evidence.
Classify only the canonical first metadata record's exact native source markers
`{"internal":"guardian"}` or `{"subagent":{"other":"guardian"}}`.
Do not infer this classification from titles, message wording, filename or the
analytics `thread_source` field alone. Ordinary CLI/editor sessions and working
subagents, including declared forks, retain their existing eligibility.

## Immutable event

`POST /v1/events` accepts one event with `Idempotency-Key` equal to `event_id`.
Use this shape, rejecting unknown fields:

```json
{
  "schema_version": 1,
  "event_id": "generated-uuid",
  "type": "conversation.projected",
  "project_id": "project-alpha",
  "policy_revision": 2,
  "connector": "codex",
  "session_id": "native-session-id",
  "source_location": {
    "path": "/home/alex/.codex/sessions/session.jsonl",
    "cwd": "/home/alex/work/alpha",
    "archive_occurrence": false,
    "modified_at": "2026-10-12T12:00:00Z"
  },
  "sequence": 1,
  "source_sha256": "64-lowercase-hex-characters",
  "projected_sha256": "64-lowercase-hex-characters",
  "observed_at": "2026-10-12T12:01:00Z",
  "title": "Project Alpha update",
  "coverage": {
    "excluded_records": 0,
    "excluded_sensitive_messages": 0,
    "excluded_context_messages": 0,
    "excluded_oversized_records": 0
  },
  "projection": {
    "version": 1,
    "messages": [
      {"role": "user", "text": "Review the latest change.",
       "line_start": 2, "line_end": 2, "timestamp": "2026-10-12T12:00:00Z"}
    ]
  }
}
```

Canonical JSON is UTF-8, sorted object keys, compact separators, preserved
Unicode, and no floats, NaN or duplicate keys. Projection SHA covers exactly
`projection`, excluding location, timestamps outside messages and title. Source
SHA is the agent's original-byte provenance claim; the server verifies the
projected SHA. Backend-generated message IDs use projected SHA and line refs.
Use integer counters and RFC3339 timestamp strings (message timestamp may be
null). Require positive sequences per `(device, project, connector, session,
source path, archive occurrence)`; archive copies retain separate locations.

Source limit is 256 MiB with streaming original-byte hashing and a 1 MiB JSONL
record buffer. Agent 0.2.1 rejects the entire source if any record exceeds that
bound. Agent 0.2.2 streams a strict bounded JSON classifier over
an oversized record and omits only a proven tool/reasoning record or an assistant
analysis message. It validates the complete record, UTF-8, duplicate keys and
scope-bearing fields before recording an omission. Unknown types, malformed
records, metadata, scope changes or visible messages above the bound reject the
entire source without advancing its checkpoint. Never blindly drain an unknown
record and collect its following messages. Accepted omissions increment
`excluded_oversized_records` and explicitly mark the projection partial.
Agent 0.2.3 additionally accepts a strictly typed, closed Codex compaction
checkpoint as an omission. It never reconstructs checkpoint text. Supported
history entries must be typed objects; unknown fields, nested native context
and unknown scope paths reject the capture. The exact token-usage telemetry
`session_id` is a UUID distinct from native thread identity; a present usage
`thread_id` must belong to the already declared lineage. Nullable unsigned
64-bit rollout ordinals are validated and never exported. This treatment is
limited to checkpoints and leaves public-message selection unchanged.
Missing final newline or concurrent source changes also reject
the capture for retry. Projected evidence always reports its supported subset;
do not claim complete conversation coverage.
The first native session metadata ID remains canonical for a fork. Later
metadata is admissible only for that ID or an ancestor reachable through an
already validated `forked_from_id` declaration. Bound the ancestry graph to
64 identities and reject cycles, changed parent declarations and unknown IDs.
Every metadata and turn context cwd must remain in the same approved project.
Never infer the canonical session ID from a filename.
Event JSON limit is 24 MiB; projection limit 18 MiB; at most
10,000 projected messages and 256 KiB UTF-8 per message. Oversized captures have
explicit failure/coverage reporting; they are not silently truncated. The
private outbox defaults to 256 MiB with backpressure, atomic capture/checkpoint
transactions, bounded retries and no credential material in events.

## Durable acceptance and reads

### Metadata-only source exclusion

The same authenticated events endpoint accepts this closed variant:

```json
{
  "schema_version": 1,
  "event_id": "generated-uuid",
  "type": "conversation.source_excluded",
  "project_id": "project-alpha",
  "policy_revision": 2,
  "connector": "codex",
  "session_id": "native-session-id",
  "source_location": {
    "path": "/home/alex/.codex/sessions/rollout.jsonl",
    "cwd": "/home/alex/work/alpha",
    "archive_occurrence": false,
    "modified_at": "2026-10-12T12:00:00Z"
  },
  "sequence": 2,
  "observed_at": "2026-10-12T13:00:00Z",
  "exclusion": {
    "reason": "internal_approval_review",
    "discriminator": "legacy_subagent_guardian",
    "header_sha256": "<64 lowercase hexadecimal characters>"
  }
}
```

The other permitted discriminator is `internal_guardian`. There is no message
projection, title, coverage, whole-source hash or projected hash in this event.
Apply the same account/device/project/policy/path admission as a projected event.
The agent emits it only after positive cwd admission and secure, stable first
header validation, before any source-body prefetch. Hash the exact first raw
header bytes; the digest is an agent provenance claim, not a server-verified
whole-source hash. No raw header or internal conversation is uploaded.

An additive, sticky classification applies only to that exact device/source
location. Exclude it from default project sources and reject cached reads by
saved source ID. New projected events cannot reactivate a classified location;
identical previously stored event replay still returns its original receipt.
Keep old blobs, versions and receipts immutable for operator audit. Never alter
approved context or guess a machine for legacy unassigned sources.

The exclusion receipt contains `receipt_id`, `event_id`, `project_id`,
`event_sha256`, `state: "stored"`, `inference_state: "not_requested"`,
`received_at`, `source_version_id`, `event_type: "conversation.source_excluded"`
and `header_sha256`. It has no `projected_sha256`. A durable immutable source
classification version backs the receipt; any internal empty storage placeholder
is not exposed as a collected conversation. Exact receipt validation precedes
dequeue, including after a lost acknowledgement. Display excluded internal
source counts separately from current conversation coverage and retained audit
history. A local adapter revision forces one reclassification of existing
checkpoints without widening permissions or deleting queued events.

### Projected conversations

Gateway constructs the private envelope `{principal, policy, event}` from
authenticated state and submits `POST /api/ingestion/events`. One backend
transaction commits event, project-scoped blob, source location/version,
association and receipt before the public API returns 202. Repeated identical
events return the original receipt; reused keys or sequences with different
content return 409. No host-wide replacement inventory or source deletion.

Receipt ID equals event ID, scoped to authenticated account/device. Receipt
includes `receipt_id`, `event_id`, `project_id`, `projected_sha256`,
`event_sha256`, `state: "stored"`, `inference_state: "not_requested"`,
`received_at` and `source_version_id`. The client validates exact identifiers
and digests before removing queued bytes. `GET /v1/receipts/:id` exposes only
the authenticated device's receipt, backed by fixed private
`POST /api/ingestion/receipts/query` with `{principal, event_id}` carrying verified
account/device identity. Local outbox retries use identical event bytes.

Source versions are immutable. Highest sequence selects the current version
only within one source location. Preserve late versions and divergent locations;
do not choose a global winner by host clock. Identical projection blobs have
one account/project-scoped blob and distinct device locations.

Existing project profiles add provenance and cached-evidence coverage without
changing approved context revisions. Add bounded cached reads and a
`hoomy_project_evidence` MCP tool under `projects:read`; ingestion credentials
cannot use it. Read by canonical project and remote source ID, with integer
offset/limit, bounded excerpts and explicit coverage. Expose corresponding
public project-evidence reads. No local path opens or model execution.

## Implementation ownership

- Collector agent: `agent/`, `distribution/`, agent README; no live publication.
- Gateway agent: `services/hoomy-gateway/`; OAuth policy, events/receipts and
  public project evidence authorization.
- Backend agent: `backend/hoomy/`, pilot tests; additive data migrations, cached
  evidence, plus `plugins/hoomy/` for the read bridge/tool and provenance UI.
- Orchestrator: this contract, deployment/runbooks, pilot scope confirmation,
  backup/migration rehearsal, signing and coordinated live acceptance.

Coordinate private route details and receipt defaults directly between agents.
Do not change the shared wire shape without notifying the orchestrator.
