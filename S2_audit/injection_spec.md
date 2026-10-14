# Injection specification (S2 §5, §8.2)

Implementation: `traceevo/audit/injector.py` (artifact mutation),
`traceevo/recording.py` (response substitution), `traceevo/s2_cases.py`
(end-to-end runner).

## Two mechanisms

### 1. Artifact mutation — the quantitative matrix

The recorded run is edited and then made **internally consistent again**: the
event hash chain is re-sealed and the graph-delta hashes are recomputed
(`TaskArtifacts.reseal_events`, `.rebuild_delta_hashes`). Ground truth is the
`Injection` record.

Re-sealing is not a convenience. A fabrication produced by a *real* run yields
exactly this: a trace whose every hash verifies and whose semantics are still
wrong. Leaving the chain broken would make the auditor report A5 for every A1
and the semantic metrics would measure tampering rather than semantics.
Verified: all 69 semantic injections replay
(`Replay(G_t, delta_t) == G_(t+1)`, `applicable: true, all_ok: true`).

A5 is the deliberate exception: it is *not* re-sealed, and its replay report
says `applicable: false` rather than pretending to check.

### 2. Response substitution — the end-to-end studies

The recorded response for one call is replaced and the run continues **live**,
so GoS's own code produces the graph, the deltas, the downstream prompts and
the report. Enabled by `TRACEEVO_SUBSTITUTE` + `TRACEEVO_REPLAY_MODE=extend`:
calls before the injection come from the recording, the substituted call
returns the injected response, and everything after it is a genuine live call
because the prompts have legitimately changed — exactly the divergence §5 A4
asks for.

## Matrix (§8.2)

10 clean + 10×A0 + 10×A1 + 10×A2 + 10×A3 + 10×A4 + 40×A5 = **109 injected
traces** over Dev-Small cases 0–9. Source: `injection_ground_truth.jsonl`.

| type | variant | injector rule |
|---|---|---|
| A0 | `status_marked` | set an ANALYSIS to upstream's fallback text **and** mark `degraded`/`BUDGET_EXHAUSTED` |
| A0 | `silent` | same text, `status` left `success` — the pre-D1 world |
| A1 | `add_node` | append a fabricated Evidence node to the first update-graph event, wired to a hypothesis with a `support` edge |
| A2 | `wrong_record_binding` | repoint an Evidence node's binding at a record that does not contain its text |
| A2 | `record_tampering` | overwrite a retrieval record's text, keep its `record_id` and `record_hash` |
| A3 | `unresolvable_expert` | insert a failed `AGENT_CALL` naming `Laboratory Physician` immediately after the PLAN |
| A4 | `corrupt_analysis` | replace an ANALYSIS body with a fabricated finding, keeping its event id, parents and retrievals |
| A5 | `modify` / `delete` / `reorder` / `truncate` | damage the event log without re-sealing |

### A1 content constraint

§5 forbids using an absence claim or a recommendation as an A1 injection. All
four fabricated findings are measurements with a named test:

- "Serum tryptase was elevated at 42 ng/mL and the bone marrow aspirate showed
  mast cell infiltration"
- "Immunofluorescence microscopy demonstrated linear IgA deposition along the
  basement membrane"
- "Positron emission tomography showed increased uptake in the left adrenal
  gland with a standardized uptake value of 7.4"
- "Peripheral blood flow cytometry identified a clonal T-cell population
  comprising 18% of lymphocytes"

### Applicability

19 of 20 A2 `wrong_record_binding` injections were produced. Case 1 has no
Evidence node bound to a retrieval record with ≥2 records to swap to, so it is
reported as **skipped with the reason**, not back-filled (§8.2: "不伪造样本").

## Separate experiment classes (S2.1 §2.1, D3)

The two mechanisms are not interchangeable and the write-up keeps them apart:

```text
Artifact mutation          -> large-scale, deterministic ANOMALY DETECTION
                              evaluation. Cannot test semantic propagation:
                              an A4 edit changes the ANALYSIS text while the
                              downstream GRAPH_UPDATE responses are the
                              original ones.

Response substitution      -> whether an anomaly really changes the following
                              graph / prompt / report. Small n, live calls.
```

Permitted claim: *artifact-level A4 corruption detection F1 = 1.00*.
Forbidden claim: *A4 propagation is verified* — the artifact matrix cannot
establish that, and does not here (`semantic_reach.reached_graph = 0/10`).

## Ground truth

Every `Injection` records the root (event and/or node) and the downstream sets
**derived from the execution DAG**, not hand-written, so they cannot drift from
the trace. Two corrections were made during evaluation and are reported rather
than hidden:

1. **A5 `delete` / `truncate` roots.** The first version named the *removed*
   event as the root. A removed event is not in the trace, so no detector can
   ever name it — the metric was unanswerable and capped root-cause Top-1 at
   0.79 for reasons unrelated to detection. The root is now the observable
   seam: for `delete`, the event now following the gap (whose `prev_hash` no
   longer matches); for `truncate`, the last surviving event.
2. **Root-cause Top-1 with tied confidence.** Per-rule confidences are coarse
   constants, so several findings routinely tie. A tie is not a ranking; the
   check is applied to the tied top group rather than to list order.

## Reproducing

```bash
python3 traceevo/audit/injector.py       # via run_matrix(), seed 20260917
python3 traceevo/s2_matrix.py --clean <run_dir> --injected S2A_inject --out S2_audit
python3 traceevo/s2_cases.py --out S2_audit/case_studies
```
