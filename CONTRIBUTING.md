# Contribute to Hoomy

Help answer an open question, reproduce a failure, improve a boundary or make the
project easier to investigate. Negative and inconclusive results are useful.
Start with the [research map](docs/research/README.md),
[research journey](docs/research/research-journey.md) and
[ways to try the current version](docs/guide/try-hoomy.md).

## Pick a question

A good contribution states what it is trying to learn. Examples:

- Does a conclusion follow from its own cited passages?
- Does a project brief reduce review time without hiding uncertainty?
- Does one boundary reject a concrete unsafe or unsupported input?

Check the existing findings before calling an idea new. The original
[EXP-0001](experiments/EXP-0001-conversation-brief/README.md) is a proposed
single-session evaluation, still `not run`; engineering pilots are not substitutes
for its protocol. Current Act/Learn code and learning proposals do not establish a
trained policy or autonomous learning.

## Work with your agent

Use [the agent guide](docs/guide/ask-your-agent.md) to connect the question to code,
tests and prior observations. Agree on a small protocol before using personal
data, changing a deployment or invoking external inference. Keep the source
commit and environment versions so another reader can identify what was tested.

For a software change, explain the observed problem, resulting behavior and
relevant checks. For an experiment, complete the
[result template](docs/research/result-template.md): hypothesis, method,
measurements, failures, limits and decision. Separate fixture results, operational
observations and research evidence. Label retrospective reconstruction and any
protocol deviations; do not imply preregistration or completed measurements.

## Share findings, not your source corpus

Public reports should use aggregate measurements and project-authored explanations.
Use fictional inputs for reproducible regression cases. Review even aggregates for
identifying details, especially in small samples.

Do not submit raw or copied conversations, prompts containing source evidence,
provider outputs or receipts, credentials, auth databases, screenshots of private
projects, personal paths, repository identities or private operator configuration.
Access to a source is not permission to republish it. The code/documentation
licenses do not grant rights over private evidence or provider-generated records.
For a security issue, follow [SECURITY.md](SECURITY.md).

## Submit a reviewable change

1. Make a focused branch from current `main` and preserve unrelated work.
2. Run checks appropriate to the affected component; see
   [the testing route](docs/guide/try-hoomy.md#1-inspect-the-pipeline-without-personal-data).
   Report skipped checks and prerequisites separately.
3. Review the complete diff and public attachments. Your agent may prepare the PR;
   submit it only with your authorization and share only material you have rights to.
4. Open a PR describing the question or bug, the result, validation and limits.
   Link relevant existing findings. Maintainers review acceptance, merging and releases.

Write project-authored documentation, diagrams, schemas and fictional examples in
English. Preserve imported evidence in its original language; do not rewrite it as
part of a documentation cleanup. Keep the current filesystem organized around
components and questions. Git records revision history; avoid phase-specific
copies of the same code or current-state documents.

The [release/export manifest](release/public-export.json) describes the original
reviewed source selection; it does not automatically authorize exporting new files,
datasets or private Git history. See [release preparation](release/README.md) for
that separate boundary. Do not edit immutable release provenance to make it appear
to cover a later contribution.

Project-authored code is [Apache-2.0](LICENSE); documentation and research are
[CC BY 4.0](LICENSE-DOCUMENTATION.md). See [NOTICE](NOTICE).
