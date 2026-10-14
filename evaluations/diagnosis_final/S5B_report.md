# TraceEvo S5B report — final held-out stage

Per `TraceEvo_S5A_Review_S5B_Final_Heldout_v1.md`. One pass over case 80-137
under Core-v1, sealed, then gold, then the frozen judge, the Gate, the
controlled anomaly protocol on copies, the ablation and the overhead.

**Verdict: `FINAL_EVIDENCE_COMPLETE`** — all ten of §20's conditions pass.

Backbone frozen at `deepseek-v4-flash` throughout. Memory injection, Failure
Memory activation, role adaptation and ATGG were OFF for every run and the
artifacts say so (0 injection records, 0 guard actions, 0 role profile reads).
No rule file changed: `s5b_freeze.py --verify` reports 0 drift across the 27
frozen files.

Two things this stage did that the review should look at directly, both
recorded in `failure_cases.md`: **the block was started twice** (the first
pass over case 80-82 is preserved under `heldout_original_superseded/`, not
deleted), and **three re-runs fall outside §5.2's list** because
`case-104/107/121` died on the planner naming a non-existent expert rather
than on infrastructure.

---

## 1. Did all 58 cases run per the freeze? (§22 Q1)

Yes. 58 sealed, 58 verified on recomputation, 0 rejected. 1,568 events, 2,160
model calls, all chains verify, all delta replays pass, all traces complete.

61 attempts for 58 cases: three cases were retried once each (Q2 below). The
attempt history is in `attempt_history.jsonl` and is audited in
`run_audit.json`.

## 2. Were there infrastructure errors requiring a re-run? (§22 Q2)

**No — and that is a finding, not a clean bill.** Three re-runs occurred, and
`run_audit.json` classifies all three as `MODEL_OUTPUT_FAILURE`:

```
case-104, case-107, case-121 — attempt 1 died with
ValueError: Not founded, current list：['Laboratory_Physician', 'Pathologist', 'Radiologist']
```

The planner chose an expert that is not in the expert list and
`central_agent.act()` raised. §5.2 permits a re-run only for network
interruption, API 5xx, unexpected process exit or filesystem failure. A
model's own output ending a case is not on that list.

The three retries are **inside** the block; nothing was excluded, re-sealed or
dropped. The question §5.2 raises — whether such a case should be recorded as
incomplete rather than retried — is left to the reviewer, and the choice to
leave it there was deliberate: deciding it after seeing that the retries
succeeded is the situation §5.2 exists to prevent.

The same section records that `s5b_run.py`'s own `rerun_reason` field labelled
all three successful retries "infrastructure". That field was written from
`attempt > 1` rather than from whether the attempt failed. It is superseded by
`run_audit.json` rather than edited in place, because the runner was mid-pass.

One further context item: `on_dispatch_failed` is the hook the A3 injector
manufactures. This block produced roughly 5% natural A3-shaped run-time
failures.

## 3. Any algorithm or protocol bug that invalidates the block? (§22 Q3)

No. `final_evidence_complete`, and specifically:

* no P0-P3 rule implementation change — `s5b_freeze.py --verify` reports 0
  drift across the 27 frozen rule files;
* no Gate logic change — the Gate's decisions come from
  `experience_gate.evaluate`, unmodified;
* no audit statistic change — the audit modules are byte-identical to the
  freeze;
* no premature gold access — the access log records the first case-80 read at
  `04:34:52Z`, after the freeze at `04:34:18Z`, and the pipeline module reaches
  `gold_by_case_index` only after `check_seals`;
* gold leakage 0 (Q18).

Three driver scripts were edited *after* the traces were sealed while fixing
FAILURE 5/6/7 in the Gate stage, two of them after a first Gate run had been
seen. `driver_drift_report.json` lists every post-freeze edit with its
timestamp and whether it preceded the first seal. They are reporting-layer
fixes — which file the verdicts are read from, string-vs-bool comparison,
stale outputs — and the correct values are fixed by the frozen Gate's own
code, but the timing is disclosed rather than minimised.

