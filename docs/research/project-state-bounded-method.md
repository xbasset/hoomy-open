# Bounded project-state methods: measurements and implementation contracts

Research record: comparative v1/v2/v3 measurements from 13 October 2026,
followed by the bounded v4 trial and reader-quality findings below. The current
execution contract is [project state](../architecture/project-state-api.md). The independently reproduced native
notification-preservation defect in method v1 has a separate correction. These
capacity measurements do
not establish the cause of either interrupted live call. No additional model
call was made for this report.

## Existing plan and measured inputs

Measurements used the same consistent, read-only NoFlinch project snapshot and
its approved immutable visible-message projections. No original Codex JSONL,
auth database, hidden analysis or other project's conversation bodies were read.
Only counts and sizes were emitted; source text, titles, paths and identifiers
are intentionally absent from this report.

The frozen v1 selection contains five planned extraction slots, 343 message
aliases, 17 source locations and two devices. This is a selection inventory,
**not proof that all those inputs were analyzed**. Only a successful completed
run can substantiate its complete recorded input coverage.

| Planned v1 extraction slot | Messages | Actual serialized payload bytes |
| --- | ---: | ---: |
| 0 | 53 | 49,783 |
| 1 | 40 | 50,169 |
| 2 | 87 | 50,824 |
| 3 | 115 | 51,160 |
| 4 | 48 | 20,387 |

The first slot contains 40,403 visible-text bytes; its full message array is
49,027 bytes. Approved context is an empty object of two bytes, coverage is 685
bytes, and the output schema is 2,307 bytes. The source/version span registry is
already excluded from model input. Repeated coverage accounts for less than two
percent of this payload; it is not demonstrated as the dominant load or failure
cause. V1's 48 KiB check bounds compact message records, not the complete native
prompt or potential generated response.

## Prototype selection

The prototype performs no inference or database mutation. It reserves machine
representation, visits unique projections in round-robin order, alternates
recent and opening context and clips individual message prefixes at valid UTF-8
boundaries. Identical projection content remains one input with multiple
provenances, not independent corroboration.

| Prototype case | Payload bytes per slot | Controlled protocol-envelope bytes per slot | Selected messages | Unread messages |
| --- | --- | --- | ---: | ---: |
| Actual context | 24,425 / 24,389 | 28,840 / 28,756 | 85 of 656 | 571 |
| Fictional 4-byte Unicode context, 4,024 bytes | 24,547 / 24,299 | 28,735 / 28,589 | 71 of 656 | 585 |

Both cases represent all 17 locations across both devices, distributed as four
and thirteen locations. Thirteen actual-context message excerpts are truncated;
the fictional-context case has ten. These are candidate selections only, not
completed model reads. The proposed result must make this substantial partial
coverage visible. It cannot describe the corpus as fully analyzed or treat
unread messages as evidence of absence.

The protocol envelope includes fixed instructions, output schema, escaped input
text and conservative bounded placeholders for protocol identifiers. It does
not claim to meter private provider or native-harness instruction overhead.

## Frozen phase limits

The method uses at most two extraction calls and one reconciliation call,
with the existing absolute 300-second run deadline. Initialization and transport
time count against that deadline. Initialization is bounded at 40 seconds;
each extraction/reconciliation call is bounded at 75/90 seconds. Extraction
must leave a 100-second reconciliation reserve. Preparation, control traffic and
network overhead remain within the absolute deadline. No latency guarantee
follows from these limits.

| Bound | Extraction | Reconciliation |
| --- | --- | --- |
| Complete serialized evidence/context payload | 24 KiB | 56 KiB ceiling; conservative bounded construction is smaller |
| Application-controlled protocol envelope | 32 KiB | 64 KiB |
| Accepted serialized output | **12 KiB** | 48 KiB |
| Assertions | At most 6 | At most 12 |
| Uncertainties | At most 2 | At most 4 |
| Contradictions | None | At most 3, across persisted extractions |
| References per item | At most 2 | At most 2 |
| Exact quote length | At most 160 Unicode characters | At most 160 Unicode characters |

Counts are upper limits, not an obligation to fill every item. An oversized
extraction output fails with its known usage retained; it is not silently pruned
to make reconciliation fit. Reconciliation receives every validated extraction
record and may cite only the exact validated alias/quote pairs it received.

