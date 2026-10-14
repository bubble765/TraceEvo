# S3C — experience expansion and memory-channel calibration: report

Scope: case 10–39 (headline) and case 0–9 (legacy supplement, marked). Case
40–49 was used for a retrieval dry-run only. Case 50–137 was not read or run.
No GoS process was started anywhere in S3C. Backbone `deepseek-v4-flash`; judge,
Gate and audit rules unchanged from S3B.

The one-line result:

> **The success path works and is now demonstrated on real data: 4 gold-safe
> verified successes pass the Gate, a poisoned twin of every one is rejected,
> and OutcomeOnly admits all 16 while TraceEvo admits none. But 4 is below the
> §11 threshold of 5, and the legacy supplement cannot close a gap it is
> excluded from. Recommendation: `NEED_MORE_VERIFIED_EXPERIENCE`.**

---

## 1. Executive summary

| §11 mandatory criterion | Status |
|---|---|
| 1. ≥ 5 gold-safe verified-success in case 10–39 | **Not met — 4** |
| 2. Validated Success Retention > 0 | Met — **1.00** (4/4) |
| 3. OutcomeOnly admits correct-but-poisoned > 0 | Met — **16/16** |
| 4. TraceEvo correct-but-poisoned admission = 0 | Met — **0/16** |
| 5. TraceEvo Incorrect-Clean < AuditOnly | Met — **0.00 vs 0.77** |
| 6. gold leakage = 0 | Met — 0 over 6 success memories |
| 7. success/failure channels separated | Met |
| 8. failure memory out of initial Plan | Met — 0 reachable |
| 9. success retriever supports abstention | Met — 10/10 abstain on 40–49 |
| 10. success memories traceable to historical traces | Met with a caveat (§3) |

**Nine of ten met; the one that is not is the one that gates S4.** §12 applies:
`NEED_MORE_VERIFIED_EXPERIENCE`, and since the count is 4 (not ≤ 2), §12's
backbone-suspicion clause does not apply — the shortfall is corpus size, not
model capability.

---

## 2. Answers to §14

### §14.1 — Distribution of `verified_success / incorrect / uncertain` in case 10–29?

**5 / 5 / 10**; judge unanimity 18/20 = 0.90.

| Verdict | Cases |
|---|---|
| `verified_success` | 11, 20, 21, 22, 27 |
| `verified_incorrect` | 12, 14, 19, 25, 28 |
| `uncertain` | 10, 13, 15, 16, 17, 18, 23, 24, 26, 29 |

The ten `uncertain` split into two mechanisms, as in S3B:

* **eight are unanimous score 1** — cases 10, 15, 17, 18, 23, 24, 26, 29. All
  three judge runs called the diagnosis "a broad category containing the
  reference". §4.2 does not admit score 1 as a success, and this is the case it
  was written for.
* **two are judge disagreement** — case 13 (1|0|1) and case 16 (1|0|1). Reported,
  not resolved: exactly three runs, no tiebreak, no majority rule.

No rule was changed after seeing these (§3.2).

### §14.2 — How many gold-safe verified-successes in case 10–39?

**4.**

| Tier | Cases | verified_success | gold-safe |
|---|---|---|---|
| A (case 10–29, S3A holdout) | 20 | 5 | **4** |
| B (case 30–39, S3B build) | 10 | 0 | 0 |
| **headline (10–39)** | 30 | 5 | **4** |
| C (case 0–9, legacy) | 10 | 2 | 2 |

**case-22 is the one that fails, and it is worth naming.** It *is* a verified
success — 3/3 score 2. It nonetheless fails §4's six-condition test on
`no_unresolved_decision_path_p3`: it carries an A1 anomaly and a P3 node used in
reasoning, which is the same shape as S3B's case-32 and makes it a Validated
Failure rather than a gold-safe success.

Counting it would have made the headline 5 and satisfied §11 criterion 1. That
is exactly why §4 lists six conditions rather than one, and why the shortcut was
not taken.

### §14.3 — Was the success Gate's `eligible` path actually executed?

**Yes — for the first time.** In S3B no candidate reached `eligible` on real
data, so the branch existed and had never run. S3C pushed every gold-safe
success through the real `gate.evaluate`:

| Tier | Case | eligibility |
|---|---|---|
| A | 11 | **eligible** |
| A | 20 | **eligible** |
| A | 21 | **eligible** |
| A | 27 | **eligible** |
| C | 5 | **eligible** (legacy) |
| C | 9 | **eligible** (legacy) |

