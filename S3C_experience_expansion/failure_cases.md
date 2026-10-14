# S3C failure cases

Failures found building S3C, plus one finding about a previous stage that S3C
inherited.

The pattern from S3A and S3B holds, with a new shape in it. S3B's failures were
mostly *an artifact that read as a finding while being an absence*. S3C's are
mostly *a number that was never computed*, and the difference matters because a
missing measurement is easier to mistake for a result than a missing file is.

---

## FAILURE 1 — §3.1 asks for a proof that cannot be produced

**What happened.** §3.1 requires:

```
S3A sealed artifact hash == S3C OutcomeVerifier input artifact hash
```

S3A never sealed the case 10–29 holdout. Searched for it: no
`sealed_artifacts.json`, no per-artifact hash manifest, and every
`_integrity.json` carries `expected_tip_hash: null`. S3A's recorded hashes are
prompt/response *sequence* hashes for the ledger equivalence test — a different
object entirely.

**Why it matters.** The requirement exists so a reviewer can be sure the
artifacts verified at S3C are the ones S3A produced. Without a seal, that is
unprovable in the form asked for, and the honest response is to say so rather
than to compute a hash now and present it as the seal.

**Handled, not fixed.** `s3c_archive.py` produces the strongest substitute and
labels it as one, and `S3C_report.md` §3 states the gap with its consequence for
§11 criterion 10 (met in substance, not in the specific form). The substitute
that does real work is the cross-check against S3A's recorded corpus
aggregates — see FAILURE 2, which is what it took to make that check mean
anything.

**Carried forward.** S3B sealed its corpus correctly. S3C's recommendation is
that S4 seal before verifying, because the cost of not sealing is exactly this:
the artifacts can be verified and their provenance cannot.

---

## FAILURE 2 — the cross-check that was supposed to prove the corpus reported four false mismatches

**What happened.** The seal-substitute cross-check recomputed S3A's recorded
corpus statistics and reported:

| metric | S3A recorded | first recomputation | verdict |
|---|---|---|---|
| n_evidence_nodes | 150 | **285** | mismatch |
| externally_grounded_rate | 0.58 | **0.3754** | mismatch |
| lineage_complete_rate | 0.84 | **0.5123** | mismatch |
| n_cases | 20 | 20 | match |
| uncommitted_call_rate | 0.2968 | 0.2968 | match |
| discarded_call_rate | 0.0107 | 0.0107 | match |

Read at face value, that says S3C is verifying a different corpus — which,
given that the seal is missing, is exactly the thing the check existed to rule
out. It would have been a finding about S3A.

**Diagnosis.** The denominator. S3A's `n_evidence_nodes` counts Evidence-typed
nodes; `audit_task` returns Signal, Evidence and Hypothesis nodes (20, 150 and
115 here). Counting all three gave 285 against 150. Restricting to Evidence
reproduced 150 / 87 / 126 — and therefore 0.58 and 0.84 — exactly.

**Why this one is worth its own entry.** The two metrics that matched did so
because the ledger rates do not depend on the node population. So the check was
*partly* working, which is the most dangerous state for a check to be in: enough
agreement to look like it functions, and a wrong axis producing confident noise
on the other.

A cross-check that is itself wrong is worse than no cross-check. It does not
merely fail to confirm — it manufactures a finding about the wrong subject, and
that finding was "the corpus is not what S3A measured", which would have
invalidated the stage.

**Fixed.** The population is now Evidence-only, with the reason recorded at the
call site. All six metrics match.

---

## FAILURE 3 — an abstention mechanism that could not abstain

**What happened.** S3C §9 requires the success retriever to abstain rather than
stretch. The calibration computed a threshold from a self-versus-other score
split and then dry-ran the retriever over case 40–49. It reported:

```
self_scores : n=0
other_scores: n=34
threshold   : 0.5      (a default, not a measurement)
dryrun      : 10 hits, 0 abstains
```

Ten of ten retrieved. An abstention mechanism reporting zero abstentions is the
one result that cannot distinguish "nothing needed abstaining" from "abstention
does not work".

**Diagnosis.** The code called `store.entry(memory_id)`, which does not exist on
`MemoryStore`. The guard was `if hasattr(store, "entry") else None`, so instead
of raising it silently produced `None` for every hit — every score was
classified as "other", `self_scores` stayed empty, the self/other comparison had
nothing to compare, and the threshold fell through to a default.