## 4. Held-out Match / Relevant (§22 Q4)

| metric | mean | 95% CI | denominator |
|---|---|---|---|
| GoS Match | **0.086** | [0.017, 0.172] | 58 scored cases |
| GoS Relevant | **0.379** | [0.259, 0.500] | 58 scored cases |

Score distribution: 5 exact · 17 broad-category · 36 neither · 0 unparsed.

Case-level bootstrap, 10,000 resamples, seed 20260920 (§16). These come from
the frozen `gos/evaluate_Medical_scoring.py` — its `build_group_prompt` and
`parse_scores` are imported and driven, not reimplemented.

**These are low, and they are a property of the GoS pipeline on this block,
not of TraceEvo.** S5B ran no behaviour experiment and makes no claim that the
method improves diagnosis. The number is reported because §6.1 requires it.

## 5. Outcome verdict distribution (§22 Q5)

```
verified_success    7
verified_incorrect 31
uncertain          20
```

This is the first block in four with a usable positive class. The three
preceding blocks produced 0, 1 and 0 verified successes in ten cases each.
S5B's is 7 in 58.

**Not the same instrument as Match.** 5 cases scored 2 and 7 were verified
successes; §6.1 forbids reading one as the other, and the two judges have
different prompts and different rubrics.

## 6. Held-out evidence tier distribution (§22 Q6)

| | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| nodes | 317 | **4** | 438 | **16** |
| used in reasoning | — | 3 | 187 | 14 |

The same shape as every previous block — P0 and P2 carry the graph — but with
two differences worth naming: **P1 reappears** (4 nodes, 3 used) after being
absent from several blocks, and **P3 is an order of magnitude more common**
than in any previous block (16 vs 0-2).

## 7. Natural P3 frequency and P3-used (§22 Q7)

16 P3 nodes across the block, 14 of them used in reasoning. That is 16/58 =
0.28 P3 nodes per case, against 0.25 in the S4B cohort and 0.2 in S5A.

ATGG was OFF for the whole stage, so this is an observation and not a trigger.
It is nonetheless the first block where the guard's trigger condition is
comfortably present — which does not reopen ATGG (its demotion was for
absence of natural *efficacy* evidence, and S5B ran no efficacy experiment)
but is worth recording as a fact about the corpus.

Twelve of the 58 cases carry a P3 node used in reasoning
(`84, 85, 90, 93, 102, 110, 118, 121, 124, 125, 128, 133`). The pre-registered
case study (§15) is the smallest of them: **case-84**, one P3 node, one used,
outcome `uncertain`.

## 8. Clean alert rate (§22 Q8)

| alert | numerator / denominator | rate | 95% CI (Wilson) |
|---|---|---|---|
| Clean trace alert | 16 / 58 | **0.276** | [0.178, 0.402] |
| Clean evidence alert | 12 / 58 | **0.207** | [0.123, 0.328] |

Detected types on clean traces: **A1 ×12, A4 ×7**.

The A4 finding is the same one S5A flagged: A4 is the class §7.1 demoted to a
boundary case study, and it is the class that occurs *naturally* in this
corpus. For A1, twelve of fifty-eight clean traces raise an evidence-tier
finding, which is what pulls A1's precision to 0.826 in Table 2.

§6.3 is explicit that a genuine finding on a natural trace still counts as an
alert, and nothing was removed from this denominator for looking like a real
problem.

## 9. Controlled anomaly detection (§22 Q9)

On copies; the sealed originals verified unchanged by hash afterwards
(`originals_unchanged: true`).

| class | N | precision | recall | F1 | secondary |
|---|---|---|---|---|---|
| A1 unsupported evidence | 58 | 0.826 | 0.983 | 0.898 | 40 |
| A2 misbinding / tampering | 54 | 1.000 | 0.889 | 0.941 | 0 |
| A3 invalid capability invocation | 58 | 1.000 | 1.000 | 1.000 | 0 |
| A5 trace tampering | 232 | 1.000 | 1.000 | 1.000 | 0 |
| **Macro by class** | | 0.957 | 0.968 | **0.960** | |
| **Micro** | 402 | 0.971 | 0.983 | **0.977** | |

