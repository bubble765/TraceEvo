# TraceEvo S2 report — observable execution state, anomaly audit, propagation tracing

Backbone: `deepseek-v4-flash` (fixed for all phases). Data: Dev-Small cases 0–9
only. Cases 10–137 untouched.

**S2 and S2.1 are both complete.** The live end-to-end case studies that S2.1
asked for have now run: one A3 live reproduction and two A4 response
substitutions, all with the fixed backbone. `case_studies/` holds the artifacts
and `case_studies/case_study_summary.md` the §7 table.

The short version: both A4 injections reached the pipeline (`prompt_changed =
true`) and both stopped at **L0** — the fabricated text never became a graph
node, an event, or report text. §8 accepts this, and the reason is structural
rather than sampling noise (§Q13).

---

## Summary of the §9 gates

| gate | required | measured | |
|---|---|---|---|
| Clean Trace FPR | ≤ 10% | **10.0%** (1 of 10 traces) | ✅ (at the boundary) |
| Clean Evidence FPR | ≤ 10% (ideal ≤5%) | **1.12%** (1 of 89 nodes) | ✅ |
| Macro anomaly F1 (primary) | ≥ 0.80 | **0.988** | ✅ |
| Observable-root Top-1 localization | ≥ 0.80 | **1.00** (108/108) | ✅ |
| Propagation-path Recall | ≥ 0.80 | **1.00** | ✅ (see Q7 caveat) |
| A5 tamper detection | = 100% | **100%** (40/40) | ✅ |
| Dependency DAG consistency | = 100% | **100%** (79/79) | ✅ |

### Per-class, three quantities kept apart (S2.1 §2.1)

`precision` / `F1` below are **primary detection**: did the auditor find the
anomaly that was injected, with false positives counted only on clean traces.
`secondary` counts detections of this type on traces injected with a *different*
type — real consequences observed on a multi-anomaly trace, not errors.
`F1 (strict)` keeps the old single-label arithmetic for continuity and is
**not** the headline number.

| type | n | precision | recall | F1 | secondary | F1 (strict) |
|---|---|---|---|---|---|---|
| A0 silent degradation | 20 | 1.00 | 1.00 | **1.00** | 0 | 1.00 |
| A1 unsupported evidence | 10 | 0.91 | 1.00 | **0.95** | 6 | 0.74 |
| A2 misbinding / tampering | 19 | 1.00 | 0.95 | **0.97** | 0 | 0.97 |
| A3 invalid dispatch | 10 | 1.00 | 1.00 | **1.00** | 0 | 1.00 |
| A4 corrupted analysis | 10 | 1.00 | 1.00 | **1.00** | 0 | 1.00 |
| A5 trace tampering | 40 | 1.00 | 1.00 | **1.00** | 0 | 1.00 |
| **macro** | | **0.985** | **0.991** | **0.988** | 6 | 0.952 |

---

## Q1 — Can `success` / `degraded` / `failed` be recorded reliably without changing GoS behaviour?

Yes. Three tests pin the three outcomes of the same code path, each asserting
the upstream-visible behaviour as well as the record: the fallback text must be
byte-identical to upstream's, and the invalid-dispatch `ValueError` must still
propagate. T1 non-invasiveness was re-run after these changes: A vs B and B vs
C are both IDENTICAL, with the prompt-sequence hash unchanged at
`37a66e6112bac731`.

Two design points worth flagging:

- **`degraded` ≠ `failed`.** The budget-exhaustion path does not raise; upstream
  returns a fixed string and the run continues. Recording it as `failed` would
  misdescribe it. What was actually broken before was that the trace said
  *nothing*: an expert that gave up was indistinguishable from one that
  analysed.
- **`model_version` is deliberately `null`.** S1 wrote the model *name* into
  `model_version`. S2 splits `model_id` (a config value, reliable) from
  `model_version` (only when a provider reports one) and adds
  `version_observable` so no downstream claim can overstate it.
  342/342 events: `model_id=deepseek-v4-flash`, `version_observable=false`.

## Q2 — Clean-trace anomaly FPR

- **Trace level: 10.0%** (1 of 10 clean traces raises any finding) — exactly at
  the ≤10% gate, with no headroom. Reported as-is.
- **Evidence level: 1.12%** (1 of 89 Evidence nodes) — inside the ≤5% ideal.

The single finding is case 4 node-12, "Patient received acupuncture for midback
pain before symptom onset". It is a restatement of the case history in the
model's own words; its input coverage (0.43) falls under the 0.70 threshold.
It is left flagged rather than tuned away — see Q5 and `failure_cases.md` F1.

