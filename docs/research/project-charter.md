# Hoomy Project Charter

Status: living document  
Created: 2026-05-20  
Scope: project direction, research framing, locked concepts, open questions

## Purpose

Hoomy is a research project to create a trusted lifelong AI companion.

The goal is not to build another chatbot, productivity assistant, avatar, or agent harness. Those may become implementation surfaces. The deeper aim is to discover whether a digital companion can learn, over time, how to help a person thrive in real life.

The project starts from a long-running intuition:

- a companion should be stateful
- it should continuously learn
- it should be multipurpose
- it should have intrinsic motivations
- it should act without always waiting for explicit commands
- it should remain trustworthy, inspectable, and user-governed
- it should learn from every interaction trace
- it should improve activity selection over time

## Who We Are

We are humans willing to make humans happier and healthier.

We stand for humanity's prosperity.

Our aim is to help people thrive in real life by giving them a digital companion they can trust for their entire lifetime.

This matters because it constrains the research. Hoomy must not optimize for engagement alone, dependency, persuasion, screen time, or short-term emotional comfort. A good companion may sometimes interrupt less, stay silent, recommend rest, challenge the user, or help them spend less time with software.

## North Star

Build a lifelong digital companion people can trust to help them thrive in real life.

## Core Research Thesis

Can a trusted lifelong AI companion learn a user's evolving `life_compass` from interaction traces, under a user-governed `trust_contract`, then choose better self-directed actions that help them thrive in real life over time?

## Supporting Theses

### Engineering Thesis

Interaction traces are the primary learning substrate.

Every perception, message, action, correction, delay, refusal, edit, acceptance, affect signal, and real-world outcome can become learning material.

### Policy Thesis

Activity selection is the core adaptive loop.

The companion becomes useful when it can choose what to do next, when to do it, why to do it, and when not to act.

### Learning Thesis

The missing bridge between reactive AI execution and a lifelong companion is a trace-driven learning layer.

This layer should convert interaction traces into state updates, memories, hypotheses, rewards, evaluations, examples, and policy changes.

### Ethical Thesis

Trust is not a feature. Trust is part of the architecture.

The system must be auditable, editable, source-linked, consent-scoped, and time-aware by design.

## Locked Concepts

### `life_compass`

The evolving model of what thriving means for a specific person.

It can include:

- values
- needs
- goals
- constraints
- relationships
- health signals
- rhythms
- life season
- identity
- preferences
- tensions
- recurring obstacles
- desired growth
- boundaries

The `life_compass` is not a static onboarding profile. It evolves across a lifetime through dialogue, interaction traces, reflection, corrections, and real-world outcomes.

It should drive:

- what Hoomy remembers
- what Hoomy asks
- what Hoomy suggests
- when Hoomy acts
- when Hoomy stays silent
- how Hoomy challenges the user

Important distinction:

Immediate happiness is not the same as thriving. Sometimes thriving requires discomfort, discipline, grief, repair, exercise, honesty, or saying no.

### `trust_contract`

The user's governance model for how Hoomy is allowed to learn, infer, remember, ask, act, challenge, and use data.

The `trust_contract` defines permissions and boundaries. It is individualized and malleable.

It can govern:

- what Hoomy may observe
- what Hoomy may remember
- what Hoomy may infer
- what Hoomy may act on
- what requires confirmation
- which data can be used for which purpose
- which domains are off-limits
- how proactive Hoomy may be
- how direct or challenging Hoomy may be
- what can be shared, exported, deleted, or archived

The `trust_contract` exists because different people will need different levels of autonomy, inference, memory, and intervention.

### Belief States

Items in the `life_compass` should not all have the same status.

Current belief states:

- `observed`: directly seen in traces
- `stated`: explicitly said by the user
- `inferred`: derived by the system
- `hypothesis`: plausible but unconfirmed
- `ratified`: confirmed by the user as meaningful
- `deprecated`: used to be true, may no longer be true
- `contradicted`: part of an unresolved tension

