"""Trace integrity auditing (S2 §5 A5).

Two layers, because the hash chain alone is not enough:

1. **Chain** (`integrity.verify_chain`): detects modify, delete-in-the-middle
   and reorder. A re-seal erases this, which is why A0-A4 re-seal and A5 does
   not.

2. **Completeness** (this module): detects **suffix truncation**, which the
   chain cannot see. Cutting the tail off a hash chain leaves a chain in which
   every surviving event still points at its predecessor, so it verifies
   perfectly -- measured on Dev-Small, `verify_chain` returns ok=True for a
   trace whose last three events (including the FINAL_REPORT) were removed.
   The gap is structural: a chain has no notion of how long it was supposed to
   be. What closes it is the cross-artifact invariants a complete run must
   satisfy:

       * the log ends in exactly one FINAL_REPORT
       * every BACKTRACK / DRILL_DOWN / non-ingest GRAPH_UPDATE has a delta,
         and every delta points at an event that exists
       * every retrieval event's record_id exists in the bindings metadata
       * every event's parent_event_ids resolve inside the task

A5 is reported as its own class and is never merged with the semantic
anomalies A0-A4 (§5 is explicit about that).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .integrity import verify_chain


@dataclass
class IntegrityFinding:
    rule: str
    severity: str = "high"
    event_id: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "severity": self.severity,
                "event_id": self.event_id, "detail": self.detail}


def detect_chain(events: Sequence[Dict[str, Any]]) -> List[IntegrityFinding]:
    report = verify_chain(list(events))
    out = []
    if not report.ok:
        out.append(IntegrityFinding(
            rule="hash_chain_broken", severity="critical",
            event_id=(report.issues[0].get("event_id", "")
                      if report.issues else ""),
            detail={"n_issues": len(report.issues),
                    "issues": report.issues[:5]}))
    return out


def detect_completeness(events: Sequence[Dict[str, Any]],
                        deltas: Optional[Sequence[Dict[str, Any]]] = None,
                        bindings: Optional[Dict[str, Any]] = None
                        ) -> List[IntegrityFinding]:
    out: List[IntegrityFinding] = []
    if not events:
        return [IntegrityFinding("empty_trace", "critical",
                                 detail={"n_events": 0})]

    reports = [e for e in events if e.get("event_type") == "FINAL_REPORT"]
    if not reports:
        out.append(IntegrityFinding(
            "missing_final_report", "critical", event_id=events[-1]["event_id"],
            detail={"n_events": len(events), "last_event_type":
                    events[-1].get("event_type"),
                    "hypothesis": ("a complete run ends in a FINAL_REPORT; if "
                                   "the log simply stops, the tail was cut")}))
    elif events[-1].get("event_type") != "FINAL_REPORT":
        out.append(IntegrityFinding(
            "final_report_not_last", "medium", event_id=reports[0]["event_id"],
            detail={"last_event_type": events[-1].get("event_type")}))

    known = {e["event_id"] for e in events}
    for e in events:
        for p in e.get("parent_event_ids") or []:
            if p not in known:
                out.append(IntegrityFinding(
                    "dangling_event_reference", "high", event_id=e["event_id"],
                    detail={"missing_parent": p}))

    if deltas is not None:
        from .dependency_graph import is_applied, is_ingest
        by_event = {d.get("event_id") for d in deltas}
        # A BACKTRACK that deleted nothing and a DRILL_DOWN that added nothing
        # mutate no state, so `hooks.on_seal_graph` deliberately writes no
        # delta for them. Counting them as missing would fire on 4 of the 10
        # clean Dev-Small traces.
        expected = [e["event_id"] for e in events
                    if (e.get("event_type") in ("BACKTRACK", "DRILL_DOWN")
                        and is_applied(e))
                    or (e.get("event_type") == "GRAPH_UPDATE"
                        and not is_ingest(e))]
        missing = [eid for eid in expected if eid not in by_event]
        if missing:
            out.append(IntegrityFinding(
                "missing_graph_delta", "critical", event_id=missing[0],
                detail={"events_without_delta": missing,
                        "n_deltas": len(deltas)}))

    if bindings is not None:
        rec_ids = {r.get("record_id") for r in bindings.get("records") or []}
        for e in events:
            event_type = e.get("event_type")
            payload = e.get("payload") or {}
            combined_retrieval = (
                event_type == "TOOL_CALL" and "retrieval_output" in payload
            )
            if event_type != "EVIDENCE_READ" and not combined_retrieval:
                continue
            for ref in e.get("output_refs") or []:
                if ref not in rec_ids:
                    out.append(IntegrityFinding(
                        "evidence_record_missing", "critical",
                        event_id=e["event_id"],
                        detail={"record_id": ref,
                                "n_records": len(rec_ids)}))
        # A truncation that keeps the FINAL_REPORT but drops the tail of the
        # graph can also show up as nodes whose creating event is gone.
        for n in bindings.get("bindings") or []:
            cbe = n.get("created_by_event")
            if cbe and cbe not in known:
                out.append(IntegrityFinding(
                    "node_creator_event_missing", "high",
                    event_id=cbe,
                    detail={"node_id": n.get("node_id"),
                            "label": (n.get("label") or "")[:80]}))
    return out


def audit_integrity(events: Sequence[Dict[str, Any]],
                    deltas: Optional[Sequence[Dict[str, Any]]] = None,
                    bindings: Optional[Dict[str, Any]] = None
                    ) -> List[IntegrityFinding]:
    return detect_chain(events) + detect_completeness(events, deltas, bindings)
