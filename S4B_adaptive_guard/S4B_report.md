# TraceEvo S4B report

Per `TraceEvo_S4A_Review_S4B_Amendment_v1.md`. S4B froze ATGG-v1, verified the
guard against regression and positive controls, and ran the No-Guard baseline
on case 60-69. §10's first branch fired.

**Verdict: `LOW_TRIGGER_DENSITY`.**

Zero of ten baseline cases carry a decision-path P3. Not one P3 node appears in
the block at all — used or unused. §10 says to stop without running the Guard
arm, and the Guard arm was not run. **ATGG was built, controlled and left
untested by data, which is a different result from ATGG failing.**

---

## 1. Why S4B is not about expert roles (§17 Q1-Q2)

*(Q1)* S4A measured three expert roles over case 10-39 and case 50-59. Their
P3-decision-use point estimates differ by a factor of 2.1 and every 95%
interval overlaps every other; no role's interval lower bound cleared the
global baseline, so no guard candidate was raised; and neither testable
predictive target showed information gain over a single global rate. §1 closes
the role-weighting branch. S4B reads no role profile.

*(Q2)* The substitution follows from what the two questions can be answered
with. *Which expert is historically riskier* needs a per-role risk estimate
that generalises across cases, and S4A showed the estimate is not there. *What
state should raise verification strength* needs a per-update signal, and the
audit layer already produces one for free: every candidate evidence node gets a
provenance tier before it is committed. The second question can be answered at
the resolution the data actually has.

## 2. The policy and where it came from (§6, §17 Q3)

`guard_policy_manifest.json`, frozen before case 60 was read, derived from the
case 10-59 audit summaries with per-file hashes. The policy did not change
afterwards (`policy_meta_changed: false`). The separate `freeze_manifest.json`
hashes 29 rule and config files and **had to be re-frozen**: the first version
was written at 11:23:26Z while the guard implementation was still being edited,
so it pinned a half-built pipeline and reported drift on four files afterwards.
`failure_cases.md` FAILURE 7. The re-freeze is dated after the runs and says so;
what shows the code was fixed *during* them is the timestamps — the last
implementation edit precedes the first case-60 event by seven minutes, and no
implementation file changed again until after the last seal. Re-verified:
**29 files checked, 0 drift.**

The historical evidence behind the policy:

| block | cases | P3 used in reasoning | P1 used in reasoning |
|---|---|---|---|
| 10-29 | 20 | 5 | 2 |
| 30-39 | 10 | 1 | 0 |
| 50-59 | 10 | 4 | 2 |
| **total** | **40** | **10** | **4** |

*(Q3)* One guard is active and two are deliberately not.

**Active — `G_P3_SOURCE`.** Trigger: candidate node is Evidence, tiers P3, and
the proposal gives it a SUPPORT or REFUTE edge. Action: one source-verification
retry (`max_retry = 1`), then RESOLVED / BLOCKED / RETRY_FAILED per §8.2. The
basis is the 10 P3-used nodes above — the one risk with empirical support in
this corpus.

**Inactive — `G_P1_SOFT`.** S3D's case-51 was a *verified success* whose
decision path carried 2 P1 soft-grounded nodes. A rule that blocked P1 would
have refused a correct trace. §3 rules P1 out of the hard guard and S4B records
it without intervening.

**Inactive — `G_ROLE_RISK`.** S4A's `NO_ROLE_SPECIFIC_ADAPTATION`. §1 closes
the branch and §19 forbids reopening it.

Writing the two inactive guards down is the part of the manifest that does
work. A manifest listing only the active guard would look identical whether or
not anyone had considered role weighting or P1, and the reason *not* to act is
what a later reviewer needs to disagree with.

## 3. The guard does what it claims (§9, §17 Q4-Q5)

### Guard OFF is exactly the pre-S4B pipeline

*(Q4)* Two runs of case 5 with the guard disabled, replayed in strict mode
against `clean_recording.jsonl` — a recording written **2026-09-17, before S4B
existed** — compared against each other and against that run's own artifacts:

```
IDENTICAL  guard_off_A vs guard_off_B (determinism)
IDENTICAL  guard_off_A vs pre-S4B run (2026-09-17)
strict_replay_clean     : true
no_guard_log_written    : true
```

Strict replay raises on any prompt that differs from the recording and on any
extra LLM call, so "identical" is a statement about the bytes GoS sent, not a
similarity judgement. Final graph hash, prediction, report and the full 37-event
semantic sequence all match the September 17th run.