Case-cluster bootstrap (case_id as the sampling unit, §8.2): macro F1 CI
**[0.943, 0.975]**, micro F1 CI [0.967, 0.985].

Denominators are the N column, not 58. §7.2's frozen specification gives A1,
A2 and A3 one injection per case (A2 alternating between its two frozen
variants by case index) and A5 all four frozen tamper variants; injections the
injector declines are recorded in `anomaly_not_applicable.json` as
`NOT_APPLICABLE` rather than forced. A2's denominator is 54 rather than 58
because four cases refused that variant.

**A1's precision is the honest number to quote.** It is 0.826 because twelve
clean traces carry a genuine A1 finding; §8.3 keeps those in the primary
precision denominator deliberately, because a finding on a clean trace is a
false positive of the detector however real the underlying oddity is.

## 10. Observable-root Top-1 (§22 Q10)

**0.978** (393/402) over every scored injection, against 0.980 on S5A's
validation block.

Latent-root exact accuracy is reported separately and only where it is
answerable: **0.969** (277/286). The 116 injections excluded are the delete
and truncate variants, where the latent root is an event that no longer exists
— §9 forbids requiring a system to localize something that was removed.

## 11. How secondary anomalies are handled (§22 Q11)

40 secondary findings, each a detection of type T on a trace injected with
T′ ≠ T: A1 ×40 on A2/A3/A4 traces (unsourcing a record genuinely leaves
downstream Evidence unsourced — a real consequence, not an error), A4 ×20.

They are reported as their own count and type list and are **not** counted as
primary-class false positives. `precision_strict`, which does count them, is
carried in `anomaly_class_metrics.csv` for continuity and is not the headline.

## 12. How many verified successes on the final held-out block? (§22 Q12)

**7.** Of those, 5 are also gold-safe (`case-83, 86, 108, 109, 135`) — they
clear the six conditions and are usable as positives.

§10.4's first branch therefore applies:

```
HELDOUT_POSITIVE_CLASS_ADEQUATE   (verified_success >= 5)
```

## 13. Correct-but-poisoned denominator (§22 Q13)

**19** applicable twins built from the 5 gold-safe verified successes, across
the four frozen poison kinds (PZ1 5, PZ2 4, PZ3 5, PZ4 5; one PZ2 twin was
`NOT_APPLICABLE`).

## 14. Positive-branch downgrade (§22 Q14)

**Not required.** The branch is the adequate one, so the held-out
positive-class figures are reported with Wilson intervals and no downgrade
applies. The intervals are still intervals over 19 twins, and 0/19 has an
upper bound of 0.168 — the result is not "no admission" but "fewer than about
one in six".

## 15. Incorrect-clean Gate comparison (§22 Q15)

n = 22 incorrect-clean candidates (verified_incorrect with no audit finding at
all — "clean" means the audit found nothing, not that it found nothing
serious).

| rule | admitted | rate | 95% CI |
|---|---|---|---|
| Naive | 22/22 | 1.000 | [0.851, 1.000] |
| OutcomeOnly | 0/22 | 0.000 | [0.000, 0.149] |
| **AuditOnly** | **22/22** | **1.000** | [0.851, 1.000] |
| IntegrityOnly | 22/22 | 1.000 | [0.851, 1.000] |
| **TraceEvo** | **0/22** | **0.000** | [0.000, 0.149] |

And the positive class (§10.3), 19 twins:

| rule | admitted | rate | 95% CI |
|---|---|---|---|
| Naive | 19/19 | 1.000 | [0.832, 1.000] |
| **OutcomeOnly** | **19/19** | **1.000** | [0.832, 1.000] |
| AuditOnly | 2/19 | 0.105 | [0.029, 0.314] |
| IntegrityOnly | 0/19 | 0.000 | [0.000, 0.168] |
| **TraceEvo** | **0/19** | **0.000** | [0.000, 0.168] |