Approved-context excerpts have a four-KiB serialized budget. Keep cleared,
skipped and missing answers distinct. Preserve full approved records and their
revision centrally; record omitted dimensions and exact UTF-8 prefix truncation
in input coverage. Full source/device/version/date/line registries remain on the
server. Model input uses opaque message and machine aliases plus compact coverage
counts. Quotes must belong to the actual provided message prefix, not another
part of its cached full body.

The native catalogue advertises `low` and `medium` for the currently selected
`gpt-6.1-sol`. Frozen extraction effort is low and reconciliation effort is
medium, using the same model and OpenAI provider. This capability check used zero
turns. The effort records the supported request accepted by the native turn RPC,
not independently verified provider effort or billing. The installed native turn
schema supports `effort` and `outputSchema`, but
has no output-token-limit field. The existing 200,000-observed-token ceiling,
unknown-usage stop and no-automatic-redispatch rule remain necessary. Native usage
is the latest correlated observation, not a billing measurement. Neither byte
limits nor a deadline guarantee aggregate spend.

## Actual serializer proof

`inference._Client.extract` serializes input with
`json.dumps(payload, ensure_ascii=False)`. `_Client.send` also uses
`ensure_ascii=False`, writes UTF-8 and appends a newline. A test invoked the real
`_Client.send` against an in-memory fake process, without starting Codex or
contacting a provider. It round-tripped the full request and nested input text.

Each fictional case respected the prospective field-length limits, the
12-KiB extraction-output quota and an **exactly 4,096-byte** serialized context.
Both validated records were retained in reconciliation input.

| Fictional encoding pressure | One accepted extraction output | Reconciliation payload | Actual native send including newline | Envelope including serialized instructions |
| --- | ---: | ---: | ---: | ---: |
| Four-byte Unicode | 12,276 | 28,743 | 32,013 | 32,560 |
| Backslashes | 12,268 | 28,727 | 57,301 | 57,848 |
| Quotes | 12,268 | 28,727 | 57,301 | 57,848 |
| Escaped controls | 12,188 | 28,567 | 36,027 | 36,574 |
| Mixed Unicode, controls, quotes and backslashes | 12,184 | 28,559 | 40,702 | 41,249 |

For valid Unicode scalar strings under this **actual ASCII-false serializer**,
quoting an existing UTF-8 JSON text expands its bytes by at most a factor of two.
Non-ASCII scalar UTF-8 is preserved; quotes and backslashes can double; escaped
controls in the inner JSON are already ASCII escape sequences. This is not a
general claim for `ensure_ascii=True`, which can expand astral Unicode further.

Enforced construction quotas give a conservative bound:

- Two extraction records: at most 24,576 serialized bytes in total.
- Context: at most 4,096 bytes; compact coverage: at most 512 bytes; fixed payload
  structure: at most 512 bytes.
- Complete reconciliation input: at most 29,696 bytes.
- Its quoted native representation: at most 59,392 bytes.
- Schema: at most 3,072 bytes; serialized instructions: at most 1,024 bytes;
  bounded controlled protocol wrapper: at most 1,024 bytes.
- Total: at most **64,512 bytes**, below the 65,536-byte envelope cap.

The implementation measures the exact complete native serialization before
central dispatch intent and again before the actual native write, refusing an
oversized packet. A serializer or protocol change
must invalidate this method configuration and its tests, rather than relying on
the arithmetic alone. Known rejected-output usage remains recorded; unknown
usage remains unknown. No tokens were estimated from these byte counts.

## Version and authorization boundary

The distinct `project-state-v2` descriptor has canonical hash
`cabc1ab9ff1b1b2f9dceeced996ee597affb98fc10865bd808f209e50572c4ca`.
Its canonical hash includes phase schema hashes,
selection/context rules, limits, model/provider policy, effort, deadline,
reconciliation reserve and serialization. The input manifest hash includes that
descriptor. Context and corpus revisions retain their existing meanings.

Expose the current method version/hash separately from each job's stored method.
An old manifest lacking a descriptor remains explicitly legacy with an unknown
stored method hash; do not invent a historical configuration. A ready v1 result
can remain an eligible cached historical read, marked `method_changed` and stale.
Refuse v1 claim/attempt/retry under v2 before any model call, without rewriting
its inputs, run or unknown-usage ledger.

