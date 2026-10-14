# Data availability (§5, §17 Q1-Q2)

Every field below was checked against case 10-39's artifacts. Fields
that do not exist there are `UNAVAILABLE` with the reason, not zero.

## Computable

| field | source | coverage |
|---|---|---|
| `n_invocations` | ledger `actor_id` | 660 expert calls |
| `accepted_calls` / `discarded_calls` | ledger `parse_status` | 1151 accepted, 17 discarded |
| `uncommitted_calls` | ledger `committed_to_state` | 355 false |
| `evidence_attributed_p0..p3` | `best_analysis_event` | 377 of 420 nodes |
| `p3_used_in_reasoning` | audit `used_in_reasoning` | 6 nodes |
| `retrieval_events` | `TOOL_CALL`+`EVIDENCE_READ` | per role |
| `structural_use_rate` | `used_in_reasoning` | 265 of 420 |

## Not computable

| field | why |
|---|---|
| `degraded_calls` | no event in case 10-39 carries status=degraded; all 855 are status=success, reason_code=NONE. There is no observable source. |
| `failed_calls` | same as degraded_calls: the status vocabulary in the artifacts has no failing value. |
| `latency` | not recorded in any artifact (S3A §5.4 noted the same). |
| `token_counts` | not recorded per invocation in the artifacts. |

## Attribution is narrower than it looks

Two links exist and only one discriminates:

* `binding.created_by_event` resolves to `Primary_Physician` for
  **all 420** nodes -- the orchestrator records the graph, so this
  link identifies who wrote it down, not who found it.
* `best_analysis_event` resolves 377 nodes to the three expert
  roles and leaves 43 as `NO_LINK`. Those 43 are reported as
  unattributed (10.2%); §5 forbids assigning them by heuristic.

The link is a grounding/coverage relation, not authorship, which is
why every field is named `evidence_attributed_*`.

Interval method: **Beta(1,1) posterior, 95% (scipy.stats.beta.ppf)**.
