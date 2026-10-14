# Initial Experiment Results — Audit-Conditioned Diagnostic Recovery

**Date**: 2026-09-29  
**Plan**: `refine-logs/EXPERIMENT_PLAN.md`  
**Status**: E1 complete on the original GoS `Match=0` cohort; R003 live-guard branch has an offline trigger audit and a two-case capped pilot, but no efficacy estimate. Exploratory development only.

## Scope and selection

The intervention cohort is exactly the **36 cases scored `Match=0` by the frozen original GoS evaluator**. The 17 `Match=1` broad-category cases and 5 `Match=2` cases are not included. No unused/new cases were added. The separate OutcomeVerifier labels (31 verified-incorrect, 20 uncertain, 7 verified-success) are not used for E1 selection.

Of the 36 cases, 11 have an audit signal (5 evidence-only, 4 post-hoc process-only, 2 mixed) and 25 are audit-clean. All 36 received one generic revision; only the 11 signal-bearing cases received an audit-conditioned revision. The audit policy leaves the other 25 cases unchanged.

## Gateway budget validation

The local proxy now applies `reasoning_effort=none` and clamps opted-in DeepSeek repair requests to at most `max_tokens=2048`; ordinary GoS requests remain unchanged. The pilot and judge runner also stop before the next call if completion usage is missing or exceeds its cap. The shared GoS YAML and original GoS traces were not changed.

After a four-call cap smoke test, the complete E1 generation used 47 repair calls: 137,977 prompt tokens and 11,845 completion tokens total; maximum single completion was **362/2,048**. All 47 responses passed the JSON schema check. The 22 grouped scoring calls used 20,047 prompt and 2,712 completion tokens total; maximum single completion was **477/512**. This empirically validates the configured path on this run; it is not a guarantee about every future upstream response. Provider price metadata was zero/null, so these values must not be interpreted as proof that the calls were free.

## E1 outcomes

| Arm | Cases | Match 0 | Match 1 (broad relevant) | Match 2 (exact) | Exact rescue from original Match=0 |
|---|---:|---:|---:|---:|---:|
| Original GoS (frozen scores) | 36 | 36 | 0 | 0 | 0 |
| Generic revision | 36 | 26 | 7 | 3 | 3/36 (8.3%) |
| Audit policy | 36 | 35 | 1 | 0 | 0/36 (0%) |

The audit-policy row carries forward the original frozen score for the 25 no-signal cases; it does not rerate unchanged baseline outputs. Among the 11 signal-bearing cases, the audit-conditioned and generic revisions tied on 9, audit-conditioned was higher on 1, and generic was higher on 1. Neither arm produced an exact match on this 11-case subset; each produced one broad-category match.

Interpretation: the one-pass judge found 3 exact and 7 broad-category matches after generic revision across the 36 original GoS failures. The audit-conditioned policy produced 0 exact and 1 broad-category match overall. This run therefore does **not** show incremental benefit from audit feedback over generic re-review. These are exploratory GoS-rubric outcomes, not a claim of clinical correctness or overall-cohort accuracy.

## Judge variability and reconciliation

The first scoring pass also rerated baseline and duplicate no-signal policy candidates. Although all 36 original frozen baseline scores were `Match=0`, that independent rerating returned 30 score-0, 5 score-1, and 1 score-2 labels. This confirms nonzero stochastic judge drift. Those reratings are retained as raw artifacts but excluded from the primary interpretation: baseline uses the original frozen score, and the unchanged no-signal policy carries that score forward.

Use [`repair_scores_reconciled.json`](results/R002_match0_full_recovery/repair_scores_reconciled.json) and [`repair_scores_reconciled.csv`](results/R002_match0_full_recovery/repair_scores_reconciled.csv) as the corrected result artifacts. The initial [`repair_scores.json`](results/R002_match0_full_recovery/repair_scores.json) and `repair_scores.csv` are preserved as raw exploratory scoring output and are superseded for interpretation. Per-call generation outputs and prompts are under `results/R002_match0_full_recovery/`; generation prompts do not contain references, original judge labels, or the text “the answer is wrong.”

## R003: Live P3 guard trigger audit and capped pilot

The exact frozen pre-commit rule was applied offline to original sealed graph-update proposals for the 7 Match=0 cases with evidence/P3 signals (90, 102, 110, 118, 124, 128, 133). It found **8 trigger events across all 7 cases** (case 124 has two). This is trigger-coverage evidence only: no response was generated and no diagnostic outcome was changed. The summary is in [`original_trace_trigger_audit.json`](results/R003_live_p3_guard_pilot/original_trace_trigger_audit.json).

An isolated guard-off/guard-on GoS pilot was then run for cases 90 and 102, using the same blinded inputs, `reasoning_effort=none`, 2,048-token request cap, one transport attempt, and a separate output tree. All four runs completed; there were **0 actual guard actions**. Per-run completion maxima were 701/1,637 tokens for case 90 and 738/647 for case 102. Total usage was 157 calls, 245,516 prompt tokens and 17,856 completion tokens. Provider price metadata was zero/null and is not evidence the calls were free. The manifests are [`case 90`](results/R003_live_p3_guard_pilot/guard_run_manifest.json) and [`case 102`](results/R003_live_p3_guard_case102_pilot/guard_run_manifest.json).

Interpretation: the capped `reasoning_effort=none` reruns did not reproduce the original P3 proposals, so they do not measure whether the guard would correct those original failures. We did not run the remaining five cases under this changed generation profile: two complete pairs already used substantial tokens and yielded no trigger, while the full S5B trace package does not contain a complete raw LLM recording for an exact guard-on replay. The original sealed artifacts remain unchanged. Do not claim real-time guard diagnostic efficacy from R003.

## Limitations and remaining boundary

- This is an oracle-selected conditional recovery study on exposed S5B development cases; it cannot establish blind trigger quality, full-cohort improvement, or external generalization.
- Only one stochastic judge pass was used for repair outputs; the baseline consistency check shows why estimates should be treated cautiously.
- E1 tests post-run correction. R003's offline rule audit shows trigger opportunities, but its two-case live rerun pilot had no actual triggers under the capped model profile, so real-time P3 guard diagnostic efficacy remains unmeasured.
- The optional blind end-to-end E2 and new/unused cases are outside the current request and were not run.
- Clinical reviewer adjudication was not conducted.
