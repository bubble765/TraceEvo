# TraceEvo S3C — review request

Per `TraceEvo_S3B_Review_S3C_Amendment_v1.md` §13/§15. **S3C stops here.**

No Capability Profile, no agent capability EMA, no memory-driven agent
selection, no S4 multi-round evolution, no backbone change, no change to the
OutcomeVerifier's success definition or to the frozen audit rules. Case 50–137
was not read or run. No GoS process was started anywhere in S3C.

The short version: **the success path works and the counterexample S3B could not
build now exists. There are 4 gold-safe successes where §11 requires 5, and the
legacy supplement is excluded from the count by construction. §12 applies —
`NEED_MORE_VERIFIED_EXPERIENCE`.**

---

## 1. What S3C found

### The success path ran for the first time

In S3B no candidate reached `eligible` on real data — the branch existed and had
never executed. S3C pushed every gold-safe success through the real Gate:
**6 candidates reached `eligible`** (4 headline, 2 legacy), and each carries its
full provenance and a payload free of gold diagnosis, right option and
historical prediction.

### §7.2's first counterexample, finally constructed

S3B demonstrated the second counterexample (wrong result, clean process →
AuditOnly admits it) and could not build the first, because it needs a trace
whose outcome was verified *correct* and case 30–39 had none.

S3C built 16 poisoned twins of real verified successes — all four poison types
against all four headline successes — and:

| rule | correct-but-poisoned admitted |
|---|---|
| B1 OutcomeOnly | **16 / 16** |
| B2 AuditOnly | 2 / 16 |
| **TraceEvo** | **0 / 16** |

### The four-column comparison is now complete

| rule | Poison AR | Correct-but-Poisoned AR | Retention | Incorrect-Clean AR |
|---|---|---|---|---|
| **TraceEvo** | **0.0000** | **0.0000** | **1.0000** | **0.0000** |
| B0 Naive | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B1 OutcomeOnly | 1.0000 | 1.0000 | 1.0000 | 0.0385 |
| B2 AuditOnly | 0.1250 | 0.1250 | 1.0000 | 0.7692 |
| B3 IntegrityOnly | 0.0000 | 0.0000 | 1.0000 | 1.0000 |

Every single-signal rule is dominated. **B3 matches TraceEvo on poison and
admits every clean wrong answer (26/26); B1 avoids clean wrong answers and
admits every poisoned one.** This is the argument S3B made and could not
demonstrate, and it now holds on a corpus containing real verified successes.

Note S3B's own finding does not shrink with the larger corpus: AuditOnly's
incorrect-clean rate was 9/10 there and is 20/26 here.

### §8's channel split is structural

The success store is constructed with `initial_plan_partitions=("success",)` and
the failure store with `()` — so the failure store answers "nothing" to any
caller asking for initial-plan material, rather than relying on the caller not
to ask. **success initial-plan reachable: 6. failure: 0.**

The first version of this was a policy file with nothing enforcing it, which
`failure_cases.md` FAILURE 5 records.

### The success retriever abstains

Threshold from a self-versus-other split over case 10–39, using no outcome
anywhere:

| pair | n | min | max |
|---|---|---|---|
| self | 6 | 44.2588 | 126.1347 |
| other | 28 | 0.4317 | 13.0990 |

Gap of 3.4×, threshold 28.6789 at the midpoint. Dry-run on case 40–49:
**0 hits, 10 abstains.** The failure mode S3B exposed — one memory injected into
ten of ten cases — does not recur, and this time the mechanism was shown to be
capable of abstaining before its zero was believed.

---

## 2. The one criterion that fails

**§11 criterion 1: ≥ 5 gold-safe verified successes in case 10–39. S3C has 4.**

| Tier | Cases | verified_success | gold-safe |
|---|---|---|---|
| A (10–29, S3A holdout) | 20 | 5 | **4** |
| B (30–39, S3B build) | 10 | 0 | 0 |
| **headline** | 30 | 5 | **4** |
| C (0–9, legacy) | 10 | 2 | 2 |

**case-22 is the one that fails the six-condition test**, and it is the case
worth looking at: it *is* a verified success (3/3 score 2), and it carries an
unresolved P3 on the decision path plus an A1 anomaly, which makes it a
Validated Failure rather than a gold-safe success. Counting it would have made
the headline 5 and passed the criterion.

§4 lists six conditions rather than one precisely so that shortcut is not
available, and it was not taken.

**The legacy supplement (§4.2) was used and changed no headline number.** §2
excludes tier C from the headline corpus and §11 counts case 10–39 only, so
4 + 2 is still not 5. It was run because §4.2 authorises it in the 3–4 branch
and it gives the success channel more than four entries to exercise.

