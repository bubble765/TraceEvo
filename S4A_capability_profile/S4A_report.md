# TraceEvo S4A report

Per `TraceEvo_S3D_Review_S4A_Amendment_v1.md`. S4A builds an audit-driven
Trustworthy Execution Capability Profile from case 10-39 and validates it
offline on case 50-59. No GoS process was started, no case outside 10-39 and
50-59 was read, no Planner was modified, no memory was injected.

**Verdict: `NO_ROLE_SPECIFIC_ADAPTATION`.**

The profile is buildable, deterministic and reconstructible; the three expert
roles' risk intervals are mutually indistinguishable and none is separable from
the global baseline in the unsafe direction; no guard candidate is raised; and
§13's item 5 — information gain over a global baseline — fails on every target.
§13 item 6's second half is satisfied, which §13 names as a valid research
result rather than a failure to measure.

`PROFILE_NOT_PREDICTIVE` is **not** claimed, and §7 explains why: the core
target's Δ-Brier interval covers zero, so "no gain exists" and "no power to
detect gain" are the same data here.

---

## 1. Data availability (§5, §17 Q1-Q2)

§5 permits only fields the artifacts actually carry, and requires anything else
to be marked unavailable rather than assigned by heuristic. The audit was run
before any profile code was written, and it changed the design twice.

### Computable

| field | source | coverage |
|---|---|---|
| `n_invocations` | ledger `actor_id` | 660 expert calls, 508 central |
| `accepted_calls` / `discarded_calls` | ledger `parse_status` | 1151 accepted, 17 discarded |
| `uncommitted_calls` | ledger `committed_to_state` | 347 false across the three expert roles (355 including the central agent, which has no profile row) |
| `evidence_attributed_p0..p3` | audit `best_analysis_event` | 377 of 420 nodes |
| `p3_used_in_reasoning` | audit `used_in_reasoning` | 6 nodes |
| `retrieval_events` | `TOOL_CALL` + `EVIDENCE_READ` | per role |
| `structural_use_rate` | `used_in_reasoning` | 243 of 377 attributed |

### Not computable

| field | why |
|---|---|
| `degraded_calls` | **no event in case 10-39 carries `status=degraded`.** All 855 are `status=success`, `reason_code=NONE`. |
| `failed_calls` | same: the status vocabulary in the artifacts has no failing value. |
| latency | not recorded in any artifact. |
| token counts | not recorded per invocation. |

This is the single most consequential finding for §9, and it is reported rather
than worked around: **§6.1's `execution_abnormal = degraded + failed` has no
observable source**, so the metric is `UNAVAILABLE` and §9.2's target is
untestable. Reporting it as `0.0` would have produced a profile that looks like
it measured zero execution risk and a validation target that looks perfectly
predicted.

### Attribution is narrower than it looks

Two links exist between a graph node and an actor, and only one discriminates:

* `binding.created_by_event` resolves to `Primary_Physician` for **all 420**
  nodes across the dev block. The central agent writes the graph, so this link
  identifies who recorded the evidence, not which expert produced it. Building
  the profile on it would have given every role zero evidence and made the
  entire stage a study of the orchestrator.
* `best_analysis_event` resolves 377 nodes to the three expert roles and leaves
  43 as `NO_LINK` (10.2%). Those 43 are reported unattributed; §5 forbids
  spreading them across roles by heuristic.

The link is a grounding/coverage relation — the analysis whose text best covers
the node — not authorship. Every field is therefore named
`evidence_attributed_*` and never `evidence_generated_*`.

## 2. The profile (§5.1, §6) — §17 Q3-Q6

```
role                  cases  calls  accepted  disc  P0  P1  P2  P3  used  P3-used
Laboratory_Physician     25    270       262     8  69   1 126   2   123        2
Radiologist              21    234       228     6  40   1  77   3    76        3
Pathologist              16    156       153     3  26   0  31   1    44        1
```

*(Q3)* The three roles have 660 calls over 25, 21 and 16 of the 30 dev cases.
All three clear §10's sufficiency threshold of 30 attributed nodes used in
reasoning, so none is `INSUFFICIENT_DATA`.

*(Q4)* **Execution abnormality is unmeasurable** — `degraded` and `failed` do not
occur, so the profile carries no role-specific execution risk at all. The one
execution signal the artifacts do carry is parse discard, distributed as
8/270, 6/234 and 3/156 — rates of 3.0%, 2.6% and 1.9%, with overlapping
intervals:

