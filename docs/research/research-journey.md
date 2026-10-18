# From conversations to a shared project state

This is a retrospective reading of published development records, not a new
experiment or a claim that every change followed a preregistered protocol.
The record distinguishes implementation checks, operator observations, measured
trials, and questions that remain open. Git preserves document versions;
this page explains the current research direction.

## The question behind the infrastructure

Can Hoomy help someone resume a project with less effort by reconstructing its
state from authorized conversations and verifiable facts, then proposing useful
work and learning from the outcome?

The [public charter](../../CHARTER.md) states the ambition and boundaries.
[EXP-0001](../../experiments/EXP-0001-conversation-brief/README.md) isolates one
part: turn a completed session into an evidence-linked brief that a person can
quickly accept or correct. Its ten-session protocol remains **not run**.
The development trials below investigated surrounding mechanisms; they neither
complete that protocol nor establish its acceptance and review-time thresholds.

```mermaid
flowchart LR
    P[Recognize projects] --> I[Express intent]
    I --> C[Collect across machines]
    C --> S[Reconstruct state]
    S --> V[Verify conclusions]
    V --> A[Suggest useful work]
    A --> L[Learn from outcomes]
```

The preview supplies pieces along this path. The diagram is a research dependency
map, not a claim that the whole loop is validated or runs automatically.

## 1. Can existing conversations reveal projects?

**Hypothesis.** Titles and folders can provide a useful first map; selected
excerpts and human correction can resolve ambiguity without reading everything.

**Build and observation.** Deterministic orchestration established metadata-first
classification and review. Hybrid investigation let an agent retrieve evidence,
but repeated exploration took substantial time and reported token usage.
A later bounded hybrid run classified prepared excerpts and left unresolved
cases explicit. A usage review found a project map useful enough to retain.

**Decision.** Preserve approved project identities and human corrections; bound
investigation rather than interpreting a larger number of associations as success.

**Limit.** These runs were not an accuracy benchmark. Different inputs, prior
human decisions and changed methods prevent a causal performance comparison.

Read [discovery 001](../experiments/001-deterministic-project-discovery.md),
[hybrid 002](../experiments/002-hybrid-discovery-checkpoint.md),
[bounded measurement 003](../experiments/003-bounded-hybrid-measurement.md),
and [usage review 004](../experiments/004-discovery-usage-and-wizard.md).

## 2. Does recognizing a project lead to useful work?

**Hypothesis.** Project history, explicit intent and completion checks can support
a relevant activity for the time a person has available.

**Build and observation.** On-demand Act/Learn introduced suggestions and feedback.
An early suggestion concerned work already completed, motivating explicit
verification and dated facts. Later trials separated preparation from fast
selection, handled unknown answers, and saved project-intent clarification.
The final selector could be fast while the full interaction still required
waiting and questions; one delivered activity assumed an unavailable attachment
control. No completed activity had an observed usefulness/attention assessment
in the recorded 8 October evaluation sample.

**Decision.** Separate intent, pending state, resource access and actual outcome.
A ready label must correspond to work the person can start. Retaining answers
must not be called demonstrated learning.

**Limit.** Neither a fast selection nor saved context proves useful recommendations,
reduced attention cost, or improvement through learning.

Read the [on-demand baseline](../experiments/006-on-demand-act-learn.md),
[verification retrospective](../experiments/007-rex-20261007.md),
[prepared ideas](../experiments/008-ready-ideas-decisions.md),
[uncertainty recovery](../experiments/009-question-pool-and-recovery.md), and
[dialogue retrospective](../experiments/010-rex-20261008.md).

## 3. Where should people interact and correct the model of a project?

**Hypothesis.** An entry point inside Codex, with an editable project profile,
can reduce navigation and keep the person's intent visible.

**Build and observation.** A thin plugin reused the existing backend. Native
opening and bridge behavior were checked; the historical note retains its
handoff limits. Project profiles added optional goals, constraints and other
dated intent fields, with revision checks and explicit clearing.

**Decision.** Keep human statements distinct from inferred task state. Use Codex
as an interaction surface while preserving durable project records outside
individual chats.

