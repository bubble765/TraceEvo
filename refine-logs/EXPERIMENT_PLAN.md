# Experiment Plan — Audit-Conditioned Diagnostic Recovery

**Problem**: TraceEvo records and audits GoS execution provenance. The current requested study is conditional recovery on cases the original GoS evaluation scored as incorrect; it does not attempt to establish full-cohort or external generalization.

**Method thesis**: On original GoS Match=0 cases, compare a generic re-review against audit-conditioned correction. This E1 run tests post-run correction only; it does not claim efficacy for the live grounding guard. Keep original traces immutable and preserve repair lineage.

**Date**: 2026-09-29

## Claim Map

| Claim | Why It Matters | Minimum Convincing Evidence | Linked Blocks |
|---|---|---|---|
| C1 — Conditional recovery: among the original GoS evaluation cases scored Match=0, revision can recover some exact matches; on the audit-signal subset, audit information improves over an equal-call generic revision. | Directly answers how many known GoS failures can be corrected and whether audit context helps beyond another model call. | Use only the frozen Match=0 cohort; repair prompts contain no label/reference; compare baseline, generic revision and audit policy with the same frozen GoS Match/Relevant evaluator. | E1 |

**Anti-claims to rule out**: apparent gains caused only by extra LLM calls; gold/reference leakage through selection, prompts, filenames or memory; gains reported only on oracle-selected failures; audit roots assumed to explain every clinical error; corrections that damage initially correct diagnoses.

## Paper Storyline

- **Current experiment must establish**: conditional recovery on original GoS Match=0 cases and, separately, the incremental value of audit feedback over generic re-review on cases with audit signals.
- **Optional future study**: full-cohort blind-trigger net benefit on a new cohort. It is not required for the user's current conditional-recovery question.
- **Experiments intentionally cut**: claiming overall diagnostic accuracy or external generalization from a failure-selected cohort; claiming every wrong answer has a provenance anomaly; broad autonomous self-evolution claims.

## Experiment Blocks

### Block 0: Baseline Error–Audit Opportunity Map (MUST-RUN, offline)

- **Claim tested**: How much of the already evaluated GoS error set is potentially addressable by evidence/provenance feedback, and how much is audit-clean reasoning error?
- **Why this block exists**: Establishes whether the proposed intervention has coverage in this corpus before spending inference budget.
- **Dataset / split / task**: S5B cases 80–137, explicitly DEVELOPMENT / EXPLORATORY only; join `diagnostic_metrics.csv`, `outcome_results.csv`, and `natural_audit_summary.csv` by case id.
- **Compared systems**: Frozen original GoS predictions; audit findings from the corresponding Core-v1 traces.
- **Metrics**: Counts by frozen GoS Match score (0/1/2), OutcomeVerifier verdict (reported separately), audit-clean vs anomaly, anomaly family, P3-used, and intersections. Do not equate `Match=0` with `verified_incorrect`.
- **Setup details**: Offline CSV/artifact analysis only; no model calls; output must not contain reference diagnosis text.
- **Success criterion**: Exact, reproducible case matrix; all source rows join once; denominators reconcile to 58.
- **Failure interpretation**: Missing/mismatched identifiers or metric disagreement blocks selection of an intervention cohort.
- **Table / figure target**: Error-family × audit-signal coverage table.
- **Priority**: MUST-RUN.

- **M0 result**: 58/58 case ids joined. Frozen GoS Match: 36 score-0, 17 score-1, 5 score-2. The primary E1 cohort is the 36 Match=0 cases; score-1 broad-category cases remain outside the primary failure set. Among Match=0 cases, **11 have audit signals** (5 evidence-only, 4 post-hoc process-only, 2 mixed), while **25 are audit-clean**. OutcomeVerifier's separate counts are 31 verified-incorrect, 20 uncertain, 7 verified-success; do not substitute this label for the GoS Match selection rule.