| role | discard | 95% CI |
|---|---|---|
| Laboratory_Physician | 8/270 | [0.0153, 0.0573] |
| Radiologist | 6/234 | [0.0121, 0.0547] |
| Pathologist | 3/156 | [0.0070, 0.0548] |

*(Q5)* Grounding composition differs in level but not in kind. P0 share runs
26/58 (Pathologist, 44.8%), 40/121 (Radiologist, 33.1%) and 69/198
(Laboratory_Physician, 34.8%). P2 dominates everywhere — 126 of 198 for the
Laboratory Physician. P1 is essentially absent: **2 nodes in the whole dev
block**, against 6 P3.

*(Q6)* **P3 decision-use risk is highest for the Radiologist**, and the ranking
is not separable from noise:

| role | P3 used | of used nodes | posterior mean | 95% CI |
|---|---|---|---|---|
| Radiologist | 3 | 76 | 0.0513 | [0.0143, 0.1097] |
| Pathologist | 1 | 44 | 0.0435 | [0.0054, 0.1177] |
| Laboratory_Physician | 2 | 123 | 0.0240 | [0.0050, 0.0570] |

The point estimates differ by a factor of 2.1 and every interval overlaps every
other. This is the table the verdict turns on, and §4 says why the overlap is
the finding rather than a nuisance.

## 3. Profile evolution (§7) — §17 Q7-Q8

`profile_snapshots/` holds 30 snapshots, `profile_after_case_10.json` through
`profile_after_case_39.json`, each using **exactly** cases 10..t. The check is
mechanical: `leakage_check.json` re-reads every snapshot and confirms
`max(uses_cases) <= t`, 30 of 30 with no violations.

*(Q7)* The curves are in `profile_evolution.png`. Two features are legible and
one is a caution:

* **Sample size dominates the early snapshots.** The P3-use rate swings between
  0 and 0.06 over the first fifteen cases and settles to 0.016–0.039 only after
  case 25. Every early excursion is one node changing hands in a denominator of
  9 or 15.
* **Structural utilization is stable and role-ordered throughout**: Pathologist
  ~0.75, Laboratory Physician ~0.62, Radiologist ~0.63. This is the one
  quantity in the profile that looks like a property of the role rather than of
  the sample — and it is also the one with no safety interpretation, since
  §6.3 forbids reading it as contribution.
* **Grounding composition drifts with case mix, not with time.** The Pathologist
  P0 share falls from 0.750 (case 11, n=4) to 0.448 (case 39, n=58); the
  Radiologist's falls from 0.556 (case 10, n=9) to 0.331 (case 39, n=121). The
  first point in each case rests on a handful of nodes, so the apparent trend is
  as likely to be the denominators growing as the roles changing — which is the
  first thing to check before reading any trend in these panels as learning.
  One caveat is visible only in the data: the Pathologist is absent from case 10
  entirely, so its curve starts at case 11 and the snapshot after case 10 has an
  empty rate for it, not a rate of zero.

*(Q8)* **Yes, and this is the most useful thing §8 produced.** With 2 of 123
nodes P3-used, the Laboratory Physician's interval is [0.0050, 0.0570] — a
factor of eleven wide. A point estimate of 0.024 with that interval is not a
measurement of a low risk; it is a statement that the data cannot distinguish
1.6% from 5.7%. The same applies to the other two roles, and it is why §4's
verdict is not "the risks are low".

## 4. Predictive validation (§9) — §17 Q9-Q11

`profile_freeze_10_39.json` was written before case 50-59's process results were
read; `s4a_validate.py` refuses to run without it, so §9.1's ordering is a file
check rather than a promise.

| target | status | n | pos | Brier role | Brier global | Δ Brier | 95% CI on Δ |
|---|---|---|---|---|---|---|---|
| execution abnormality (§9.2) | `UNTESTABLE_NO_POSITIVES` | 288 | 0 | — | — | — | — |
| parse discard *(secondary, not §9.2)* | ok | 288 | 4 | 0.013989 | 0.013873 | **+0.000117** | [+5.5e-06, +2.0e-04] |
| unsupported evidence risk (§9.3) | ok | 86 | 3 | 0.034118 | 0.033707 | **+0.000411** | [−3.97e-04, +1.02e-03] |

