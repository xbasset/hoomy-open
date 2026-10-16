# Experiment 012 — Project intent as a starting point

Date: 2026-10-09. Builds on experiment 011; plugin version 0.2.0.

## Hypothesis

A useful recommendation needs a visible, editable account of what the user wants,
not only inferred work history. A project profile can provide that starting point
and expose the distinction between user intent, observed task state and unknowns.

## Smallest usable experience

Hoomy opens on **Mes projets**. Select a project, read its existing context, enter
one goal and save. All six fields are optional:

| Field | Meaning |
| --- | --- |
| `goal` | Desired outcome |
| `motivation` | Why this outcome matters to the user |
| `priority` | What matters next |
| `constraint` | Limits or conditions to respect |
| `success_criterion` | How the user will recognize success |
| `situation` | The user's account of current progress |

The same profile shows Discover's inferred description, examined activities and
their dated evidence, recent project-specific research and uncertainties, linked
conversations, and activity feedback. Linked conversations are not claimed to be
recently read. Global research is not attributed to an individual project.

The optional guided conversation prepares a Codex composer with the project ID.
The user sends it. The Hoomy skill reads the profile, asks one question at a time,
proposes wording, and saves only user-approved fields. It does not start work.
The profile's activity button carries the selected project into the existing
suggestion screen; the user then submits the request.

## Persistence and data flow

No second database or engine. `project_context_answers` remains the append-only
record of dated user context. Direct edits carry `source=user_project_profile`,
a dimension, answer, response, timestamp and superseded record ID. Clearing a
field writes a tombstone so an older answer cannot return. Generation and source
scope apply as before. A revision token rejects concurrent stale writes.

`GET /api/projects/context/<id>` assembles the profile. `POST
/api/projects/context` applies a partial revision-checked patch. MCP exposes
`hoomy_project`, `hoomy_update_project`, and `hoomy_project_guide`.

Saved context flows into both Codex research and Decisions selection/question
review. A changed profile cancels outstanding or ready selections using its old
context and invalidates cached question reviews. The next selection reads current
context. Handoff also uses the current selection's context.

Intent never changes completion evidence: writing “I finished something” in the
narrative does not automatically mark a candidate completed. Existing evidence,
expiry and pending-state gates still apply. No inference starts merely by saving.

## Validation and next observation

- Isolated backend tests: persistence, partial edits, clearing, atomic validation,
  concurrent revisions, stale selection rejection and unchanged completion facts.
- MCP/bridge contract tests include read/edit/guide behavior.
- Browser test against a disposable fixture: saved a fictional goal and motivation,
  reloaded, verified persistence and prepared the guided composer link.
- Real user goals and feedback were not fabricated during tests.

Native guided-chat navigation and the improvement in recommendation relevance
remain user tests. The existing fast-decision input budget is 64 KB; excessively
long multi-project context fails explicitly rather than being silently truncated.
The profile is a current view with bounded research/feedback history, not a full
audit explorer or automatic synchronization of every Codex conversation.

Test sequence: open Hoomy, select one project, state one goal, optionally use guided
clarification, save, then request an activity for that project. Check whether its
rationale reflects the saved goal and whether unknown task states remain explicit.