Explicit refresh carries the current `method_hash` read by the UI or native
helper. Missing or stale hashes conflict and require a reread; malformed hashes
are invalid. The existing native OAuth permissions suffice. Exact-method reviewed
retry continues to copy its own immutable inputs; it is not a mechanism for
silently applying a different method to v1.

This document records the frozen implementation and its no-provider measurement
proof. Healthy deployment and native catalogue/configuration checks do not prove
a real v2 generated result; the later failed trial is recorded below. None of the
prototype counts establish published conclusions.

## V3: resolved immutable passages and complete reconciliation bound

The authorized v2 trial failed literal quote membership on one of nine model
references. All supplied aliases existed; eight references were exact, while
one contained generated replacement characters. The validator correctly refused
publication. No quote was repaired and no further provider call was authorized
by that trial.

The distinct `project-state-v3` descriptor hash is
`c247a076a1712ab3300107ff98e9e5cf9f07abe2c4c275e34e3a546495fe42aa`.
Model references are now closed `{passage_id}` objects. The server preassigns
112-scalar contiguous slices of the already approved prefixes, preserving all
text without duplication. It resolves exact quotes and original offsets from the
frozen registry. Reconciliation receives the referenced original passages once,
not opaque IDs alone or whole-message citation fallback.

Keeping 12 KiB extraction outputs alongside resolved passage text did not fit
the existing native cap under mixed escaping. The explicitly versioned v3
accepted extraction cap is **6 KiB**; counts remain six assertions, two
uncertainties and two references per item. Other input, output, model, effort and
deadline limits are unchanged. This is a recorded method change, not silent
pruning of a completed result or rewriting a v2 run.

The complete conservative reconciliation bound accounts for all 32 possible
referenced passages, maximal permitted byte quotas and escaping:

| Component | Bound in bytes |
| --- | ---: |
| Two accepted extraction records through outer JSON encoding | 24,576 |
| Exact 4 KiB approved-context quota through outer encoding | 8,192 |
| 512-byte compact coverage quota through outer encoding | 1,024 |
| 32 × 112 source scalars, at most seven bytes after two encodings | 25,088 |
| Exact payload framing with 32 fixed-format passage IDs | 1,650 |
| Frozen reconciliation schema | 2,211 |
| Frozen serialized instructions | 855 |
| Reserved protocol wrapper, including maximal bounded IDs | 1,024 |
| Native newline | 1 |
| **Total** | **64,621** |

This is below 65,536 bytes by 915 bytes. The maximal tested 128-control-byte
thread identifier and safe-integer request ID consume 974 wrapper bytes.
The actual native request is still measured before intent and before its write;
the bound does not replace either guard. Rejected known usage remains recorded.
Neither byte counts nor native usage counters guarantee billing.

The independent count-only v3 measurement on the same frozen approved snapshot
selects **65 of 656 messages** in two slots of 27 and 38, containing 266 exact
passages. All 17 locations remain represented, four VPS and thirteen Mac.
Nine message prefixes are truncated and 591 messages unread. Serialized payloads
are 24,467/24,042 bytes and bounded native frames 29,821/29,413 bytes. The actual
approved context is two bytes and compact coverage 155 bytes.

These are planned-input measurements made without a model, not proof of
completed reads or publication. Added passage-ID metadata changes the bounded
selection, while each selected prefix remains lossless across its slices. The
historical 85-of-656 prototype belongs to v2 and must not be reused for v3.


## Device-specific evidence requirement


`project-state-v4` has descriptor hash
`08e25c40f12d032becc99bbc35b279a369bd80e1305115796c7849668bc59450`.
It retains v3's model, phase efforts, 112-scalar passages and all input, output,
wire, deadline and observed-usage limits. Each distinct projection is assigned
once to its first origin device, retaining every origin. At most two devices
receive separate extraction slots without spill between slots; a larger scope
stops before dispatch. Supported assertions or scoped uncertainties must cite
the assigned device, and reconciliation must retain evidence for every required
device. Empty results remain legal but stop with `insufficient_device_evidence`,
with known usage preserved. No facts are invented to meet that requirement.

Reconciliation groups validated outputs under compact machine headers and
receives each referenced passage's text once. Shared copies remain one witness.
Corpus counters are server metadata, not model-authored conversation conclusions;
new outputs cannot use `coverage_gap`. Historical records retain their taxonomy.
Instructions require complete short sentences and proof within selected passages.

