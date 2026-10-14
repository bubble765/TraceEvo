# Experience Eligibility Contract (S3A §5)

**Status: defined, not implemented.** S3A §5 and §11 forbid building a memory
store, injecting anything into the planner, updating a Capability Profile or
running a self-evolution experiment. This document specifies what S3B would be
allowed to admit, and — more usefully — which of the fields it needs are *not
computable today*.

The contract exists now because S2.1's D1 ruling created a specific hazard: the
audit produces `P2_DERIVED` nodes with `externally_grounded = false`, and a
memory that stored those as if they were external facts would be quietly wrong
in a way no downstream metric would show. §5's job is to make that mechanically
checkable before anything is stored.

---

## 1. The record

One candidate per completed trace:

```json
{
  "trace_id": "...",
  "task_id": "case-12",
  "outcome": null,
  "integrity_ok": true,
  "invocation_complete": true,
  "semantic_trace_complete": true,
  "has_critical_anomaly": false,
  "evidence_profile": {"p0": 4, "p1": 0, "p2": 3, "p3": 0},
  "uncommitted_calls": 11,
  "eligibility": "manual_review",
  "reasons": ["outcome is not computable without a gold label"]
}
```

## 2. Eligibility rules

§5's admission principle, as a decision procedure. `Validated Success` is the
only class a memory may retrieve from as fact.

```text
integrity_ok AND invocation_complete AND semantic_trace_complete
AND NOT has_critical_anomaly
AND no unresolved P3 on the decision path
AND outcome judged correct
        -> eligible                    (Validated Success)

a failure whose anomaly root is localized
        -> failure_experience          (Validated Failure: what went wrong)

anything else
        -> reject, or manual_review when a judgement call is needed
```

## 3. Field-by-field: what is computable *today*

§10 asks directly whether any field cannot be computed. The honest answer is
that **one cannot**, and the contract is written so that this is visible rather
than papered over with a default.

| field | computable | source | caveat |
|---|---|---|---|
| `trace_id` | **yes** | run directory + `task_id` | — |
| `task_id` | **yes** | event log | — |
| `integrity_ok` | **yes** | `integrity_auditor` + hash chain + delta replay | — |
| `invocation_complete` | **yes** | invocation ledger | true when no call is left `pending` and every stage that should appear does |
| `semantic_trace_complete` | **yes** | dependency DAG + `FINAL_REPORT` presence | — |
| `has_critical_anomaly` | **yes** | `anomaly_detector` severity | severity is a per-rule constant, so this is ordinal, not calibrated |
| `evidence_profile` | **yes** | `evidence_auditor` P0–P3 | the tier is a deterministic decision on a semantic boundary; see §4 |
| `uncommitted_calls` | **yes** | invocation ledger | — |
| `eligibility` | **yes** | derived from the above | — |
| `reasons` | **yes** | derived | — |
| **`outcome` (judged correct)** | **NO** | needs a gold label | **see §3.1** |

### 3.1 `outcome` is the field that cannot be computed

"Outcome judged correct" is not derivable from the trace. The trace records what
the pipeline did, and a run that reaches a confidently-worded wrong diagnosis
looks identical to one that reaches a right one.

Where a label exists — Dev-Small's `Right Option` column — it can be filled in.
Everywhere else it cannot, and S3B must not substitute a proxy. The obvious
proxies are all wrong in the same direction:

- **self-reported confidence** — the model already writes `(leading hypothesis,
  0.45)` into its prose (S2 `failure_cases.md` F4). Using it as a label would
  store the model's opinion as the ground truth about itself.
- **absence of anomalies** — the audit's clean-trace alert rate is 10% on
  Dev-Small and is measured on the holdout in this package. Silence is not
  correctness.
- **agreement with the retrieved records** — A1's whole point is that a node can
  rest on nothing and still read fluently.

Consequence for S3B: on data without labels, **`Validated Success` is not
issuable**. The only classes available are `failure_experience` (where the root
is localized by an anomaly detector, so no correctness judgement is needed) and
`manual_review`. A design that needs `Validated Success` on unlabelled data has
to acquire labels, not a better proxy.

## 4. The tier constraints, and why they bind

§5's constraints, restated as what a memory may do with each tier:

| tier | may enter a success experience as | reason |
|---|---|---|
| **P0** `DIRECT_GROUNDED` | external/input fact | the content is in the case input or in a retrieval record |
| **P1** `SOFT_GROUNDED` | only after review | a candidate record above a 0.35 threshold; similarity is not identity |
| **P2** `DERIVED` | may be kept, **never as external fact** | traceable to a real ANALYSIS or to the input, but not independently true |
| **P3** `UNSUPPORTED_ASSERTION` | **not on the decision path** | nothing external or internal supports it |

Two mechanical checks follow, both available from S2's output:

```text
no P3 node with used_in_reasoning = true may appear in a success experience
no ledger row with committed_to_state = false may contribute content
```

The second is new in S3A and is the one S2 could not state. Before the ledger,
a discarded model response was invisible; a memory built only from events would
have looked complete while silently omitting the fact that a call was thrown
away. Concretely: the A4 injections in `a4_invocation_recheck/` produced
`committed_to_state = false` responses that no event records. A gate reading
events alone cannot tell those apart from "nothing happened".

## 5. What S3B must NOT do with this contract

- Do not implement retrieval, storage or planner injection from this document.
- Do not treat `manual_review` as a soft yes. It is a queue, not a verdict.
- Do not run on case 30–137; §2 reserves them for S3B/S4/S5.
- Do not tune the audit rules so that more traces land in `eligible`. The
  clean-trace alert rate on the holdout is reported as measured (§7), and a
  `NEED_REVIEW` verdict means "ask a human", not "widen the threshold".
