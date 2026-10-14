# TraceEvo S4A — review request

Per `TraceEvo_S3D_Review_S4A_Amendment_v1.md` §19. **S4A stops here.**

No case 60-137 run or read; no Planner modification; no Success Memory
injection; no Failure Memory activation; no expert-selection change; no online
adaptive guard; P1 Gate and frozen audit rules unmodified; backbone unchanged.
No GoS process was started at all — every input is an existing artifact.

The short version: **the profile is buildable and the roles are not
distinguishable. §13's item 5 fails, item 6's second half holds, and the
recommendation is `NO_ROLE_SPECIFIC_ADAPTATION`.**

---

## 1. What S4A found

### The profile builds, and one of its four dimensions has no data at all

```
role                  cases  calls  accepted  disc  P0  P1  P2  P3  used  P3-used
Laboratory_Physician     25    270       262     8  69   1 126   2   123        2
Radiologist              21    234       228     6  40   1  77   3    76        3
Pathologist              16    156       153     3  26   0  31   1    44        1
```

**`degraded_calls` and `failed_calls` do not exist in the artifacts.** All 855
events in case 10-39 are `status=success`, `reason_code=NONE`. So §6.1's
`execution_abnormal = degraded + failed` is `UNAVAILABLE`, and §9.2's primary
validation target has zero positives.

This was checked before any profile code was written, and it is the reason §9.2
is reported as `UNTESTABLE_NO_POSITIVES` with no Brier printed. A Brier of 0.0
over a target with no positives would have read as flawless prediction.

### Attribution was nearly built on the wrong link

`binding.created_by_event` resolves to `Primary_Physician` for **all 420**
nodes — the central agent writes the graph. A profile built on that link would
have attributed zero evidence to every expert role and turned S4A into a study
of the orchestrator.

The link that discriminates is `best_analysis_event`: 377 of 420 nodes resolve
to a specific expert, 43 stay `NO_LINK` and are reported unattributed rather
than spread by heuristic (§5). It is a grounding/coverage relation, not
authorship, which is why every field is named `evidence_attributed_*`.

### The three roles are indistinguishable

| role | P3 used | of used nodes | posterior mean | 95% CI |
|---|---|---|---|---|
| Radiologist | 3 | 76 | 0.0513 | [0.0143, 0.1097] |
| Pathologist | 1 | 44 | 0.0435 | [0.0054, 0.1177] |
| Laboratory_Physician | 2 | 123 | 0.0240 | [0.0050, 0.0570] |

Point estimates differ by a factor of 2.1; every interval overlaps every other.
§4 below is why the overlap is the finding.

### It does not predict better than one global number

| target | status | n | pos | Δ Brier | 95% CI on Δ |
|---|---|---|---|---|---|
| execution abnormality (§9.2) | `UNTESTABLE_NO_POSITIVES` | 288 | 0 | — | — |
| parse discard *(secondary, not §9.2)* | ok | 288 | 4 | **+0.000117** | [+5.5e-06, +2.0e-04] |
| unsupported evidence risk (§9.3) | ok | 86 | 3 | **+0.000411** | [−3.97e-04, +1.02e-03] |

Positive Δ means the role profile scores *worse* than the global baseline. No
guard candidate is raised either:

| role | classification | P3-use CI lower bound | global mean |
|---|---|---|---|
| Laboratory_Physician | `NORMAL` | 0.0050 | 0.0286 |
| Radiologist | `NORMAL` | 0.0143 | 0.0286 |
| Pathologist | `NORMAL` | 0.0054 | 0.0286 |

§10.2's rule needs the lower bound to exceed the global mean. None does, and no
interval was widened to manufacture one.

---

## 2. §13's criteria

| # | condition | met | evidence |
|---|---|---|---|
| 1 | deterministically reconstructible | ✅ | rebuild test; no model or clock in the path |
| 2 | no future-case leakage | ✅ | 30/30 snapshots use only ≤ t |
| 3 | ≥ 2 roles with enough observations | ✅ | 3, all ≥ 30 attributed used nodes |
| 4 | ≥ 1 core target testable on 50-59 | ✅ | §9.3, n=86 |
| 5 | information gain (Δ Brier < 0) | ❌ | no target reached it |
| 6 | guard candidate **or** roles alike | ✅ | no candidate; roles alike |

`go_conditions_met = False`, over all six evaluated.

---

## 3. Recommendation — `NO_ROLE_SPECIFIC_ADAPTATION`