The existing v3 ready result and its 34,753-token ledger remain immutable. All its
resolved citations originated on Mac despite two-device input. A read-time
`device_coverage` qualifier reports provided, cited and uncited devices, so the
panel labels that result as partial across machines. This amendment does not
establish acceptance of a new generated state or authorize another provider call.


## Trial outcomes and watcher reliability

These aggregate findings describe bounded trials, not a current independent
verification of the conversation claims or universal runtime performance.
Exact deployment receipts and operator timelines remain in Git history.

The historical `project-state-v3` method hash is
`c247a076a1712ab3300107ff98e9e5cf9f07abe2c4c275e34e3a546495fe42aa`.
The historical v2 method hash is
`cabc1ab9ff1b1b2f9dceeced996ee597affb98fc10865bd808f209e50572c4ca`.
One human-authorized v2 extraction reached native completion and reported 12,606
observed tokens (11,385 input and 1,221 output). Its freeform citation failed
exact membership validation, so the job is `failed`, with no published state.
The context and corpus revisions remained unchanged. The trial is consumed;
there is no automatic retry.

One separately human-authorized v3 job reached technical `ready` with three
completed calls and 34,753 native-observed tokens. Independent native cached
reads preserved its ledger, exact Mac citation, context and corpus revisions,
and the failed v2 metadata. The queued-job read was empty and invoked no model.
Its bounded selection provided 65 of 656 messages from 17 sources on two devices,
but all 22 unique citations originated on the Mac. This was technical publication
evidence, not accepted multi-device synthesis.

A separately human-authorized v4 job completed two extractions and one
reconciliation, reporting 33,879 native-observed tokens with no unknown usage.
Its immutable raw revision is
`9df5488f5409d32dadfd74d7bb9f13424000283d7c7f1b775b8682069c055601`.
The bounded selection provided 60 of 656 messages, 232 passages and 17 sources
on two devices; seven messages were clipped and 596 were unread. Raw output
contained 22 unique citations: 12 from the VPS and 10 from the Mac. Independent
native cached checks confirmed exact representative citations, unchanged
context/corpus and call ledgers, preserved v2/v3 records and an empty queue.
The deployed v4 method hash is
`08e25c40f12d032becc99bbc35b279a369bd80e1305115796c7849668bc59450`.

The deployed reader-quality policy masks three defective raw items with
transparent IDs and fixed reasons. Its visible revision is
`90b4d250110b8c3dab8afbbabcae92444cfee00dbbbaf2db5b5397d90298de75`,
with nine assertions, three uncertainties and 18 unique citations still
representing both devices. Production HTTPS health and actual native cached
reads passed without any further refresh or provider call. The raw revision,
33,879-token ledger, approved context and corpus remain unchanged.

Idle Mac watcher acceptance passed on one explicitly authorized attempt using
the pinned readiness release. The process remained stable for 85.022 seconds,
was owned by launchd (`PPID:1`), and had one native controller child and no
inference child. The queue remained empty; compared bounded run/request/call
metadata and hashes were identical before and after, with no refresh or model
call. This is idle-service evidence, not another UI-triggered generation.
The user confirmed final visual acceptance on 14 October 2026: nine visible conclusions, three uncertainties, three quality exclusions and both Mac/VPS source passages with machine/date/line evidence. This recorded confirmation introduced no further generation or runtime check.

Initial watcher attempts exited with unlocalized `ambiguous_completion`. A
later measured attempt stopped at a 25-second `initialize` RPC timeout while
the native process and reader remained active; no downstream tool, OAuth or
model stage was reached. The underlying slowness cause remains unknown. Watch
now shares one absolute 40-second startup budget across `initialize` and
`thread/start`, with the latter also capped at 25 seconds. Other RPC and model
budgets are unchanged, and the successful attempt does not establish an exact
ACK time. `RunAtLoad:true` and `KeepAlive:false` prevent automatic relaunch after
failure; there is no retry or environment/QoS widening. The later preservation
proof covers its recorded metadata, not a missing full-ledger baseline for the
earlier failed starts.

Selection stays deliberately partial and fair across machines and sources. The
read-only v2 pilot prototype planned 85 of 656 eligible messages across 17 sources,
with 13 clipped messages and 571 unread messages. This is a selection measurement,
not a published synthesis or completed live acceptance. Coverage and omitted
inputs must remain visible; these counts are not a universal coverage promise.
