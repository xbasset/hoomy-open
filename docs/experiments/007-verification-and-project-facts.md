# Experiment 007 — Verification and explicit project facts

Date: 2026-09-27. Predecessor: [on-demand baseline](006-on-demand-act-learn.md).
Status: implemented; [daily-use retrospective](007-rex-20261007.md) published on 7 October 2026.

## Observed failure

The first real suggestion was relevant to an older conversation, but the user had
already completed the proposed activity. The model explicitly lacked current state.
The saved trace showed eight sources selected and only four read: the former 16 MiB
whole-file reader rejected four large conversations. Tool-heavy excerpts also crowded
out human exchanges. The form displayed 30 minutes while the user's sentence said 15.
Private records remain in ignored runtime files, not this documentation.

Absence of a final completion message must never imply unfinished work. This is
an uncertainty problem as well as a recommendation and retrieval problem.

## Flow

1. The human describes the current situation. Project and duration controls are
   optional. An explicit numeric duration in the sentence overrides the default.
2. Snapshot Discover projects, recent assessments and dated project facts. Retrieve
   up to eight linked conversations from the bounded catalog.
3. Generate provisional candidates with a precise `completion_claim` and
   `needs_confirmation`. The claim describes this activity, not the whole project.
4. A separate Codex SDK agent verifies whether the provisional selected activity
   is still useful, if possible. It chooses the verification using available native
   tools. There are no domain-specific checks, examples, skill lists or routing rules.
5. Reconsider the candidates using the dated observations and limits. A failed or
   inconclusive check remains unknown. A changed selection is not automatically
   verified merely because a different candidate was checked.
6. If needed, ask a quick human confirmation. Already done records completion and
   requests another activity. Unknown records uncertainty and offers another idea.
   The follow-up does not ask another preliminary question; it suggests or abstains.
7. Keep usefulness assessments separate: only an activity actually performed and
   evaluated contributes observed impact and attention to learning metrics.

The verification budget is 90 seconds including setup. The stage uses the installed
Codex Python SDK and the existing ChatGPT sign-in/model (`gpt-5.6-luna`, medium).
Native shell and live web tools are available; MCP/apps, project instruction loading
and skill discovery remain disabled. Filesystem writes are sandbox-blocked. External
read-only behavior and task scope are instruction constraints, not a network-level
proof that arbitrary requests are harmless. Never execute the suggested activity,
read credentials/unrelated data, or send private source content in public queries.
Cancellation discards late results. Unavailable verification degrades to unknown.

## Minimal data change

| Field | Meaning and lifetime |
| --- | --- |
| `act_facts.request_id` | One editable fact per suggested activity; joins the immutable request. |
| `project_id` | Existing Discover identity; merged projects resolve at context creation. |
| `claim` | Precise completion statement copied from the candidate, with a legacy title fallback. |
| `answer` | `already_done`, `todo`, or `unknown`; never infer preference from completion. |
| `source` | Explicit user confirmation or completed-activity assessment. |
| `confirmed_at` | Date of the declaration; it is not an eternal guarantee. |
| request `facts` | Frozen snapshot of relevant declarations at decision time, beyond the 30-assessment window. |
| source `agent_verification` | Dated finding, status, evidence, limits, prompt version and bounded observable tool trace. |

No separate task database or Understand model is introduced. Facts do not rewrite
Discover descriptions. Semantic reconciliation happens in the LLM context: later
statements about the same claim supersede older assumptions; unresolved conflicts
stay explicit. Facts survive until edited or the existing explicit database reset.
This is contextual adaptation, not weight training. Unbounded fact growth is a
known future pressure; compact only when actual usage demonstrates the need.

## Retrieval and UI

The reader streams up to 512 MiB per source, skips oversized individual records,
and retains bounded recent user/assistant/tool excerpts (3,200/3,200/800 characters).
It preserves source hashes and line IDs, detects changing sources and labels partial
coverage. It does not promise exhaustive history. The catalog remains capped at 160.

The desktop starts on **Pour maintenant**: a sentence, visible interpreted duration,
and **Trouver une activité**. Optional filters live under **Préciser ma demande**.
Confirmation buttons record real state without requiring an impact score. Source
counts distinguish conversations actually read from those selected.

## Validation and retest

- Python suite: 180 tests, one environment-gated skip; Swift: 16 tests passed.
- Replay of the first request's eight selected sources now reads all eight and
  retains human messages. This is retrieval validation, not a new recommendation.
- Real SDK smoke test: the agent chose a command to inspect a fictional local
  checklist, returned confirmed with the file evidence and freshness limits in
  15.88 seconds. No project-specific tool was implemented.
- Automated coverage includes persistent facts, follow-up context, unknown state,
  cancellation, unavailable verification, reconsideration, large records and duration.
- After unlocking, native visual review confirmed the real app on **Pour maintenant**,
  the preserved feedback, the three confirmation buttons, optional project controls,
  and 15 minutes parsed from the restored user draft. No new real request was sent.
- The signed app, Docker backend and host worker were rebuilt/relaunched. All 15
  project objects and the existing request/feedback match the pre-update export;
  Discover remains paused. A full pre-update SQLite backup passed `quick_check`
  under ignored `pilot/runtime/checkpoints/20260927-before-act-v2/`.
- A complete real-project confirmation-to-next-suggestion trial remains for the user;
  automated tests cover that data flow with fictional evidence.

Retest on the preserved database:

1. Open **Pour maintenant**, describe your available time, request one activity.
2. Check whether the activity is still relevant and whether uncertainty is honest.
3. If already done, click **Déjà fait → autre idée**. Verify the dated confirmation
   and a new suggestion; inspect the saved context for the explicit fact.
4. If uncertain, click **Je ne sais pas**. Hoomy must preserve uncertainty, not mark
   the work as completed or learn that you dislike it.
5. Perform a useful activity and assess actual impact and attention. Repeat later;
   report repeated completed suggestions and unnecessary questions.

Success means fewer stale suggestions and less user effort resolving the agent's
uncertainty. The first trial exposed a failure; these checks do not yet prove learning.
