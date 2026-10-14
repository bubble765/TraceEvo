"""S3B-B §5: the experience candidate, and what may never be in one.

An experience is split in two halves that never mix:

    retrieval metadata   used to find a neighbour. Never enters a prompt.
    injectable payload   the only thing a planner ever sees.

The split is what makes §11's rule enforceable rather than aspirational: the
retrieval key may contain a case's complaint verbatim (it is compared, not
shown), while the payload may not contain case content at all.

**Deterministic extraction.** §5.2 asks for structured extraction rather than a
second "summarise this into advice" LLM, and the reason is the same one S3A
ended on: a rule you can read is a rule you can audit. Everything below is
derived from events, the ledger and the audit summary by counting and by
looking up a fixed table. There is no model in this file.

**What is deliberately not extracted.** §5.2's example payload has an
`evidence_modalities` field (`["imaging", "pathology"]`). It is not implemented.
Nothing in the trace records a modality — the retrieved records are free text,
so any such field would come from a hand-written keyword map, i.e. exactly the
kind of locale-shaped rule S3A just demonstrated does not travel (2 of 5
holdout P3 nodes were absence phrasings the frozen regex had never seen).
Shipping a second one would repeat the mistake in the one component whose
contents get injected into a planner prompt. What replaces it is the tier
profile, which is measured rather than classified.

**What may never be stored** (§5.2), enforced by construction and tested:

* the reference diagnosis / `Right Option` — nothing in this module is given
  them; `Experience.forbidden_content()` scans the built payload for them and
  `experience_gate` refuses a candidate that trips it;
* a historical patient's identity or prediction;
* specific fabricated medical facts — a failed trace stores the *pattern*, not
  the text that failed;
* uncommitted model responses — only ledger rows with `committed_to_state`
  contribute;
* P2 content presented as external fact — the payload carries the tier profile
  and the constraint text, never the node text.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from traceevo.audit.cross_graph_binding import is_retrieval_result_event

EXTRACTOR_VERSION = "s3b-deterministic-v2"

# This is a deliberately small, non-clinical vocabulary. The central planner
# records one of these codes per expert dispatch; free-form rationale and
# hypothesis text are never copied into an injectable strategy.
PLANNING_INTENTS = frozenset({
    "assess_hypothesis",
    "interpret_existing_evidence",
    "seek_discriminating_evidence",
    "resolve_evidence_conflict",
    "review_uncertainty",
    "general_review",
})

WORKFLOW_ACTIONS = {
    "PLAN": "central_plan",
    "AGENT_CALL": "consult_expert",
    "TOOL_CALL": "retrieve_evidence",
    "EVIDENCE_READ": "retrieve_evidence",
    "ANALYSIS": "expert_analysis",
    "GRAPH_UPDATE": "update_reasoning",
    "DRILL_DOWN": "deepen_hypothesis",
    "BACKTRACK": "backtrack",
    "FINAL_REPORT": "prepare_report",
}

_MAX_EXPERT_SEQUENCE = 16
_MAX_PLANNING_STEPS = 16
_MAX_WORKFLOW_SEQUENCE = 32

# §6.2: a failure stores what went wrong and what to check next, keyed by the
# detector that found it. Fixed text, no model, and no trace content.
FAILURE_PATTERNS: Dict[str, Dict[str, Any]] = {
    "A0": {
        "failure_pattern": "DEGRADED_EXECUTION",
        "avoid": ["Do not build a conclusion on an expert that fell back to "
                  "its budget-exhausted text"],
        "recommended_check": ["Check the expert's ANALYSIS status before "
                              "treating its output as an analysis"],
    },
    "A1": {
        "failure_pattern": "UNSUPPORTED_EVIDENCE",
        "avoid": ["Do not treat an unsupported agent assertion as clinical "
                  "evidence"],
        "recommended_check": ["Verify source binding before using the "
                              "evidence for support or refute"],
    },
    "A2": {
        "failure_pattern": "MISBOUND_RECORD",
        "avoid": ["Do not treat a retrieval as evidence for a claim it does "
                  "not contain"],
        "recommended_check": ["Re-read the bound record and confirm it "
                              "actually states the claim"],
    },
    "A3": {
        "failure_pattern": "INVALID_DISPATCH",
        "avoid": ["Do not request an expert by a name the roster does not "
                  "contain"],
        "recommended_check": ["Use the roster's own spelling when dispatching"],
    },
    "A4": {
        "failure_pattern": "CORRUPTED_ANALYSIS",
        "avoid": ["Do not accept an analysis whose content did not come from "
                  "the evidence it cites"],
        "recommended_check": ["Compare the analysis against its retrieved "
                              "records"],
    },
    "A5": {
        "failure_pattern": "TRACE_INTEGRITY",
        "avoid": [],
        "recommended_check": ["Treat a trace whose integrity does not verify "
                              "as unusable for experience"],
    },
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Experience:
    """One candidate, with both halves and the provenance §10 requires."""

    memory_id: str
    source_trace_id: str
    task_id: str
    task_retrieval_key: str
    outcome_verdict: str
    audit_summary_hash: str
    payload: Dict[str, Any] = field(default_factory=dict)
    eligibility: str = ""
    gate_reasons: List[str] = field(default_factory=list)
    evidence_profile: Dict[str, int] = field(default_factory=dict)
    uncommitted_calls: int = 0
    extractor_version: str = EXTRACTOR_VERSION
    created_at: str = ""
    # Kept for the audit trail (§10) and never injected:
    source_trace_hashes: Dict[str, str] = field(default_factory=dict)

    def retrieval_metadata(self) -> Dict[str, Any]:
        """§5.1. Compared, never injected."""
        return {
            "memory_id": self.memory_id,
            "source_trace_id": self.source_trace_id,
            "task_retrieval_key": self.task_retrieval_key,
            "task_id": self.task_id,
            "outcome_verdict": self.outcome_verdict,
            "audit_summary_hash": self.audit_summary_hash,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "source_trace_id": self.source_trace_id,
            "task_id": self.task_id,
            "task_retrieval_key": self.task_retrieval_key,
            "outcome_verdict": self.outcome_verdict,
            "audit_summary_hash": self.audit_summary_hash,
            "payload": self.payload,
            "eligibility": self.eligibility,
            "gate_reasons": self.gate_reasons,
            "evidence_profile": self.evidence_profile,
            "uncommitted_calls": self.uncommitted_calls,
            "extractor_version": self.extractor_version,
            "created_at": self.created_at,
            "source_trace_hashes": self.source_trace_hashes,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Experience":
        known = {k: v for k, v in d.items()
                 if k in cls.__dataclass_fields__}
        return cls(**known)

    def injectable_text(self) -> str:
        """The payload as it would appear in a prompt. Used by the leakage
        guard; the formatter owns the actual rendering."""
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True)


def strategy_from_events(events: List[Dict[str, Any]],
                         ledger_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extract an ordered, content-free collaboration strategy from events."""
    calls_by_plan: dict[str, list[str]] = {}
    expert_sequence: list[str] = []
    for event in events:
        if event.get("event_type") != "AGENT_CALL":
            continue
        if event.get("status", "success") != "success":
            continue
        payload = event.get("payload") or {}
        role = str(payload.get("expert") or event.get("tool_or_model") or "").strip()
        if not role:
            continue
        expert_sequence.append(role)
        for parent in event.get("parent_event_ids") or []:
            calls_by_plan.setdefault(str(parent), []).append(role)

    # PLAN.response currently is a JSON list. Read only the allowlisted expert
    # role and intent fields; malformed JSON and arbitrary text are discarded.
    planning_steps: list[dict[str, str]] = []
    for event in events:
        if event.get("event_type") != "PLAN":
            continue
        event_id = str(event.get("event_id") or "")
        called_roles = calls_by_plan.get(event_id, [])
        if not called_roles:
            continue
        response = (event.get("payload") or {}).get("response")
        try:
            proposed = json.loads(response) if isinstance(response, str) else response
        except (json.JSONDecodeError, TypeError):
            proposed = []
        proposed_by_role: dict[str, str] = {}
        if isinstance(proposed, list):
            for item in proposed:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("expert_name") or "").strip()
                if not role:
                    continue
                intent = str(item.get("planning_intent") or "general_review").strip()
                if intent not in PLANNING_INTENTS:
                    intent = "general_review"
                proposed_by_role[role] = intent
        for role in called_roles:
            planning_steps.append({
                "expert_role": role,
                "intent": proposed_by_role.get(role, "general_review"),
            })

    workflow_sequence: list[str] = []
    for event in events:
        action = ("retrieve_evidence" if is_retrieval_result_event(event)
                  else WORKFLOW_ACTIONS.get(str(event.get("event_type") or "")))
        if action and (not workflow_sequence or workflow_sequence[-1] != action):
            workflow_sequence.append(action)
    if len(workflow_sequence) > _MAX_WORKFLOW_SEQUENCE:
        workflow_sequence = workflow_sequence[:_MAX_WORKFLOW_SEQUENCE - 1] + ["additional_steps"]

    counts: Dict[str, int] = {}
    for e in events:
        et = e.get("event_type", "")
        counts[et] = counts.get(et, 0) + 1
    committed = [r for r in ledger_rows if r.get("committed_to_state")]
    return {
        # Keep the old deduplicated field for already-written memory formats;
        # the ordered sequence is the new injection-facing representation.
        "expert_roles_used": list(dict.fromkeys(expert_sequence)),
        "expert_sequence": expert_sequence[:_MAX_EXPERT_SEQUENCE],
        "planning_steps": planning_steps[:_MAX_PLANNING_STEPS],
        "workflow_sequence": workflow_sequence,
        "retrieval_count": sum(1 for event in events
                                if is_retrieval_result_event(event)),
        "backtrack_count": counts.get("BACKTRACK", 0),
        "drilldown_count": counts.get("DRILL_DOWN", 0),
        "graph_update_count": counts.get("GRAPH_UPDATE", 0),
        "analysis_count": counts.get("ANALYSIS", 0),
        "plan_count": counts.get("PLAN", 0),
        "uncommitted_calls": len(ledger_rows) - len(committed),
    }


