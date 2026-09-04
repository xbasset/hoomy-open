# Hoomy

Hoomy is open research into whether an AI companion can help a person move the work that matters with less attention.

## The first experiment

We are building one small thing first:

> Can Hoomy turn a completed Codex session into a brief that is accurate, traceable, and quick to correct?

The first public prototype will:

1. read a session selected by the user;
2. summarize its intention, outcome, open questions, and possible next step;
3. link every important statement to evidence in the local session;
4. let the user accept, edit, or remove what Hoomy produced.

It is not usable yet. [EXP-0001](experiments/EXP-0001-conversation-brief/README.md) defines the pilot and the decision it must support.

## What comes next

The next project update will contain:

- a locally runnable prototype;
- instructions for using it with sessions you control;
- the aggregate pilot result, including failures and the decision about what to do next.

No source session will be published.

## Privacy

- Source sessions remain under the user's control. Hoomy does not collect or publish them.
- The prototype has no telemetry.
- This project provides no public or synthetic conversation corpus.
- Never post conversations, prompts, credentials, private names, repository names, or local paths in an issue or pull request.

The prototype is local software. If external inference is introduced, Hoomy must show what will leave the device and who will receive it before the run begins.

## Take part

Right now, useful contributions are focused: challenge the experiment, help build the prototype, or review the result. Open an issue before substantial work and keep pull requests small. This is a maintainer-led project, and no contribution should contain private source material.

## License

Code is licensed under [Apache-2.0](LICENSE). Documentation and research are licensed under [CC BY 4.0](LICENSE-DOCUMENTATION.md). Any future CC0 material will be clearly marked where it appears.
