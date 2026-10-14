"""Capability policy: who may be called, and what a dispatch is allowed to do.

§5 A3 asks for two things: a dispatch to an expert that cannot be resolved, and
a role requesting a capability outside its remit. Both are decidable from the
trace plus a declared policy, so this module has no judgement in it.

The important detail is *name resolution*. Upstream (`Central_Agent.act`)
compares `agent.name == expert_name` exactly. The model, however, writes the
human-readable form:

    "Laboratory Physician"   (requested by the planner)
    "Laboratory_Physician"   (the agent that exists)

Under exact matching that is a hard failure; under any normalisation it is the
same agent. Both facts matter, so the policy reports them separately:

    resolved=False, reason=INVALID_EXPERT_DISPATCH, near_miss="Laboratory_Physician"

`near_miss` is the diagnostic an operator needs, and it is exactly the natural
defect studied in §10.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

# Tools each role may invoke. A role is either the central agent (which plans
# and edits the graph) or an expert (which retrieves and analyses).
CENTRAL_TOOLS = {"PLAN", "GRAPH_UPDATE", "AGENT_CALL", "BACKTRACK", "DRILL_DOWN",
                 "FINAL_REPORT"}
EXPERT_TOOLS = {"Evidence_Text", "TOOL_CALL", "EVIDENCE_READ", "ANALYSIS"}


def normalize_agent_name(name: Any) -> str:
    """Case/space/underscore-insensitive form of an agent name."""
    return re.sub(r"[\s_\-]+", "", str(name or "")).strip().lower()


@dataclass
class DispatchCheck:
    requested: str
    resolved: bool
    reason: str = "NONE"
    resolved_to: str = ""
    near_miss: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_violation(self) -> bool:
        return not self.resolved


@dataclass
class CapabilityPolicy:
    """The roster and tool grants a run was supposed to obey."""

    roster: List[str] = field(default_factory=list)
    expert_tools: Dict[str, Set[str]] = field(default_factory=dict)
    central_tools: Set[str] = field(default_factory=lambda: set(CENTRAL_TOOLS))

    @classmethod
    def from_events(cls, events: Sequence[Dict[str, Any]]) -> "CapabilityPolicy":
        """Derive the roster from the trace.

        Two sources, because one is not enough:

        * the names of dispatches that **succeeded** -- a run that only ever
          dispatched `Primary_Physician` and `Pathologist` had those two
          agents available;
        * the `available` list a **failed** dispatch records. This is the only
          roster evidence an aborted task has: the A3 live reproduction dies at
          the very first dispatch, so there are no successes at all, and
          without this source the policy cannot re-derive the near-miss name
          that §4 requires the auditor to name from the trace alone.
        """
        roster: List[str] = []
        for e in events:
            if e.get("event_type") != "AGENT_CALL":
                continue
            for name in (e.get("payload") or {}).get("available") or []:
                if name and name not in roster:
                    roster.append(name)
            if e.get("status") != "success":
                continue
            name = e.get("tool_or_model") or (e.get("payload") or {}).get("expert")
            if name and name not in roster:
                roster.append(name)
        return cls(roster=roster)

    @classmethod
    def from_config(cls, path: str) -> "CapabilityPolicy":
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        spec = cfg.get("capability_policy") or {}
        return cls(roster=list(spec.get("roster") or []),
                   expert_tools={k: set(v) for k, v in
                                 (spec.get("expert_tools") or {}).items()},
                   central_tools=set(spec.get("central_tools") or CENTRAL_TOOLS))

    # ------------------------------------------------------------- dispatch
    def resolve(self, requested: str) -> DispatchCheck:
        """Exact match first (upstream's rule), then report the near miss."""
        if requested in self.roster:
            return DispatchCheck(requested=requested, resolved=True,
                                 resolved_to=requested)
        norm = normalize_agent_name(requested)
        for name in self.roster:
            if normalize_agent_name(name) == norm:
                return DispatchCheck(
                    requested=requested, resolved=False,
                    reason="INVALID_EXPERT_DISPATCH", resolved_to="",
                    near_miss=name,
                    detail={"cause": "name_form_mismatch",
                            "requested_normalized": norm,
                            "available_normalized": normalize_agent_name(name),
                            "hint": (f"upstream compares names exactly; "
                                     f"{requested!r} != {name!r}")})
        return DispatchCheck(
            requested=requested, resolved=False,
            reason="INVALID_EXPERT_DISPATCH",
            detail={"cause": "not_in_roster", "roster": list(self.roster)})

    def check_event(self, event: Dict[str, Any]) -> Optional[DispatchCheck]:
        """Check one recorded AGENT_CALL against the policy."""
        if event.get("event_type") != "AGENT_CALL":
            return None
        requested = (event.get("payload") or {}).get("expert") or \
            event.get("tool_or_model") or ""
        if not requested:
            return None
        check = self.resolve(requested)
        # A recorded failure is a violation whether or not the name resolves
        # now: the roster is derived from successes, so a failed name will not
        # be in it, but be explicit rather than relying on that.
        if event.get("status") == "failed" or \
                event.get("reason_code") == "INVALID_EXPERT_DISPATCH":
            check.resolved = False
            check.reason = event.get("reason_code") or "INVALID_EXPERT_DISPATCH"
        return check if check.is_violation else None

    # -------------------------------------------------------------- tools
    def tool_allowed(self, role: str, event_type: str,
                     tool_or_model: str = "") -> bool:
        if role == "central":
            return event_type in self.central_tools
        granted = self.expert_tools.get(role)
        if granted is None:
            return event_type in EXPERT_TOOLS or tool_or_model == "Evidence_Text"
        return event_type in granted or tool_or_model in granted

    def check_tool_grant(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        role = event.get("actor_role") or "expert"
        et = event.get("event_type", "")
        if self.tool_allowed(role, et, event.get("tool_or_model") or ""):
            return None
        return {"cause": "tool_not_granted", "role": role,
                "actor_id": event.get("actor_id", ""),
                "event_type": et, "tool_or_model": event.get("tool_or_model", ""),
                "granted": sorted(self.expert_tools.get(
                    event.get("actor_id", ""), EXPERT_TOOLS))}
