# Hoomy project charter

## The ambition

Hoomy explores a lifelong AI companion that learns what matters to a person as their priorities change, and helps them thrive in real life.

AI can do more than ever. Human attention remains limited. We want to understand whether a companion can help people decide what deserves attention, carry work forward, and know when to act, ask, or stay quiet.

This is a research direction, not a claim about what the current prototype can do. The mechanisms must earn their place through experiments.

## What we want to learn

- **What matters:** Can Hoomy learn from user-authorized interactions and corrections without mistaking a temporary preference, an inference, or silence for a lasting truth?
- **When to help:** Can it make assistance useful enough to justify the attention spent explaining, deciding, and reviewing? When is leaving someone alone the better choice?
- **How to earn trust:** Can people understand, correct, and limit what Hoomy believes and does as its capabilities grow?

## Our boundaries

People define what progress means for them. More output, longer conversations, and more screen time are not success by themselves. Rest, intentional pauses, and less interaction with Hoomy can be good outcomes.

Users choose what Hoomy may see, infer, remember, and do. Its understanding must be inspectable, correctable, and deletable. Guesses must remain distinguishable from facts; consequential actions require confirmation or a specific prior authorization, not permission inferred from behavior.

Open research does not mean open personal data. Source sessions remain private, and Hoomy has no telemetry. Any external processing must identify the recipient and data involved and require the user's explicit choice. The project provides neither public nor synthetic input datasets.

## Where we begin

Our first use case is a coding-agent chief of staff. We begin with [EXP-0001](experiments/EXP-0001-conversation-brief/README.md): turning one selected Codex session into an accurate, evidence-linked brief that is quick to correct.

We will share the method, code, aggregate results, failures, and what they change about our next step. Each experiment should help us decide whether to improve, expand, redesign, or stop.

Contribute by challenging the experiment, helping build it, or trying a release with your own authorized data. Never share the source sessions.
