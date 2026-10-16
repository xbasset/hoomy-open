# Progressive discovery wizard

The discovery screen now follows four milestones: access, title discovery,
human decisions, and the project database. A fixed footer holds progress and
actions; the central area presents one decision at a time. Diagnostic logs,
catalog corrections and maintenance operations remain under Details.

## Consent boundaries

1. Local access inventories Codex titles and directories without reading
   conversation bodies or calling inference. Exclusions are collected first.
2. A separate action enables title classification through the existing Codex
   account. The local inventory pauses at `awaiting_consent` until then.
3. Optional completion asks separately for targeted conversation excerpts and
   automatic associations to validated projects. New projects still need review.

These are application-level consent boundaries, not a new macOS permission
dialog or a standalone installer. The pilot still requires its local service.

## Human decisions

Keep is the primary action. Inactive, edit, merge and reject are secondary
choices. Rejection explains that it marks source conversations as no-project;
deferral does not. Deferrals are view-local; source proposals remain persisted.
Finishing without optional completion is stored per data generation on this Mac.
An existing completed database opens directly to its projects, without reset.

## Verification

`python3 -m unittest discover -s tests` checks backend behavior, including no
inference or body snapshot before title consent, followed by successful resume.
`swift test --package-path apps/HoomyPilot` checks wizard routing and UI models.

For disposable interactive QA, run `python3 tests/wizard_preview.py`, then open
the built app with `--args --api-port 8876`. This uses two fictional sources,
a temporary store and fake classification; it never reads real history or calls
the model. Quit the preview app before reopening normally on port 8765.

The interactive check exercised local inventory, separate title consent,
active/inactive review, excerpt-consent explanation, opting out of completion,
and the resulting project database. Existing project data is not reset by this
release. Parent/child session attribution remains a separate follow-up.