## Q2b — S2.1 §1 D1: `externally_grounded` and `lineage_complete`

The tier alone lets a downstream consumer read P2 as if it were external
evidence, so two more dimensions are now reported on every node (and written to
`evidence_boundary_analysis.csv`):

| dimension | meaning | measured over 89 Evidence nodes |
|---|---|---|
| `externally_grounded` | the content appears in the case input or in a retrieval record (P0 only) | **False for 37** (41.6%) |
| `lineage_complete` | there is a concrete recorded antecedent for the node's **content** | **False for 30**; true for input (32), bound record (21), expert ANALYSIS (6) |

Two consequences worth stating plainly:

- **P2 `expert_synthesis` is not external evidence.** It has a complete
  lineage (an ANALYSIS event that really happened) and it is not a fact from
  the repository. `externally_grounded = false`, `lineage_complete = true`.
- **Self-declared derivations have no content lineage at all.** An absence
  claim, a recommendation, domain knowledge or an agent inference is traceable
  only to "an agent asserted it" — `lineage_complete = false`. The execution
  lineage exists (the GRAPH_UPDATE is real), but that is a different claim and
  must not be read as grounding.

S3's Experience Gate must not give `P2 expert_synthesis` the same credibility
as P0; the two flags are what make that mechanically checkable.

> **Correction (S3A, 2026-09-18).** This row previously read "False for 27",
> which contradicts `evidence_boundary_analysis.csv`: the false cases are
> absence_claim 18 + agent_inference 6 + domain_knowledge 3 + recommendation 2 +
> none 1 = **30**, and 30 + 59 true = 89. The breakdown beside it was right and
> the total was not. Nothing downstream changes — the CSV was and is the source
> — but the count is corrected here rather than left to disagree with its own
> file.

## Q3 — Where S1's 41.6% `AGENT_GENERATED` Evidence actually went

S1 reported 37 of 89 Evidence nodes (41.6%) as `AGENT_GENERATED` and explicitly
refused to call that a fabrication rate. S2's tiering resolves them:

| tier | n | share | derivations |
|---|---|---|---|
| **P2 `DERIVED`** | 35 | 94.6% | absence claim 18, expert synthesis 6, agent inference 6, domain knowledge 3, recommendation 2 |
| **P1 `SOFT_GROUNDED`** | 1 | 2.7% | a candidate record above the soft threshold, not a match |
| **P3 `UNSUPPORTED_ASSERTION`** | 1 | 2.7% | the case-4 node above |

Whole-corpus distribution over 89 Evidence nodes: **P0 52** (input 32 +
record 20), **P1 1**, **P2 35**, **P3 1**.

The headline: S1's "41.6% have no retrieval source" decomposes into "94.6% of
those are legitimate derived content". The largest single class was *not* agent
inference — it was **absence claims (18)**, statements that a test or record was
unavailable. S1's absence regex caught almost none of them, which is precisely
how a 41.6% figure turns into a false fabrication rate if it is read as one.

## Q4 — Per-class Precision / Recall / F1

See the table at the top. Full matrix in `per_class_metrics.csv`;
injected-by-detected confusion in `matrix_summary.json`.

Three quantities are reported separately, per S2.1 §2.1, because collapsing
them produces a number that is worse than any of the three:

1. **Primary anomaly detection** — was the injected anomaly found. Macro
   precision 0.985 / recall 0.991 / F1 0.988.
2. **Clean false-positive rate** — 10.0% at trace level, 1.12% at Evidence
   level. Per type, `clean_fp_rate` is 0 everywhere except A1 (0.10).
3. **Secondary consequence findings** — 6, all of type A1, all on traces whose
   primary injection was A0/A2/A3/A4. These are reported as their own column.
   They were previously counted as A1 false positives; that is what took its
   strict precision down to 0.59 and made a working detector look broken.

## Q5 — Which anomaly class is hardest, and why

**A1** (primary F1 0.95; strict F1 0.74). With §2.1's separation applied, the
honest diagnosis is:

1. **Most of the apparent error was a metric artefact, not a detector defect.**
   6 of the 7 strict "false positives" are detections on traces whose primary
   injection was A0/A2/A3/A4. Degrading an ANALYSIS or tampering a record
   genuinely leaves downstream Evidence nodes unsourced, so the A1 rule firing
   there is correct behaviour on a multi-anomaly trace. Reported now as
   *secondary consequence findings*.