*(Q9)* **No.** On the core target the role profile is 0.0004 *worse* than a
single global number, and the interval covers zero. On the secondary target it
is worse by 0.0001 with an interval that excludes zero — statistically
distinguishable and practically nothing.

*(Q10)* The P3 target is attributable and was evaluated: 86 nodes used in
reasoning in the eval block, 3 of them P3-used. §5's `NO_LINK` nodes are
excluded rather than assigned, so the denominator counts only attributable
evidence. The target is *available*; its sample is small.

*(Q11)* **Which dimensions have predictive information: none demonstrated.**
§9.2's execution dimension has none of the required data. §9.3's grounding
dimension has a point estimate in the wrong direction. What the profile does
carry reliably is descriptive — role participation, call counts, grounding
composition, structural utilization — and none of those was tested as a
predictor because §9 names two targets and neither is one of them.

**§9.2's instruction was followed verbatim.** The primary target has zero
positives, so it is reported `UNTESTABLE_NO_POSITIVES` and no Brier is printed.
A Brier of 0.0 there would have been a perfect score computed from nothing.
The discard target is labelled `NOT §9.2` in its name, in the CSV, and in its
note, because it is a different quantity and is not a substitute for the one
§9.2 asks for.

## 5. Actionability (§10) — §17 Q12-Q13

*(Q12)* **No guard candidate is raised.**

| role | classification | P3-use CI lower bound | global posterior mean |
|---|---|---|---|
| Laboratory_Physician | `NORMAL` | 0.0050 | 0.0286 |
| Radiologist | `NORMAL` | 0.0143 | 0.0286 |
| Pathologist | `NORMAL` | 0.0054 | 0.0286 |

§10.2's rule requires the role's interval lower bound to exceed the global
posterior mean. No role's does. The execution guard cannot fire even in
principle, because its input does not exist. §10.2 closes with *S4A 不为了制造
candidate 放宽 interval*; none was widened.

*(Q13)* **"Risks are similar", not "data is insufficient" — and the difference
is load-bearing.** All three roles clear the sufficiency threshold of 30
attributed used nodes, and all three are classified `NORMAL` rather than
`INSUFFICIENT_DATA`. If they had been under-measured the correct verdict would
be `PROFILE_DATA_INSUFFICIENT`; §13 supplies that option separately and §14
distinguishes it from this one. The roles were measured, the measurements
overlap, and the conclusion is that differentiated scheduling is not warranted.

The two positive controls that keep this from being an unfalsifiable zero:
`test_the_guard_rule_can_fire` constructs a role whose lower bound clears the
global mean and asserts the rule raises a candidate; and every role's bound is
also asserted to stay below it, so if the real data ever crosses the line the
test fails and the report's headline has to be rewritten.

## 6. Outcome association is not a capability score (§6.4) — §17 Q14

The profile reports `verified_success_cases_participated` and its two siblings
as **descriptions only**, and no policy reads them.

A role appears in a success case because of the case's clinical content, the
Planner's expert selection, and the other agents' contributions — not only
because of that role's work. The dev block makes the confound concrete: the
Laboratory Physician appears in 25 of 30 cases, the Pathologist in 16. The
Pathologist is absent from 14 cases, and nothing about those cases is a fact
about pathology. Weighting experts by success-case participation would mostly
re-weight them by which cases happened to request their specialty.

The same reasoning bars the more tempting version: **structural utilization**
(§6.3) is reported under exactly that name and never as causal contribution. A
node appearing in the final reasoning can mean the expert's analysis was used,
or that it was restated by the central agent, or that the graph update recorded
it without it bearing on the answer. The measure counts the first two and the
third together and cannot separate them; the name says so.

## 7. Why `PROFILE_NOT_PREDICTIVE` is not claimed — §17 Q11-Q12, and the verdict

Both testable targets came out positive (worse than the baseline), which is the
direction that would support `PROFILE_NOT_PREDICTIVE` — *role profile 在 50–59
上不比 global baseline 更有信息*. This stage declines to claim it, for a reason
that is visible only in the interval:

```
core target (§9.3) Δ Brier = +0.000411,  95% CI [-0.000397, +0.001018]
positives = 3
```

The interval covers zero. With three positives in 86 units, this test cannot
distinguish *no information gain exists* from *no power to detect a gain that
does exist*. Claiming "not predictive" would assert the first from data
consistent with the second, and it would be the mirror of the error §9.2 warns
against — reading a null result as a measured absence rather than as an
unmeasured one.