`timestamp`, `event_hash` and `prev_hash` are excluded, and the exclusion is
recorded in `regression_results.csv`: the latter two are hashes *over* the
timestamp, so they differ whenever the clock does. Excluding the time but not
the hashes computed from it produced a false `REGRESSION` on the first run —
`failure_cases.md` FAILURE 2.

### The controls show the rule fires, and only on what it names

*(Q5)* `positive_control_results.csv`, six controls, all passing. Each varies
exactly one condition and holds the rest at their minimum, which is what makes
a non-trigger attributable:

| id | candidate | tier | edge | trigger | isolates |
|---|---|---|---|---|---|
| A | P3 unsupported, no source | P3 | support | **yes** | §9.2's requirement |
| B | same, refute edge | P3 | refute | **yes** | §8 names both relations |
| C | same, `refines` edge | P3 | refines | no | the **edge** condition |
| D | verbatim retrieved record | **P0** | support | no | the **tier** condition |
| E | expert-synthesis text | P2 | support | no | §9.2's P2 requirement |
| F | Hypothesis node | — | support | no | the **node-type** condition |

C, D and F are the ones that matter: without them, a silent guard and a guard
that fires on everything outside the rule would look the same.

The first version of these controls did not isolate anything. It supplied a
retrieved troponin record as background and then asserted that "Troponin is
markedly elevated" was P3 — with that record present the frozen classifier
returns **P1**, so the probe measured its own setup. `failure_cases.md`
FAILURE 1.

## 4. Phase 1: the baseline arm (§10, §17 Q6-Q7)

Case 60-69, Memory OFF, capability routing OFF, ATGG OFF, blinded dataset, ten
cases sealed and ten seals verified.

| case | P3 used | P3 unused | P2 on decision path | anomalies | guard triggers |
|---|---|---|---|---|---|
| 60 | 0 | 0 | 2 | — | 0 |
| 61 | 0 | 0 | 2 | — | 0 |
| 62 | 0 | 0 | 3 | — | 0 |
| 63 | 0 | 0 | 1 | — | 0 |
| 64 | 0 | 0 | 3 | — | 0 |
| 65 | 0 | 0 | 5 | — | 0 |
| 66 | 0 | 0 | 3 | — | 0 |
| 67 | 0 | 0 | 4 | — | 0 |
| 68 | 0 | 0 | 7 | — | 0 |
| 69 | 0 | 0 | 3 | — | 0 |

*(Q6)* **Zero decision-path P3 in ten cases — and zero P3 nodes of any kind.**
The count was taken without gold: `s4b_risk.py` reads events, bindings and
deltas plus the blinded dataset, and carries no code path that could open a
diagnosis.

The decision-path evidence that *is* there is `P2_DERIVED` — hypotheses,
absence claims, expert synthesis and agent inference. That is the reasoning the
pipeline is built to do, correctly labelled, and it is reported separately for
exactly that reason. The first version of the metric counted it as "ungrounded
Evidence" and reported 1-3 unsupported nodes per case; `failure_cases.md`
FAILURE 6.

*(Q7)* **No.** §10's branch is `0 cases → LOW_TRIGGER_DENSITY`, and 0 is what
the block produced. The Guard arm was not run and `guard_60_69/` holds a
`NOT_PRODUCED` record rather than an empty directory.

## 5. Why the block is quiet, and how far that can be pushed

| block | cases | P3-used cases | rate |
|---|---|---|---|
| 10-29 | 20 | 5 | 0.250 |
| 30-39 | 10 | 1 | 0.100 |
| 50-59 | 10 | 4 | 0.400 |
| **60-69** | **10** | **0** | **0.000** |

Under the pooled historical rate of 10/40 = 0.25 per case, ten clean cases
happen about **5.6%** of the time (6.7% under the Beta(11, 31) posterior
predictive). That is low enough to say this block is quieter than the corpus it
came from, and not low enough to say anything changed.

Both readings are available and this stage takes neither. What can be said is
narrower and is what §10 asks: **ten cases did not exercise the guard.**

**What this does not mean.** It is not evidence that ATGG does not work. The
rule fires on the §9.2 controls; there was no P3 in this block for it to fire
on. A guard that is never asked is not a guard that failed.

## 6. What was and was not produced (§12, §17 Q8-Q15)

*(Q8)* **Zero guard triggers**, because the arm that would produce them did not
run. `guard_actions.jsonl` and `guard_case_summary.csv` carry `NOT_PRODUCED`
records with the reason, not empty files — an empty file and a file nobody
wrote are indistinguishable to a later reader, and the reason is the
deliverable.