### Block 1: Oracle-Selected Failed-Case Repair (E1; exploratory development)

- **Claim tested**: Given a case known by an independent evaluator to be incorrect, does audit-root-conditioned repair improve its final outcome beyond simply asking the model to try again?
- **Why this block exists**: Directly answers the conditional question “how many known GoS failures can be rescued?” while isolating the value of audit feedback from extra inference.
- **Dataset / split / task**: S5B cases 80–137, exposed development data. The primary target subset is exactly the **36 cases with frozen GoS `Match=0`**; `Match=1` broad-category cases are reported separately and not called errors. This is intentionally an oracle-selected conditional-recovery study, not an end-to-end deployment test.
- **Compared systems**: (A) immutable GoS baseline with its original frozen scores; (B) one generic revision call for every Match=0 case; (C) one audit-conditioned revision for cases with an audit signal (11 cases in this cohort), with the other 25 cases left unchanged and carrying forward their original frozen scores.
- **Metrics**: Exact rescue = generated repair output rescored Match=2 / 36 original Match=0 cases; broad relevant = score 1; still unmatched = score 0. Compare generic vs audit-conditioned repair on the 11 signal-bearing cases. Do not stochastically rerate unchanged baseline outputs for the primary metric. Report case-level transitions and additional calls/tokens/latency. OutcomeVerifier verdicts remain separate.
- **Setup details**: Use `deepseek-v4-flash` with `reasoning_effort=none`, `max_tokens<=2048`, and a fail-closed usage monitor. The repair model may receive patient input, original reasoning graph, and the permitted audit summary, but never the gold diagnosis, judge score, outcome label, or a “this answer is wrong” message. Preserve baseline artifacts and write repairs as separate child artifacts. Record prompts, model/version, usage and cost metadata.
- **Success criterion**: Audit-conditioned revision yields more exact/broad matches than generic revision on the same signal-bearing cases under the same frozen GoS evaluator, with reference-free prompts and no unsupported evidence fabrication. Report the raw paired counts and uncertainty; do not claim full-cohort accuracy.
- **Failure interpretation**: If generic revision and audit-conditioned revision tie, any gains may come from another model call rather than provenance. On the 25 audit-clean Match=0 cases, the audit policy has no signal to act on; any correction there belongs to generic re-review, not audit localization.
- **Observed result**: 36-case E1 completed. Generic revision yielded 3 exact and 7 broad-category matches; the audit policy yielded 0 exact and 1 broad-category match after carrying the frozen score on no-signal cases. On the 11 signal cases audit-vs-generic was 1/9/1 (audit higher/tie/generic higher). See `EXPERIMENT_RESULTS.md`; this does not support an incremental audit-feedback benefit in this run.
- **Scoring reconciliation**: A first exploratory pass rerated unchanged baseline outputs and found 6/36 label changes. The corrected result reuses original frozen baseline scores and scores only generated revisions; raw first-pass artifacts remain preserved.
- **R003 live-guard side study (exploratory, not an E1 efficacy result)**: Reapplying the frozen pre-commit rule offline to sealed original proposals found 8 exact trigger points across all 7 Match=0 evidence/P3 cases. A separate guard-off/on pilot on cases 90 and 102 ran with `reasoning_effort=none`; all four workflows completed within 2,048 tokens, but none reproduced a live guard action. It therefore validates isolation/budget only, not guard benefit. The remaining five cases were not run under a generation profile that failed to reproduce the original trigger.
- **R003 replay boundary**: The S5B invocation ledger stores hashes rather than a complete raw model-response recording. The original sealed events retain selected graph-update outputs but not every prompt/response needed for exact full-workflow replay. Do not treat the capped rerun as a counterfactual replay of the original GoS trace.
- **Table / figure target**: Case-level transitions (incorrect→success, incorrect→uncertain, unchanged), with audit family and repair action.
- **Priority**: MUST-RUN for exploratory mechanism validation; confirmatory claim waits for E2.

