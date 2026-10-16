# Assumptions Register

This register tracks assumptions that must be tested, weakened, strengthened, or killed.

Status values:

- `open`: named but not tested
- `testing`: experiment underway
- `supported`: current evidence supports it
- `weakened`: current evidence challenges it
- `rejected`: no longer guiding the project
- `reframed`: replaced by a sharper assumption

## A001: Interaction traces contain learnable signal

Status: open  
Claim: Repeated traces can improve future activity selection.  
Why it matters: This is the engineering core of Hoomy.  
Current evidence: conceptual only.  
Next test: choose a first playground, then define the smallest trace needed for that playground.

## A002: A user's definition of thriving can be modeled over time

Status: open  
Claim: `life_compass` can represent a moving, individualized definition of thriving.  
Why it matters: Without this, Hoomy cannot optimize for real-life flourishing.  
Current evidence: conceptual only.  
Next test: draft `life_compass` schema v0 and apply it to user zero.

## A003: Trust requires auditability

Status: open  
Claim: Inspectable, source-linked, editable beliefs increase trust.  
Why it matters: Hoomy will infer sensitive things.  
Current evidence: strong intuition, not tested.  
Next test: prototype belief card with source traces and correction flow.

## A004: Intrinsic drives improve continuity

Status: open  
Claim: Bootstrap drives make the companion feel more coherent and self-directed.  
Why it matters: Hoomy should not be only reactive.  
Current evidence: conceptual only.  
Next test: define drives v0 and compare against no-drive policy in a replay.

## A005: Activity selection is the right adaptive core

Status: open  
Claim: The key learning loop is choosing better next activities, not generating better text alone.  
Why it matters: This determines architecture.  
Current evidence: conceptual only.  
Next test: build a fixed activity vocabulary and compare policies.

## A006: Simple reward signals are insufficient but necessary

Status: open  
Claim: Early reward can start simple, but must expand beyond clicks, ratings, and immediate affect.  
Why it matters: Reward defines what the system becomes.  
Current evidence: conceptual only.  
Next test: define reward v0 with immediate and delayed layers.

## A007: Automation should follow cadence, not replace it

Status: open  
Claim: A 24/7 runner is useful only after daily review discipline exists.  
Why it matters: Background work can create noise.  
Current evidence: manual `/goal` loop exists.  
Next test: run the manual loop only after the first playground is selected.