*profile 可构建，但 role 差异不足，不应基于它区别调度.*

§13 names this as a valid research result rather than a measurement failure, and
§14 supplies `PROFILE_DATA_INSUFFICIENT` separately for the case where the roles
were under-observed. They were not: all three clear the sufficiency threshold
and are classified `NORMAL`, not `INSUFFICIENT_DATA`.

**`PROFILE_NOT_PREDICTIVE` is deliberately not claimed.** The core target's
interval covers zero:

```
Δ Brier = +0.000411,  95% CI [-0.000397, +0.001018],  positives = 3
```

With three positives in 86 units the test cannot separate *no gain exists* from
*no power to detect gain*. Claiming "not predictive" would assert the first from
data consistent with the second — the same error as reading §9.2's null as a
measured absence. What the data supports is that **no information gain was
demonstrated**, which is what §13 item 5 asks for and does not get.

**The practical conclusion for S4B is the same either way**: no differentiated
scheduling is warranted, and §11's constraint applies regardless —
*能力画像只能调节验证强度与协作策略，不能单凭历史风险剥夺当前病例所需的专业能力*.

---

## 4. What this does and does not establish

**Established.** The profile is a deterministic function of the artifacts and
rebuilds identically. No snapshot sees a future case. Three expert roles have
adequate observations on both the call and the evidence dimensions. Their safety
intervals overlap. No guard candidate clears §10.2's threshold. Memory stayed
off and both stores stayed untouched.

**Not established — and this is the honest limit.**

1. **That the roles are equally safe.** Overlapping intervals mean "cannot tell
   apart", not "the same". The Laboratory Physician's P3-use interval spans
   [0.0050, 0.0570] — a factor of eleven. That is not a low risk measured; it is
   a range the data cannot narrow.
2. **That the profile is not predictive.** Per §3.
3. **That audit-driven capability profiling fails in general.** S4A tested one
   field set, one target family and one corpus. The finding is about this
   corpus and these metrics.
4. **Anything about diagnosis.** S4A ran no behaviour experiment, injected
   nothing, and changed no call.

**Carried forward from S3D and unchanged.** The reusable-success corpus is 4.
The 58-row cross-case review queue is still `UNREVIEWED` and was not consumed.
28.6789 remains a self-identity threshold.

---

## 5. What S4A would hand to S4B, if approved

Not a guard. Two things worth more than one:

1. **The execution dimension is a data problem before it is a modelling one.**
   Nothing in the pipeline records a degraded or failed call, so an execution
   guard cannot be trained, evaluated or falsified on current artifacts. Any
   S4B that wants one needs the status vocabulary extended *and* a corpus that
   exercises it, which is a larger change than a threshold.
2. **The grounding dimension is measurable and currently uninformative.**
   P3-use is rare (6 nodes in 30 dev cases) and rare events need large corpora
   to separate roles. Rather than a guard, the natural next measurement is
   whether P3-use clusters at all — by case type, by expert, or by whether the
   evidence auditor's `soft_grounded_review` label predicts it. That is a
   question about the audit layer, not about agents.

A third observation, offered as a caution rather than a task: S3D found the
§6.1 P1 rule blocking a case the six-condition test passed, and S4A found the
P1 tier nearly empty (2 nodes in 30 cases) while P3 is what the profile can
count. If the paper's safety story rests on the P1 rule, that rule is being
exercised by roughly one case in fifteen — which is worth knowing before S4B
plans an experiment around it.

---

## 6. How to check this without trusting the summary

1. `python3 -m pytest S4A_capability_profile/tests/test_s4a.py` — 20 tests.
2. **The two positive controls**, because S4A's headline is a zero:
   `test_the_guard_rule_can_fire` (the §10.2 rule produces a candidate when the
   data warrants one) and `test_a_predictive_profile_beats_the_global_baseline`
   (the Brier comparison can come out negative).
3. `failure_cases.md` — five failures. Four are about what a statistic was
   computed over; one is a test that would have pushed the implementation away
   from its requirement.
4. `data_availability.md` — what was checked, what was found missing, and why
   the attribution link is what it is.
5. `profile_evolution.csv` — 90 rows; every curve in the PNG is drawn from it.
6. `profile_prediction_results.csv` — all 662 scored units.
7. `leakage_check.json` — the per-snapshot no-future check.

## 7. Artifacts

§16's list is complete, plus `profile_validation.json`,
`profile_actionability.json` and `memory_hold.json`.
