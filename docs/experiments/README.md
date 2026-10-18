# Experiment records by question

These are authored observations and design records from iterative development.
They retain their original numbering and checkpoint claims. Read the
[research journey](../research/research-journey.md) for their relationship to the
current preview; use current component guides for installation.

**Two numbering schemes coexist.** `EXP-0001` is the single-session brief
protocol under `experiments/`; it remains **not run**. `Experiment 001` below is
the separate project-discovery development record. Neither numbering nor a
working implementation implies that a protocol passed.

| Question | Records | Evidence status |
| --- | --- | --- |
| Can one session become a trustworthy, quick-to-correct brief? | [EXP-0001](../../experiments/EXP-0001-conversation-brief/README.md) | Protocol; not run |
| Can metadata and small excerpts identify projects? | [001: deterministic discovery](001-deterministic-project-discovery.md) | Working baseline and local measurements; no accuracy benchmark |
| Can investigation resolve ambiguous associations? | [002: hybrid checkpoint](002-hybrid-discovery-checkpoint.md) | One operator's measured run; accuracy unestablished |
| Can the investigation be bounded? | [003: bounded hybrid](003-bounded-hybrid-measurement.md) | Isolated real-history measurement; not a paired comparison |
| What does discovery require from the person? | [004: usage and guided flow](004-discovery-usage-and-wizard.md) | Observed review workflow and proposed changes |
| What should we build before expanding the cognitive design? | [005: data design review](005-data-design-review.md) | Design decision to test daily use |
| Can Hoomy suggest a useful next activity? | [006: on-demand Act/Learn](006-on-demand-act-learn.md) | Implemented baseline; learning unmeasured |
| Is an activity still pending, and can that be checked? | [007: verification](007-verification-and-project-facts.md), [retrospective](007-rex-20261007.md) | Implementation and small usage review; relevance and delay remain issues |
| Can preparation be separated from fast selection? | [008: prepared ideas](008-ready-ideas-decisions.md) | Prototype measurements and integration checks; broader design remains proposed |
| What happens when the person cannot answer a question? | [009: uncertainty and recovery](009-question-pool-and-recovery.md) | Implemented recovery and a smoke check; no calibrated benchmark |
| Can clarification and inspectable research improve orientation? | [010: dialogue and explorer](010-project-dialogue-and-research-explorer.md), [retrospective](010-rex-20261008.md) | Persisted context and observed trial; useful end-to-end activity and learning unproven |
| Can Codex provide the interaction surface? | [011: first plugin](011-codex-plugin-first-test.md) | Bridge/native opening observations; checkpoint-specific handoff limits |
| Can people directly express their project intent? | [012: project profile](012-project-profile.md) | Persistence and fixture checks; recommendation benefit unmeasured |
| Can machines share authorized project evidence? | [Collection and authentication lessons](../research/collection-and-authentication-lessons.md) | Aggregate integration findings from a private pilot |
| Can a bounded synthesis retain evidence from both machines? | [State methods v2–v4](../research/project-state-bounded-method.md) | Bounded trials, failures, citations and reader-quality outcomes; no general truth or utility evaluation |

## Ask your agent

> For the question I selected, summarize the hypothesis, observed result, decision
> and remaining uncertainty. Locate the relevant implementation and tests. Identify
> any statement that is only a proposal or belongs to a historical checkpoint.

Results are published as authored findings and aggregates. Private sessions,
logs and credentials are not experiment contributions. A new investigation
should declare its own protocol and version; it must not silently update a
historical outcome or reuse an old experiment's name as proof of success.
