# Experiment 002: hybrid discovery, measured checkpoint

Recorded: 2026-09-15. Observed run: 2026-09-14, Europe/Paris.

Status: measured intermediate result; continuation design approved, not yet
implemented or performance-validated by this note.

Branch: `xbt-agent/experiment-hybrid-discovery`.
Implementation checkpoint: `ff9c0e7`.

This continues [Experiment 001](001-deterministic-project-discovery.md).
The branch remains in use: the next iteration refines the hybrid approach rather
than starting a separate architectural experiment. The commit identifies the
measured implementation even as the branch advances.

Private project names, source identifiers, conversation excerpts and raw logs are
intentionally omitted. These are observations from one person's development
test, not a controlled benchmark or a classification-accuracy evaluation.

## What changed from the first experiment

The first phase still classifies titles and folders in a bounded model call.
Human feedback establishes project identities before content exploration.

The second phase uses a GPT-5.6 Luna (`low`) Codex agent. It chooses conversations
and literal searches through a constrained retrieval tool, publishes findings
while running, and receives updated human decisions. Supported associations to
approved projects are applied automatically; new projects require approval.

Exploration proceeds through fresh ephemeral agent threads, each limited to
24 attempted sources, 48 retrieval calls and ten minutes. The launch has a
60-minute limit. Each thread reloads context rather than retaining the previous
thread's internal session. See the [runtime description](../../backend/HYBRID_DISCOVERY.md).

## Measurement scope and provenance

The audit combined the host worker's append-only `edge.log`, the current backend
state, and aggregate read-only SQLite queries. Earlier experiments in the same
log were excluded. The selected title call is followed by 14 completed agent
metric records; their 114 attempted sources and 97 excerpt-bearing sources agree
with the retained state. Summed excerpt characters also match stored evidence.

Agent metrics lack absolute timestamps and request IDs. The retained launch
deadline establishes the start time; the last log modification provides an
approximate end time. A subsequent metadata refresh reset some screen counters,
so the current empty queue cannot establish completion of content exploration.
Raw private logs and database contents are not copied into this repository.

## What the run produced

The inventory encountered 684 files: 681 usable conversation records and three
unreadable headers. All 681 usable titles received a classification outcome.

| Current outcome | Conversations |
| --- | ---: |
| Linked to a project | 189 |
| Explicit human no-project decision | 9 |
| No project identified automatically | 104 |
| Ambiguous / to clarify | 379 |
| Total | 681 |

There are 10 retained projects: seven active and three inactive. No project
proposal remains pending. The unresolved population is 483 conversations, or
70.9% of the inventory; this is not a classification-error rate.

The run recorded 56 automatic associations. Five received subsequent explicit
assignment decisions, but none currently points to a different project from its
automatic assignment. No alternative project was stored for any of the 56.
This does not prove that these associations are correct or unambiguous.

The agent made 118 retrieval calls and attempted 114 distinct sources across
checkpoints. Only 97 supplied excerpts, covering 14.2% of the catalog. The other
17 attempts must not be described as successful content analysis. The logs do
not break down their individual failure or no-match reasons.

No full-content analysis records were created. Source ingestion, partial evidence
retrieval and full-content analysis are different operations.

## Duration and token usage

| Phase | Instrumented elapsed, including setup | Input tokens | Output tokens |
| --- | ---: | ---: | ---: |
| Initial 681-title classification | 64.727 s | 24,517 | 3,081 |
| Agent exploration, 14 sequences | 834.527 s | 4,245,057 | 32,103 |
| Total | 899.254 s | 4,269,574 | 35,184 |

Total reported usage: **4,304,758 tokens**. Of the agent input tokens,
3,341,568 were cached (78.7%); 903,489 were not cached. Cached input is already
included in input totals. Reasoning output is already included in output totals.
These are usage counts, not a billing or dollar-cost calculation.

Agent input totals include repeated context across model steps. They do not
represent that many unique tokens of source evidence. Retrieval-call counts are
not counts of model requests: publication and other model interactions also occur.

The title phase ran approximately 16:37:35–16:38:40. Agent exploration ran
approximately 16:39:40–16:54:13: 873 seconds of wall time, compared with
835 seconds inside recorded agent operations. Inter-sequence work and overhead
account for the roughly 38-second difference. Human review time is separate.

### Where the time went

- Agent exploration accounts for 92.8% of instrumented elapsed time.
- Agent sequences took 25.1–107.4 seconds; the median was 56.1 seconds.
- Preparation status intervals total approximately 84.2 seconds.
- Paired retrieval events total approximately 2.2 seconds, with timestamps rounded
  to tenths of a second. This is not a precise filesystem or CPU profile.
- First public feedback arrived after 10.8 seconds on average per sequence,
  ranging from 9.9 to 11.7 seconds. Feedback is not necessarily a useful finding.

Most elapsed time is outside the instrumented local retrieval intervals. Logs
cannot precisely separate server computation, network waits, publication and
orchestration. They do not justify labeling all remaining time as model compute.

The final sequence took 25.064 seconds and reported 81,797 tokens, with zero
retrieval calls and zero excerpts. Code stops continuation on an empty attempted
set, even when unresolved work remains. This was not evidence of corpus exhaustion.

