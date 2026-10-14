# Capability policy (S2 §5 A3)

Implementation: `traceevo/audit/policy.py`.

## Rule

A dispatch is legal when the requested name is **exactly** an entry in the
roster. That is not a design choice — it is what upstream does:

```python
# gos/agents/central_agent.py, act()
matching_experts = [agent for agent in self.experts if agent.name == expert_name]
if not matching_experts:
    raise ValueError(f"Not founded, current list：{[a.name for a in self.experts]}")
```

The policy reproduces that comparison, then adds the diagnostic upstream does
not have. When exact matching fails but a normalised match exists, the report
carries `near_miss`:

```json
{"requested": "Laboratory Physician",
 "resolved": false,
 "reason": "INVALID_EXPERT_DISPATCH",
 "near_miss": "Laboratory_Physician",
 "detail": {"cause": "name_form_mismatch",
            "hint": "upstream compares names exactly; 'Laboratory Physician' != 'Laboratory_Physician'"}}
```

`resolve()` never repairs the name. §5 requires the upstream exception to
propagate unchanged, and it does: the hook records the failure and
`central_agent.act()` re-raises.

## Roster source

`CapabilityPolicy.from_events()` derives the roster from the dispatches that
**succeeded** in the trace. That is the only roster evidence the artifacts
contain, and it is sufficient: a name that never succeeds is exactly the set
of names the policy must reject. `from_config()` is available for a declared
policy file; the Dev-Small runs use the derived form.

## Tool grants

| role | granted |
|---|---|
| central | `PLAN`, `GRAPH_UPDATE`, `AGENT_CALL`, `BACKTRACK`, `DRILL_DOWN`, `FINAL_REPORT` |
| expert | `Evidence_Text`, `TOOL_CALL`, `EVIDENCE_READ`, `ANALYSIS` |

`check_tool_grant()` reports a `tool_not_granted` violation. **This rule fired
on nothing in S2**, in either the clean or the injected traces: the surgical
scenario in this dataset gives every expert the same single tool
(`Evidence_Text`), so there is no cross-role tool misuse to observe. The rule
is implemented and unit-testable but its recall is unmeasured here, and it
should not be reported as a validated detector. It becomes measurable in S3+
on a domain with heterogeneous tool grants.

## The natural defect this models

`gos/prompts/prompt_generation.py:212` — the planner's own worked example —
reads:

```
- agent_descriptions: [
    {"expert_name":"Laboratory Physician","description": ...},
```

while `gos/configs/Domain_Configs.yaml` declares
`expert_names: ["Laboratory_Physician", "Pathologist", "Radiologist"]`.

The prompt teaches a name form the code cannot resolve. The model follows its
own example, `act()` finds nothing, and the task aborts. See
`natural_failure_case.md`.
