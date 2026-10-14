# TraceEvo S5A — review request

Per `TraceEvo_S4B_Review_S5A_Method_Freeze_v1.md` §20. **S5A stops here.**

No case 80-137 read or referenced; no role adaptation reopened; ATGG not
enabled; no memory injected or activated; the outcome judge not modified; the
P0-P3 rules, audit rules and Gate unmodified; S5B not started.

The short version: **Core-v1 is frozen and the protocol runs cleanly end to end
on an unopened block. All ten of §15's conditions pass → `GO_S5B_FINAL_HELDOUT`.
The Gate's positive branch could not be validated here because the block
produced no verified success.**

---

## 1. §15's ten conditions

| # | condition | result |
|---|---|---|
| 1 | case 70-79 10/10 complete | ✅ 10 sealed, 0 crashed |
| 2 | 10/10 seals correct | ✅ 10 verified, 0 rejected |
| 3 | gold leakage = 0 | ✅ 0, over 3 candidates × 27 cross-case pairs |
| 4 | no systematic audit/ledger/replay break | ✅ all complete, all chains, all replays |
| 5 | Core ON/OFF non-invasive | ✅ identical and repeatable |
| 6 | anomaly protocol repeats | ✅ sample re-run, every field matches |
| 7 | Gate procedure executable | ✅ absent-class branch taken correctly |
| 8 | no rule changed for the validation | ✅ 27 frozen files, 0 drift |
| 9 | every final table script-generatable | ✅ 9/9 have generators |
| 10 | case 80-137 unread | ✅ no module references it |

---

## 2. What the block showed

```
                 P0    P1    P2    P3
nodes            58     0    93     2
used              -     0    41     2

outcomes:  9 verified_incorrect · 1 uncertain · 0 verified_success
gate:      7 manual_review · 3 failure_experience · 0 eligible
```

**P3 reappears** — twice, both in case-76, both genuinely unsourced (token
coverage 0.167 and 0.0 against a frozen floor of 0.35). ATGG was off and this
is an observation, not a trigger. It is worth noting the block *would* have
exercised the guard, unlike case 60-69: that is a fact about the blocks.

**Clean alert rate is 0.30 trace / 0.10 evidence**, driven by A4 ×2 and A1 ×1.
The A4 is notable — §11 demoted that class to a boundary case study, and it is
the one that occurs naturally here (a Radiologist analysis asserting "7 mg/dL
indicates renal dysfunction" with no source, reaching the report).

**The controlled anomaly protocol**, on copies, originals hash-verified
unchanged:

| class | n | precision | recall | F1 |
|---|---|---|---|---|
| A1 | 10 | 0.909 | 1.000 | 0.952 |
| A2 | 18 | 1.000 | 0.889 | 0.941 |
| A3 | 10 | 1.000 | 1.000 | 1.000 |
| A5 | 40 | 1.000 | 1.000 | 1.000 |
| **primary** | **78** | **0.987** | **0.974** | **0.981** |

Observable-root top-1 accuracy **0.980**. Eight secondary findings, reported
as their own count and not counted as false positives (§12). Ten A4 rows as a
case study (§11). **A0 excluded from the headline** — see §4 below.

**Non-invasiveness holds**: Core ON vs Core OFF identical on prediction, report
and GoS graph, repeatable, strict replay clean. Local overhead is 15.8 ms audit
and **12.8 µs** gate per case; network time is reported separately and tokens
are `TOKEN_UNAVAILABLE`.

---

## 3. `GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION`

§13's second branch. The block produced no verified success, so there is no
positive class for correct-but-poisoned twins. §13 says what to do — do not
touch the judge, do not hunt for cases, report the named absence — and all
three were done. S3C's 16 twins over case 10-29 remain the development evidence.

This is the third block with 0 or 1 verified success in ten cases (30-39: 0,
50-59: 1, 70-79: 0) against 5 in 20 for the migration block.

---

## 4. Two decisions this stage made on its own, flagged for disagreement

**(a) A0 was refused, not simulated.** §11 keeps A0 only if reliable
degraded/failed instrumentation exists, and forbids faking a status to fill the
category. S4A established that no event in this pipeline carries either status.
So the `status_marked` variant — which writes `status="degraded"` — was refused
outright for all ten cases, and the `silent` variant, which does not fabricate a
status, is reported in a `conditional` group **outside** the headline. The
headline fell from n=98/F1=0.985 to n=78/F1=0.981 when the 20 A0 rows came out.
`failure_cases.md` FAILURE 2.

**(b) The gold on case 70-79 was read, unlike case 60-69.** §9 step 8 requires
it after seal verification, and §9 step 9's Gate needs an outcome verdict. It
was read only after all ten seals verified. Case 60-69's gold remains unopened
per §D1.

---

## 5. What this does and does not establish

**Established.** Core-v1 is frozen with its *real* thresholds (the first freeze
wrote the digest of an empty dict — FAILURE 1). The pipeline runs end to end on
an unopened block with clean seals and clean replays. The anomaly protocol is
repeatable and localizes to 0.980. The Gate executes and takes its absent-class
branch without adjustment. Non-invasiveness holds by the strongest test
available. All nine final tables are script-generatable.

**Not established.** That the Gate's positive branch works on this block — it
produced no positive class. That detection generalizes beyond the injection
procedure that generated the examples: precision and recall are measured
against injections this codebase makes, which is consistency *with its own
fault model*, not detection in the wild. That the method improves diagnosis —
S5A ran no behaviour experiment.

**Unchanged.** The reusable corpus is 4. The 58-row cross-case review queue is
still `UNREVIEWED`.

---

## 6. Two things to weigh before spending case 80-137

1. **The success rate is the binding constraint, not the protocol.** Three
   consecutive blocks produced 0, 1 and 0 verified successes in ten cases each.
   S5B runs 58 cases; on the recent rate that is a handful of verified
   successes. The Gate's positive branch and any poisoned-twin analysis for the
   final paper scale with that number, not with 58.
2. **`GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION` is the finding S5B should be
   designed around.** S5A validated the protocol and could not validate the
   Gate's positive branch on an unopened block. If case 80-137 behaves like the
   last three blocks, the paper's Gate evidence will again be development data
   — a defensible paper, but a different one from the plan.

Neither is a reason to change Core-v1.

---

## 7. How to check this without trusting the summary

1. `python3 -m pytest S5A_protocol_validation/tests/test_s5a.py` — 17 tests,
   including that the freeze holds real thresholds, that the runner has no way
   to read a reference, and that no module names a case above 79.
2. `Core-v1_freeze_manifest.json` — the frozen method, its three OFF switches
   with reasons, and the file hashes. `s5a_freeze.py --verify` re-checks.
3. `failure_cases.md` — five failures. Three are comparisons whose rules did
   not match their purpose; one is a category counted without its precondition;
   one is a precision that erased its own value.
4. `anomaly_detection_results.csv` + `root_localization_results.csv` — every
   injection, every detection, latent and observable roots in separate columns.
5. `anomaly_not_injectable.json` — the 12 withheld injections and the reason
   each was withheld, including the ten A0 refusals.
6. `pass_conditions.json` — §15's ten conditions with the artifact behind each.
7. `final_table_generator_check.md` — which script produces which table.

## 8. Artifacts

§17's list is complete, plus `pipeline_summary.json`, `gate_drill.json`,
`anomaly_headline.json`, `overhead_summary.json`, `nonintrusive_summary.json`,
`repeatability_check.json` and `pass_conditions.json`.
