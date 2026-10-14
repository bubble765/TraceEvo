# TraceEvo S2 — review request

Per `TraceEvo_Research_Plan_v1.md` §13. **S2 stops here.** No Experience
Memory, no Experience Gate, no Capability Profile, no planner history
injection, no self-evolution, no experiments on cases 10–137. Those are
S3/S4 and none were started.

---

## 1. S2.1 — executed; here are the results

The command in §3 ran, with the fixed backbone and no changes to any audit
threshold, Evidence rule or piece of GoS reasoning:

```bash
python3 traceevo/s2_cases.py --out S2_audit/case_studies --a3-case 1 --a4-cases 8,5
```

Artifacts are in `case_studies/`, the §7 table in
`case_studies/case_study_summary.md`. It answers §5.1–5.4 for each A4 case.

### A3 — live reproduction of a naturally discovered defect (§4): passed

Everything §4 lists was verified from the trace and the run log, including the
original `ValueError` still being raised, `FINAL_REPORT` absent, the trace
flushed before the process died, and the auditor re-deriving the near miss
`Laboratory_Physician` from the trace alone. Detail in
`natural_failure_case.md` and `case_studies/A3_case1/`.

### A4 — the two substitutions: both **L0**, and both for the same structural reason

| | A4-case8 | A4-case5 |
|---|---|---|
| §5.1 prompt changed | yes | yes |
| §5.2 graph changed | yes (11 → 20 nodes) | yes (15 → 22 nodes) |
| §5.3 semantic level | **L0** | **L0** |
| §5.4 root localized | **n/a** | **n/a** |

**§5.1 and §5.2 are not the good news they look like, and §5.3 is not the bad
news it looks like.** Once the substituted response is served, every later call
is live rather than replayed, so the injected graph is a fresh sample of the
same case — `graph changed = yes` says the run diverged, not that the fabricated
text propagated. What §5.3 tracks is the fabricated text, and it appears in no
node, no event and no report.

**Why.** GoS's expert agent runs a decision stage and then a content stage
(`agents/expert_agent.py:79`, `:126`). The first is parsed for
`{type, decision}` only; the second's prompt is built from
`decision_result["decision"]` alone (`prompts/prompt_generation.py:333-334`).
So a fabricated analysis delivered at the decision stage is dropped before it
can become state — the decision flips `retrieve → analyze`, the content stage
disagrees, the step is discarded as a parse error, and **no event is emitted for
it**. Same mechanism in both cases. See `failure_cases.md` FAILURE 5.

### Two things a reviewer should check before accepting this

1. **§5.4 is `n/a`, not `no`, and I made that distinction in the code.** There
   is no injected ANALYSIS event, so there is no root for the auditor to name.
   Reporting "no" would claim a detection miss the detector never had a chance
   to make. `root_localized.applicable` was added for this; if you would rather
   the §7 cell read `no`, say so and it is a one-line change.
2. **The decision/content disagreement is not an injection signature.** It fires
   spontaneously — case 5's clean run had 3, its injected run 2. The result
   records both counts so the injection is not credited with a standing GoS
   behaviour. This was caught while writing the case up and corrected.

### What this means for S3 (not executed)

Per §8, L0 is a valid outcome, and the recommendation §8 anticipated is the one
the data supports: **do not use this anomaly class as an S3 poisoning source.**
An anomaly GoS absorbs before it becomes state cannot degrade stored experience.
More broadly, an anomaly that leaves no event is invisible to a trace-only
audit — the same limit A5 truncation already exposed — so the audit boundary
needs a second observability channel more than it needs more rules over the same
events.