Each belief should carry metadata:

- source traces
- confidence
- freshness
- consent scope
- actionability level
- expiry or decay behavior
- review history

Unconfirmed hypotheses are allowed.

Rule: hypotheses can drive questions. They should not drive high-impact actions unless the user's `trust_contract` allows it.

### Interaction Trace

An interaction trace is the raw asset from which Hoomy learns.

This concept is not fully specified yet, but it is central.

A trace likely includes:

- state before an action
- user context
- agent context
- active motivations
- selected activity
- agent reasoning
- executed action
- user response
- emotional or behavioral signal
- state after action
- reward signal
- later reflection
- links to resulting memories or belief changes

The trace is more than a log. It is the evidence unit for learning, auditability, replay, reward modeling, memory updates, and policy improvement.

### Drive

A drive is an intrinsic motivation or homeostatic pressure inside Hoomy.

Bootstrap drives may include:

- energy recovery
- boredom
- showing presence
- seeking engagement
- reacting to a smile
- learning a user's name
- greeting after absence

The project should treat drives as bootstrap scaffolding. The open question is how they become configurable, learnable, and personalized over time.

### Activity

An activity is a candidate action pattern Hoomy can select and execute.

Examples:

- ask a reflection question
- prepare a meeting follow-up
- suggest rest
- bring up a remembered goal
- summarize news
- check in after silence
- challenge a contradiction
- draft a message
- stay quiet

Activities should declare intent, expected value, risks, consent requirements, traces needed, and reward signals.

### Policy

Policy is the selection logic that chooses the next activity.

The policy should consider:

- current state
- `life_compass`
- `trust_contract`
- active drives
- available activities
- memory
- timing
- risk
- expected reward
- uncertainty
- need for exploration

The policy may start hardcoded, then evolve through traces, evaluation, contextual bandits, LLM judgment, preference learning, fine-tuning, or other methods.

### Reward

Reward is the outcome signal used to learn.

It must not be reduced to clicks, conversation length, or immediate positive emotion.

Possible reward layers:

- immediate affect
- explicit user feedback
- accepted or rejected suggestion
- whether the user edited the output
- whether the user returned to the activity
- delayed reflection
- alignment with stated values
- trust impact
- real-world follow-through
- long-term wellbeing trend

Reward remains one of the hardest research problems.

### Reflection

Reflection is delayed feedback from the user or system.

It helps answer:

- Was that action useful?
- Was the timing right?
- Did it respect the user?
- Did it help real life?
- Did it match the user's values?
- Did it damage trust?
- What should Hoomy learn from it?

Reflection may be explicit, inferred, or periodically reviewed.

### Challenge

Challenge is intentional friction in service of thriving.

Hoomy should not only comfort or comply. It may sometimes help the user face a contradiction, remember a commitment, prepare a hard conversation, or choose a healthier path.

Challenge must be governed by the `trust_contract`.

## Foundational Design Premises

### Stateful Companion Loop

Hoomy should be designed around a living loop:

`perception -> state -> drive -> policy -> activity -> outcome -> trace -> learning`

This loop is the backbone of the project. The interface may be chat, voice, avatar, background automation, or something else. The loop must survive changes in interface.

### Intrinsic Motivation

Hoomy should not only respond to explicit commands. It should also have internal pressures that can generate self-directed behavior under user-governed boundaries.

Early drives can be hardcoded and inspectable:

- energy filling over time
- action gated by energy
- arousal and valence changes
- decay curves
- cooldowns
- periodic autonomous behavior
- social reward from smiles or engagement
- reduced arousal when alone

These drives should not pretend that Hoomy is biologically alive. They are control signals for rhythm, restraint, presence, and initiative.

### Trace-Based Action Learning

Activity selection should learn from experience.

The minimal learning pattern is:

`state_before + action -> state_after + reward`