Six candidates took the path `eligible → success partition → retrieval index →
injectable payload`. Traceability and payload checks are in
`success_path_check.csv`: every memory carries `memory_id`, `source_trace_id`,
source hashes, `audit_summary_hash`, `outcome_verdict`, gate reasons and
`extractor_version`; none carries the gold diagnosis, the right option or the
historical prediction in its payload.

Over all 30 clean headline candidates the verdict distribution is
**4 eligible, 6 failure_experience, 20 manual_review** — the `eligible` branch
is no longer hypothetical.

### §14.4 — What is Validated Success Retention?

**1.00 for TraceEvo** (4 of 4 headline gold-safe successes admitted), and
**1.00 for every baseline** — on *this* corpus, where every rule admits every
success, retention does not discriminate. That is worth stating plainly: §11
criterion 2 is satisfied, and satisfying it required no work from the Gate,
because these four traces are clean by construction of the six-condition test.

The discrimination is in the other two columns.

### §14.5 — How many correct-but-poisoned successes were constructed?

**16** — all four poison types applied to all four headline successes, 16/16
applicable. This is the counterexample S3B could not build (§6).

| poison | anomaly | successes it was applied to |
|---|---|---|
| PZ1 `add_node` | A1 | 11, 20, 21, 27 |
| PZ2 `wrong_record_binding` | A2 | 11, 20, 21, 27 |
| PZ3 `status_marked` | A0 | 11, 20, 21, 27 |
| PZ4 `modify` | A5 | 11, 20, 21, 27 |

§6 asks that each kind act on at least one real verified-success trace; each
acted on four. Note PZ2 is applicable 4/4 here, where in S3B it was 8/10 — the
success traces all have the second record the variant needs.

### §14.6 — How many did OutcomeOnly admit?

**16 of 16 (rate 1.00).**

### §14.7 — How many did TraceEvo admit?

**0 of 16.**

### §14.8 — Does AuditOnly still admit incorrect-clean traces?

**Yes, and this is now a measured rate rather than an anecdote.**

| rule | Incorrect-Clean Admission Rate |
|---|---|
| **TraceEvo** | **0.0000** (0/26) |
| B0 Naive | 1.0000 (26/26) |
| B1 OutcomeOnly | 0.0385 (1/26) |
| B2 AuditOnly | **0.7692 (20/26)** |
| B3 IntegrityOnly | 1.0000 (26/26) |

The S3B observation was "AuditOnly admitted 9 of 10 clean traces"; over 26
clean non-gold-safe candidates from two corpora it is 20 of 26. The defect is
structural and does not shrink with a larger corpus.

**The full four-column picture** (`expanded_gate_metrics.csv`):

| rule | Poison AR | Correct-but-Poisoned AR | Retention | Incorrect-Clean AR |
|---|---|---|---|---|
| **TraceEvo** | **0.0000** | **0.0000** | **1.0000** | **0.0000** |
| B0 Naive | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| B1 OutcomeOnly | 1.0000 | 1.0000 | 1.0000 | 0.0385 |
| B2 AuditOnly | 0.1250 | 0.1250 | 1.0000 | 0.7692 |
| B3 IntegrityOnly | 0.0000 | 0.0000 | 1.0000 | 1.0000 |

Every single-signal rule is dominated. **B3 matches TraceEvo on poison and
admits every clean wrong answer; B1 avoids clean wrong answers and admits every
poisoned one.** Only the conjunction is best on all four, which is the claim
S3B made and could not demonstrate.

### §14.9 — Was the case 0–9 legacy supplement used, and why?

**Yes.** §4.2 authorises it when Tier A+B holds 3–4 gold-safe successes, and
A+B holds 4.

It was verified rather than assumed: case 0–9 yields **2 verified_success** (5
and 9), both gold-safe, both admitted by the Gate. They are marked in three
places — `corpus_source = legacy_dev` and `headline_eligible = false` in the
payload itself, not only in a side file, plus `tier: C` in every table.

**They changed no headline number.** §2 excludes them from the headline corpus
and §11 criterion 1 counts case 10–39 only, so 4 + 2 is still not 5. The
supplement's purpose here is to give the success channel more than four entries
to exercise; it cannot and does not close the §11 gap.

