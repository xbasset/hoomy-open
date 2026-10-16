# Agentic development target

Status: target operating model for Hoomy development

This document adapts the “agentic software factory” pattern to Hoomy. It is a
development target, not a claim that the current pilot is already autonomous or
production-ready. The target keeps deterministic safety boundaries and human
decisions outside the model's control while allowing agents to build, inspect,
measure and improve the system.

## The target loop

```text
Builder / researcher
        |
        | defines outcome, constraints and decision rule
        v
Codex development agent <---- repository, authored docs, fixtures,
        |                     experiment records and approved tools
        v
Build + test + measurement harness
        |
        v
Parallel specialist review ---- data/privacy | runtime/infrastructure | product risk
        |
        +--> low-risk change ---------> bounded agentic deploy
        |                                      |
        +--> higher-risk change -> human review|
                                               v
                                      pilot / production-like runtime
                                               |
                    +--------------------------+--------------------------+
                    |                                                     |
              performance factory                                  incident assistant
              detects regressions                                   investigates failures
              proposes fixes                                        proposes mitigations
                    |                                                     |
                    +--------------------> Codex backlog <---------------+
```

The important property is the feedback loop: runtime observations become
traceable issues and proposed changes, but they do not become silent changes to
the product's doctrine, privacy scope or human-decision policy.

## Mapping to Hoomy

| Factory role | Hoomy implementation target | Current starting point |
| --- | --- | --- |
| Builder | Human sets the research outcome, scope, acceptance rule and risk tolerance | Research Ops goal and experiment templates |
| Codex | Agent edits Python, Swift, docs and tests using repository context | Pilot code and current Codex workflow |
| Internal systems | Source code, `AGENTS.md`, contracts, ingestion model, fixtures and authored experiment reports | Present across `backend/` and `docs/`; private imported evidence stays outside the public source boundary |
| Build and test CI | Python unit suite, Swift build/tests, contract checks, fixture replay and performance harness | Python and Swift checks documented in `backend/README.md` |
| Agentic review | Separate passes for evidence/privacy, data model, runtime reliability and product experience | Human review and experiment retrospectives; specialist passes are not yet formalized |
| Risk classification | Low-risk docs/tests/local refactors versus changes to consent, source access, evidence validation, persistence or automatic association | Safety constraints are in `CONTRACT.md` and `HYBRID_DISCOVERY.md` |
| Agentic deploy | Bounded local pilot launch with generation checks, rollback/reset and visible progress | `backend/run.py`, Docker backend and macOS app |
| Production | The running pilot is the production-like surface for now; later this can mean a private release channel | No public production deployment |
| Performance factory | Fixed inventory replay, latency/token/evidence measurements and regression comparison | `backend/measure_bounded.py` and Experiment 001–003 |
| Incident assistant | Reads aggregate logs and state, explains failures and proposes a patch or experiment | Failure visibility exists; automated incident analysis does not |

## Non-negotiable control plane

Agents may propose and implement changes, but these decisions remain explicit and
deterministic in application code:

- scope confirmation, path exclusions and consent before source processing;
- read-only source access and bounded retrieval budgets;
- exact evidence and snapshot validation;
- persistence of human corrections and protection against late model results;
- generation/request identity, cancellation, retries and reset behavior;
- risk classification and the approval gate for changes affecting privacy,
  inference policy, automatic associations or external distribution;
- aggregate-only public telemetry and no raw conversation content in Git.

Conversation text remains untrusted evidence, never executable instructions.

## Repository organization

Keep the existing pilot as the executable product slice and make the factory
around it explicit:

```text
docs/
  architecture/        target operating model and boundaries
  experiments/          immutable experiment reports, numbered by run
  research/ops/         goals, assumptions, decisions and review cadence
backend/
  hoomy/                runtime behavior and agent orchestration
  tests/                fast deterministic and fake-model tests
  fixtures/             fictional, sanitized replay inputs and expected outputs
  benchmarks/           repeatable measurement entry points and result schemas
  review/                data-model and ingestion design proposals
  apps/                 SwiftUI review surface
```

Private imported evidence and its retrieval indexes are separately permissioned
operator inputs. They are excluded from the public source tree and are never
rewritten or promoted to authored documentation by this target workflow.

The current code already has most of these responsibilities, but `fixtures/`
and `benchmarks/` should become named homes rather than leaving all experiment
inputs and measurement scripts at the top of `backend/`. Move or add files only
when a real experiment needs them; the organization itself must not create a
new generic framework prematurely.