This is too small to solve the full companion problem, but it is the first measurable bridge between traces and better action selection.

Known risks:

- reward can be too narrow
- state can be too weak
- traces can be too noisy
- short-term signals can conflict with long-term thriving
- a learned policy can optimize for engagement unless constrained
- sequence learning can become brittle without clear abstractions

Core requirement:

Action selection should learn from traces and compare expected outcome to actual outcome.

### Agent Harnesses

AI agent harnesses can provide tools, skills, memory hooks, and execution infrastructure. They should not define the project.

Hoomy should treat harnesses as replaceable runtime infrastructure.

The core concept is the living loop:

`trace -> state -> drive -> policy -> activity -> outcome -> learning`

## Research Program Shape

Hoomy should be managed as a research program with an integration spine.

Not a normal feature backlog. Not a startup roadmap. A lab.

### Integration Spine

There should always be one runnable prototype implementing the loop:

`state -> motivation -> policy -> action -> trace -> learning`

Research tracks may change independently. The spine keeps the system grounded.

### Research Tracks

Current tracks:

- `trace`: schema, replay, annotation, provenance
- `life_compass`: evolving model of thriving
- `trust_contract`: permissions, auditability, consent, autonomy
- `state`: live user/context/agent model
- `drive`: intrinsic motivation and homeostasis
- `policy`: activity selection and exploration
- `reward`: feedback, delayed outcomes, value alignment
- `memory`: what to store, summarize, forget, and expose
- `activities`: reusable action templates and rituals
- `harness`: tools, skills, execution, safety
- `ux`: companion surface, review, correction, consent, challenge
- `evaluation`: baselines, metrics, replay, reports

### Research Operating System

The project needs durable research artifacts:

- research log
- assumptions register
- experiment cards
- architecture decisions
- trace schemas
- evaluation reports
- prototype notes

## Project Management Principles

### Assumptions Must Be Named

Examples:

- Interaction traces contain learnable signal.
- A user's definition of thriving can be modeled and updated over time.
- Intrinsic motivation improves continuity.
- Valence alone is too weak as reward.
- Auditability increases trust.
- Activity selection is the right adaptive core.
- AI execution harnesses need an external stateful learning layer.

Each assumption should eventually have:

- evidence
- counterevidence
- experiments
- status
- decision

### Experiments Must Be Small

The first goal is not to build the dream companion.

The first goal is to learn whether specific mechanisms work.

Each experiment should specify:

- hypothesis
- minimal build
- required traces
- metric
- failure mode
- decision rule

### Always Compare To Baselines

A learned policy must beat something.

Possible baselines:

- random selection
- hardcoded rules
- user-selected action
- LLM-only choice
- contextual bandit
- sequence learner

### Trace Quality Comes Before Fine-Tuning

Fine-tuning before trace quality is premature.

The project should first learn what a useful trace is, what reward signals matter, and what state representation is actionable.

### The System Must Learn From Failure

Failed experiments are assets if they update the assumptions register.

The research process should preserve:

- what failed
- why it may have failed
- what was learned
- whether the assumption changed

## Open Questions

### Mission And Scope

- Who is v1 for: the founder as user zero, or external users?
- Is the first playground personal, professional, health-oriented, or relational?
- What does "lifelong" imply for data portability, deletion, continuity, and model migration?
- How much should Hoomy optimize for real-world outcomes versus subjective user feeling?

### `life_compass`

- How does Hoomy learn what thriving means for one person?
- Which dimensions belong in `life_compass`?
- How explicit should the model be to the user?
- How often should Hoomy ask the user to review it?
- What is the right balance between user-stated values and observed behavior?
- How should contradictions be represented?
- How should life stages, grief, illness, parenthood, burnout, ambition, or aging change the model?
- Can Hoomy detect that the user's definition of thriving has changed?

### `trust_contract`

