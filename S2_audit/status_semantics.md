# S2 §3.1 — Observable execution status semantics

## Fields added to `ProvenanceEvent`

| field | type | meaning |
|---|---|---|
| `status` | `success` \| `degraded` \| `failed` | how the operation ended |
| `reason_code` | see below | why, when it is not plain success |
| `model_id` | string | the model **name** the config selected |
| `model_version` | string \| null | only when the provider actually reports one |
| `version_observable` | bool | which of the two the reader is looking at |

### `reason_code`

```text
NONE                          no reason to report
BUDGET_EXHAUSTED              the retrieval budget ran out before an analysis
INSUFFICIENT_INFORMATION_FALLBACK  upstream's fixed fallback text was returned
INVALID_EXPERT_DISPATCH       the requested agent does not exist / is not permitted
TOOL_ERROR
LLM_ERROR
PARSER_ERROR                  model output unusable (unparseable / inconsistent)
TRACE_INTEGRITY_ERROR
```

`__post_init__` rejects any value outside these enums, so a typo cannot become
a new category. `test_status_and_reason_code_are_validated` pins that.

## The three outcomes are the same code path

All three come from `Expert_Agent.run()`; the *returned text* and the *control
flow* are untouched, only the recorded observation differs. Each is pinned by a
test that also asserts the upstream-visible behaviour:

| outcome | trigger | recorded | test |
|---|---|---|---|
| `success` | the expert chose `analyze` | `success` / `NONE` | `test_normal_analysis_emits_success` |
| `degraded` | budget exhausted, empty query, unparseable decision, or `analyze` with empty content | `degraded` + a reason code | `test_budget_exhaustion_emits_degraded` |
| `failed` | the planner named an agent outside the roster | `failed` / `INVALID_EXPERT_DISPATCH` **and the original `ValueError` still propagates** | `test_invalid_dispatch_emits_failed_then_reraises` |

`test_budget_exhaustion_emits_degraded` asserts that the returned string is
byte-identical to upstream's fixed fallback — the hook marks the path, it does
not rewrite it.

### Why `degraded` and not `failed`

The degraded paths do not raise. Upstream returns a fixed fallback string and
the run continues, so the operation *completed* — on a fallback. Calling that
`failed` would misdescribe it and would make every budget exhaustion look like
a crash. The pre-existing defect this fixes is that the trace said nothing at
all: an expert that retrieved nothing and gave up was indistinguishable from
one that analysed successfully.

## `model_version` is deliberately null

S1 wrote the model name into `model_version`, which overstated what was known:
`deepseek-v4-flash` is a config value, not a build identifier. S2 splits the
two. `model_id` is the name (reliable, read from
`configs/LLM_Configs.yaml:default_model_path`); `model_version` stays `null`
and `version_observable` stays `false` until a provider reports one.

Measured on the 10 clean Dev-Small traces: 342/342 events carry
`model_id=deepseek-v4-flash`, `model_version=null`, `version_observable=false`.

## What is *not* covered

`status` describes an operation's outcome, not its quality. A `success`
analysis can be wrong, unsupported or fabricated — that is what A1/A4 are for.
Nothing in this layer judges content, and nothing in this layer changes
behaviour: the T1 non-invasiveness check (A vs B vs C, prompt-sequence hash
`37a66e6112bac731`) still reports IDENTICAL after these changes.
