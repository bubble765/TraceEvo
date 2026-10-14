# Natural failure case study: `"Laboratory Physician"` vs `"Laboratory_Physician"`

§10 requires a real failure, not a constructed one, to show that TraceEvo
explains genuine operational faults and not only injected ones.

## Status of this study — read first

**The natural occurrence is documented but its raw trace was not retained.**
It was observed during S1 (recorded in `S1_provenance/S1_report.md` §6.3 and
`S1_provenance/REVIEW_REQUEST.md`), before the provenance layer was capturing
the case that hit it. What exists today is:

| evidence | status |
|---|---|
| the S1 observation of the crash | **real**, written up in the S1 report |
| the root cause, readable in the source | **real**, verified below |
| the resulting trace shape, reproduced through the real code path | **done** — live reproduction, `case_studies/A3_case1/` |
| the same trace shape produced by artifact injection | **done**, 10/10 on Dev-Small (§A3 below) |

So the *defect* is natural and verified in the source; the *trace* in this
package is a reproduction, not the original capture. It is labelled as such and
should not be cited as a natural trace.

## Root cause, verified in the source

The planner's prompt contains a worked example
(`gos/prompts/prompt_generation.py:212`):

```
- agent_descriptions: [
    {"expert_name":"Laboratory Physician","description":"Responsible for analysis of blood/pathological test data ..."},
    {"expert_name":"Pathologist", ...},
    {"expert_name":"Radiologist", ...}
  ]
Output JSON:
[
  {"expert_name":"Pathologist"},
  {"expert_name":"Radiologist"}
]
```

The roster (`gos/configs/Domain_Configs.yaml:2`):

```yaml
expert_names: ["Laboratory_Physician", "Pathologist", "Radiologist"]
```

`act()` matches exactly:

```python
matching_experts = [agent for agent in self.experts if agent.name == expert_name]
```

The prompt teaches `Laboratory Physician` (space); the roster holds
`Laboratory_Physician` (underscore). A model that follows its own example
produces a name upstream cannot resolve.

## Failure chain

```text
PLAN                       planner selects an expert list containing
                           "Laboratory Physician"
   │
   ├─ AGENT_CALL  status=failed
   │  reason_code=INVALID_EXPERT_DISPATCH
   │  payload.expert   = "Laboratory Physician"
   │  payload.available= ["Laboratory_Physician", "Pathologist", "Radiologist"]
   │  payload.error    = "Not founded, current list：[...]"
   │
   └─ ValueError propagates out of act()
        │
        └─ main.py has no try/except around the case body
             │
             └─ the process aborts; every remaining case in the run is lost
```

Two properties matter and both are preserved deliberately:

1. **Nothing repairs the name.** No normalisation, no retry, no fallback to a
   similarly-named agent. §5 requires the upstream exception to propagate
   unchanged; `test_invalid_dispatch_emits_failed_then_reraises` pins this.
2. **The provenance log survives the crash.** Events are appended and flushed
   per event (`EventLogger.append`), so the trace up to the failure is on disk
   even though `finish_task` — which writes the summary, integrity report,
   graph and PNG — never runs. This is the one place where a task's artifacts
   are deliberately incomplete, and the integrity auditor reports it as
   `missing_final_report` rather than silently accepting a short trace.

## What the audit reports

Reproduced by artifact injection (A3, 10/10 traces):

| field | value |
|---|---|
| `type` | `A3` |
| `detector_rule` | `name_form_mismatch` |
| `root_event_id` | the failed `AGENT_CALL` |
| `severity` | `medium` |
| `confidence` | `1.0` |
| `evidence[0].requested` | `"Laboratory Physician"` |
| `evidence[0].near_miss` | `"Laboratory_Physician"` |
| `evidence[0].detail.hint` | `upstream compares names exactly; 'Laboratory Physician' != 'Laboratory_Physician'` |
| `affected_events` | every event downstream of the PLAN |
| `reaches_report` | `false` — correctly: the task aborted before a report |

Detection on the A3 matrix is 10/10 (precision 1.0, recall 1.0, F1 1.0), and
the root-cause Top-1 is 10/10.

## Why this matters for §12 Q10

The failure is not "the model was wrong". The model was doing what its prompt
demonstrated. The audit's job here is not to catch a liar but to say precisely
where the contract between the prompt and the code broke, with the exact string
comparison that failed. That is the shape of finding S3's Experience Gate would
need to act on, and it is available from the trace alone.

## The live reproduction — run (S2.1 §4)

Per §4 the correct name for this is a **live reproduction of a naturally
discovered defect**, not a "naturally occurring trace": the run reproduces a
known defect through the real code path by substituting one response. The
naturally occurring original was the S1 observation above.

`traceevo/s2_cases.py --a3-case 1` substitutes the planner's response with

```json
[{"expert_name": "Laboratory Physician"}]
```

and runs the real code path. It was executed; artifacts are in
`case_studies/A3_case1/`.

| §4 requirement | measured |
|---|---|
| PLAN emitted | `evt-case-1-00003` |
| requested expert = `"Laboratory Physician"` | yes — in the substituted response and the recorded `AGENT_CALL` |
| `AGENT_CALL` failed | `evt-case-1-00004` |
| `reason_code = INVALID_EXPERT_DISPATCH` | yes |
| the original `ValueError` still raised | yes — `ValueError: Not founded, current list：['Laboratory_Physician', 'Pathologist', 'Radiologist']`, process `rc=1` |
| `FINAL_REPORT` absent | yes |
| integrity auditor found an incomplete task | yes |
| trace flushed before the process died | yes — 4 events survive |
| TraceEvo did **not** repair the agent name | yes — no normalisation anywhere in the dispatch path |
| auditor re-derives the near miss from the trace alone | yes — `Laboratory_Physician`, from `payload.available` on the failed dispatch |

The near-miss derivation is the part worth noting: the task dies at its *first*
dispatch, so there are no successful dispatches to build a roster from. Had the
policy learned the roster only from successes, the audit would have had nothing
to name. `CapabilityPolicy.from_events` therefore also reads the `available`
list the failed dispatch records — see `traceevo/audit/policy.py`.

One caveat about the reproduction, disclosed rather than hidden: the run reused
the recording an earlier `--a3-case 1` pass had written instead of recording
again, so the ~50 calls before the substitution point are that pass's. The call
that matters — the planner's — is the substituted one, and every call after it
is live. The result is the real code path raising its real exception.