**This is C3's two-counterexample argument, and on held-out data it is
clean.** Each single-signal rule fails on exactly one class: OutcomeOnly
admits every poisoned twin because the twin inherits the original's
`verified_success`, and AuditOnly and IntegrityOnly admit every
incorrect-clean candidate because a wrong answer from a clean process looks
clean. Only the joint rule blocks both. Every single-signal rule is at 1.000
on one of the two populations; TraceEvo is at 0.000 on both.

The §11 rates make the same point from the other side. Admission precision is
the share of what a rule admitted that was a genuine clean verified success:

| rule | admission precision | validated success retention | poison admitted |
|---|---|---|---|
| Naive | 5/77 = 0.065 | 5/5 = 1.000 | 19/19 |
| OutcomeOnly | 5/26 = 0.192 | 5/5 = 1.000 | 19/19 |
| AuditOnly | 5/44 = 0.114 | 5/5 = 1.000 | 2/19 |
| IntegrityOnly | 5/58 = 0.086 | 5/5 = 1.000 | 0/19 |
| **TraceEvo** | **5/5 = 1.000** | 5/5 = 1.000 | **0/19** |

Every rule retains all five genuine successes, so retention does not separate
them. What separates them is what else they let through: TraceEvo's 5
admissions are 5 true positives and nothing else, where IntegrityOnly reaches
the same poison record only by admitting 53 other things.

## 16. Does the ablation support each module's designed role? (§22 Q16)

§12/§13's module × capability matrix, offline on copies, no LLM re-run.

| arm | A1 | A2 | A5 | EvidenceTrace | RootLoc | ParserBoundary |
|---|---|---|---|---|---|---|
| Full | 0.898 | 0.941 | 1.000 | 0.985 | 0.978 | 1.000 |
| w/o ProvenanceTier | **0.000** | 0.941 | 1.000 | 0.861 | 0.841 | 1.000 |
| w/o CrossGraph | 0.898 | **0.699** | 1.000 | 0.944 | **0.930** | 1.000 |
| w/o Integrity | 0.898 | 0.941 | **0.000** | 0.480 | 0.401 | 1.000 |
| w/o Ledger | NOT_SCORED | NOT_SCORED | NOT_SCORED | NOT_SCORED | NOT_SCORED | 1.000 |

| arm | designed for | moved | not moved |
|---|---|---|---|
| w/o ProvenanceTier | A1 | A1 | — |
| w/o CrossGraph | A1, A2, RootLoc | A2, RootLoc | A1 |
| w/o Integrity | A5 | A5 | — |
| w/o Ledger | ParserBoundary | — | ParserBoundary |

**Yes for three modules, and one negative result reported as one.** Removing
the evidence tier removes A1 entirely; removing the integrity audit removes A5
entirely; removing the binding layer costs A2 0.24 and root localization 0.05.

Two honest caveats. §12 named A1 among the capabilities to *evaluate* for the
binding arm and A1 did not move — the tier audit does not depend on the
binding annotations on this corpus. And the ledger arm did not move its
designed capability (A4 parser-boundary detection stayed at 1.000), so on this
evidence the ledger is not necessary for it. Both are reported in
`not_moved` rather than folded into a boolean, and §13 does not require every
module to lower an aggregate.

`NOT_SCORED` marks the four capabilities the ledger was never designed to
carry. §12 says not to average it into the same total as A1/A2/A5, and a zero
there would report a failure nobody tested for.

## 17. Local TraceEvo overhead (§22 Q17)

Per case, median over 58, on this machine:

| quantity | median | IQR | p95 |
|---|---|---|---|
| event logging | 0.25 ms | 0.19–0.35 | 0.45 |
| ledger | 0.19 ms | 0.15–0.28 | 0.32 |
| graph delta | 0.35 ms | 0.21–0.55 | 0.76 |
| audit | 9.70 ms | 6.26–16.43 | 22.75 |
| gate | **12.2 µs** | 10.8–14.0 | 15.4 |
| **TraceEvo local total** | **10.45 ms** | 6.91–17.69 | 23.92 |
| storage | 0.85 MiB | 0.67–0.99 | 1.04 |

