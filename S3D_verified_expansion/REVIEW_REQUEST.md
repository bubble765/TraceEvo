# TraceEvo S3D — review request

Per `TraceEvo_S3C_Review_S3D_Amendment_v1.md` §17. **S3D stops here.**

No Capability Profile, no agent-capability EMA, no adaptive agent selection, no
Planner change, no memory injection, no failure-memory activation, no use of
unreviewed cross-case retrievals in a Planner, no change to the judge, the Gate
or the audit rules, no backbone change. Case 60-137 was not read or run.

The short version: **the headline count reached 5 and the corpus did not move.
§12's item 4 fails while the other nine pass, and the reason is a gap between
two bars that §D1 forbids this stage from closing.**

§15 offers `GO S4A` / `VERIFIED_SUCCESS_SCARCITY_PERSISTS` /
`NEED_RETRIEVAL_REDESIGN`. This stage recommends none of the three, and §6 below
explains why the accurate answer is a fourth.

---

## 1. What S3D found

### The count reached the threshold

```
block        verified_success   gold_safe   eligible
10-29 (S3C)          5              4           4
30-39 (S3B)          0              0           0
50-59 (S3D)          1              1           0
headline             6              5           4
```

`headline_gold_safe = 5`, against §6's requirement of ≥ 5. case-51 is a clean
verified success on all six of §5's conditions: no anomaly, no unresolved P3,
integrity OK, invocations complete.

### And the fifth success is not usable

The frozen Gate returns `manual_review` for case-51:

```
2 P1 soft-grounded node(s) on the decision path;
§6.1 sends these to review rather than admitting them
```

§5's six conditions do not include §6.1's soft-grounding rule. The four S3C
headline successes all have `used_P1 = 0`; case-51 has `used_P1 = 2`. **So
`headline_gold_safe` and `headline_eligible` are different numbers — 5 and 4 —
and only the second one is a corpus.**

### The seal works, and was shown to fail first

Ten cases, ten seals, ten verified, zero rejected, ten artifacts per case.
`test_a_tampered_artifact_fails_the_seal` modifies one artifact and asserts the
seal breaks and names it; `test_a_deleted_artifact_fails_the_seal` covers the
other failure mode; `test_check_seals_returns_a_row_per_seal` stops either from
passing vacuously. Memory being off is measured, not asserted: **0 injection
records** across all ten cases.

The first version of the seal covered five of the ten artifacts — §3.3's graph
and report among the missing. That is `failure_cases.md` FAILURE 1, and it is
the failure this stage exists to fix, occurring in the fix.

### The Gate's silence did not change

20 correct-but-poisoned twins cumulatively (S3C's 16 + S3D's 4): **TraceEvo 0,
OutcomeOnly 20.** Fewer than S3C's 16, reported with its confound rather than
against it — see §4.

### Gold leakage is 0 over a non-empty set

5 entries, 4 injectable, 80 cross-case pairs, `vacuous: false`. The first
version scanned the retrievable set, which is empty here, and would have printed
`passed: true` from a loop that ran zero times. FAILURE 5.

---

## 2. The four-column comparison, extended

| rule | Poison AR | Correct-but-Poisoned AR | Retention | Incorrect-Clean AR |
|---|---|---|---|---|
| **TraceEvo** | **0.0000** | **0.0000** | **1.0000** | **0.0000** |
| B0 Naive | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B1 OutcomeOnly | 1.0000 | 1.0000 | 1.0000 | 0.0385 |
| B2 AuditOnly | 0.1250 | 0.1250 | 1.0000 | 0.7692 |
| B3 IntegrityOnly | 0.0000 | 0.0000 | 1.0000 | 1.0000 |

Unchanged from S3C and still dominated on every axis. Note B3 ties TraceEvo on
both poison columns while admitting every clean wrong answer, so the poison
column alone never separated them — the separation is in column four.

---

## 3. The cross-case retrieval audit, and 28.6789

20 query cases (40-49, 50-59), top-3, self-memories excluded, query text from
the blinded copy through the same `build_retrieval_key` the runtime uses:

```
all 58 rows : 0.76 - 15.12     top-1 median 6.67
above_self_identity_threshold : 0
```

**Not one cross-case score reaches 28.6789.** §9.1's point is now demonstrated
rather than argued: the threshold separates a case from itself, and as an online
cross-case threshold it would abstain on 20 of 20 queries — which means "0 hits"
cannot distinguish correct abstention from having nothing to say. Renamed to
`self_identity_separation_threshold`, demoted to diagnostic, recorded in
`self_identity_threshold_note.md`. **S4 must not inherit it.**

Top-1 is spread across all four memories (6/5/5/4), so the ranking discriminates
rather than collapsing onto one generic memory. Whether any pair is *apt* is not
answerable here and was not attempted.

---

## 4. What this does and does not establish

**Established.** Every case sealed before its label was read, and the hash check
was shown to be capable of failing. Memory was off, measurably. No gold leaked,
over a non-empty scan. The Gate's admission of poisoned twins stayed at zero
cumulatively. Failure memory stayed unreachable. No frozen file drifted — 27
checked, 0 drift, and the `gos` diff is byte-identical to S3C's.

