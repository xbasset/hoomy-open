# Experiment 001: deterministic project discovery

Date: 2026-09-14

Status: working experimental baseline, not a selected long-term architecture

Preservation branch: `xbt-agent/experiment-deterministic-discovery`

## Why we built it

Hoomy needs to recognize a person's projects from their existing activity, then
improve that understanding through conversation and correction. We started with
a deliberately constrained process: inspect conversation titles and folders,
propose projects, ask the person to validate them, and only then inspect selected
conversation content.

This gave us something real to test. It also exposed a tension: we were encoding
an increasingly detailed investigation strategy before knowing which strategy
would be useful in everyday use.

This note preserves the first attempt and its lessons. Measurements come from
local development runs, not a controlled benchmark. Private project names,
conversation text, identifiers, and raw logs are intentionally omitted.

## What the implementation does

The pilot combines a macOS review interface, a local Python worker, a Python
backend, and private SQLite storage. A custom Codex app-server adapter invokes
GPT-5.6 Luna with low reasoning effort. In this implementation, Codex is a
restricted model transport: the model cannot use tools to investigate the data.
Application code chooses what it sees and what happens next.

The process evolved into these stages:

1. Inventory titles and folder metadata without reading conversation bodies.
2. Classify metadata into project proposals, no project identified, or ambiguous.
3. Let the person keep, correct, merge, reject, or keep projects as inactive.
4. Reclassify unresolved titles using the validated project context.
5. Rank candidates locally, extract small conversation excerpts, and classify
   those excerpts together in another model call.
6. Return uncertain associations for human review rather than silently accepting
   them as facts.

The initial implementation processed batches of 80 titles. We changed the first
pass to one call for a bounded inventory: at most 2,000 records and 200,000
metadata/context characters. Larger and incremental workloads retain batching.
Compact source references and output schemas reduce repeated text.

Automatic completion currently selects at most 12 conversations, reserving some
capacity for exploration beyond known projects. Local matching extracts at most
four 750-character excerpts per conversation. It does not scan every unresolved
conversation body. Selected raw snapshots remain private; the classification
call receives excerpts, metadata, and project context, not those full snapshots.

Reviewed examples are capped at ten per project, selected for lexical diversity,
with tentative pattern hints and negative examples. This is contextual memory,
not model training or demonstrated semantic learning.

Implementation details: [fast discovery](../../backend/FAST_DISCOVERY.md),
[runtime contract](../../backend/CONTRACT.md), and
[local triage](../../backend/hoomy/local_triage.py).

## What we measured

### First discovery

The early 679-title run required nine sequential batches. Its first four model
calls took approximately 82, 81, 69, and 71 seconds. That run was interrupted;
we do not have a completed nine-batch baseline to compare against.

Two later fresh runs sent all 679 titles in one call:

| Measurement | Run A | Run B |
| --- | ---: | ---: |
| Input tokens | 24,384 | 24,384 |
| Output tokens | 3,349 | 3,323 |
| Classification elapsed, including setup | 68.3 s | 69.7 s |
| Model turn elapsed | 62.8 s | 63.1 s |
| First project preview, measured from turn timing origin | 11.2 s | 19.2 s |
| Valid returned project proposals | 13 | 9 |

Setup time is not included in the preview figures: in Run B the first preview
arrived about 26 seconds after classification started. A streamed response is
also not necessarily a useful project preview.

The input size did not require nine small calls for this inventory. Combining
the initial inventory removed repeated sequential waits. It did not make the
model instantaneous, nor establish classification accuracy. Different proposal
counts across the two runs deserve investigation; they are not themselves a
quality score.

### Completion after human review

At one checkpoint, review had produced seven validated projects and 202 linked
conversations. Four conversations had an explicit human no-project decision.
The unresolved pool contained 123 metadata-level no-project outcomes and 350
ambiguous outcomes: 473 titles in total.

The subsequent automatic completion run recorded:

| Operation | Scope | Elapsed | Input tokens | Output tokens |
| --- | --- | ---: | ---: | ---: |
| Contextual metadata classification | 473 titles | 54.0 s | 20,546 | 2,546 |
| Local extraction | 12 selected conversations | 0.7 s | — | — |
| Grouped excerpt classification | 12 conversations | 21.0 s | 13,438 | 689 |

Local extraction produced 23,286 excerpt characters. The observed end-to-end
interval was approximately 78 seconds including overhead. Token counts are
reported usage, not a dollar-cost estimate; these runs used the Codex
subscription transport. Reasoning tokens are included in output counts, not
added a second time.