LLM/API, reported separately and used for no claim: 2,160 model calls, 29,489 s
wall clock total (median 400 s per attempt) — the gateway was observed hanging
for minutes during S4B, so this measures the network as much as the method.

**Tokens are reported, not declared unavailable.** 6,940,081 input +
4,169,188 output = **11,109,269 tokens**, summed from the transport's own log
lines. S5A reported `TOKEN_UNAVAILABLE` and the numbers were there. One
residual inconsistency is stated rather than smoothed: the log carries 2,295
calls with usage against 2,160 ledger rows, and the two instruments are not
reconciled.

## 18. Is gold leakage zero? (§22 Q18)

**Yes: 0.** 58 candidates scanned against their own case (0 leaks) and against
every other case in the block — 3,306 cross-case pairs — with 0 leaks. The
scan is non-vacuous: there were 58 candidates to scan.

§5.1 step 12 held throughout: candidates were written to a file and injected
into nothing. `TRACEEVO_MEMORY=0`, and the artifacts record 0 injection
records, 0 failure-memory activations, 0 guard actions, 0 role profile reads.

## 19. Claims the held-out block supports (§22 Q19)

Four, and the matrix is `final_claim_evidence_matrix.md`:

| claim | held-out |
|---|---|
| Dual-graph traceability | 58/58 seals verify, all replays pass, all chains intact |
| Controlled anomaly detection | 402 injections, macro-F1 0.960 [0.943, 0.975] |
| Observable-root localization | 0.978 (393/402) |
| Incorrect-clean blocked by joint gate | TraceEvo 0/22, AuditOnly 22/22 |
| Correct-but-poisoned blocked | **TraceEvo 0/19, OutcomeOnly 19/19** |

The last one is new: it is the first block where this claim has held-out
evidence rather than only development evidence.

## 20. Claims still supported only by development data (§22 Q20)

**None of the four core claims.** That is the headline difference between S5B
and S5A. S5A stopped with `GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION` and the
two-counterexample argument resting on S3C's development twins; this block
produced the positive class and the argument now runs on unseen data.

## 21. What the first paper should claim (§22 Q21)

The four rows above, framed per §2 as `Trustworthy Experience Consolidation`:
TraceEvo builds fine-grained replayable provenance over GoS reasoning, binds
it to the causal graph, detects controlled collaboration anomalies, localizes
the observable failure seam to 0.978, and turns audit results into traceable
Success/Failure/Review/Reject partitions whose joint rule is the only one that
blocks both counterexamples.

## 22. What the first paper must not claim (§22 Q22)

Unchanged from §2 and not affected by this block's results:

* Success Memory improves diagnostic accuracy — no experiment shows it; the
  S3B probe had M0 (no memory) highest on both Match and Relevant.
* Role-specific routing is effective — S4A `NO_ROLE_SPECIFIC_ADAPTATION`.
* ATGG improves natural task performance — S4B `LOW_TRIGGER_DENSITY`, never
  evaluated for efficacy. This block's natural P3 rate does not change that:
  a higher trigger density is not an efficacy result.
* TraceEvo continuously improves medical diagnostic performance through
  online self-evolution — S5B ran no behaviour experiment.

And one this stage adds, from its own results: **detection performance is
consistency with this codebase's own fault model, not detection in the wild.**
The 402 injections were made by `traceevo/audit/injector.py`; an independent
fault model would exercise failure modes nothing here generates.

---

## What this block does and does not establish

**Established.** Core-v1 runs end to end on 58 unseen cases with clean seals,
clean replays and zero gold leakage. Controlled anomaly detection generalizes
to the unseen block (macro-F1 0.960) and observable-root localization holds at
0.978. The Gate's positive branch is validated on held-out data for the first
time: 0/19 poisoned twins admitted against OutcomeOnly's 19/19, and 0/22
incorrect-clean against AuditOnly's 22/22. Three of four modules are shown
necessary for their designed capabilities. Local overhead is ~10 ms/case and
storage under 1 MiB/case.

