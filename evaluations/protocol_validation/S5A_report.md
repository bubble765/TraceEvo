# TraceEvo S5A report

Per `TraceEvo_S4B_Review_S5A_Method_Freeze_v1.md`. S5A froze Core-v1, ran the
full standard pipeline on case 70-79, ran the controlled anomaly protocol on
copies, drilled the Gate, and measured non-invasiveness and overhead.

**Verdict: `GO_S5B_FINAL_HELDOUT`** — all ten of §15's conditions pass.

No GoS behaviour was added; Memory injection, role adaptation and ATGG were off
for every run and the artifacts say so. Case 80-137 was not read.

---

## 1. Why `LOW_TRIGGER_DENSITY` is accepted, and what was not done (§18 Q1-Q2)

*(Q1)* S4B's negative result is valid and narrow. ATGG OFF was shown to restore
GoS exactly against a recording made before it existed; the positive controls
showed the `P3 + Evidence + support/refute` rule fires and that P0, P2,
Hypothesis and refines-only do not; and case 60-69's natural trajectories
contained **zero P3 nodes**. The pre-registered branch for that outcome is to
stop, and it was followed.

The result does not say ATGG would not work. It says the natural trigger density
in that block is too low to evaluate it, and §0 accepts exactly that reading.

*(Q2)* Neither tempting repair was taken:

* **Widening the P3 search into case 70-79** would have converted a validation
  block into a hunt for a rare event. §D2 forbids it and 70-79 was used for its
  stated purpose instead.
* **Promoting P1 or P2 to a hard guard** would have contradicted two findings.
  case-51 is a *verified success* whose decision path carried two P1 nodes, so
  blocking P1 refuses correct traces; and this block's decision paths are
  carried by P2 — hypotheses, absence claims, expert synthesis — which is
  legitimate reasoning. §D3 keeps the tiers as they are.

A third option exists and is noted rather than acted on: this is now the second
corpus in which P3 is rare while P1/P2 carry the decision path. That is a
question about what ATGG should guard, and it is exploratory work, not Core-v1.

## 2. What Core-v1 contains (§18 Q3-Q4)

*(Q3)* `Core-v1_freeze_manifest.json`, written before case 70 was read, names
both methods and maps each item to the module that implements it.

**Method-1, Dual-Graph Auditable Reasoning** — ProvenanceEvent, Model
Invocation Ledger, GraphDelta, Cross-Graph Binding, Evidence Provenance Tier,
Execution Dependency DAG, Integrity Audit, Evidence Audit, Observable-root
Localization, Propagation Tracing.

**Method-2, Audit-Gated Trustworthy Experience Consolidation** — Post-hoc
Outcome Verification, Experience Eligibility Contract, Validated
Success/Failure/Review/Reject, OutcomeOnly/AuditOnly/TraceEvo Gate,
Success/Failure Memory Partition, Gold Leakage Guard.

*(Q4)* Four modules are kept in the repository and demoted out of the first
paper's core: the role-specific Capability Profile, ATGG-v1, cross-case Success
Memory injection, and Failure Memory online activation. Each carries its
finding — S4A's `NO_ROLE_SPECIFIC_ADAPTATION`, S4B's `LOW_TRIGGER_DENSITY`, and
the S3B probe's M0 result for memory. They belong in Discussion, Negative
Findings and Appendix, and the manifest records them as decisions rather than
omissions.

## 3. The validation block (§18 Q5-Q8)

Ten cases, ten seals, ten verified, zero rejected. All 330 events, 456 ledger
calls and 10 delta chains replay clean.

### Natural evidence distribution

| | P0 | P1 | P2 | P3 |
|---|---|---|---|---|
| nodes | 58 | 0 | 93 | **2** |
| used in reasoning | — | 0 | 41 | **2** |

*(Q5)* The distribution is the same shape as every previous block: P0 and P2
carry the graph, P1 is absent, P3 is rare.

*(Q7)* **P3 reappears — twice, both in case-76, both used in reasoning.** Both
are genuinely unsourced (`derivation: none`):

* `node-9` — "Laboratory tests show thrombocytopenia with platelet count
  84 K/μL" — token coverage 0.167, below the frozen 0.35 soft floor.
* `node-14` — "The patient was still taking rifampin 300 mg daily when nausea,
  vomiting, fever, and abnormal laboratory results…" — coverage 0.0.