### Optional future block: Blind End-to-End Feedback Evaluation (E2; not in current scope)

- **Claim tested**: Does the deployable policy improve diagnostic outcomes when it has no gold-informed knowledge of which GoS cases are wrong?
- **Why this block exists**: Oracle-selected E1 estimates conditional recovery potential but cannot establish real-world trigger quality or net benefit.
- **Dataset / split / task**: A genuinely new, unopened DiagnosisArena block or an independently sourced benchmark. S5B cases 80–137 are not eligible as new held-out data because their references and outcomes have been inspected; older development cases must also be audited for prior exposure before reuse.
- **Compared systems**: (A) GoS; (B) GoS + live P3 source guard; (C) GoS + blind post-run audit-triggered repair; (D) combined policy only if B/C each pass their own gate. A same-extra-call/no-audit control is recommended on a manageable subset to isolate the audit signal.
- **Metrics**: Primary = adjudicated verified-success rate and paired net change on the full cohort; secondary = exact Match, Relevant, regression rate among baseline-correct cases, rescue rate among independently adjudicated baseline errors, calibration/uncertainty, unsupported-evidence rate, anomaly/repair precision, extra calls/tokens, latency, and cost. Report case-level paired bootstrap CIs and exact McNemar test where appropriate.
- **Setup details**: Pre-register cohort, outcome definitions, prompt/schema, model/backbone, temperature, retry budget, retrieval corpus, and stopping rules. Hide references until all baseline and intervention outputs are sealed. The runtime trigger must use only patient inputs, retrieved sources, graph/provenance artifacts and any blinded clinical critic—not gold or benchmark score. Keep arm-specific artifacts immutable.
- **Success criterion**: Positive paired full-cohort outcome difference with CI excluding zero, while the regression and safety checks stay within predeclared bounds. A higher rescue rate only on a gold-selected subset is insufficient for C2.
- **Failure interpretation**: No net gain means the feedback policy is not shown to improve diagnosis; improved provenance metrics may still support the audit contribution. Higher extra-call cost or harmful regressions can make the policy unattractive even if a subset improves.
- **Table / figure target**: Full-cohort GoS vs feedback results, per-case transitions, costs, and subgroup outcomes by audit family.
- **Priority**: OUT OF CURRENT SCOPE. Needed only for a later claim about overall or generalizable diagnostic improvement.

## Run Order and Milestones

| Milestone | Goal | Runs | Decision Gate | Cost | Risk |
|---|---|---|---|---|---|
| M0 | Establish available error/anomaly overlap | B0 offline join and validation | 58 unique rows, denominators reconcile, identify audit-clean errors | Negligible; local CPU | Confusing evaluator labels; mitigated by preserving each score separately |
| M1 | Implement safe repair artifacts and test contracts | Structured repair schema, prompt builder, branch writer, mock tests | No reference/outcome fields in repair payload; original seal unchanged; malformed repair rejected | Local CPU; no API | Baseline overwrite or hidden leakage; immutable child-run design + tests |
| M2 | Capped pilot and E1 contract validation | Generic revision vs audit-conditioned revision on cases 80 and 90 | Passed: 4 repair outputs parsed; max completion usage 248/2,048; no source/config changes | 4 repair calls + 2 grouped judge calls | Price metadata is unavailable/zero; do not infer that calls were free |
| M3 | Complete conditional E1 on original Match=0 cases | Generic revision for all 36; audit revision for 11 signal cases | Completed: 47 repair outputs; all parsed and within cap; 22 judge groups; reconciled result reports conditional outcomes only | 47 repair calls + 22 scoring calls; 149,822 generation tokens and 22,759 scoring tokens | One stochastic judge pass; baseline rerating drift is reported, not treated as repair |
| M3b | R003 live-guard sanity pilot | Original sealed trigger audit + guard OFF/ON reruns for cases 90 and 102 only | Offline: 8 exact rule triggers across 7 cases. Capped reruns: 0 actual triggers, so stop before expanding | 157 GoS model calls; 245,516 prompt + 17,856 completion tokens | `reasoning_effort=none` changed generated proposals; these runs do not estimate original-condition guard efficacy |
| M4 | Optional future E2 | New full-cohort blind-trigger comparison | Not required for the current user request | Requires new data and separate budget | All current case indices have already been exposed |

