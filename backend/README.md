# Hoomy Python backend and domain components

The `hoomy` package runs the private central service behind the authenticated
[gateway](../services/hoomy-gateway/README.md). It owns project context, immutable
scoped conversation projections, cached citations and explicitly queued bounded
project state. Central deployment and project-data migration are described in
[central operations](../deploy/central/README.md) and
[state execution](../deploy/project-state/README.md).

| Component | Current responsibility |
| --- | --- |
| `hoomy/server.py` and `store.py` | Private HTTP facade and durable SQLite domain store |
| `hoomy/ingestion.py` | Approved projected messages, source versions, receipts and sticky exclusions |
| `hoomy/project_state.py`, `project_state_contract.py`, `project_state_quality.py` | Frozen inputs, bounded execution policy, exact passages and conservative cached reader projection |
| `hoomy/state_executor.py` | Explicit remote jobs via the installed central plugin; idle watch makes no model call |
| Discovery, facts, activity and learning modules | Preserved experimental algorithms and fixtures, separate from central cited-state inference |
| `apps/HoomyPilot`, `run.py`, `compose.yaml` | Experimental local desktop/runtime; preserve its established app, image and volume identities |

Run deterministic checks from the repository with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python3 -m unittest discover -s backend/tests`.
State deployment fixtures use new temporary project databases and prepare-only
service definitions. They do not establish live OS reboot, Keychain or provider
behavior. The Python module names remain `hoomy.*`; the directory move changes
source paths without migrating installed data or changing signed collectors.

## Experimental local application

The sections below document the retained local exploration and research tools.
They are an experimental baseline, not the active central collection interface.
When a store carries central archive authority, the local launcher refuses its
legacy worker and the archive rejects writes/legacy jobs; never clear that guard
or reset an archive as a routine install step. Inspect current server authority
before any separately authorized local experiment.

Comparisons, failed approaches and measurements remain in
[experiment reports](../docs/experiments/001-deterministic-project-discovery.md),
[hybrid discovery](HYBRID_DISCOVERY.md) and
[bounded discovery measurements](../docs/experiments/003-bounded-hybrid-measurement.md).
The local UI and method quality still need scoped human evaluation.

## Prepared ideas and a bounded question pool

The [8 October usage retrospective](../docs/experiments/010-rex-20261008.md) closes
the current evaluation checkpoint: intent is reused and final selection is fast,
but preparation, resource access and an actionable destination still limit utility.
The next experiment remains open for design.

[Experiment 010](../docs/experiments/010-project-dialogue-and-research-explorer.md)
adds research restitution and open questions about the project's current goal or
motivation. Human intent is examined before technical investigation when no dated
intent is available. Answers persist as project context and reuse the research already
read. **Explorer cette recherche** opens the live execution graph and lets the user
attach feedback to a specific step for subsequent research.

[Experiment 009](../docs/experiments/009-question-pool-and-recovery.md) evolves
**Pour maintenant**. Enter the time and current goal, then **Trouver une activité**.
Known pending work is selected quickly. Otherwise Hoomy prepares alternatives in the
background and resumes the same interaction. There is no Refresh/Prepare prerequisite.

The pool contains at most ten active candidates and exposes up to five questions
reviewed by Decisions for the current request. System 2 proposes questions and their
intended audience; Decisions reviews them in a batch before display. Agent tasks and
external dependencies are not assumed to be the user's knowledge. Generic agent
verification has one bounded attempt per candidate; unresolved work is kept out of
ready activities. **Questions prêtes pour toi** lets the person choose what to answer.

**Je ne sais pas**, **Dépend d’un tiers**, **À vérifier par Hoomy** and **Passer** preserve
uncertainty. **Déjà fait** excludes the exact completed idea; **À faire et possible**
records pending work and feasibility for 24 hours. **Donner mon retour** separately
records actual usefulness and attention. No trained ranking weights are claimed.

Empty and failed searches show concrete recovery options. **Mes projets** contains
Discover's tools; the activity screen does not display the unrelated discovery menu.
Existing projects, trial history and answers survive the additive update.

The host's `~/Library/Application Support/HoomyPilot/decisions-config.json` contains
`{"key_file": "/absolute/path/to/authorized.env"}`. Only `OPENAI_API_KEY` is read, for the
official OpenAI endpoint. It stays out of Docker, Swift and Codex. Codex research uses
its existing login. Keep Discover paused for background preparation and verification.

From this directory, `python3 run.py start` updates backend, worker and app. Quit the
previous app process first. Back up SQLite and retain the Docker volume; do not reset
Discover. Opening the screen starts no new inference, but authorized queued work may
continue. Cancelling the current background research clears queued checks as well.

## Fast recognition, then selected deepening

The current implementation inventories Codex titles and folder metadata first,
without reading conversation bodies. GPT-5.6 Luna (`low`) examines a fresh, bounded inventory
in one call; larger inventories and incremental updates use automatic, checkpointed batches.
There is no project-count cap. Streamed source-valid project previews remain provisional
until the complete response is checked. Every title has an explicit outcome; refresh does not repeat completed metadata. Keep or correct a proposal before
choosing **Approfondir**, which reads up to three linked conversations per run.
Existing briefs and corrections are preserved. See [Fast discovery](FAST_DISCOVERY.md)
for limits, interruption handling, and the experience design.

The new build must be relaunched to replace an already-open app; save any review
before quitting. Building alone does not update that window.

## Ingestion design reference

The broader target operating model is documented in
[Agentic development target](../docs/architecture/agentic-development-target.md).
This pilot is currently the production-like runtime in that model: its normal
tests are deterministic or fake-model checks, while replay measurements and live
SDK smoke tests are explicit and isolated.

The [target database model](review/TARGET_DATABASE_MODEL.md) provides proposed
relational entities, typed fields, fictional example values, constraints, and a
current-to-target mapping. It is a design proposal, not the installed schema.
Project-authored documentation and diagrams use English; imported source records
retain their original language.

Before extending the pilot, review the [ingestion model and process](review/INGESTION_REVIEW.md).
It separates what exists from proposed revisions, with an interactive model,
process, and evolution explorer in `review/ingestion-explorer.html` for discussion
in Codex. No real conversations are embedded in that explorer.

The running build is a first slice, not proof of complete-history coverage or
useful project understanding. The checkpoint focuses on evidence, versioning,
project membership, corrections, and visible discovery progress.

## Launch

Requirements: macOS 14+, Xcode command-line tools, Python 3.12+, Docker Desktop,
and Codex signed in with your ChatGPT account (`codex login`).

From this `pilot` directory:

```sh
python3 run.py start
```

This starts the Docker backend, builds and opens **Hoomy Pilot**, and starts the
local collector. Repeating it pauses discovery and replaces the collector so code
updates take effect; it does not create a second collector. Quit the app before
relaunching after an update. The first build downloads the official Python
container image. No history is processed
until you confirm scope in the app.

To re-check the subscription-backed Codex adapter independently of discovery:

```sh
python3 run.py check-inference
```

The command requires an exact typed confirmation, consumes one small Codex
request, and sends only a fixed protocol probe—never conversation or project
content. It verifies ChatGPT sign-in, the restricted no-tools boundary, and a
structured model response, then prints only safe version, model, and pass fields.

## Run discovery

1. Read the scope screen. This version reads local Codex `sessions` and
   `archived_sessions`, not Claude histories or other machines. Add excluded
   repository paths before starting. Exclusion follows the session's recorded
   working directory; it cannot identify every sensitive topic inside a session.
   If exclusions exist, conversations without a usable recorded directory are
   held back as `scope_unknown`, not processed.
2. Confirm that these conversations may be processed through Codex with your
   account, then start discovery. Local storage does **not** mean local inference.
   Analysis consumes your subscription allowance. There is no Hoomy telemetry.
3. Review the metadata-based proposals. **Garder**, **Corriger**, or
   **Pas un projet** decides each one; no progress or next actions are inferred
   from titles. **Chercher d’autres projets** explicitly starts another pass.
4. Open a kept project and choose **Approfondir** for source-linked summaries.
   Detailed statement review is optional. Advanced regrouping stays in the project
   detail; dismissing a project is not a privacy exclusion.
5. Pause whenever useful. The collector runs independently of the window. Sleep
   suspends computation; transient transport errors have bounded retries and a
   persisted queue. Completed conversations remain saved. An interrupted summary
   chunk may be repeated, and repeated failures require explicit resume.

History is not automatically a task list. An unfinished intention in an old
conversation is not proof that you want it resumed now.

## Give feedback during this iteration

Use this private Codex conversation. Keep Hoomy in the foreground and either:

- In a voice chat, say “Look at the Hoomy screen” and describe the problem;
  with Appshots enabled, Codex can inspect the current app at that moment.
- In text chat, capture/crop the relevant screen and attach it here.

Then say, briefly:

- What you expected Hoomy to understand.
- What it showed or changed instead.
- Whether the issue is understanding, evidence, interaction, or wording.

For example, point to a grouping that should be split or an unexplained change.
Screenshots and Appshots can contain private project information and are sent to
Codex for this feedback conversation. Inspect or crop them first; keep unrelated
apps out of view and do not post captures in the public repository. If a visual
is unnecessary, describe the issue without a capture.

Use the app's corrections for your actual understanding, and this conversation
for feedback on the product itself. Brief reviews save your verdict and review
time locally. They are development feedback, **not** a completed research pilot.

## Start fresh

In the app, choose **Reset Hoomy…** and confirm. Or, with the backend running:

```sh
python3 run.py reset
```

This permanently clears Hoomy's copied snapshots, briefs, project groupings,
corrections, reviews and discovery state. It stops discovery and returns the app
to scope confirmation. Results from the old run cannot repopulate the new one.
Your original Codex conversations and sign-in are untouched. Reset does not erase
provider-side processing records, machine backups, or screenshots you have shared.

To stop the local services without erasing understanding:

```sh
python3 run.py stop
```

Close the app window separately. `python3 run.py status` shows local health and
coverage without dumping conversation contents.

## Experimental local runtime boundaries

- The API is unauthenticated and published only on `127.0.0.1:8765`. Do not expose
  it to your network. Other processes under your local account can access it.
- SQLite lives in a dedicated Docker volume; edge lifecycle files live in
  `~/Library/Application Support/HoomyPilot`. Neither belongs in Git. Credentials
  stay with the host Codex installation and are never mounted into the container.
- Text-only analysis. Images and other non-text blocks are preserved in raw data
  but not interpreted. Invalid files and snapshots above 16 MiB are surfaced as
  failures, not silently counted as analyzed. Source format compatibility is experimental.
- Exact source quotations are checked mechanically. Their relevance and the
  quality of the interpretation still require human review.
- There is no agent dispatch, autonomous project action, iOS distribution,
  opportunity radar, satisfaction optimization, or public results export yet.
- The ten-session evaluation comes after the method is frozen. No performance
  claim follows from this development build.

Design and API decisions: [CONTRACT.md](CONTRACT.md).

Developer checks: `python3 -m unittest discover -s tests -v` and
`swift build --package-path apps/HoomyPilot`.