Archive chosen: `S3A_runs/s3a_ledger/ledger_on` — the latest Dev-Small run,
produced after the S2 freeze and carrying the same instrumentation. Recorded
because "which Dev-Small run" is not recoverable from the artifacts alone.

### §14.10 — Are success and failure memories on separate channels?

**Yes** — and structurally, not by convention.

`MemoryStore` now takes an `initial_plan_partitions` policy. The success store
is built with `("success",)`; the failure store with `()`. So the failure store
answers `initial_plan_ids() == []` no matter what a caller asks it, and the
boundary is a property of the store rather than a rule the retrieval code is
trusted to remember (`memory_channel_policy.md`).

`retrievable()` is unchanged: failure memories remain reachable by retrieval, as
§6.2 intends. §8's narrower channel is added *inside* that, which is why the
default-constructed store in S3B is unaffected
(`test_a_default_store_keeps_the_s3b_policy`).

### §14.11 — Is failure memory fully out of initial-Plan retrieval?

**Yes.**

| channel | stored | auditable | initial-plan reachable |
|---|---|---|---|
| success | 6 | yes | **6** |
| failure | 7 | yes | **0** |

Each failure memory carries `failure_pattern`, `activation_condition` and
`activation_status: inactive_until_S4`. **No activation logic was implemented** —
§8 says S3C does not, and this is where the S4 trigger will attach.

### §14.12 — Success retrieval on the case 40–49 dry-run: hit vs abstain?

**0 hits, 10 abstains.**

Every probe case abstained. That is the correct behaviour and not a failure:
the success memories come from case 10–39, and no case 40–49 is a close enough
neighbour. §9.2 requires `return []` rather than a stretch, and the retriever
returned `[]` ten times.

It also means the S3B probe's central artefact does not recur: S3B injected
one failure memory into ten of ten cases because nothing asked whether it
belonged. Here the equivalent question is asked, and the answer is no.

### §14.13 — How was the threshold set without using future outcomes?

**By a self-versus-other split over case 10–39 — no outcome appears anywhere in
the computation.**

For each success memory, its own source case's pre-task text was used as a
query, and every other case's text as well:

| pair | n | min | max |
|---|---|---|---|
| self | 6 | 44.2588 | 126.1347 |
| other | 28 | 0.4317 | 13.0990 |

The two distributions do not overlap and are separated by a factor of 3.4. The
threshold is their midpoint, **28.6789**, which is why the dry-run abstains
cleanly rather than sitting on a knife edge.

Case 40–49 contributed only pre-task queries. No 40–49 outcome was read, and
the dry-run file records `retrieved_or_abstained` per case so the claim can be
checked.

**Caveat.** Six memories and 34 pairs is a thin calibration set. The separation
is wide enough that the threshold is not delicate, but a wider corpus should
re-calibrate, and a 28.68 threshold should not be carried into S4 as a
constant.

### §14.14 — Is gold leakage still 0?

**Yes — 0 over 6 initial-plan-retrievable success memories.**

Same guard as S3B §13, run over the rendered block each success memory would
contribute. No live prompt exists to scan, because §10 forbids re-running the
behaviour arms and none was run.

### §14.15 — Are there ≥ 5 real, traceable success memories for S4?

**No — there are 4 headline (and 2 legacy, which do not count).**

Traceability is intact for all of them: `source_trace_id`, source hashes, audit
summary hash, outcome verdict, gate reasons and extractor version are present
on every entry, and `memory_audit_links.jsonl` carries 13 links across both
channels. What is missing is the sixth memory, not the provenance of the four.

### §14.16 — Recommendation

**`NEED_MORE_VERIFIED_EXPERIENCE`.**

Not `GO S4` — criterion 1 fails at 4 of 5.
Not `RECONSIDER_BACKBONE` — §12 reserves that for ≤ 2, and 4 is above it. The
build corpus (case 30–39, 0 of 10) is the weaker half, and it is a 10-case
sample; the holdout (case 10–29, 5 of 20) shows the pipeline can produce
successes when the cases permit.

---

## 3. The seal gap, stated plainly

§3.1 asks for a specific proof:

```
S3A sealed artifact hash == S3C OutcomeVerifier input artifact hash
```

**It cannot be produced. S3A never sealed the case 10–29 holdout.** There is no
`sealed_artifacts.json` for that corpus, and each `_integrity.json` carries
`expected_tip_hash: null` — a chain that verifies against nothing published.

