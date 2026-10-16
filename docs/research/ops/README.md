# Hoomy Research Ops

Purpose: keep the research moving every day without losing the thread.

The operating principle is:

`one day -> one goal -> one artifact -> one review question`

The project is research, so progress is not only code. A useful day may produce a clarified assumption, an experiment card, a trace schema, a failed prototype, a decision, or a sharper question.

## Daily Contract

Use `/goal` to set or resume the daily contract.

Format:

```md
/goal
Today we advance: <one objective>
Done when: <observable artifact>
Constraint: <time, scope, safety, or quality bound>
Review needed: <what the human should judge in 15 minutes>
```

If no explicit `/goal` is provided, Codex should propose the smallest next move from [Current Goal](./current-goal.md), [Backlog](./backlog.md), and [Assumptions Register](./assumptions-register.md).

## Daily Review

Every working day should end with a short review in `daily/YYYY-MM-DD.md`.

The review must be readable in 15 minutes and contain:

- goal
- concrete artifacts
- evidence learned
- decision needed
- test or review task
- next proposed goal

## Roles

Codex sets pace by:

- proposing the next smallest useful goal
- keeping docs coherent
- creating or updating artifacts
- running experiments when possible
- comparing against baselines
- ending with one decision question

The human steers by:

- reading the daily review
- testing one artifact when needed
- answering one decision question
- correcting mission, taste, risk, or priority

## Weekly Cadence

Monday: choose one assumption to pressure-test.  
Tuesday and Wednesday: build the smallest experiment or artifact.  
Thursday: evaluate and compare to baseline.  
Friday: synthesize, update assumptions, and choose the next direction.

## Automation Stages

Stage 1: manual `/goal` loop.

Stage 2: scheduled local or remote daily review generation.

Stage 3: background runner for trace replay, evals, prototype checks, and report generation.

Guardrail: automation may propose, test, and report. It must not silently change core doctrine or mark assumptions as validated.

## Files

- [Current Goal](./current-goal.md)
- [Backlog](./backlog.md)
- [Decision Log](./decisions.md)
- [Assumptions Register](./assumptions-register.md)
- [Goal Template](./goal-template.md)
- [Daily Review Template](./daily-review-template.md)
- [Experiment Template](./experiment-template.md)
- Daily review artifacts (private working records; not exported)
- Completed goal records (private working records; not exported)