## Test and experiment ladder

Every change should identify the highest rung it needs. Higher rungs are slower,
more expensive and more sensitive; they do not replace lower-rung tests.

### 1. Deterministic contract tests

Run on every change. Use tiny fictional inputs and fake inference. Cover:

- scope, consent, exclusions and source-size limits;
- API schemas and generation/request rejection;
- exact quote and snapshot validation;
- correction precedence, idempotency, reset and cancellation;
- bounded selection, checkpointing and retry behavior;
- Swift model/view-state decoding where applicable.

These tests answer: “Can the system preserve its invariants?” They must never
read the user's real history or consume a live model request.

### 2. Component and integration tests

Run on every change touching a component. Exercise the Python worker, HTTP
backend, SQLite store and Swift client with local fixtures and fake model
responses. Test both successful and partial/failure paths, including pause,
resume, stale results, no evidence and exhausted budgets.

These tests answer: “Do the parts cooperate under controlled conditions?”

### 3. Replay benchmarks

Run for changes to retrieval, classification, orchestration or persistence. Start
from a copied logical store and a fixed, sanitized inventory. Record one result
file per run containing:

- commit and experiment ID;
- input inventory and reviewed-context identifiers;
- wall time split by preparation, model calls and persistence;
- concurrency, calls, attempted/successful sources and remaining work;
- evidence characters/bytes by role and fallback policy;
- input, output and cached tokens;
- automatic associations, pending proposals, unresolved work and corrections;
- unsupported findings, missed cases and human review effort when labeled data
  exists.

The benchmark must not mutate the live store. Reports in `docs/experiments/`
contain aggregate results only. This is the right level for comparing the
deterministic baseline with hybrid and bounded-hybrid successors.

### 4. Explicit live smoke tests

Run only with an opt-in flag and fictional data in a temporary directory. Verify
the signed-in SDK adapter, tool boundary, streaming progress, publication and
feedback revision handling. Keep this separate from the normal test command;
the existing `HOOMY_AGENT_SMOKE=1` convention is the right pattern.

These tests answer: “Does the external integration still work?” They do not
prove classification quality, real-history recall or acceptable product speed.

### 5. Human pilot validation

After the method is frozen, run a defined number of distinct reviewed sessions
with a fixed review form. Track usefulness, correction effort, unsupported
claims, missed project boundaries and perceived waiting time. Do not mix these
ratings with development smoke runs or present a development build as a
validated pilot.

## Change and review policy

Each experiment should produce one report using the existing
[experiment template](../research/ops/experiment-template.md). Add:

1. a unique experiment number and status (`proposed`, `running`, `measured`,
   `adopted`, `rejected` or `superseded`);
2. a fixed baseline and decision rule before running;
3. an isolation statement explaining which store, inventory and model context
   were used;
4. a results artifact with reproducible command and aggregate output;
5. a follow-up decision that names what changes in code or remains unchanged.

Review should be parallel where possible:

- evidence/privacy: source access, consent, leakage and citation support;
- data/runtime: state transitions, retries, concurrency and recovery;
- product: clarity of progress, uncertainty and correction affordances;
- performance: latency, token use, evidence volume and remaining work.

If any reviewer classifies a change as high risk, a human engineer reviews the
diff and the relevant experiment evidence before deployment. “Low risk” means
low impact if wrong, easy to rollback and not a change to the trust boundary;
it does not mean “the diff is small.”

## Incremental adoption plan

1. Treat this document as the target and keep the current pilot behavior
   unchanged.
2. Add `backend/fixtures/` for fictional replay inputs and expected outputs.
3. Add `backend/benchmarks/` or move measurement entry points there once a second
   benchmark needs the same reporting shape.
4. Standardize experiment result metadata and attach each report to a commit.
5. Add a local pre-merge check that runs deterministic tests, Swift build/tests,
   contract checks and a small fixture replay.
6. Add specialist review checklists to pull requests or experiment reports.
7. Add aggregate failure/performance summaries before considering an incident
   assistant or automatic fix proposals.
8. Only after repeated replay and human review should a bounded local deploy be
   allowed to update a pilot build automatically.

The immediate next experiment should validate the organization itself: replay
one known bounded-hybrid case through the fixture/integration/benchmark ladder,
confirm that every result is isolated and reproducible, and measure the review
time needed to understand a failure. It should not introduce another discovery
algorithm at the same time.