**Limit.** Integration and persistence checks do not establish lower cognitive
load or better suggestions. The local plugin experiment is not the same runtime
configuration as the later remote acceptance plugin.

Read the [plugin trial](../experiments/011-codex-plugin-first-test.md) and
[project profile experiment](../experiments/012-project-profile.md).

## 4. Can one project span several machines?

**Hypothesis.** A central source of reference, with separately authorized clients,
can combine evidence while preserving project scope and human context.

**Build and observation.** The private pilot connected a Mac and a Linux VPS to
a central service. Native user OAuth and collector enrolment have separate
authority. Collection required an explicit project/root grant; immutable visible
message projections were delivered through a durable outbox. Integration trials
exercised acknowledgement loss, revocation and cached reads. Operator acceptance
confirmed remote project context and excerpts from both machines.

**Decision.** Keep approved context and collected evidence central. Do not rely on
SSH sessions for service lifetime, local copies as silent fallbacks, or a shared
identity as permission for every operation. Collection does not start inference.

**Limit.** Heartbeats do not prove upload; exact stored hashes do not independently
verify an original machine's source claims. Private pilot acceptance is not
validation across arbitrary users, platforms or Codex versions. The preview is
configured for a single operator workspace, not a multi-tenant hosted product.

Read [collection and authentication lessons](collection-and-authentication-lessons.md),
[ingestion architecture](../architecture/multi-machine-ingestion.md), and the
[current collection guide](../../deploy/central/collection.md).

## 5. Do citations make a project-state synthesis trustworthy?

**Hypothesis.** Bounded extraction and reconciliation over frozen passages can
produce conclusions whose sources are inspectable across machines.

| Trial | Observation | Decision or remaining limit |
| --- | --- | --- |
| v2: model-written quotes | One quotation failed exact membership; no state was published. | Refuse altered quotes; do not repair them into apparently valid evidence. |
| v3: server-resolved passage references | Technical completion with exact citations, but all 22 unique citations came from the Mac despite two-machine input. | Input representation alone does not establish output coverage. |
| v4: extraction per machine | Both machines were cited. A reader-quality policy excluded three defective raw items, leaving nine conclusions, three uncertainties and 18 unique citations. | Preserve raw results and visible exclusion reasons; lexical checks are not semantic fact checking. |

The v4 selection supplied **60 of 656 eligible messages**, spanning 17 sources.
Seven message prefixes were clipped and 596 messages were unread. Operator
acceptance confirmed the visible counts and Mac/VPS passages with dates and
lines. These are partial, dated statements from conversations, not a current
independent audit of the repositories or deployments. Separate live-verification
experiments exist; they are not automatically part of this synthesis pipeline.

**Decision.** Expose method, partial coverage, uncertainties and provenance. Keep
cached reading independent from new inference. An exact citation must not be
presented as proof that the attached conclusion follows from it.

**Limit.** We have not established general semantic correctness, current factual
accuracy, or that the synthesis saves the person effort. Those require evaluation
beyond the successful infrastructure and citation checks.

Read [bounded methods and outcomes](project-state-bounded-method.md) and the
[current state contract](../architecture/project-state.md).

## What the 0.0.1 preview makes possible

The preview packages a runnable research base: project discovery mechanisms,
central context, authorized multi-machine collection, a Codex plugin, and explicit
bounded state generation with inspectable evidence. Local experiments and design
alternatives remain available; their presence is not evidence that they form one
validated autonomous loop. See the [root guide](../../README.md) for how to try it.

The next valuable investigation is whether a person can **trust, correct and use**
the reconstructed state. For example:

1. Pick a bounded project and freeze the evaluated inputs and method version.
2. Review each conclusion for support, contradiction, uncertainty and freshness;
   record what independent checks were actually performed.
3. Compare resuming work from the synthesis with the person's usual approach.
   Count correction effort and total orientation time, including failures.
4. Publish authorized aggregate findings, limitations and the resulting decision;
   retain private conversations and identifying evidence outside the contribution.

This is a proposed direction, not a completed result or a replacement for
EXP-0001. Define the protocol and evaluation rule before observing the outcome.
For later work, the [learning pipeline](learning-pipeline.md) asks what feedback,
baselines and held-out evaluation would be needed to claim learning rather than
context reuse.
