# Hoomy

**Building an AI companion that understands your projects and helps you decide what deserves your attention.**

An open research project, starting with developers and their coding agents.

The current preview supplies infrastructure for experimenting with project
understanding from authorized conversations and, as the research develops,
verifiable facts. It brings selected Codex conversations together, keeps your
intent explicit, and presents project views you can inspect through their sources.
It does not yet automatically fact-check those views.

Our first concrete question is whether that view is **accurate, useful, and easier
to review than reopening the conversations**. We are building the tools to test it.

## A working research preview

[Version 0.0.1](https://github.com/xbasset/hoomy-open/releases/tag/v0.0.1) includes
macOS/Linux collection, a self-hosted central service, and a Codex plugin for
project context, cached conversations and cited syntheses. A private two-machine
pilot exercised this pipeline; it did not establish general reliability or
attention savings. Fact checking and Act/Learn remain experimental.

This is an operator-oriented preview: setup requires your own server and identity
configuration. The default endpoint is not an invitation to a hosted service.
Collection needs explicit project authorization; optional synthesis separately
sends selected text to OpenAI through your Codex account. See
[capabilities, data boundaries and limits](docs/guide/current-capabilities.md).

## Choose your route

- **[Understand the research](docs/research/README.md)** — the questions,
  [iterations and findings](docs/research/research-journey.md), and what remains unproven.
- **[Try Hoomy](docs/guide/try-hoomy.md)** — start with software fixtures, then
  optionally test your own project and review a synthesis.
- **[Contribute an experiment](CONTRIBUTING.md)** — use your agent to investigate
  the code, prepare a protocol and report aggregate findings.

After cloning, [ask your agent](docs/guide/ask-your-agent.md) to trace a question
to code, tests and research evidence. There is no public conversation dataset or
one-command hosted demo.

Code: [Apache-2.0](LICENSE). Project-authored documentation and research:
[CC BY 4.0](LICENSE-DOCUMENTATION.md). See [NOTICE](NOTICE) and the
[research charter](CHARTER.md).
