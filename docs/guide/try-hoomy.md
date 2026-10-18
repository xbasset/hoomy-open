# Try Hoomy

Choose the question before the installation. These routes increase in operational
commitment and data exposure; completing one does not authorize the next.

| Route | Question | Inputs and external calls | Expected result |
| --- | --- | --- | --- |
| 1. Software fixtures | Does the implemented pipeline enforce its contracts? | Fictional test inputs; no model/provider call or personal history | Passing assertions and inspectable failure cases |
| 2. Your project | Can approved conversations from your machines reach one project? | Selected visible text uploaded to your own Hoomy server; GitHub login | Cached sources from each approved machine |
| 3. A cited synthesis | Are the conclusions justified and useful? | A bounded selection and approved context sent to OpenAI through Codex | A dated, partial synthesis you review against citations |

The public source contains fictional software fixtures, not a supplied research
corpus or a ready-to-run interactive demo. Begin with
[questions for your agent](ask-your-agent.md). Keep research results separate from
software test results.

## 1. Inspect the pipeline without personal data

Requirements: Git and Python 3.12. Run from the cloned repository root. These
focused suites use temporary fixture stores and fake model/transport objects;
they do not read your Codex history or require an account:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent python3 -m unittest discover -s agent/tests -p test_collection.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python3 -m unittest test_ingestion test_project_state test_project_state_quality test_state_executor
```

Inspect [projection fixtures](../../agent/tests/test_collection.py),
[ingestion fixtures](../../backend/tests/test_ingestion.py),
[state fixtures](../../backend/tests/test_project_state.py),
[reader-quality fixtures](../../backend/tests/test_project_state_quality.py) and
[executor fixtures](../../backend/tests/test_state_executor.py). Ask your agent to
show a positive case and a rejection case for the boundary you care about.

For broader component checks, follow the [backend](../../backend/README.md),
[agent](../../agent/README.md), [gateway](../../services/hoomy-gateway/README.md)
and [plugin](../../plugins/hoomy/README.md) instructions. Node 24 and dependency
installation are needed for JavaScript and cross-component suites; dependency
downloads are separate from offline fixture execution. Some integration fixtures
can skip when prerequisites are missing: report skips, not just a green exit.

**Stop if:** a focused test fails or unexpectedly requests credentials, private
history or a provider call. Investigate the exact test and environment first.
Passing tests establish behavior on those fixtures, not a functioning deployment,
semantic accuracy or product utility.

## 2. Collect one authorized project

This route is operator-led. Budget a setup session; there is no measured universal
installation time. You need an HTTPS origin, a GitHub App and explicit identity
allowlist, Docker Compose/reverse proxy configuration, persistent private storage
and a reviewed signed collector distribution. macOS collection requires a logged-in
graphical user; Linux uses a dedicated systemd service under the enrolling user.
The current deployment is one private workspace, not a public multi-tenant service.

Use the existing runbooks in this order:

1. [Deploy the central service](../../deploy/central/README.md): configure your
   origin, GitHub callback, volumes and canonical project records. Canonical project
   bootstrap is currently operator-assisted; there is no public project-creation
   endpoint or complete self-service onboarding wizard. This guide does not fill
   that remaining installation gap. Keep the Python
   API private. Leave collection and state execution disabled initially.
2. [Install and enrol the agent](../../agent/README.md) against your own chosen
   server. The installer must be built and served by that operator; the template
   is not an installable release. Ordinary enrolment authorizes presence only.
3. [Approve collection](../../deploy/central/collection.md) for one project and
   exact working-directory/history roots. Review the policy, stop the old daemon
   before grant replacement, complete phone approval, then review/start its service.
4. [Connect the remote plugin](../../plugins/hoomy-remote-acceptance/README.md).
   Its MCP configuration and skill both pin the original origin. Review/adapt
   `mcp.json` and the expected-origin check in `skills/hoomy/SKILL.md` in your owned
   source copy for your server before installation; do not edit an installed cache.
   Confirm the central-origin marker and approved context before reading cached messages.
5. For a two-machine test, repeat machine enrolment and policy approval separately,
   binding both sets of roots to the same canonical project. Read a cached source
   from each machine, with its date and original line references.

The collector's installation examples default to `https://my.hoomy.dev`; that
endpoint does not grant access. Do not upload there unless its operator explicitly
admits you. A source checkout does not supply a signed release or an authorized
server. A GitHub login does not by itself authorize conversation collection.

**Expected evidence:** an authenticated heartbeat, durable acceptance receipts,
and readable cached sources for the chosen project. Verify delivery after service
restart and, on Linux, after closing SSH. A registered machine alone is not proof
of delivery. Cached reads invoke no model.

**Data:** selected visible user/assistant messages and metadata reach the chosen
server; hidden reasoning, tool bodies and known injected context are excluded.
Projection is partial and credential-pattern filters are imperfect. Review the
scope and applicable rights before including your own project. Do not use a client
or employer corpus merely because it is accessible locally.

**Stop if:** the origin/project is wrong, an unapproved source appears, receipts
cannot be matched, or excluded content is visible. Stop the collector and resolve
the policy/version issue before extending scope. Preserve queues and receipts for
private diagnosis; do not post them as public research results.

## 3. Evaluate one bounded synthesis

Only proceed after cached sources and project context are correct. Collection
consent does not authorize inference. Read the
[state overview](../../deploy/project-state/README.md),
[executor runbook](../../deploy/project-state/EXECUTOR.md) and
[current method findings](../research/project-state-bounded-method.md).

Requirements include an explicitly enabled central project, the remote connection's
state permissions, and a Mac with the compatible installed Codex runtime/account.
The existing pipeline uses OpenAI through that account; another provider is not a
demonstrated substitute. Review what selected text will be sent and agree a bounded
trial before execution. Codex usage limits apply; observed tokens are not a hard
monetary cap.

The documented native connection command is:

```sh
deploy/project-state/run-state.sh login
```

In the remote project panel, inspect the context and corpus revisions, then
explicitly request a refresh. The manual runner consumes that already queued job:

```sh
deploy/project-state/run-state.sh run --project-id PROJECT_UUID --run-id RUN_UUID
```

Replace the placeholders with that approved project and queued run. Do not launch
another runner beside an installed watcher. The current method supports at most
two eligible machines: bounded extraction per machine, then reconciliation. An
unsupported device count stops before inference. Uncertain outcomes stop for
review; do not treat an error as permission to repeat a potentially completed call.

**Review:** inspect every conclusion and its cited passages; distinguish a recorded
statement from a current fact. Record missing, contradictory, outdated or unrelated
evidence, unread coverage, corrections and review time. An exact quote is necessary
provenance, not proof of the conclusion. The central method does not check today's
repository, deployment or live web. Do not enable an autonomous action from this test.

**Expected result:** either a dated, partial cited view or an explicit failed/
`needs_review` outcome. Both are valid experimental observations. No automatic
retry is required to produce a successful-looking result. Optional watcher setup
belongs after pilot review, as described in the runbook; it is not needed for the
first manual trial.

**Stop if:** authorization, usage or execution outcome is unclear; evidence is
missing; or the trial exceeds the reviewed scope. Preserve the central record and
inspect it before deciding on another trial.

## Report what you learned

Use the [result template](../research/result-template.md) and
[contribution guide](../../CONTRIBUTING.md). Report software validation, live
operational observations and research measurements separately. A private pilot
can establish feasibility in its setting without establishing general usefulness.
EXP-0001's [ten-session protocol](../../experiments/EXP-0001-conversation-brief/README.md)
is a separate proposed evaluation and remains `not run`.