*(Q9-Q10)* **Not applicable.** The P3-used comparison and the
RESOLVED/BLOCKED breakdown both need two arms.

*(Q11)* **No new failures.** All ten baseline traces completed, all ten seals
verify, and the guard's own system-correctness property was secured in advance:
a committed delta is normalised to carry `nodes`, `edges` and `edit`, and a
retry whose schema is wrong is treated as `RETRY_FAILED` rather than passed
downstream to raise (`test_normalisation_guarantees_the_keys_the_caller_indexes`).

*(Q12)* Overhead is reported for the one arm that ran.
`token_overhead = TOKEN_UNAVAILABLE` — §12.2 forbids estimating it, and the API
does not report per-call usage. Wall-clock is in `run.log` and is not a basis
for any claim. The guard's marginal cost (one extra LLM call per trigger) is
**unmeasured**, because there were no triggers.

*(Q13-Q14)* **Not applicable.** `diagnostic_transitions.csv` needs a second
arm, and the highest-priority regression §12.3 names — baseline
`verified_success` → guard `verified_incorrect` — cannot occur without a guard
arm.

*(Q15)* **Partly, and the honest answer is narrower than §18's question
assumes.** ATGG does not demonstrate that state-driven verification beats
historical role risk, because it was never triggered. What the stage does
establish is that the machinery for the comparison exists and is sound:
the rule is the frozen classifier applied pre-commit rather than a new one,
it fires exactly and only where §8 says, disabling it is provably inert, and
the arm it would be compared against is sealed and reproducible.

The case *against* role-based routing does not rest on S4B — S4A settled it.
What S4B adds is that the state-driven alternative is now built to the same
standard, and the reason it is untested is a property of the block, not of the
mechanism.

## 7. The gold on case 60-69 was not read

§11 conditions outcome verification on *both* arms being sealed. There is one
arm, so the OutcomeVerifier was not run and `outcome_results.csv` and
`judge_raw/` carry `NOT_PRODUCED` records.

This is a deliberate choice and it has a cost worth stating: the baseline's
diagnostic quality for case 60-69 is now unknown, and could have been measured.
It was not measured for two reasons. No part of §10's decision uses it — the
branch turns on P3 density, which was computed without gold. And reading it
would spend the block's gold-unopened status, which is the only remaining value
case 60-69 has if the review decides to re-scope it.

If the reviewer would rather have the baseline's diagnostic numbers than the
option to re-scope, that is a decision this report is happy to have made
differently — `s3d_verify.py` and `s4b_run.py --check-seals` are both in place
and the ten seals verify.

## 8. Recommendation — `LOW_TRIGGER_DENSITY`

§15's option, chosen by §10's own rule rather than by judgement: 0 of 10, and
the rule for 0 is *stop*.

The three things the reviewer has to decide, none of which S4B can settle:

1. **Whether case 60-69 is the right block for this experiment.** It is not
   obviously bad — it is the next unopened block and it was frozen for this —
   but on the historical rate a block of ten was always marginal: at 0.25
   P3-cases per case, ten cases yield a median of 2-3, and a block with 0 or 1
   is not rare. §10 anticipated this with its branch structure, which is why
   the stage stopped cheaply instead of spending a Guard arm on nothing.
2. **Whether to widen the block or change the target.** §19 forbids editing
   ATGG-v1 and re-testing on the same cases. Any of the three would need a
   fresh decision: more cases (case 70-79 is reserved and unopened), a broader
   trigger (P1 is the obvious candidate — it is the tier that *does* appear,
   and S3D's case-51 is the reason it was excluded), or a different
   operationalisation of "unsupported".
3. **Whether P3-only was the right first guard at all.** The block's evidence
   is that P2 dominates the decision path and P3 does not occur. That is one
   block, so it is weak evidence — but it is the second time this corpus has
   shown the P3 tier to be rare while P1/P2 carry the decision path, and a
   guard aimed at the tier that appears would be aimed at something the data
   can actually test.

This report's own reading is (3), with (1) as the practical constraint: the
tier that is absent is the one the guard watches, and the tier that is present
is the one §3 rules out. That is a design question about what ATGG should
guard, and it belongs to the review rather than to this stage.

**S4B stops here.** No case 70-137 read or run; P0-P3 rules unmodified; the P1
Gate not relaxed; no role routing; no memory injected or activated; ATGG-v1 not
modified; no S4C work started.
