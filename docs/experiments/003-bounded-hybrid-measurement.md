# Bounded hybrid discovery: first measurement

Date: 2026-09-15. Branch: `xbt-agent/experiment-hybrid-discovery`.

Continuation of [Experiment 002](002-hybrid-discovery-checkpoint.md), not a new
architecture branch. This is a single real-history measurement, not an accuracy
benchmark or a paired comparison against the previous run.

## Implementation

Keep the title scan unchanged. After project review and explicit content consent:

1. Prepare evidence locally, prioritizing user inputs and assistant passages with
   explicit summary headings. Select at most two 300-character excerpts per source.
2. Classify packets of at most 100 sources with at most three concurrent Luna low
   calls, preserving source references and current validated project context.
3. Publish associations to approved projects; new project proposals await review.
4. Run one bounded agent investigation over residual cases, allowing broader
   user-visible assistant evidence when initial evidence is insufficient.
5. Preserve remaining work rather than interpreting budget expiry as completion.

The phase-two budget is 235 seconds. Preparation and classification share the
first 72% of the budget; the remaining time is available to the agent. Local
operations and process teardown can add overhead, so this is not a hard real-time
guarantee. Existing consent, source-size limits and human-decision protections
remain in force. The first title pass is not rerun when completing discovery.

## Scope and isolation

The benchmark started from the current 681-title catalog, 10 approved projects,
189 linked conversations and 483 unresolved candidates. This context already
contains human decisions from the previous experiment; the tests are not paired.

A read-only SQLite transaction exported catalog and decision state into a private
temporary store. Previous source snapshots and excerpt caches were not reused.
New source reads and writes occurred only in that temporary store. The live
application's projects, decisions and associations were not modified.

The benchmark invokes the same preparation, classification, publication and
checkpoint code as the worker, but calls the temporary store directly rather
than through HTTP. Consequently it measures the discovery engine, not full GUI
latency or the production HTTP ingestion overhead. The temporary result store
was discarded after aggregate measurement; no private source text is published.

Invocation (explicitly sends real-history excerpts through the signed-in model):

```sh
cd backend
.venv-agent/bin/python measure_bounded.py \
  --container hoomy-private-pilot-backend-1 --root /path/to/codex-history
```

## Observed performance

| Measurement | Result |
| --- | ---: |
| Phase-two wall time, including final checkpoint/state read | 167.37 s |
| Local preparation, including temporary-store ingestion | 16.54 s |
| All grouped classifications saved, from phase-two start | approximately 96.7 s |
| Agent investigation | 65.93 s |
| Classification calls | 5, at most 3 concurrent |
| Candidate sources attempted locally | 483 |
| Conversations classified on prepared excerpts | 437 |
| Grouped classification failures | 0 |
| Agent retrieval calls / attempted sources | 9 / 9 |
| Agent sources yielding excerpts | 4 |
| Distinct sources with stored excerpts at completion | 439 |

The other 46 candidates did not produce a first-stage evidence packet. They were
not classified by the grouped calls. Four agent sources yielded excerpts, two of
which expanded the set of distinct sampled sources. Read attempts do not count
as successful excerpt analysis.

| Token usage | Grouped classification | Agent | Total |
| --- | ---: | ---: | ---: |
| Input | 96,824 | 54,649 | 151,473 |
| Output | 6,738 | 2,414 | 9,152 |
| Total | 103,562 | 57,063 | 160,625 |
| Cached input, already included above | 1,792 | 15,104 | 16,896 |

The measured version logged grouped totals and agent totals separately; the
table sums those records. Subsequent telemetry wiring also includes agent usage
in the final aggregate and logs partial agent usage on timeout. Phase animations,
heartbeat updates and distinct-source counters were refined after this run;
those refinements were fixture-tested, not remeasured on real history.

Preparation loaded 448,562,347 raw bytes from successfully captured sources
(448.56 decimal MB). It selected 215,804 characters / 216,071 UTF-8 bytes.
The agent supplied another 4,268 excerpt characters, potentially overlapping
prepared evidence. Their sum is not a deduplicated unique-evidence count.
Raw bytes are not network payload bytes; repeated context and serialization are
not represented by excerpt byte counts.

## Result distribution in the isolated store

| State | Before | After |
| --- | ---: | ---: |
| Linked conversations | 189 | 462 |
| Conversations in pending project proposals | 0 | 24 |
| Ambiguous | 379 | 131 |
| No project identified automatically | 104 | 55 |
| Explicit human no-project decisions | 9 | 9 |
| Total | 681 | 681 |

There were 273 new automatic associations. These are model assignments, not
human-confirmed correct classifications. The workload queue retained 137 sources;
that queue is distinct from the 186 conversations currently labeled ambiguous
or no-project, and from pending proposal membership. These counters must not be
treated as interchangeable measures of completeness.

## Interpretation and remaining validation

The measured phase two took 2 min 47 s. Adding the prior unchanged title-pass
measurement of 64.73 s gives **approximately 3 min 52 s**, excluding human review.
This is a projection, not a new measured end-to-end run from a clean inventory.

The earlier agent exploration took approximately 14 min 33 s and reported
4,277,160 tokens. This continuation is much smaller and faster in observed usage,
but its improved reviewed context, different candidate set and retrieval policy
prevent a causal speed/quality claim from this single comparison.

The five-minute target is plausible for this corpus under these conditions.
It is not yet validated for GUI-to-GUI latency, other corpora, repeated runs or
classification accuracy. Next validation should review automatic associations,
especially missed or incorrectly merged project boundaries, using a fixed labeled
sample and identical context for paired retrieval-policy comparisons.