**Not established — and this is the point.**

1. **That the Gate resists poisoning of case-51.** case-51 is `manual_review`
   even clean. Forcing `integrity_ok = True`, PZ2 produces *no anomaly at all*
   and is held by the same two P1 nodes that held the clean case. Three of four
   poison types are caught independently by the anomaly layer; one is not, and
   its 0 is a property of case-51 rather than a defence. **S3C's 0/16 on four
   real `eligible` successes remains the poison-resistance result.** S3D's 0/4
   is a robustness check whose confound is reported.
2. **That the headline count of 5 is a corpus.** It is not; the corpus is 4.
3. **That anything improves diagnosis.** S3D ran no behaviour experiment and
   §9's retrievals were never injected. S3B's probe finding still stands: on ten
   cases, M0 (no memory) led on Match and Relevant.

**Unchanged.** §2 of S3C's warning holds. So does the judge observation: S3D
produced 3 more unanimous `1|1|1` uncertains (5 of 10 cases uncertain), making
11 across S3C and S3D. If the binding constraint on this corpus is the judge's
power to separate "right" from "broadly right", widening the build block will
produce more uncertains before it produces more successes.

---

## 5. §12's must-items

| # | item | status |
|---|---|---|
| 1 | seal-before-outcome on 50-59 | met — 10/10 |
| 2 | frozen config/audit/judge/Gate unchanged | met — 27 files, 0 drift |
| 3 | `headline_gold_safe >= 5` | met — 5 |
| 4 | new success's real eligible path checkable | **NOT MET** |
| 5 | correct-but-poisoned admission 0 | met — 0/4, confounded, §4 |
| 6 | gold leakage 0 | met — non-vacuous |
| 7 | failure memory out of initial Plan | met |
| 8 | 28.6789 demoted | met |
| 9 | review queue generated | met — 58 rows, all `UNREVIEWED` |
| 10 | case 60-137 not read | met |

---

## 6. Recommendation

**None of §15's three.** The count reached 5 but the corpus did not grow: §12
item 4 fails while 1-3 and 5-10 pass.

- `VERIFIED_SUCCESS_SCARCITY_PERSISTS` would misdescribe a block that produced a
  success and a count of 5.
- `GO S4A` would misdescribe a store with no new entry in it, and would carry a
  number into S4 that overstates the corpus by exactly what S3D was run to fix.
- `NEED_RETRIEVAL_REDESIGN` is not it either — the retriever behaved correctly
  here; it abstained, which is what §9 asks of it.

**What actually happened is narrower: two bars disagree and no one has decided
which one counts for S4.**

```
§5's six conditions  ->  headline_gold_safe = 5   (§6's number)
§6.1's P1 rule       ->  headline_eligible  = 4   (the store's number)
```

Three ways forward, ranked as this stage would rank them:

1. **Accept 4 and treat `headline_gold_safe` as a diagnostic.** S4 starts with
   four reusable successes; the S3D block contributes none. The paper says 4.
2. **Widen the build block (60-69).** The prior over 40 cases is roughly one
   success per ten cases, and the P1 rate on this block is a single observation,
   so the yield of a further block is genuinely uncertain.
3. **Decide whether §6.1's P1 rule should bound the headline count as well as
   admission.** A rules question, and §D1 freezes the rule — which is why this
   report does not resolve it.

**This stage's own reading is (1)**, because §6.1's P1 rule is the one that asks
whether a strategy is worth copying, which is the question S4 depends on. That
is an argument, not a decision. Both numbers are in
`headline_success_summary.json`.

---

## 7. How to check this without trusting the summary

1. `python3 -m pytest S3D_verified_expansion/tests/test_s3d.py` — 16 tests,
   including the seal's tamper positive control.
2. `failure_cases.md` — six failures. FAILURE 1 is the seal covering five of ten
   artifacts; FAILURE 2 is the test that pinned the broken set; FAILURE 5 is a
   leak scan that passed over an empty set. They are the honest index of how
   much of the above to believe.
3. `seals/case-XX_seal.json` versus `seals/case-XX_seal.partial.json` — what was
   sealed when, and the `supersedes` field recording the correction.
4. `gold_safe_triage_50_59.csv` — §5's six conditions per case, each named, so
   the count of 1 can be re-derived.
5. `expanded_store_admission.csv` — the per-field §7 check, the Gate verdict,
   and its reasons, on the one case that matters.
6. `new_success_poisoned_defence_layers.json` — the per-twin layer separation
   that isolates the confound in §4.
7. `cross_case_retrieval_topk.csv` — all 58 scores, from which
   `above_self_identity_threshold = 0` can be recomputed.

## 8. Artifacts

§14's list is complete, plus `verified_success_but_unsafe_50_59.json`
(empty — there is no case-22 in this block), `expanded_store_admission.csv`,
`store_split.json`, `new_success_poisoned_defence_layers.json`,
`leakage_scan_cross_case.csv`, `s3d_outcome_summary.json` and
`run_summary.json`. The ten superseded five-suffix seals are kept as
`seals/case-XX_seal.partial.json` rather than overwritten.
