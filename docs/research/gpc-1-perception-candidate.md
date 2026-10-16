# GPC-1: Candidate for Perception and Activity Selection

Status: proposed for future exploration; not tested or selected for integration.
Recorded: 2026-09-24.
Source: [harshatheg/GPC-1](https://huggingface.co/harshatheg/GPC-1), suggested by the project owner.

## Reported Capabilities

The indexed model card describes a Qwen3.5-35B-A3B-based multimodal classifier with user-defined labels and probabilities, bounded numerical estimates, image-conditioned coordinates, and scoring over enumerated allowed JSON records. Numerical fields use 101 support points; their weighted mean can fall between points. The documented serving path requires NVIDIA hardware and memory for BF16 weights, with a matching backbone and adapter.

These are publisher claims, not Hoomy measurements. Direct page retrieval was unavailable during this review; the initial assessment used the search-indexed model card. Verify the source, model revision, deployment requirements, and evaluation evidence before running an experiment.

## Hoomy Hypothesis

Schema-constrained predictions may make observations easier to consume in the perception-to-state pipeline. Useful output structure does not establish factual accuracy or calibrated uncertainty. Observable behavior and inferred intent or affect must remain distinguishable.

## Proposed First Experiment

- Use a small, fixed set of consented or synthetic images with independently annotated observable states, including ambiguous and out-of-scope cases.
- Begin with one categorical task, such as whether a person is visibly present. Include an explicit unknown label and evaluate abstention separately.
- Compare GPC-1 with a prompted multimodal baseline using the same images, labels, and annotation rubric. Pin model revisions and record prompts and hardware.
- Measure classification quality, calibration where probabilities are available, errors on ambiguous cases, abstention coverage, latency, and memory use. Measure structural validity separately from semantic correctness.
- If the first task is promising, evaluate object localization with annotated boxes, coordinate error, and intersection-over-union. Check box consistency explicitly.

Before execution, agree on task-specific quality and resource thresholds. Advance only if measured gains justify deployment costs; otherwise retain this as a research reference. No model download, deployment, or experiment has been performed.

## Activity Selection Hypothesis

The project owner also suggested evaluating GPC-1 as a candidate policy component. Given a state summary, user goals, active drives, recent activity history, and a finite set of eligible activities, categorical scoring could select the next activity. This is a proposed application of the reported interface, not an established model capability on Hoomy tasks.

Apply eligibility and trust-contract constraints before constructing the candidate set. Include staying silent or deferring when appropriate. If activity parameters must be selected together, enumerated allowed records could represent valid activity-and-parameter combinations.

Class probabilities describe the model's preference among supplied alternatives; they are not measured expected rewards or probabilities that the activity will benefit the user. Learning from outcomes would require a separate feedback and policy-update process.

### Proposed Replay Experiment

- Freeze a small activity vocabulary and replay cases containing only information available at the original decision time.
- Compare GPC-1, hardcoded rules, random eligible selection, and a prompted LLM using identical state and eligible candidates.
- Have a human assess acceptable choices, including silence, without seeing which selector produced them. Track agreement, inappropriate initiative, repeated suggestions, latency, and sensitivity to candidate ordering and wording.
- Log the state, eligible set, selected activity, model scores, and human corrections. Do not infer the outcomes of unchosen activities from replay judgments.
- Set acceptance thresholds before execution. Consider a prospective trial only if replay results justify it; measure acceptance and delayed usefulness there separately from offline preference agreement.

Perception and activity selection should be evaluated independently so that observation errors do not obscure selector quality.

## Connections to the research design

The earlier design selected a typed decision baseline and model-independent
`model_evaluations`. GPC-1 remains a future comparator, not a replacement decision.
Public evidence classification and activity selection require separate tests.

- [Jev](https://typesafe.ai/) is a functional comparison for typed decisions and probability outputs. Provider calibration claims still need task-specific evaluation.
- [Jimothy](https://github.com/AndrewPrifer/jimothy) explores small local classifiers from Jev-compatible examples. Stable corrected tasks may justify a later comparison; using another classifier as teacher requires an evaluated adapter rather than an assumed drop-in integration.
- Intervention timing, attention cost and withdrawing assistance are separate policy questions. A classifier can supply observations or estimates without implementing a control-learning loop.
- Ranking predefined learning experiments is a narrower hypothesis than learning a goal generator. Historical private conversations and source assessments are not exported as this note's dataset.

## Additional Testable Hypotheses

| Hypothesis | Bounded task | Evidence that would support it |
| --- | --- | --- |
| Better evidence classification | Classify a claim as reported, inferred, supported by a cited observation, or unresolved, while preserving author and scope | Fewer unsupported upgrades to verified status on human-reviewed cases |
| Better clarification selection | Rank predefined questions by whether their answers could change the next decision | More resolved decision ambiguities per user question |
| Better intervention timing | Choose offer now, defer, ask, or silence from current context | Similar useful progress with fewer interruptions in a prospective comparison |
| Better attention-cost estimates | Estimate framing and review effort for an activity | Smaller prediction error against recorded human effort |
| Better feedback interpretation | Separate timing rejection, content rejection, correction, and unknown | Fewer false persistent preferences after temporary deferrals; absent feedback remains unknown |
| Better targeted retrieval | Choose which unresolved claim or source to inspect next | More resolved uncertainties per unit of reading cost than repeated broad analysis |
| Better learning-goal selection | Rank a finite set of candidate experiments by expected learning value and effort | Greater measured uncertainty reduction at a fixed experiment budget |

The NoFlinch design study already provides useful cases: reported test success versus user-confirmed resolution, build-specific availability, and temporary deferral versus loss of interest. Start with evidence classification and clarification selection; compare Jev, GPC-1, and a prompted baseline on frozen inputs before considering policy changes. Model disagreement can identify review cases, but is not ground truth.