**ATGG was not enabled and this is not a trigger.** §18 Q7 asks for the
observation only. It is worth recording that the block *would* have exercised
the guard, unlike case 60-69 — a fact about the blocks, not about the guard.

### Clean alert rate

*(Q6)* Three of ten clean traces raised an alert — **Clean Trace Alert Rate
0.30** — and one raised an evidence-tier finding, **Clean Evidence Alert Rate
0.10**. The detected types were **A4 ×2 and A1 ×1**.

The A4 finding is worth naming: it is the class §11 demoted to a boundary case
study, and it is the one that occurs *naturally* in this corpus. In case-74 a
Radiologist analysis asserted "7 mg/dL indicates renal dysfunction" with no
source in the input or the retrieved records, and it reached the report.

### Integrity and localization

*(Q8)* **Stable.** All ten traces complete, all ten hash chains verify, all ten
delta replays pass, zero critical alerts. Observable-root localization is
reported per anomaly below.

## 4. Outcome and Gate (§18 Q9-Q10)

| verdict | n | cases |
|---|---|---|
| verified_success | **0** | — |
| verified_incorrect | 9 | 70, 72-79 |
| uncertain | 1 | 71 (`1\|1\|1`) |

Gate: 7 `manual_review`, 3 `failure_experience`, 0 `eligible`.

*(Q10)* **No new correct-but-poisoned example exists, and §13's second branch
applies: `GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION`.**

The block produced no verified success at all, so there is no positive class to
build twins from. §13 says explicitly what to do: do not modify the judge, do
not search for cases, report the named absence. All three were done.
`gate_validation_results.csv` records the absence rather than an empty table,
and S3C's 16 twins over case 10-29 remain the development evidence for the
two-counterexample argument.

This is the third block with zero or one verified success (30-39: 0/10,
50-59: 1/10, 70-79: 0/10) against 5/20 in the migration block. The reusable
corpus remains **4**.

## 5. The controlled anomaly protocol (§18 Q13)

108 injections attempted on copies, 98 produced, 12 withheld with reasons.
**`originals_unchanged: true`** — the sealed traces were verified by hash after
injection, because "we only read it" is the claim that quietly stops being true.

| class | n | tp | fn | precision | recall | F1 | secondary |
|---|---|---|---|---|---|---|---|
| A1 unsupported evidence | 10 | 10 | 0 | 0.909 | 1.000 | 0.952 | 6 |
| A2 misbinding / tampering | 18 | 16 | 2 | 1.000 | 0.889 | 0.941 | 0 |
| A3 invalid capability invocation | 10 | 10 | 0 | 1.000 | 1.000 | 1.000 | 0 |
| A5 trace tampering | 40 | 40 | 0 | 1.000 | 1.000 | 1.000 | 0 |
| **primary total** | **78** | **76** | **2** | **0.987** | **0.974** | **0.981** | **8** |

**Reported groups, per §12:**

* **Clean** — alert rate 0.30 trace, 0.10 evidence.
* **Primary injected** — the table above. The one false positive is on a clean
  trace (A1), and the 8 secondary findings are detections of a different class
  on an injected trace, reported as their own count and **not** counted as false
  positives. `precision_strict` (which does count them) is 0.526 for A1 and is
  in `per_type` for continuity; it is not the headline.
* **Secondary** — 8, each a real downstream consequence.
* **Root** — observable-root top-1 accuracy **0.980** over 98 scored
  injections. For 78 of the 98 the latent root equals the observable root; for
  the other 20 — all A5 delete/truncate, where the latent root is an event that
  no longer exists — the two are kept apart in
  `root_localization_results.csv` and the metric is never called "exact
  injected root localization".
* **Case study** — 10 A4 rows, reported under §11's boundary heading.

**A0 is not in the headline.** §11 keeps A0 only if reliable degraded/failed
instrumentation exists; S4A established that no event in this pipeline carries
either status. The `status_marked` variant was **refused outright** because it
writes `status="degraded"` — manufacturing the precondition it requires. The
`silent` variant ran and all 10 were detected, but they are reported in
`conditional_not_in_headline` and excluded from the precision/recall/F1 above.
`failure_cases.md` FAILURE 2.

*(Q13)* Repeatability holds: §15's condition 6 re-ran detection on a sample of
the injected copies and every field matched.

## 6. Non-invasiveness and overhead (§18 Q11-Q12)