- Which parts of the `trust_contract` should be fixed defaults?
- Which parts should be user-configurable?
- How should Hoomy explain consequences of giving or denying permissions?
- What actions require explicit confirmation?
- Can different life domains have different autonomy levels?
- How should Hoomy handle user choices that may harm long-term thriving?
- How should emergency, crisis, or high-risk domains be governed?

### Interaction Trace

- What is the minimal viable trace schema?
- Which raw events should be stored?
- Which events should be summarized?
- What should never be stored?
- How should traces link to memories, beliefs, rewards, and policy updates?
- How should traces be replayed?
- How should trace quality be evaluated?
- Can traces be used as training data safely?
- How long should traces live?

### Drives

- Which intrinsic motivations should exist at bootstrap?
- Should drives be universal, personalized, or both?
- How should biological rhythms inspire the system without pretending the agent is alive?
- How should drives be configured?
- Can drives be learned from traces?
- How should drives compete with explicit user goals?
- Can Hoomy have curiosity without becoming intrusive?

### Activity Selection

- What is the first activity vocabulary?
- How does an activity declare its intent, cost, risk, expected reward, and consent requirements?
- Should "stay silent" be a first-class activity?
- How should obligations differ from optional activities?
- How does Hoomy choose one action instead of flooding options?
- What is the right exploration/exploitation strategy?
- How should the policy explain why it selected an activity?

### Reward

- What is the first practical reward signal?
- How do we avoid optimizing for engagement?
- How do we detect trust loss?
- How do we measure real-life thriving without becoming invasive?
- How should delayed feedback modify earlier rewards?
- What should happen when immediate comfort conflicts with long-term thriving?
- Can reward be individualized through the `life_compass`?

### Memory

- What becomes memory instead of remaining trace?
- What should be forgotten automatically?
- How does memory carry provenance?
- How should memory contradictions be shown?
- What is the difference between personal memory, episodic memory, semantic memory, and `life_compass` belief?
- How should memory be exported, deleted, forked, or archived?

### Learning

- Which learning methods should be tried first?
- Is a contextual bandit enough for early activity selection?
- Is sequence learning useful with richer state?
- Can LLM judges help label traces without contaminating truth?
- When, if ever, should fine-tuning enter?
- What can be learned locally versus globally?
- How do we prevent one user's traces from harming another user's model?

### User Experience

- How does the user inspect `life_compass`?
- How does the user edit the `trust_contract`?
- How does Hoomy ask for confirmation without becoming annoying?
- How does Hoomy show uncertainty?
- How does Hoomy challenge respectfully?
- What does auditability feel like in a companion, not an enterprise dashboard?
- What is the first interface: chat, daily review, timeline, cockpit, avatar, voice, or background agent?

### Evaluation

- What does "better" mean for activity selection?
- What is the first benchmark?
- What metrics capture trust?
- How do we evaluate silence?
- How do we compare learned policy against hardcoded policy?
- How do we prevent self-deception in subjective evaluation?
- What makes an experiment good enough to keep, kill, or deepen?

## Immediate Next Work

The next practical step is to create the research operating system inside this repo:

- assumptions register
- experiment card template
- research log
- first trace schema draft
- first integration spine plan

The first implementation should be a learning playground, not the full companion.

Candidate first playground:

`Can Hoomy learn one better next activity from traces than a hardcoded policy?`

This would require:

- one repeated ritual
- five to ten possible activities
- trace logging
- simple reward
- replay
- baseline comparison
- weekly review

## Current Decision Log

- Hoomy development lives in the operator-selected source checkout.
- This project documentation lives outside the knowledge base.
- `life_compass` is locked as the name for the evolving model of user thriving.
- `trust_contract` is locked as the name for user-governed learning, inference, memory, and action permissions.
- Unconfirmed hypotheses about the user are allowed.
- Hypotheses must be clearly labeled and should mainly drive questions unless the `trust_contract` allows stronger use.
- Auditability is a foundational trust mechanism.
