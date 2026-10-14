# TraceEvo S5B — final review request

Per `TraceEvo_S5A_Review_S5B_Final_Heldout_v1.md` §21/§23. **S5B stops here.**
Algorithm development ends with this stage.

**`FINAL_EVIDENCE_COMPLETE`** — §20's ten conditions all pass. The short
version: the final held-out block is clean, the Gate's positive branch is
validated on unseen data for the first time, and three things happened that
need a reviewer's judgement rather than mine.

---

## 1. The three things to look at directly

**(a) The block was started twice.** The freeze was written at `04:34:18Z` and
the run started at `04:34:52Z`; five driver scripts were then edited, from an
integration check against already-published S5A artifacts and from reading the
run log. Cases 80-82 had by then been sealed by a superseded code version. The
pass was stopped, the three traces were preserved under
`heldout_original_superseded/` and `seals_superseded/` with the reason in
`discarded_pass.json`, and the block re-ran from case 80 under one code
version. **Nothing was deleted, and the freeze manifest was not rewritten** —
re-freezing after case 80 had been read would have made its hashes match the
final code while destroying what the manifest exists to establish.

**(b) Three re-runs fall outside §5.2's list.** `run_audit.json`:

```
retries: 3 (case-104, case-107, case-121)
cause:   MODEL_OUTPUT_FAILURE —
         ValueError: Not founded, current list：['Laboratory_Physician', ...]
verdict: 3 of 3 re-run(s) fall outside §5.2's list
```

The planner named an expert that does not exist and `central_agent.act()`
raised. §5.2 permits a re-run for network interruption, API 5xx, unexpected
process exit or filesystem failure; a model's own output ending a case is not
on that list. The retries are **inside** the block — nothing was excluded or
re-sealed — and the question of whether such a case should instead be recorded
as incomplete is left open, deliberately, because deciding it after seeing
that the retries succeeded is what §5.2 exists to prevent.

**(c) The runner's `rerun_reason` field is wrong and was not edited.**
`s5b_run.py` wrote `"infrastructure: process exited 0 without integrity.json"`
on the *successful* retry, because the field is derived from `attempt > 1`
rather than from whether the attempt failed. The runner was mid-pass, so
`traceevo/s5b_run_audit.py` was written instead: a read-only module that
reconstructs every attempt from the console log, classifies each failure
against §5.2's list, and states which runner records it supersedes.

## 2. Result summary

| | |
|---|---|
| Block | case 80-137, 58 cases, one pass |
| Sealed / verified / rejected | 58 / 58 / 0 |
| Events · model calls | 1,568 · 2,160 |
| Outcomes | 7 success · 31 incorrect · 20 uncertain |
| Gate | 5 eligible · 16 failure_experience · 37 manual_review · 0 reject |
| Match / Relevant | 0.086 [0.017, 0.172] / 0.379 [0.259, 0.500] |
| Clean trace / evidence alert | 0.276 [0.178, 0.402] / 0.207 [0.123, 0.328] |
| Evidence tiers | P0 317 · P1 4 · P2 438 · P3 16 (P3 used 14) |
| Controlled anomaly | 402 injections, macro-F1 **0.960** [0.943, 0.975] |
| Observable-root Top-1 | **0.978** (393/402) |
| Gold leakage | 0, non-vacuous |
| Local overhead | 10.45 ms/case · gate 12.2 µs · storage 0.85 MiB/case |

**The Gate's two counterexamples, on held-out data:**

| rule | incorrect-clean admitted | correct-but-poisoned admitted |
|---|---|---|
| Naive | 22/22 | 19/19 |
| OutcomeOnly | 0/22 | **19/19** |
| AuditOnly | **22/22** | 2/19 |
| IntegrityOnly | 22/22 | 0/19 |
| **TraceEvo** | **0/22** | **0/19** |

Each single-signal rule is at 1.000 on exactly one of the two populations.
Only the joint rule is at 0.000 on both. This is C3's argument, and S5A could
not run it on an unopened block — that block produced no verified success.
This one produced 7 (5 gold-safe), so §10.4's first branch applies and the
positive class is real.

The §11 admission metrics agree. Every rule retains all 5 genuine successes,
so retention does not separate them; **admission precision does**, and it is
the share of what a rule admitted that was actually a clean verified success:

