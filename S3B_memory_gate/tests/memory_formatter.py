"""S3B §12: the only thing a planner ever sees.

Two blocks, and the wording is load-bearing rather than decorative:

    [Trusted Prior Experience]   process guidance from verified executions
    [Failure Prevention]         what went wrong, and what to check

Both say, in the prompt itself, that these are **not patient facts and must not
be treated as evidence**. §12 requires that sentence, and it matters here for a
concrete reason: a strategy hint like "retrieval_count: 3" is harmless, but
"verify source binding before using evidence" is one inference away from being
read as a finding about the current patient. The header forecloses that.

**What this module will not do.** It renders only the `payload` half of an
experience. The retrieval key — which does contain the historical case's
complaint text — is never rendered, never interpolated, never summarised into
the block. That is §11's rule, and it is why the two halves are separate types
in `experience.py` rather than one blob with a flag.

The formatter also refuses to invent. `evidence_modalities` was never
extracted, so it is never printed. A field that is absent stays absent rather
than becoming an empty list a reader might take for "none were used".

**P2 never becomes a fact here** (§6.1). No node text from any trace reaches
this module; the closest thing is the tier *count* in the profile, which is
rendered as a constraint sentence selected by `grounding_constraints`, not as
a claim.
"""
from __future__ import annotations

from typing import Any, Dict, List

HEADER = """[Trusted Prior Experience]

The following items are process guidance from previously verified executions.
They are NOT patient facts and MUST NOT be treated as evidence.

{items}

Use them only to guide planning, tool/evidence checks, and collaboration.
The current patient's evidence always has priority."""

FAILURE_HEADER = """[Failure Prevention]

The following items are process guidance from previously verified executions.
They are NOT patient facts and MUST NOT be treated as evidence.

{items}

Use them only to guide planning, tool/evidence checks, and collaboration.
The current patient's evidence always has priority."""


def _strategy_lines(strategy: Dict[str, Any]) -> List[str]:
    lines = []
    roles = strategy.get("expert_roles_used") or []
    if roles:
        lines.append(f"Consultation pattern: {', '.join(roles)} were consulted "
                     f"in a verified execution.")
    counts = [
        ("retrieval_count", "evidence retrievals"),
        ("drilldown_count", "hypothesis refinements"),
        ("backtrack_count", "backtracks"),
        ("analysis_count", "expert analyses"),
    ]
    used = [f"{strategy[k]} {label}" for k, label in counts if strategy.get(k)]
    if used:
        lines.append("Execution shape: " + ", ".join(used) + ".")
    return lines


def render(experiences: List[Dict[str, Any]]) -> str:
    """§12: render admitted experiences into the optional prompt block.

    Returns "" when there is nothing to inject. Returning an empty block with a
    header would tell the planner that memory was consulted and found nothing,
    which is not the same experiment as running with memory off.
    """
    if not experiences:
        return ""

    success_lines: List[str] = []
    failure_lines: List[str] = []
    for e in experiences:
        payload = e.get("payload") or {}
        kind = payload.get("type")
        if kind == "validated_success":
            success_lines.extend(_strategy_lines(payload.get("strategy") or {}))
            for c in payload.get("grounding_constraints") or []:
                success_lines.append(f"Check: {c}.")
        elif kind == "validated_failure":
            pattern = payload.get("failure_pattern") or "ANOMALOUS_EXECUTION"
            failure_lines.append(f"Pattern to avoid: {pattern}.")
            for a in payload.get("avoid") or []:
                failure_lines.append(f"Avoid: {a}.")
            for r in payload.get("recommended_check") or []:
                failure_lines.append(f"Check: {r}.")

    blocks = []
    if success_lines:
        blocks.append(HEADER.format(
            items="\n".join(f"{i}. {ln}" for i, ln in enumerate(success_lines, 1))))
    if failure_lines:
        blocks.append(FAILURE_HEADER.format(
            items="\n".join(f"{i}. {ln}" for i, ln in enumerate(failure_lines, 1))))
    return "\n\n".join(blocks)


def inject_into_plan_prompt(plan_prompt: str, experiences: List[Dict[str, Any]]) -> str:
    """Append the block at the end of the plan prompt, as §12 specifies.

    A pure string append: no existing prompt text is touched, so with an empty
    experience list the prompt is byte-identical to GoS's own.
    """
    block = render(experiences)
    if not block:
        return plan_prompt
    return plan_prompt.rstrip() + "\n\n" + block + "\n"