*(Q11)* **Core ON vs Core OFF is identical.** Three strict replays of a
recording made on 2026-09-17: Core OFF, Core ON, Core ON again.

```
core_off_vs_on_identical : true    (prediction, report, GoS final graph)
core_on_repeatable       : true
strict_replay_clean      : true
runs_completed           : true
```

Strict replay raises on a differing prompt or an extra LLM call, so the prompt
and response sequences match by enforcement rather than by measurement; the
comparison adds that both arms produced the same prediction, report and graph
from the same recording.

*(Q12)* Local processing cost, per case, median over ten:

| quantity | value |
|---|---|
| audit | 15.8 ms |
| gate | **12.8 µs** |
| TraceEvo total | 15.5 ms |
| storage | 967 KiB |

**LLM network time is reported separately and is not part of any of these**,
per §14 — the gateway was observed hanging for minutes during S4B, so a
wall-clock figure including it measures the network as much as the method.
Token usage is `TOKEN_UNAVAILABLE`: the API does not report per-call usage and
§14 forbids estimating it.

## 7. §15's ten conditions (§18 Q14-Q15)

| # | condition | result |
|---|---|---|
| 1 | case 70-79 10/10 complete | ✅ 10 sealed, 0 crashed |
| 2 | 10/10 seals correct | ✅ 10 verified, 0 rejected |
| 3 | gold leakage = 0 | ✅ 0 over 3 candidates × 9 cross-case pairs, non-vacuous |
| 4 | no systematic audit/ledger/replay break | ✅ all complete, all chains, all replays |
| 5 | Core ON/OFF non-invasive | ✅ identical and repeatable |
| 6 | anomaly protocol repeats | ✅ sample re-run, all fields match |
| 7 | Gate procedure executable | ✅ absent-class branch taken correctly |
| 8 | no rule changed for the validation | ✅ 27 frozen files, 0 drift |
| 9 | every final table script-generatable | ✅ 9/9 have generators |
| 10 | case 80-137 unread | ✅ no module references it |

*(Q14)* **All nine final tables are generated by script** from sealed artifacts
— `final_table_generator_check.md` lists each with its generator. No table
carries a number that exists only in prose.

*(Q15)* **`GO_S5B_FINAL_HELDOUT`.** Ten of ten.

## 8. What this does and does not establish

**Established.** Core-v1 is frozen, hashed and reconstructible; its thresholds
are the real ones. The pipeline runs end-to-end on an unopened block with clean
seals and clean replays. The controlled anomaly protocol is repeatable and
localizes to 0.980 top-1 on the observable root. The Gate executes and takes
its absent-class branch without being adjusted. Non-invasiveness holds by the
strongest test available. Local overhead is small and separated from network
cost.

**Not established.** That the Gate's positive branch works *on this block* — it
produced no positive class, so the two-counterexample argument still rests on
S3C's development data. That detection generalizes beyond the injection
procedure that generated the examples: precision and recall are measured
against injections this codebase makes, which is a statement about the
detector's consistency with its own fault model, not about detection in the
wild. That the method improves diagnosis — S5A ran no behaviour experiment.

**Unchanged.** The reusable corpus is 4. S4A's role finding and S4B's ATGG
finding stand. The 58-row cross-case review queue is still `UNREVIEWED`.

## 9. Recommendation

**`GO_S5B_FINAL_HELDOUT`**, on §15's ten conditions, with two things the review
should weigh before spending case 80-137:

1. **The success rate is the binding constraint, not the protocol.** Three
   consecutive blocks produced 0, 1 and 0 verified successes in ten cases each,
   against 5 in 20 for the migration block. S5B will run 58 cases; on the recent
   rate that is a handful of verified successes, and on the migration rate it is
   a dozen. The Gate's positive branch and any corrected-but-poisoned analysis
   for the final paper scale with that number, not with the 58.
2. **`GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION` is itself the finding S5B
   should be designed around.** S5A validated the protocol and could not
   validate the Gate's positive branch on an unopened block. If the final
   held-out block behaves like 30-39, 50-59 and 70-79, the paper's Gate
   evidence will again be development data — which is a defensible paper but a
   different one from the one planned, and it is cheaper to decide that now.

Both are corpus questions and neither is a reason to change Core-v1.

**S5A stops here.** No case 80-137 read; no role adaptation reopened; ATGG not
enabled; no memory injected or activated; the outcome judge not modified; S5B
not started.