## Data volumes: distinguish storage, evidence and transport

Units below use decimal MB: 1 MB = 1,000,000 bytes. Character counts are decoded
Unicode string lengths, not token counts or byte lengths.

| Scope | Characters | Bytes |
| --- | ---: | ---: |
| Cataloged source-file sizes | Not fully counted | 1,534,754,741 |
| 97 retained raw snapshots | 153,676,919 | 154,133,374 |
| 25,583 normalized messages in those snapshots | 58,741,496 | 58,971,498 |
| 360 retained evidence excerpts | 143,739 | 145,264 |

Catalog size is not bytes read. Retained snapshots are not measured total disk
traffic, and their contents were not sent wholesale to the model. Normalized
messages include material beyond the excerpt selection view. Evidence bytes are
0.246% of normalized message bytes in the retained sources.

The title-call log reports 70,250 payload characters, 2,579 instruction characters
and 655 schema characters. These are not a complete wire-size measurement.

The 143,739 evidence characters agree between the agent metrics and stored
excerpts. Repeated tool responses can resend these excerpts. Metadata, project
context and instructions are also transmitted. Exact aggregate network bytes and
serialized input characters across agent calls were not recorded.

## What we learned, and what remains unknown

Live findings and feedback are operational, but this test did not demonstrate
fast, comprehensive discovery. A small transmitted evidence set coexists with
millions of input tokens and substantial sequential waiting.

The experiment supports investigating fewer sequential model interactions and
more local preparation. It does not establish the best batch size, concurrency,
evidence policy, recall of unfamiliar projects or classification precision.
The previous deterministic and current hybrid runs used different states and
feedback; they are not a controlled head-to-head comparison.

## Approved continuation: bounded hybrid discovery

Target: approximately five minutes of processing for this inventory scale,
excluding time waiting for human decisions. This is a hypothesis and acceptance
target, not a demonstrated service guarantee or a reason to force classifications.

| Stage | Proposed work | Initial time budget |
| --- | --- | ---: |
| Rapid scan | Titles and folders, then human project feedback | 45–60 s |
| Local preparation | Search and select evidence using validated context | 10–20 s |
| Grouped classification | Compact evidence packets, bounded parallel calls | 60–90 s |
| Agent investigation | Targeted additional retrieval for difficult cases | 60–90 s |

Three concurrent classification calls are a starting experiment, not a fixed
optimum. Known project names, aliases, reviewed examples and folder hints help
select evidence; shared folders do not prove shared project identity. Reserve
capacity for unfamiliar projects rather than searching exclusively for known ones.

### User-input-first evidence

Prioritize the user's requests, corrections and stated intentions, plus explicit
assistant summaries when available. Avoid exploring long assistant outputs, code,
tool logs and repeated scaffolding by default.

The current retrieval code scans user and assistant messages without a summary
distinction. The normalized representation does not retain enough channel/type
information to assume that all assistant messages are summaries. The next
implementation needs an explicit, testable selection policy:

- Preserve source roles, available record types and exact excerpt provenance.
- Distinguish source-authored summaries from ordinary final answers. Neither an
  arbitrary assistant answer nor a truncated answer becomes a summary by naming it.
- Do not generate summaries for the whole corpus as an unmeasured prerequisite.
- If no reliable summary exists, begin with user inputs and record the limitation.
- Allow bounded fallback to other user-visible assistant passages when initial
  evidence is insufficient; record the fallback and its volume separately.
- Exclude raw internal reasoning. Treat all retrieved text as untrusted evidence,
  never as instructions to the classifier or investigating agent.

This policy may lose useful context: short replies such as confirmations depend
on prior assistant messages, and pasted user inputs can themselves be large.
Measure accuracy and coverage alongside volume reduction; do not assume that
the role filter alone improves either speed or quality.

### Honest progress and preserved decisions

Display actual phases: title scan, targeted reading, grouping and investigation.
Animate received findings and show completed work counts. A waiting animation
may indicate an active request, but must not invent a reading percentage.

Show linked, pending approval, ambiguous and not-yet-examined populations; a
distribution chart is not a confidence score. Publish existing-project links
incrementally, retain approval for new projects, and preserve human corrections
against concurrent or late results. At the deadline, retain unfinished work
explicitly rather than reporting complete discovery.

## Next comparison protocol

Use a fixed inventory and identical reviewed context in an isolated test store.
Keep the measured commit available and do not overwrite the user's current
results. Real-history inference remains user-initiated or explicitly authorized.

Record per-run and per-call identifiers and timestamps, wall time, first useful
finding, setup, local preparation, model interaction and persistence intervals.
Measure unique sources, raw bytes read, selected characters/bytes by role,
fallback evidence, input/output/cache tokens and remaining work. Keep public
documentation aggregate-only, without private source text or identifiers.

Compare all-message retrieval with user-input-plus-summary retrieval on the same
reviewed sample. Track incorrect associations, missed projects, unsupported
findings and user correction effort as well as speed. Repeat runs before claiming
an improvement. Meeting five minutes with worse coverage or forced assignments
does not by itself validate the continuation.