**§12's backbone clause does not apply.** It is reserved for ≤ 2; 4 is above it.
The shortfall is corpus size. The split between tiers is the informative part:
case 30–39 produced 0 of 10 while case 10–29 produced 5 of 20, on the same
model and the same frozen rules.

---

## 3. The seal gap — a finding about S3A, not about S3C

§3.1 requires `S3A sealed artifact hash == S3C OutcomeVerifier input artifact
hash`. **That proof cannot be produced: S3A never sealed the case 10–29
holdout.** There is no manifest, and every `_integrity.json` carries
`expected_tip_hash: null`.

Rather than compute a hash now and call it a seal, S3C produced and labelled a
substitute. The part that does real work is the cross-check against S3A's
recorded corpus aggregates — **6 of 6 reproduce exactly**:

| metric | S3A recorded | S3C recomputed |
|---|---|---|
| n_cases | 20 | 20 |
| n_evidence_nodes | 150 | 150 |
| externally_grounded_rate | 0.58 | 0.58 |
| lineage_complete_rate | 0.84 | 0.84 |
| uncommitted_call_rate | 0.2968 | 0.2968 |
| discarded_call_rate | 0.0107 | 0.0107 |

**§11 criterion 10 is therefore met in substance and not in the form §3.1
asked for**, and the report says so rather than rounding it up.

One thing to carry forward: the first version of this cross-check reported four
*mismatches*, because it counted all audited nodes where S3A counts Evidence
nodes only. A cross-check that is itself wrong manufactures a finding about the
wrong subject — here, "the corpus is not the one S3A measured", which would have
invalidated the stage. It is `failure_cases.md` FAILURE 2.

---

## 4. What this does and does not establish

**Established.** The Gate's `eligible` branch executes correctly on real
verified successes. A poisoned twin of a verified success cannot reach
`eligible` (0/16) while OutcomeOnly admits every one (16/16) — §7.2's first
counterexample. The four-column comparison dominates every single-signal rule.
Success and failure are on separate, structurally enforced channels. The success
retriever abstains. No gold leakage.

**Not established.** That ≥ 5 successes exist. That any of this improves
diagnosis — S3C ran no behaviour experiment (§10), and S3B's probe found M0 (no
memory) scoring highest on Match and Relevant. That the 28.68 threshold
generalises: it comes from 6 memories and 34 pairs, and a wider corpus should
re-calibrate rather than inherit it.

**Unchanged from S3B.** The warning the S3B probe gave still stands: on ten
cases, memory did not improve the process metrics and M0 led on accuracy. S3C
did not test that and does not answer it.

---

## 5. Recommendation

**`NEED_MORE_VERIFIED_EXPERIENCE`** — case 10–39 holds 4 gold-safe successes
against a threshold of 5.

Not `GO S4`. Not `RECONSIDER_BACKBONE` — §12 reserves that for ≤ 2, and the
holdout tier (5 of 20) shows the pipeline produces successes when the cases
permit.

For the reviewer, two things to weigh:

1. **The build corpus is the weak half.** case 30–39 produced 0 successes in 10;
   case 10–29 produced 5 in 20. Same model, same frozen rules, different cases.
   Widening the *build* set is the direct remedy and is a corpus decision S3C
   cannot make alone.
2. **Eight of case 10–29's ten uncertains are unanimous score 1** — "a broad
   category containing the reference". If a wider corpus produces many of
   those, the binding constraint is the judge's discriminative power between
   "right" and "broadly right", not the Gate's strictness.

## 6. How to check this without trusting the summary

1. `python3 -m pytest traceevo/tests/test_evolution.py` — 47 tests.
2. `failure_cases.md` — six failures, five of which are *a number produced by
   not measuring*. They are the honest index of how much of the above to believe.
3. `triage_gold_safe.csv` — §4's six conditions per case, with the failing one
   named, so the count of 4 can be re-derived rather than accepted.
4. `expanded_gate_results.csv` — 30 clean candidates × 5 rules, from which every
   rate in the four-column table can be recomputed.
5. `retrieval_dryrun_40_49.csv` — the abstention claim, per case.

## 7. Artifacts

§13's list is complete, plus `triage_gold_safe.csv`,
`success_path_check.csv`, `incorrect_clean_admission.json` and the poisoned-twin
summary. `legacy_outcome_results_0_9.csv` and `legacy_judge_raw/` are present
because the §4.2 branch was taken; every legacy entry is marked
`corpus_source = legacy_dev` and `headline_eligible = false` **in its payload**,
not only in a side file.