def grounding_constraints(evidence_profile: Dict[str, int]) -> List[str]:
    """Fixed guidance, selected by the measured tier profile.

    Deterministic: the same profile always yields the same list. The P2 clause
    is not optional, because P2 is where D1's hazard lives — a derived node
    that reads like an external fact.
    """
    out = ["Only use evidence that can be traced to the case input or to a "
           "retrieved record",
           "Verify evidence provenance before narrowing the hypothesis"]
    if evidence_profile.get("p1", 0):
        out.append("Soft-grounded evidence is a lead, not a verified fact; "
                   "check its provenance before relying on it")
    if evidence_profile.get("p2", 0):
        out.append("Derived content (expert synthesis, inference, absence "
                   "claims) must not be treated as external evidence")
    if evidence_profile.get("p3", 0):
        out.append("Unsupported assertions must not be used to support or "
                   "refute a hypothesis")
    return out


def build_candidate(task_id: str, trace_hashes: Dict[str, str],
                    retrieval_key: str, outcome_verdict: str,
                    events: List[Dict[str, Any]],
                    ledger_rows: List[Dict[str, Any]],
                    audit_summary: Dict[str, Any],
                    anomaly_types: List[str]) -> Experience:
    """§5: assemble one candidate. No model, no trace text in the payload."""
    from traceevo.audit.event_schema import utc_now

    strategy = strategy_from_events(events, ledger_rows)
    ev_profile = {
        "p0": audit_summary.get("by_tier", {}).get("P0_DIRECT_GROUNDED", 0),
        "p1": audit_summary.get("by_tier", {}).get("P1_SOFT_GROUNDED", 0),
        "p2": audit_summary.get("by_tier", {}).get("P2_DERIVED", 0),
        "p3": audit_summary.get("by_tier", {}).get("P3_UNSUPPORTED_ASSERTION", 0),
    }
    audit_hash = sha256_text(
        json.dumps(audit_summary, ensure_ascii=False, sort_keys=True))

    # Medium-severity recoverable issues no longer discard a useful correct
    # strategy; they become a generic caveat. High/critical issues still route
    # to prevention memory and can never masquerade as a successful strategy.
    from traceevo.audit.anomaly_detector import SEVERITY
    blocking_anomalies = [
        anomaly for anomaly in anomaly_types
        if SEVERITY.get(anomaly, "critical") in {"high", "critical"}
    ]
    recoverable_anomalies = [
        anomaly for anomaly in anomaly_types
        if anomaly not in blocking_anomalies
    ]
    strategy["risk_flags"] = []
    if ev_profile["p1"]:
        strategy["risk_flags"].append("soft_grounded_evidence_present")
    if recoverable_anomalies:
        strategy["risk_flags"].append("recoverable_execution_warning")

    if blocking_anomalies:
        # A failure experience names the pattern, never the text that failed.
        lead = sorted(blocking_anomalies)[0]
        pattern = FAILURE_PATTERNS.get(lead, {
            "failure_pattern": "ANOMALOUS_EXECUTION", "avoid": [],
            "recommended_check": ["Review the localized anomaly before "
                                  "reusing anything from this trace"]})
        payload: Dict[str, Any] = {
            "type": "validated_failure",
            "failure_pattern": pattern["failure_pattern"],
            "avoid": list(pattern["avoid"]),
            "recommended_check": list(pattern["recommended_check"]),
        }
    else:
        payload = {
            "type": "validated_success",
            "strategy": {k: v for k, v in strategy.items()
                         if k != "uncommitted_calls"},
            "grounding_constraints": grounding_constraints(ev_profile),
        }

    return Experience(
        memory_id=f"exp-{task_id}-{audit_hash[:12]}",
        source_trace_id=task_id,
        task_id=task_id,
        task_retrieval_key=retrieval_key,
        outcome_verdict=outcome_verdict,
        audit_summary_hash=audit_hash,
        payload=payload,
        evidence_profile=ev_profile,
        uncommitted_calls=strategy["uncommitted_calls"],
        created_at=utc_now(),
        source_trace_hashes=dict(trace_hashes),
    )


# --------------------------------------------------------------- §5.2 guards
def forbidden_terms(gold: Dict[str, Any]) -> List[str]:
    """The strings a payload must never contain. Case-insensitive compare."""
    out = []
    for key in ("final_diagnosis", "right_option"):
        v = str(gold.get(key) or "").strip()
        if len(v) >= 3:
            out.append(v)
    return out


def forbidden_content(exp: Experience, gold: Dict[str, Any],
                      trace_prediction: str = "") -> List[str]:
    """Return the forbidden strings this candidate actually contains.

    Scans the injectable half only: the retrieval key legitimately holds case
    text, is never injected, and would otherwise trip every check.
    """
    blob = exp.injectable_text().lower()
    hits = []
    for term in forbidden_terms(gold):
        if term.lower() in blob:
            hits.append(f"gold:{term[:60]}")
    for term in (trace_prediction,):
        t = str(term or "").strip()
        if len(t) >= 8 and t.lower() in blob:
            hits.append(f"prediction:{t[:60]}")
    return hits