## Compute and Data Budget

- **GPU-hours**: None expected; inference uses the configured LLM transport. This depends on provider availability and is not a local GPU training experiment.
- **M0/M1 compute**: Local CPU only.
- **E1 API budget (observed)**: 47 repair calls + 22 scoring calls in the full run; each repair request was clamped to at most 2,048 tokens and each scorer request to 512. Maximum observed completion usage was 362 and 477 respectively. The proxy path was tested empirically; provider price metadata remained zero/null and is not evidence of free service.
- **R003 pilot usage (observed)**: Two original failure cases, two arms each; 157 LLM calls, 245,516 prompt tokens and 17,856 completion tokens total. Maximum single completion was 1,637/2,048. No guard action fired under this capped profile; do not scale the remaining five cases without changing the experimental rationale.
- **E2 API budget**: Out of current scope; if later requested, estimate after E1 and obtain a fresh cohort.
- **Data preparation needs**: A read-only joined development matrix; an explicit exposure manifest; a new sealed test block for confirmation.
- **Human evaluation needs**: At least two blinded clinical reviewers for ambiguous/broad-category diagnoses, with disagreement adjudication and a frozen rubric.
- **Biggest bottleneck**: Unopened data and clinically valid outcome adjudication, followed by LLM inference cost.

## Risks and Mitigations

- **Failure-selected cohort overgeneralized**: label all E1 metrics conditional on original Match=0; do not infer overall accuracy.
- **Gold leakage through oracle-selected case ids**: in E1, the evaluator may select the cohort but the repair agent receives no label, score, gold, or case-selection reason. Treat E1 as conditional/oracle-triggered, not deployable.
- **Audit-clean clinical errors**: explicitly measure them; no audit-root repair is possible without a separate blinded clinical verifier.
- **Extra calls masquerade as audit value**: compare audit-conditioned repair with an equal-call generic revision control.
- **Gateway token cap**: earlier high-effort responses exceeded the requested cap. The repair/live-guard opt-in path sets `reasoning_effort=none`, clamps request `max_tokens`, and fails closed on missing/over-cap per-call usage. The completed runs observed no per-call overshoots, but this is not a guarantee about all upstream responses; exclude the aggregate end-of-run usage line when checking per-call caps; do not treat zero-priced metadata as free service.
- **A repair worsens a correct answer**: excluded by the Match=0 selection rule, so E1 does not estimate regressions on baseline-correct cases; only a future E2 could.
- **GraphDelta mistaken for workflow resume**: graph replay reconstructs graph state, not all LLM/tool state. Until checkpoint/resume is implemented, perform a bounded repair call against a sealed graph snapshot and append a new child trace rather than claiming partial workflow replay.
- **Protocol drift**: hash prompts, code, evaluator, model config, dataset split and repair schema before confirmation; no post-hoc threshold changes.

## Final Checklist

- [x] Main claim limited to conditional recovery on original GoS Match=0 cases
- [x] Novelty isolated with an equal-call generic-revision control
- [x] Oracle-selected conditional evaluation separated from optional future E2
- [x] Used S5B set marked exploratory only
- [x] Audit-clean errors and regressions explicitly measured
- [x] Frontier component: not applicable; this is an audit-feedback mechanism, not a new foundation model
- [x] E1 generation is reference-blind, original traces/config remain unchanged, output caps validated
- [x] E1 completed on exactly the 36 original GoS Match=0 cases; no unused/new cases added
- [x] Frozen baseline score carried forward for no-signal audit policy; raw judge drift separated