**The one item I could not settle without a reviewer:** whether §5.4's `n/a`
above should be treated as S2.1 passing its §8 gate clause 4 ("auditor 对 A3/A4
root 均能定位到真实异常起点"). For A3 it plainly passes. For A4 the auditor had
no root to locate. My reading is that §8 clause 4 cannot bind when the anomaly
never became an event; a stricter reading would fail the gate. Flagged rather
than decided.

Everything else in §11 is delivered and reproducible from the artifacts.

## 1b. S2.1 §1 decisions, as implemented

| decision | implemented as |
|---|---|
| **D1** `expert_synthesis` stays P2, but P2 ≠ external evidence | `TierDecision` gains `externally_grounded` / `lineage_complete` / `lineage_source`, in `evidence_boundary_analysis.csv` and pinned by 6 tests. 37 of 89 Evidence nodes are not externally grounded; 27 have no content lineage. |
| **D2** absence regex frozen | No regex was added or changed in S2.1. The rules are the S2 baseline; S3 runs the migration audit on Dev-Evolve before anyone looks at the results. |
| **D3** artifact-level A4 may claim detection, not propagation | `injection_spec.md` states the permitted and forbidden claims; `S2_report.md` Q7 covers the artifact matrix and Q13 the end-to-end measurement. Propagation is now **measured** (L0) rather than unestablished. |
| **D4** `noop_state_change` is INFO | Unchanged from S2 — `severity="info"`, excluded from the DAG error count, surfaced as an informational finding (4 occurrences). |

§2.1's three-way metric split and §2.2's rename are in `s2_matrix.py` and the
report: primary detection (macro F1 **0.988**), clean FP rate, secondary
consequence findings (6, reported separately), and the metric is named
**Observable-root Top-1 localization accuracy**, with `latent_root_event_id` /
`observable_root_event_id` both persisted per trace.

## 2. Decisions already taken that a reviewer may want to reverse

1. **Thresholds were not tuned to clean up the last false positive.** Case 4
   node-12 would need `input_inference_min_coverage ≤ 0.29`, which is inside
   the natural cluster (0.00–0.29). It is reported instead. → `failure_cases.md`
   F1, `S2_report.md` Q2.
2. **A5 was separated from the semantic detectors.** Once any integrity
   finding exists, A1/A0/A2/A4 are not run on that trace. §5 asks for the
   separation; it also removed 4 spurious A1 findings on truncated logs, since
   node usage is itself derived from the damaged log. → `anomaly_detector.py`.
3. **Two ground-truth definitions were corrected after the fact**, both
   documented with before/after effect: A5 `delete`/`truncate` roots (Top-1
   0.79 → 1.00) and root-cause Top-1 with tied confidence. → `injection_spec.md`.
4. **A2's `wrong_record_binding` rule was rewritten** to re-run the same
   classifier that made the binding, rather than requiring "some other record
   matches better" (which missed 3 of 10). → `anomaly_detector.py`.
5. **`tool_not_granted` is implemented but has zero measured recall.** The
   dataset gives every expert the same single tool, so cross-role misuse cannot
   occur. It should not be cited as validated.
6. **Severity and confidence are per-rule constants**, not calibrated
   probabilities. Ordinals only.

## 3. What I would most like reviewed

- **The P0–P3 boundary.** It is the load-bearing definition of the whole audit
  and it is a semantic judgement made deterministic, not a measurement. The
  specific question: is `expert_synthesis` (a node restating a dispatched
  expert's ANALYSIS) correctly P2 rather than P3? It is 6 of the 37
  `AGENT_GENERATED` nodes, and calling it P3 would take clean-trace Evidence
  FPR from 1.1% to ~7.9%. → `evidence_auditor.py`, `evidence_boundary_analysis.csv`.
- **The absence regexes.** 18 of 37 `AGENT_GENERATED` nodes are absence claims
  and the patterns that catch them were extended node-by-node on Dev-Small.
  This is the highest overfitting risk in the package and it is invisible to a
  numeric sensitivity sweep. → `cross_graph_binding.py`, `S2_report.md` Q11.
- **Whether A4's artifact-level matrix should be reported at all**, given it
  cannot exercise propagation. Current position: report detection F1 = 1.00,
  and report the artifact matrix and the end-to-end study as two separate
  experiments (§2.1 D3), which is what §1 above does.
- **The `noop_state_change` finding** (4 occurrences): a backtrack that fires
  with nothing to delete. Is that informational, or should it be a warning?
  → `dependency_graph.py`.

## 4. Deliverables (§11)

```text
S2_audit/
├── S2_report.md                        ✅  (§12's 12 questions answered)
├── REVIEW_REQUEST.md                   ✅  this file
├── schema_v2.json                      ✅  generated from the dataclasses
├── status_semantics.md                 ✅
├── capability_policy.md                ✅
├── evidence_boundary_analysis.csv      ✅  162 nodes, 18 columns
├── injection_spec.md                   ✅
├── injection_ground_truth.jsonl        ✅  110 records (1 skipped, with reason)
├── clean_audit_results.csv             ✅
├── anomaly_results.csv                 ✅
├── per_class_metrics.csv               ✅
├── root_cause_results.csv              ✅
├── propagation_results.csv             ✅
├── integrity_results.csv               ✅
├── dependency_dag_results.csv          ✅
├── natural_failure_case.md             ✅  defect verified; live reproduction in case_studies/
├── case_studies/                       ✅  §6: A3_case1/, A4_case8/, A4_case5/, case_study_summary.md
├── example_audit_report.json           ✅
├── example_path.png                    ✅  11-step path, both graphs
├── failure_cases.md                    ✅  2 successes, 5 failures
├── tests/                              ✅  118 tests
├── git_diff.patch                      ✅
└── git_commit.txt                      ✅
```

Supporting: `evidence_boundary_sensitivity.csv`, `matrix_summary.json`,
`anomalies.jsonl`. Raw runs in `S2A_runs/` and `S2A_inject/`; the case-study
runs in `case_studies/_work_*/`.

## 5. Reproducing

```bash
# 1. clean run + artifacts (+ reruns the S1 non-invasiveness proof)
python3 traceevo/t1_noninvasiveness.py --cases 0-9 --tag s2a_dev10 \
  --workdir "$PWD/S2A_runs" --recording "$PWD/S1_runs/t1_dev10/llm_responses.jsonl"

# 2. evidence boundary (§4)
python3 traceevo/s2_evidence.py \
  --runs S2A_runs/s2a_dev10/C_prov_on_replay/traceevo --out S2_audit --cases 0-9

# 3. injection matrix (§8.2) + metrics (§8.3)
python3 -c "from traceevo.audit.injector import run_matrix; \
  run_matrix('S2A_runs/s2a_dev10/C_prov_on_replay/traceevo/runs/C_prov_on_replay', 'S2A_inject', list(range(10)))"
python3 traceevo/s2_matrix.py --clean S2A_runs/s2a_dev10/C_prov_on_replay/traceevo/runs/C_prov_on_replay \
  --injected S2A_inject --out S2_audit --cases 0-9

# 4. bundle (§11)
python3 traceevo/s2_package.py --out S2_audit \
  --runs S2A_runs/s2a_dev10/C_prov_on_replay/traceevo/runs/C_prov_on_replay \
  --injected S2A_inject --cases 0-9

# 5. tests
python3 -m pytest traceevo/tests -q      # 118 passed

# 6. the case studies (run; re-derivable without live calls from the artifacts)
export TRACEEVO_API_KEY=...
python3 traceevo/s2_cases.py --out S2_audit/case_studies --a3-case 1 --a4-cases 8,5
# report-only rerun of the A4 measurements, no model calls:
python3 traceevo/s2_cases.py --out S2_audit/case_studies --a4-cases 8,5 --rescore
```

## 6. Stopping

S2.1 is complete and stops here. No S3 work has been started: no Experience
Memory, no Experience Gate, no Capability Profile, no planner history
injection, no self-evolution, and no case outside Dev-Small 0–9 has been read
or run. Awaiting review (§1 above lists the one item I would not settle
without one).