What was produced instead, in `corpus_manifest.json`:

| substitute | what it establishes | what it does not |
|---|---|---|
| chain re-verification | 20/20 traces are internally consistent now | unchanged since S3A |
| dated S3C manifest | sha256 of every artifact as S3C read it | immutability in between |
| **aggregate cross-check** | **6/6 of S3A's recorded corpus statistics reproduce exactly** | — |

The third is the one that does real work, and it is the only available test
that the corpus verified *is* the corpus S3A produced. Recomputed against
`S3A_migration/migration_summary.json`:

| metric | S3A recorded | S3C recomputed | match |
|---|---|---|---|
| n_cases | 20 | 20 | ✓ |
| n_evidence_nodes | 150 | 150 | ✓ |
| externally_grounded_rate | 0.58 | 0.58 | ✓ |
| lineage_complete_rate | 0.84 | 0.84 | ✓ |
| uncommitted_call_rate | 0.2968 | 0.2968 | ✓ |
| discarded_call_rate | 0.0107 | 0.0107 | ✓ |

The first attempt at this check reported four mismatches, which would have read
as "S3C is verifying a different corpus". The cause was mine: S3A counts
Evidence-typed nodes and `audit_task` also returns Signal and Hypothesis nodes.
Restricting the population reproduced all six values exactly. Recorded because a
cross-check that is itself wrong is worse than no cross-check — it produces a
finding about the wrong thing.

**Consequence for §11 criterion 10.** "All new success memories traceable to a
sealed historical trace" is met in substance (chain verified, hashes recorded,
corpus confirmed by aggregate agreement) and not in the specific form §3.1
asked for. It is reported as met-with-caveat rather than as met.

**Recommendation for S4:** seal the corpus before verifying it. S3B did this
correctly (`sealed_artifacts.json`) and S3A did not; the cost of the omission is
that this stage can verify the artifacts but cannot prove they are the ones that
were run.

---

## 4. What S3C did not do

- No GoS process was started. Case 40–49 was queried offline for retrieval only
  and its outcomes were never read (§9.1, §10).
- Case 50–137 was not read or run (§2, §15).
- No Capability Profile, no agent capability EMA, no memory-driven agent
  selection, no multi-round evolution, no backbone change (§15).
- The OutcomeVerifier's success definition, the judge, and the frozen audit
  rules were not modified (§15). `verify_trace` was imported, not reimplemented.
- No threshold was tuned against case 50+ or against any 40–49 outcome (§9).
- No activation logic for failure memory was implemented (§8).

---

## 5. Artifact map

| File | Contents |
|---|---|
| `corpus_manifest.json` | Input seal substitute: hashes, chain checks, seal gap, aggregate cross-check |
| `sealed_artifact_check.csv` | Per-case chain status and artifact hashes |
| `archive_outcome_results_10_29.csv` | case 10–29 verdicts, scores, `reference_sha256` |
| `archive_judge_raw/` | Full judge transcripts (contain references; evaluation only) |
| `legacy_outcome_results_0_9.csv` | Tier C verdicts (marked `headline_eligible=false`) |
| `legacy_judge_raw/` | Tier C judge transcripts |
| `triage_gold_safe.csv` | §4's six conditions, per case, with the failing one named |
| `triage_summary.json` | Branch decision and headline count |
| `expanded_candidates.jsonl` | Every clean candidate with tier and eligibility |
| `expanded_gate_results.csv` | 30 clean candidates × 5 rules, per candidate |
| `success_path_check.csv` | Traceability and payload-cleanliness per success |
| `success_poisoned_candidates.{csv,jsonl}` | 16 correct-but-poisoned twins |
| `success_poison_summary.json` | §7.2's first counterexample |
| `expanded_gate_metrics.{csv,json}` | The four-column rule comparison |
| `incorrect_clean_admission.json` | Incorrect-Clean Admission Rate per rule |
| `success_memory_store/`, `failure_memory_store/` | The two channels |
| `memory_channel_policy.{md,json}` | §8's policy |
| `success_retrieval_calibration.{csv,json}` | Self/other split and threshold |
| `retrieval_dryrun_40_49.csv` | Abstention dry-run |
| `leakage_scan_results.csv`, `leakage_summary.json` | §13-equivalent scan |
| `memory_audit_links.jsonl` | 13 links across both channels |
| `failure_cases.md` | Failures found building S3C |
| `tests/` | 47 tests, all passing |