**Why the `hasattr` guard matters.** It converted an `AttributeError` into a
wrong answer. The code was written to be defensive about an API that was in fact
present under a different name (`retrievable()`, which returns the records and
their `source_trace_id`). A `hasattr` check around a call whose result is
required is not defensive; it is a way to keep going without the ingredient.

**Fixed.** `retrievable()` supplies the mapping. The split is now:

| pair | n | min | max |
|---|---|---|---|
| self | 6 | 44.2588 | 126.1347 |
| other | 28 | 0.4317 | 13.0990 |

Threshold **28.6789**, midpoint of a gap that spans a factor of 3.4, and the
dry-run abstains on **10 of 10**. The separation is wide enough that the
mechanism is demonstrably doing the work rather than sitting on a boundary.

---

## FAILURE 4 — three baselines' retention was 0.0 because it was never computed

**What happened.** `expanded_gate_metrics.csv` reported
`validated_success_retention = 0.0` for B0_Naive, B1_OutcomeOnly, B2_AuditOnly
and B3_IntegrityOnly. Every one of those rules admits every clean success.

**Diagnosis.** The retention column for the baselines was filled from a
placeholder expression rather than from running them. TraceEvo's value was real
(4/4); the other four were zero by construction.

Read in a report, `retention 0.0` for four rules and `1.0` for the fifth is a
strong claim — "only TraceEvo preserves validated successes" — and it would have
been false in the direction that flattered the thing being evaluated.

**Fixed.** Every baseline is now run on every clean success candidate, in
`build_success_candidates`, and the column is read from those verdicts. All five
rules retain 1.00, and the discrimination moves to the two columns where it is
real: Correct-but-Poisoned Admission and Incorrect-Clean Admission.

**Note.** This is the first failure in three stages where the wrong number
flattered TraceEvo. It was wrong in the same way as the others — computed by not
computing — and it is recorded in the same place for the same reason.

---

## FAILURE 5 — channel separation was a policy document, not a boundary

**What happened.** The first version of S3C-F built two stores and wrote a
`memory_channel_policy.md` saying failure memories were not initial-plan
retrievable. Both stores were constructed identically, and the failure store answered
`retrievable_ids()` with seven entries across its four partitions.

§8's rule held only because the code that would have queried the failure store
for initial-plan material did not exist yet. The rule was a comment.

**Why that is a defect and not a simplification.** §8's requirement is about
what S4 will do. A rule enforced by the absence of a caller is enforced by
nothing, and the caller §8 is written against is precisely the one S4 will add.

**Fixed.** `MemoryStore` takes an `initial_plan_partitions` policy. The success
store is built with `("success",)`, the failure store with `()`. The failure
store now answers `initial_plan_ids() == []` to any caller, and two tests pin it
— including `test_a_default_store_keeps_the_s3b_policy`, which asserts that
S3B's stores are unchanged, because a boundary that quietly narrows existing
behaviour is its own failure.

Result: **success initial-plan 6, failure initial-plan 0.**

---

## FAILURE 6 — the legacy archive was at a path that did not exist

**What happened.** The first `--legacy` run pointed at
`S3A_runs/s3a_ledger/ledger_on/traceevo/runs`, which holds one directory
(`ledger_on/`) and no traces.

**Diagnosis.** The run directory repeats the tag: `.../runs/ledger_on/`. Cheap
to fix, recorded because it is the same class as S3B's FAILURE 5 — a path
assumed rather than checked — and because this time it was caught by listing the
directory before the run rather than by a traceback after it.

**Fixed.** Path corrected and verified before running: 10 integrity files, 10
event files, case-0 present.

---

## A note on what these six have in common

Five of the six are *a number produced by not measuring*: an unprovable seal
reported as a cross-check, a cross-check counting the wrong population, a
threshold that fell through to a default, a retention column never computed, and
a policy with no enforcement behind it.

S3B's failures were absences that looked like results. S3C's are results that
were never computed. The second is harder to notice, because the output is a
number in a table rather than a missing file — and three of these five were
caught only because a second, independent thing disagreed with the first.