What the data does support is the weaker and still useful statement: **no
information gain was demonstrated**, and §13's item 5 — which asks for a
demonstrated gain — fails.

### §13's criteria, as evaluated

| # | condition | met | evidence |
|---|---|---|---|
| 1 | profile deterministically reconstructible | ✅ | `test_the_profile_rebuilds_identically`; no model or clock in the path |
| 2 | no future-case leakage | ✅ | 30/30 snapshots use only ≤ t |
| 3 | ≥ 2 roles with enough observations | ✅ | 3 roles, all ≥ 30 attributed used nodes |
| 4 | ≥ 1 core risk target testable on 50-59 | ✅ | §9.3, n=86 |
| 5 | information gain (Δ Brier < 0) on a core target | ❌ | no target reached it |
| 6 | a guard candidate **or** proof roles are alike | ✅ | no candidate; roles indistinguishable |

`go_conditions_met = False` over all six evaluated criteria. §13's GO list is a
conjunction and item 5 fails.

### §14's four-way choice

**`NO_ROLE_SPECIFIC_ADAPTATION`** — *profile 可构建，但 role 差异不足，不应基于
它区别调度*. This is the finding that survives all three analyses: the roles'
risk intervals overlap, no guard candidate clears its threshold, and no
role-specific predictor beat the global baseline. §13 explicitly names this
outcome as a valid research result rather than a failure to measure.

`GO_S4B_ADAPTIVE_GUARD` is not available: item 5 fails and no candidate exists.
`PROFILE_NOT_PREDICTIVE` is not claimed, per above. `PROFILE_DATA_INSUFFICIENT`
does not apply: criteria 3 and 4 — §13's own data-adequacy conditions — are met.

## 8. Memory and retrieval hold (§12) — §17 Q15

```
Success Memory Store : retained (4 headline entries)
Failure Memory Store : retained (initial_plan_ids = [])
Memory Injection     : OFF
cross_case_retrieval_review.csv : 58 rows, all UNREVIEWED, not consumed
```

*(Q15)* Success memory is not injected in S4A for the three reasons §12 gives,
and the third is the one S4A is now able to state with evidence: **the
retriever's only calibrated number was shown in S3D to be a self-identity
threshold**, not a cross-case relevance threshold. All 58 cross-case scores in
`cross_case_retrieval_topk.csv` fall below 28.6789, so as an online threshold it
would abstain on every query. There is no threshold to inject with, four
reusable successes is too few to calibrate one, and the review queue that would
produce the labels has no reviewer yet.

S4A advances one track and not the other:

```
advanced     : audit feedback -> capability profile
not advanced : cross-case success memory -> planner
```

They are decoupled for this stage. The profile is built from audit fields and is
unaffected by how few successes the store holds; the store is untouched by
anything S4A measured.

## 9. Deliverables and how to check them

§16's list is complete, plus `profile_validation.json`,
`profile_actionability.json`, `memory_hold.json` and
`new_success_poisoned_defence_layers.json` (carried from S3D).

1. `python3 -m pytest S4A_capability_profile/tests/test_s4a.py` — 20 tests.
   Two are positive controls (`test_the_guard_rule_can_fire`,
   `test_a_predictive_profile_beats_the_global_baseline`) that keep the zeros
   from being indistinguishable from broken rules.
2. `failure_cases.md` — five failures. Four are about what a statistic was
   computed over; one is a test that would have pushed an implementation away
   from its requirement.
3. `profile_evolution.csv` — 90 rows, from which every curve can be redrawn.
4. `role_profile_intervals.csv` — every ratio with its count, posterior mean
   and 95% interval, as §8 requires.
5. `profile_prediction_results.csv` — all 662 scored units (576 expert
   invocations + 86 evidence nodes), from which both Brier scores can be
   recomputed.
6. `leakage_check.json` — the per-snapshot check behind §13 item 2.

## 10. Stopping

S4A stops here. No case 60-137 was run or read; the Planner was not modified;
Success Memory was not injected; Failure Memory was not activated and its store
still answers `[]` to any initial-plan caller; no expert selection was changed;
no online adaptive guard was implemented; the P1 Gate and the frozen audit rules
are unmodified; the backbone is unchanged. §15's case 60-69 experiment remains
for a future S4B and requires external approval.
