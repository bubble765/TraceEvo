# TraceEvo S3A — review request

Per `TraceEvo_S2.1_Review_S3A_Amendment_v1.md` §11. **S3A stops here.** No
Memory Store, no planner injection, no Capability Profile, no self-evolution
experiment, no case outside 10–29 read or run. Those are S3B and none were
started.

---

## 1. What S3A found

**Both goals met, and the second one found something the first was built for.**

### Goal A — the invocation ledger (Q1–Q5)

The channel works and is provably inert. Over the Dev-Small replay, ledger off
and ledger on produce identical prompts, responses, predictions, reports,
graphs, deltas and semantic provenance; the only difference is 469 rows of
ledger. Coverage is 469/469 calls, and 288/288 model-produced events bind to
their call by exact text hash with zero unbound.

**A4 case5/8 are now localized to a model call** — §4's goal — and the shape is
not the one §4 predicted. The injected response is `accepted`, not `discarded`:
it is well-formed JSON with a legal `decision`, so the decision parser is right
to take it. What the injection *causes* is the next call being discarded with
`DECISION_CONTENT_MISMATCH`. Case 5's root is `call-case-5-00005` with the
substitution's own prompt hash; case 8's is `call-case-8-00005`. Reporting "the
injected call was discarded" would have been false, so the result reports the
pair and §4's two localization questions get different answers, as §4 asked.

### Goal B — the blind migration audit (Q6–Q9, Q12)

**Verdict GO.** 20/20 holdout cases completed; no schema or DAG break; frozen
rules verified byte-identical after the last case.

The headline is stability where it matters: `externally_grounded` is **58.0% on
the holdout against 58.4% on Dev-Small**, and the tier mix barely moves (P0 58.0
vs 58.4, P2 37.3 vs 39.3). A boundary overfitted to ten cases would have moved.

---

## 2. Two things a reviewer should decide, not me

### 2.1 The PASS has no margin, and the only way to get one is forbidden

```text
clean_trace_alert_rate   0.2500    §7 flags at > 0.25
```

Exactly on the line. Five alerted traces out of twenty; a sixth would have
forced `NEED_REVIEW`. This is the same position S2 was in at a 10.0% clean FPR
against a ≤10% gate, and it is reported the same way rather than rounded.

The available moves are both someone else's call:

- **Widen the absence rule.** It would take the count from 5 to 3 and the rate
  from 0.25 to 0.20 — tuning a rule on the data it is being evaluated against.
  §7 forbids it and D2 was written to prevent it. I did not do it.
- **Accept a smaller holdout.** Not a decision the measurement can make for you.

### 2.2 `Validated Success` is not issuable, and that is a design input for S3B

§5's admission rule requires `outcome judged correct`. That field **cannot be
computed from a trace** — `experience_eligibility_contract.md` §3.1 shows every
available proxy fails in the direction of storing a guess as a fact. Self-
reported confidence is already written into the model's prose (S2 F4); absence
of anomalies is a 25%-alert-rate signal, not a correctness one.

So on unlabelled data the audit can issue `Validated Failure` (a localized
anomaly root needs no correctness judgement) and `manual_review`, and cannot
issue `Validated Success`. **If S3B is designed around a Gate that admits
successes, it needs labels, not a better gate.** This is the single most
consequential thing in S3A and it is a design constraint on the next stage, not
a defect to repair.

---

## 3. The absence rule does show the predicted soft spot

S2 §Q11 named the absence regex as the highest overfitting risk; D2 froze it.
On the holdout it misses two phrasings it was never shown:

```text
"No fever was documented at presentation"                          -> clinical_evidence
"No infectious exposure or positive cultures have been identified"  -> clinical_evidence
```

Both are absence claims that should be P2, and both came out P3, which is what
produced 2 of the 5 alerts. Verified by running the frozen regex against the
strings, not inferred.