2. **One genuine clean-trace false positive remains**, and it must not be
   tuned away: case 4 node-12 needs `input_inference_min_coverage ≤ 0.29`,
   which is inside the natural cluster (0.00–0.29). Tuning it would be the
   overfitting §4.2 forbids and §9 forbids using to mask the problem.
3. **The P2 boundary is a judgement call by construction.** "Legitimate derived
   content" vs "unsupported assertion" is a semantic distinction; the tiers
   make it deterministic and auditable but not free.

One of the 6 secondary detections is still unexplained: an A1 firing on an A3
trace. The A3 failure aborts the task before any report, so no downstream
Evidence node should lose support. This is flagged for S3 rather than
explained away.

A2 and A4 were the hardest to *detect* during development; both needed
empirical correction (A2's "some other record matches better" rule missed 3 of
10; A4's sentence-level coverage flagged 74% of clean analyses). Both are now
at F1 ≥ 0.97.

**Per §9's instruction, no threshold was lowered to hide this.** The
false-positive taxonomy is in `failure_cases.md` F1.

## Q6 — Observable-root Top-1 localization accuracy

**1.00 (108/108).** For every detected anomaly, the auditor's most-confident
explanation names the root it is scored against.

**On the name.** Per S2.1 §2.2 this metric is reported as *Observable-root
Top-1 localization accuracy*, not as "exact injected root localization". The
two are different concepts and the distinction is load-bearing:

```text
latent injection root   where the damage was actually done
observable failure seam where a detector can see it in the surviving trace
```

They coincide for every injection except A5 `delete` / `truncate`, where the
latent root is an event that **no longer exists in the trace** — naming it is
impossible for any detector, not merely hard. Scoring against it capped the
metric at 0.79 for reasons that had nothing to do with localization quality,
so the ground truth was moved to the seam. Every trace now carries both ids
(`latent_root_event_id`, `observable_root_event_id`) and
`root_cause_results.csv` reports whether they coincide, so the correction is
auditable rather than absorbed.

`injection_spec.md` documents this and the tied-confidence convention.

## Q7 — Does the propagation path cover the annotated path?

**Structurally yes: affected-event recall = 1.00** against the DAG-derived
ground truth.

**This number must be read carefully.** Both the detector's downstream set and
the injector's ground truth are computed from the execution DAG — so 1.00 shows
the plumbing is wired correctly, not that the DAG is the true causal story.
§7 forbids reading execution dependency as model-internal causation, and
nothing here does.

The semantically meaningful measurement is `semantic_reach`, and it says
something less flattering:

| injection | reached a graph node | reached the report text |
|---|---|---|
| A1 (fabricated node) | 10/10 (by construction) | 0/10 verbatim |
| A4 (corrupted analysis) | **0/10** | 0/10 |

**A4 artifact-level corruption does not propagate into the graph**, because the
graph nodes come from the original `GRAPH_UPDATE` responses which do not
contain the fabricated text. The artifact mechanism by construction cannot
exercise A4 propagation; only the end-to-end study can. That study has now run
— see **Q13**: both cases are L0, for a mechanism that is structural rather
than sampling noise. The 0/10 above and the 0/2 in Q13 agree, which is worth
noting: two different mechanisms, the same answer.

`reaches_report_rate = 0.77` across detected anomalies. The 23% that do not
reach the report are the correct ones: A3 (10/10 — the task aborts before any
report) and A5 `truncate` (10/10 — the report was removed).

## Q8 — Which anomalies reach the final report, and which stop in the middle?

| anomaly | reaches the report | why |
|---|---|---|
| A0 (degraded analysis) | yes | the run continues; the fallback text feeds the next update |
| A1 (fabricated node) | yes, via the DAG | the node is wired into a hypothesis at the first update-graph |
| A2 `record_tampering` | 7/10 | unreached when the tampered record was read at the last step |
| A3 invalid dispatch | **no** | the task aborts; there is no report to reach |
| A4 corrupted analysis | yes, via the DAG | but see Q7 and Q13 — in the artifact matrix the corruption cannot reach the graph by construction, and in the end-to-end study it stops at L0 |
| A5 `truncate` | **no** | the report itself was removed |
| A5 `modify` / `delete` / `reorder` | mostly yes | the log is damaged but a report survives |

## Q9 — Does the dependency DAG have broken links or wrong parents?

**No: 79/79 traces consistent, 0 errors.** Over 342 clean events and 69
semantic injections, checked against nine parent rules plus step-locality,
topological order, dangling references and report reachability.

Two real defects were found and fixed during this work, both of which would
have produced silent wrong answers:

- **`on_seal_graph` anchored on stale events.** It used `ctx.last("DRILL_DOWN")`,
  which keeps returning a step-1 drill-down forever, so every later PLAN
  pointed at a superseded state change and deltas were attributed to unrelated
  events. Caught on cases 8 and 9.
- **Sealing no-op state changes.** A backtrack that fires with
  `deleted_levels=[]` (cases 2, 7, 8, 9) mutates nothing. It is now recorded as
  `applied: false`, the next PLAN correctly does not depend on it, and no empty
  delta is written. Reported as an *informational* finding (`noop_state_change`,
  4 occurrences) rather than an error.

The DAG check also caught a checker bug worth naming: iterating a dict yields
its keys, so `set(a_edges) - set(b_edges)` silently dropped the edge *type* —
the same class of bug S1 hit when the support→refute flip was invisible to
`diff_states`.

## Q10 — Is the S2 audit output enough to support S3's Experience Gate?

**Partly.** What S3 can rely on today:

- a per-anomaly record in the §6 schema, with root, severity, confidence, the
  detector rule, the affected event/node sets, and the path to the conclusion;
- root-cause localization at 1.00 and integrity detection at 100%;
- a capability policy that explains *why* a dispatch failed, with the near-miss
  name — the shape of finding an Experience Gate would act on.

What is missing before an Experience Gate should consume this:

- **Severity and confidence are hand-assigned constants per rule**, not
  calibrated. They are ordinals, not probabilities.
- **The A1 boundary is not adjudicated.** One clean-trace finding is left for
  human judgement; a gate that acts automatically would act wrongly on it.
- **A4 propagation is measured, not established** — L0 in both end-to-end cases
  (Q7, Q13). Not a gap in the measurement; a negative result about the class.
- **`tool_not_granted` has zero measured recall** — the dataset gives every
  expert the same single tool, so cross-role misuse cannot occur in it.
- **D1's two flags are not yet consumed by anything.** They exist so the Gate
  *can* distinguish derived content from external evidence; no Gate exists yet.

## Q11 — Which rules look overfit to Dev-Small and need re-checking in S3?

Ranked by how much they depend on this corpus:

1. **The absence/inference/knowledge regexes** (`cross_graph_binding.py`).
   Several patterns ("was not obtained", "have not been performed", "is a
   targeted therapy") were added because a *specific* Dev-Small node needed
   them. Highest overfitting risk in the package. The sensitivity sweep is
   flat for the numeric thresholds, but regex coverage is not measurable that
   way.
   **S2.1 §1 D2 freezes these rules as the S2 baseline.** No further
   case-specific regex will be added on Dev-Small, and the rules will not be
   tuned toward 0 FPR. S3 must run a **migration audit** on the unused
   Dev-Evolve subset *before* looking at the results: freeze the rules, run the
   audit, report clean Evidence FPR / P0–P3 distribution / absence-claim miss
   rate on the new data, and only then decide whether a more general semantic
   classifier is needed.
2. **`expert_synthesis_min_coverage = 0.5` and
   `input_inference_min_coverage = 0.7`.** The sweep is flat across 0.3–0.8 and
   0.5–0.9, which is reassuring for *variance* but does not show the thresholds
   are right for other corpora — only that this corpus has no cases near them.
   Only `soft_min_coverage` moves anything (one node, between 0.35 and 0.45).
3. **The narrative markers** ("concludes", "independently supports",
   "recommends") are English clinical-prose patterns.
4. **The claim-extraction rules for A4** were tuned against 44 clean analyses;
   the language-mixing limit (Q12 F4) is a known hole.
5. **Everything is per-domain.** The A3 policy is roster-generic, but the roster
   itself comes from the medical config; the IT/FailureDiagnosis domains were
   never used in S2.

The full sweep is `evidence_boundary_sensitivity.csv`, as §4.2 requires.

## Q12 — Success and failure cases

Two successes and five failures with diagnosis, in `failure_cases.md`.

---

## Q13 — S2.1: does an injected anomaly actually propagate?

The artifact matrix cannot answer this (D3: re-sealing edits text while the
downstream responses stay the original ones). The response-substitution studies
can: the recorded response for one call is replaced and the run continues
**live**, so GoS itself computes the following graph, prompts and report.

A3 is the live reproduction of the naturally discovered defect and is reported
in `case_studies/A3_case1/`. The two A4 cases, per §5:

| | A4-case8 | A4-case5 |
|---|---|---|
| §5.1 prompt changed | **yes** (`2686d649…` → `d67203e9…`) | **yes** (`ea797fcb…` → `4cd11d54…`) |
| §5.2 graph changed | yes (11 → 20 nodes) | yes (15 → 22 nodes) |
| §5.3 semantic level | **L0** | **L0** |
| §5.4 root localized | n/a — no event carries it | n/a — no event carries it |
| report token coverage of the injected text | 0.059 (clean 0.000) | 0.235 (clean 0.059) |

Reading the two "yes" columns against the two negative ones is the point of
§2.1's warning. `Graph changed = yes` is **not** propagation: once the
substitution is served, every later call is live instead of replayed, so the
injected graph is a fresh sample of the same case. What §5.3 tracks is the
fabricated *text*, and it appears in no node, no event and no report.

### Why both cases stop at L0 — the mechanism

This is the useful result, and it is reproducible across both cases:

1. The substituted response is served at the expert agent's **decision stage**
   and logged verbatim (`Step 1 Decision: {"type": 1, "decision": "analyze",
   "content": "Flow cytometry … 42 ng/mL …"}`).
2. That stage is parsed for `{type, decision}` only. The fabricated `content`
   field is **dropped**, and the decision flips from `retrieve` to `analyze`.
3. A second, **content stage** call then generates content for the flipped
   decision. Its prompt is built from `decision_result["decision"]` alone —
   `prompts/prompt_generation.py:333-334` — so the fabricated text is not in it
   either.
4. The content stage returns `retrieve`, contradicting the decision stage, and
   the step is discarded as a parse error. **No event is emitted for it.**

So the injection's entire footprint is one extra discarded step. Two
consequences:

- **Trace-only auditing cannot see this class of anomaly at all.** There is no
  event to localize, which is why §5.4 is `n/a` and not `no`. This is the same
  shape as the A5 truncation limit: a real defect that the event log cannot
  express.
- The decision/content disagreement is **not** an injection signature. It fires
  in the clean runs too — case 5's clean run had 3, its injected run 2; case
  8's clean run had 0, its injected run 1. Reporting the injected count alone
  would have credited the injection with a standing GoS behaviour. Both counts
  are in `case_studies/A4_case*/clean_vs_injected.json` as `footprint`.

§8 explicitly does not require L1/L2, and the recommendation it anticipates is
the right one: an anomaly class that GoS absorbs before it becomes state cannot
be a poisoning source for S3. A4-at-the-decision-seam is now measured as such
rather than assumed.

### What is *not* claimed

- Not claimed: A4 propagation is verified. It is measured, and it is L0.
- Not claimed: exact injected-root localization = 100%. The A5 delete/truncate
  roots are the **observable failure seam**; the latent injection root is
  recorded separately (`injector.Injection.latent_root_event_id`). The metric
  is *Observable-root Top-1 localization accuracy*, 108/108.
- Not claimed: the strict macro-F1 (0.952) is the headline. A1's strict
  precision of 0.91 is depressed by secondary findings that are *real*
  consequences of other injections; the primary macro-F1 is 0.988 and the clean
  false-positive rate is reported separately.

---

## Changes to GoS

`git_commit.txt` and `git_diff.patch`. No GoS reasoning logic was altered. The
S2 edits to the working copy are: `central_agent.py` (one `on_dispatch_failed`
hook before the existing raise), `expert_agent.py` (observational `tx_exit_cause`
tracking and the two `on_analysis` status arguments), and
`configs/TraceEvo_Configs.yaml` (model identity keys). All are no-ops when
`traceevo.enabled` is false.

## Test suite

118 tests in `tests/` (the copy in `S2_audit/tests/` runs standalone with `pytest S2_audit/tests`). The ones that matter most:

| file | pins |
|---|---|
| `test_execution_status.py` | the three §3.1 outcomes; the upstream text and exception are unchanged |
| `test_dependency_dag.py` | all nine §3.2 parent rules, one broken per test; step locality; cycles; report reachability |
| `test_evidence_auditor.py` | the P0–P3 cascade, and the clean-trace FPR gate as a test |
| `test_injector.py` | A0–A4 re-seal and replay; A5 does not; A5 truncation is invisible to the chain |
| `test_retrieval_binding.py` | record hashes; A2 tamper detection; the chain stays intact after an EVIDENCE_READ |
| `test_graph_replay.py` | typed edge deltas; support↔refute flips |
| `test_llm_replay.py` | positional record/replay |
