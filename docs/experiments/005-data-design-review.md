# Data design review checkpoint

Date: 2026-09-26. Status: design reviewed; not a deployed cognitive architecture.

The preserved Discover project map is a useful input. Recognizing project identity
and linking conversations does not establish current project understanding. The
user confirmed this boundary, not every association or the complete proposed model.

A private case study explored seven structures: projects, sources, understanding
revisions, decision cycles, activity candidates, model evaluations and interaction
events. Source records and the case study remain under ignored `pilot/runtime/`.
No Jev inference ran; populated evaluations were an analyst replay, not measured
model performance. The checkpoint export is not a full live database backup.

A local interactive review app in `sites/hoomy-review` exposes the structure,
input/transformation/output steps, editable example copies, field proposals,
step verdicts and contextual comments. D1 saves the review separately from sources;
Markdown/JSON exports support a conversational handoff. TypeScript, production
build, persistence after reload and stale-write protection passed. Hosting was
not completed because the Sites plugin files disappeared during the session.
The private `lib/design-data.json` is intentionally excluded from Git; restoring
this workshop requires copying the private study's `noflinch-model-data.json` there.
Do not publish private records to the source repository.

## User review and decision

The user wants real daily usage, not further expansion of the architecture:

- Suggest the next useful activity on explicit request.
- Estimate impact and active attention; record explicit observed values afterward.
- Learn from these discrepancies and expressed preferences using the existing LLM.
- Preserve candidates and the context behind the decision.
- Keep absence of response distinct from rejection and usefulness.
- Defer dedicated learned rankers, sensors and proactive triggering.

The next experiment skips a separate Understand phase. Act & Learn will establish
what existing LLMs and bounded project-source retrieval already achieve, revealing
which missing understanding would actually improve recommendations. No learning
improvement has yet been demonstrated.