The local extraction step was cheap in this run. We have not established whether
it selected the best conversations, missed unfamiliar projects, or reduced the
person's correction effort. The resulting classifications still need review.

## What testing taught us

**Coverage is not confidence.** “All titles examined” does not mean “all projects
understood.” Metadata, partial excerpts, and deeper reading must remain visibly
distinct. A coverage chart cannot honestly stand in for a reliability score.

**The person should resolve meaning, not schedule the investigation.** Requiring
conversation-by-conversation selection transfers the retrieval work to the user.
Validated project identities, corrections, examples, and boundaries should help
the system choose the next useful evidence.

**The residual labels are not two clean populations.** “No project identified” is
a tentative judgment from limited evidence. “Ambiguous” also includes missing or
invalid model decisions. They are useful review states, but do not yet justify
two entirely separate investigation pipelines.

**Project identity and project activity are different.** An inactive project is
still a project. Similar names also need an explanation and a merge action, not
an assumption that two generated labels represent two distinct intentions.

**Progress must describe actual work.** Users need to know what is being read,
what remains, and whether content is being accessed. Transport events and
source-valid provisional results are meaningful signals; invented reading
percentages are not. The first preview is a more useful latency measure than the
first response byte.

**Malformed output should not erase useful results.** Invalid source references
can be rejected locally while valid proposals remain available. The affected
records stay unresolved, and human decisions must survive retries and new runs.

## Where complexity accumulated

The deterministic approach required explicit policies for inventory selection,
batching, pause/resume, stale results, source validation, prompt construction,
output normalization, candidate ranking, excerpt deduplication, memory selection,
project merging, and the interface's representation of each state.

Coding agents make this code easier to produce. They do not remove the need to
understand its behavior, test it, and maintain it as sources and use cases change.
Each new history format still needs reliable access and interpretation; each
hand-built investigation policy adds another behavior to maintain.

The baseline remains valuable. Deterministic policies offer explicit budgets and
testable contracts, and may eventually be the right way to optimize a stable,
well-understood workflow. The concern is sequencing: optimizing an investigation
we have not yet learned to design may slow the discovery of actual usefulness.

The implementation has 119 Python tests and seven Swift tests. These cover
software contracts and fixtures, not the accuracy of project discovery on a
representative population.

## Next experiment: let the agent investigate

Hypothesis: a Codex agent using **GPT-5.6 Luna**, invoked through the Codex SDK,
can choose useful searches and minimal evidence more flexibly than our fixed
pipeline, while reducing the amount of investigation policy we maintain.
Neither better quality nor lower latency or token use is established yet.

The SDK supports starting, continuing, and resuming local Codex threads, which
fits an explicit human-feedback checkpoint. See the official
[Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk).

The proposed experiment is deliberately small:

1. Expose only a read-only titles-and-folders inventory for the first stage.
   Ask for proposed projects with supporting references and uncertainties.
2. Stop for human validation, correction, merging, and activity choices.
   Keep these decisions in authoritative application state outside the agent.
3. After explicit consent, allow bounded access to conversation evidence.
   Let the agent choose searches, inspect small excerpts, and seek additional
   evidence only when useful. Continue with the validated context.
4. Present supported associations and unresolved questions for review. Do not
   silently promote proposals to accepted projects.

This changes who chooses the investigation steps, not just the API wrapper.
The initial experiment should not grow into a generic harness-adapter platform.

Some code must stay deterministic: source access boundaries, consent, read-only
permissions, budgets, cancellation, reference validation, and preservation of
human decisions. A read-only sandbox alone is not a privacy boundary; the agent
must only be able to access the intended source view. Conversation text is
untrusted evidence, not instructions. No publishing or source modification is
part of discovery.

Use an isolated experiment store and a fixed inventory. Compare against the
preserved baseline with the same validated feedback and, initially, the same
model and reasoning effort. Record time to first useful proposal, total duration,
token usage, tool calls, evidence volume, correction effort, and unsupported
associations. Repeat runs before interpreting differences. Track engineering
effort as well as inference cost.

The immediate question is not “can an agent classify text?” It is “can it build
an understanding that a person recognizes, with less waiting and less work from
that person, without reading more than necessary?”

The agent-led approach is proposed here, not implemented or validated by this
retrospective. The deterministic experiment is preserved for comparison, not
discarded as a failure.