The rule has **not** collapsed — it classifies absence claims at roughly the
Dev-Small rate (23 vs 18 nodes). What this shows is that its coverage is
*locale-shaped*: it knows the phrasings ten cases produced. That is the finding
to hand to S3B, and the recommendation is a classifier that does not enumerate
phrasings rather than a longer alternation.

**Left unfixed**, per §7. The other 3 of the 5 P3 nodes are a negative clinical
finding that is arguably an absence claim and two measurement claims a human
needs to read against their records.

---

## 4. Corrections made to earlier artifacts

1. **A rule was edited after it was frozen.** I wrote
   `rule_freeze_manifest.json`, started the holdout, then fixed a ledger bug.
   `--verify` caught it. The run was stopped after one case, the partial output
   archived to `S3A_runs/s3a_migration_aborted_freeze_race/`, the manifest
   regenerated, and the run restarted from case 10. Reported rather than
   quietly re-frozen. `failure_cases.md` F6.
2. **`S2_audit/S2_report.md` Q2b said `lineage_complete` was "False for 27".**
   Its own CSV says 30 (18+6+3+2+1), and 30+59 = 89. Corrected in place with a
   dated note. No downstream number changes — all of them came from the CSV.
3. **§4's expected record shape was wrong** (above), and the measurement was
   kept rather than the expectation. `failure_cases.md` F7.

---

## 5. Deliverables (§9)

```text
S3A_migration/
├── S3A_report.md                          ✅  §10's 12 questions
├── REVIEW_REQUEST.md                      ✅  this file
├── model_invocation_schema.json           ✅  generated from the writer
├── experience_eligibility_contract.md     ✅  defined, not implemented
├── migration_config.yaml                  ✅  the frozen data split
├── rule_freeze_manifest.json              ✅  verified unchanged post-run
├── migration_results.csv                  ✅  20 rows
├── migration_evidence_analysis.csv        ✅  150 Evidence nodes, 22 columns
├── migration_call_analysis.csv            ✅  748 calls
├── migration_review_queue.csv             ✅  12 rows
├── a4_invocation_recheck/case{5,8}.json   ✅
├── regression_results.csv                 ✅  11 fields, ledger off vs on
├── failure_cases.md                       ✅  9 failures, 1 correction
├── tests/                                 ✅  133 tests
├── git_diff.patch                         ✅
└── git_commit.txt                         ✅
```

Supporting: `migration_summary.json`, `migration_call_analysis.csv`. Raw runs
in `S3A_runs/` (`s3a_ledger`, `s3a_a4`, `s3a_migration`, and the archived
`s3a_migration_aborted_freeze_race`).

## 6. Reproducing

```bash
# 1. the ledger's non-invasiveness (§8), from S2's existing recording
python3 traceevo/s3a_regression.py --cases 0-9 \
  --recording S1_runs/t1_dev10/llm_responses.jsonl --tag s3a_ledger

# 2. the A4 recheck (§4) -- deterministic replay, no live calls
python3 traceevo/s3a_a4_recheck.py --cases 5,8

# 3. freeze, then the blind audit (§6). Order matters.
python3 traceevo/s3a_freeze.py --write S3A_migration/rule_freeze_manifest.json
python3 traceevo/s3a_migration.py \
  --run-dir S3A_runs/s3a_migration/traceevo/runs/migration_holdout \
  --out S3A_migration --cases 10-29 \
  --freeze-manifest S3A_migration/rule_freeze_manifest.json
python3 traceevo/s3a_freeze.py --verify S3A_migration/rule_freeze_manifest.json

# 4. tests and the deliverable check
python3 -m pytest traceevo/tests -q       # 133 passed
python3 traceevo/s3a_package.py --out S3A_migration --check
```

Step 3's live run needs `TRACEEVO_API_KEY`; the other three do not.

## 7. Stopping

S3A is complete and stops here. Awaiting review. §2 above lists the two
decisions I would not make unilaterally — the zero-margin PASS, and whether
S3B is to be designed around `Validated Failure` only or the project is to
acquire labels.