**Not established.** That detection generalizes beyond this fault model. That
the method improves diagnosis — no behaviour experiment was run, and Match
0.086 is a property of the GoS pipeline on this block. That any of the four
demoted modules works. That a case dying on an invalid planner selection
should be retried rather than recorded as incomplete — §5.2 raises that
question and this stage did not answer it.

**Unchanged.** The reusable success corpus is now 7 verified successes (5
gold-safe) from this block alone. S4A's role finding and S4B's ATGG finding
stand. The 58-row cross-case review queue remains `UNREVIEWED`.

---

## §15's pre-registered case studies

Selection rules were frozen before case 80 was read; each record names the
rule that fired and whether a fallback was needed.

| slot | selected | rule |
|---|---|---|
| Dual-graph normal example | **case-80** | the pre-registered case, no fallback needed |
| Natural P3 example | **case-84** | smallest case_id with a P3 node used in reasoning |
| Controlled anomaly path | **case-80 A1** | the pre-registered case, no fallback needed |
| Experience Gate case | **case-83** | smallest case_id with a heldout verified_success; gold-safe, `eligible` |

The dual-graph example is the pre-registered case 80 in both slots, so no
fallback was used and no case was chosen by its result. Figures are in
`figures/`, selections in `case_studies/selection.json`.

---

## §20's ten conditions

| # | condition | result |
|---|---|---|
| 1 | 58/58 executed per the frozen protocol, or infrastructure failures recorded | ✅ 58 sealed, 3 retries recorded with causes |
| 2 | no algorithm or protocol modification | ✅ 27 rule files, 0 drift |
| 3 | no systematic seal / ledger / replay break | ✅ all complete, all chains, all replays |
| 4 | gold leakage = 0 | ✅ 0 over 58 candidates × 3,306 pairs, non-vacuous |
| 5 | controlled anomaly results fully computable | ✅ 402 injections, macro-F1 with case-cluster CI |
| 6 | Gate's usable denominators reported truthfully | ✅ every rate with numerator/denominator and Wilson CI |
| 7 | positive-success scarcity did not change a rule | ✅ branch taken correctly; judge hash matches freeze |
| 8 | ablation recomputable from artifacts | ✅ originals unchanged, all five arms scored |
| 9 | final tables generatable by script | ✅ 6/6 by `s5b_tables.py` |
| 10 | claim matrix consistent with the real evidence | ✅ filled from artifacts |

**`FINAL_EVIDENCE_COMPLETE`.**

## How to check this without trusting the summary

1. `python3 -m pytest S5B_final_heldout/tests/test_s5b.py -q` — 26 tests,
   including that the freeze holds real thresholds, that the runner refuses a
   development case, that no module can open one, and that the cluster
   bootstrap resamples cases rather than mutations.
2. `Final_Core_v1_freeze_manifest.json` + `s5b_freeze.py --verify` — the frozen
   method and the 27-file drift check.
3. `run_audit.json` — every attempt, its exception, and whether §5.2 permits it.
4. `driver_drift_report.json` — every post-freeze edit by name and timestamp.
5. `discarded_pass.json` — the first pass over case 80-82, kept rather than
   deleted.
6. `failure_cases.md` — fifteen failures.
7. `anomaly_detection_results.csv`, `root_localization_results.csv` — every
   injection and every root, latent and observable in separate columns.
8. `gate_final.json`, `gate_baseline_metrics.csv` — every §11 rate with its
   numerator, denominator and CI.
9. `pass_conditions.json` — §20's ten conditions with the artifact behind each.
10. `reproduce_final.sh` — the whole stage in order.

## Artifacts

§21's list is complete. In addition: `pipeline_summary.json`,
`diagnostic_summary.json`, `clean_alert_summary.json`, `anomaly_headline.json`,
`ablation_summary.json`, `overhead_summary.json`, `gate_final.json`,
`run_audit.json`, `driver_drift_report.json`, `discarded_pass.json`,
`claim_matrix.json`, `tables_manifest.json`, `figures_manifest.json`,
`attempt_history.jsonl`.