```
Naive          5/77  = 0.065
OutcomeOnly    5/26  = 0.192
AuditOnly      5/44  = 0.114
IntegrityOnly  5/58  = 0.086
TraceEvo       5/5   = 1.000
```

## 3. §20's ten conditions

| # | condition | result |
|---|---|---|
| 1 | 58/58 per the frozen protocol, or failures recorded | ✅ 58 sealed; 3 retries recorded with causes |
| 2 | no algorithm or protocol modification | ✅ 27 rule files, 0 drift |
| 3 | no systematic seal / ledger / replay break | ✅ all complete, all chains, all replays |
| 4 | gold leakage = 0 | ✅ 0 over 58 candidates × 3,306 cross-case pairs |
| 5 | controlled anomaly results computable | ✅ macro-F1 with case-cluster CI |
| 6 | Gate denominators truthful | ✅ every rate with numerator/denominator and Wilson CI |
| 7 | scarcity did not change a rule | ✅ judge hash matches freeze |
| 8 | ablation recomputable | ✅ originals unchanged, five arms scored |
| 9 | tables script-generatable | ✅ 6/6 |
| 10 | claim matrix matches evidence | ✅ filled from artifacts |

## 4. Claims

**Supported by the held-out block** — dual-graph traceability; controlled
anomaly detection; observable-root localization; incorrect-clean blocked by
the joint gate; correct-but-poisoned blocked.

**Not claimed, unchanged by this block** — memory improves diagnosis; role
routing improves safety; ATGG improves natural tasks; online self-evolution
improves diagnostic performance.

**One limitation this stage adds.** Detection performance is consistency with
this codebase's own fault model, not detection in the wild. All 402 injections
were made by `traceevo/audit/injector.py`.

## 5. Three judgement calls I made, flagged for disagreement

1. **Discarding the first pass rather than keeping its three traces.** Keeping
   them would have produced an identical-looking block with a mixed code
   provenance — a defect with no symptom, since all three sealed and verified.
2. **Fixing the Gate's file-reading bug after seeing a wrong result.** The
   first Gate run reported all-zero admissions including for `B0_Naive`, which
   admits every candidate by construction. The edit chose *which file is
   authoritative*, and the file that carries the verdicts is the one the
   frozen writer wrote them to. `driver_drift_report.json` marks this edit
   `predates_run: false`; the timing is disclosed rather than minimised.
3. **Reporting tokens instead of `TOKEN_UNAVAILABLE`.** S5A declared the field
   unavailable; the gateway's log carries input and output counts per call, so
   the field was a false statement about the instrument. Nothing is estimated.

## 6. What this does not establish

That detection generalizes beyond this fault model. That TraceEvo improves
diagnosis — no behaviour experiment was run, and Match 0.086 is a property of
the GoS pipeline on this block. That any of the four demoted modules works.
That a case dying on an invalid planner selection should be retried.

## 7. How to check this without trusting the summary

1. `python3 -m pytest S5B_final_heldout/tests/test_s5b.py -q` — 26 tests.
2. `s5b_freeze.py --verify` — 0 drift across the 27 frozen rule files.
3. `run_audit.json` — every attempt, its exception, §5.2's verdict on it.
4. `driver_drift_report.json` — every post-freeze edit, by name and timestamp.
5. `discarded_pass.json` — the first pass, preserved.
6. `failure_cases.md` — fifteen failures.
7. `pass_conditions.json` — §20's ten conditions with the artifact behind each.
8. `reproduce_final.sh` — the whole stage in order.

## 8. Artifacts

§21's list is complete, plus `pipeline_summary.json`,
`diagnostic_summary.json`, `clean_alert_summary.json`, `anomaly_headline.json`,
`ablation_summary.json`, `overhead_summary.json`, `gate_final.json`,
`run_audit.json`, `driver_drift_report.json`, `discarded_pass.json`,
`claim_matrix.json`, `tables_manifest.json`, `figures_manifest.json` and
`attempt_history.jsonl`.

**S5B stops here.** §23: no algorithm development, no extra cases, no memory
injection, no ATGG, no role routing, no judge change, no threshold or Gate
change. The next stage is result audit, statistics, writing and figures.
