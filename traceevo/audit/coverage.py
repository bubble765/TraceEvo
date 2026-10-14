"""T2: required-operation coverage (amendment §6 T2).

`coverage = observed_required_events / expected_required_events`

The expected counts are derived from the **upstream GoS trace log** written by
`utils.public_function.log_to_file` -- a source the provenance layer does not
control and cannot influence. That independence is what makes coverage a check
on the instrumentation rather than a restatement of it.

Marker -> required event mapping (see S1_provenance/event_manifest.md):

    "generating basic symptom and evidence nodes"  -> GRAPH_UPDATE (ingest)
    "generating L1 hypos"                          -> GRAPH_UPDATE (ingest)
    "Here are the plan of central expert"          -> PLAN
    "Step 1 - Decision Making"                     -> AGENT_CALL (+ANALYSIS)
    "Step N Observations:"                         -> completed TOOL_CALL
    "Central agent decided to update the graph"    -> GRAPH_UPDATE
    "backtracking invoked"                         -> BACKTRACK
    '"type": 2' after "report or dive deeper"      -> DRILL_DOWN
    final report response                          -> FINAL_REPORT
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

INGEST_BASIC = "generating basic symptom and evidence nodes"
INGEST_L1 = "generating L1 hypos"
PLAN = "Here are the plan of central expert"
EXPERT_RUN = "Step 1 - Decision Making"
OBSERVATION = re.compile(r"Step \d+ Observations:")
GRAPH_UPDATE = "Central agent decided to update the graph"
BACKTRACK = "backtracking invoked"
REFINE_OR_REPORT = "decide to report or dive deeper"
TYPE2 = re.compile(r'"type"\s*:\s*2')

# event_type -> whether it must appear in every run
REQUIRED = {
    "GRAPH_UPDATE": True, "PLAN": True, "AGENT_CALL": True, "TOOL_CALL": True,
    # EVIDENCE_READ is a legacy event type. New traces combine request and
    # returned content on one TOOL_CALL event.
    "EVIDENCE_READ": False, "ANALYSIS": True, "FINAL_REPORT": True,
    "BACKTRACK": False, "DRILL_DOWN": False,
}


@dataclass
class CoverageRow:
    task_id: str
    event_type: str
    expected: int
    observed: int
    required: bool

    @property
    def covered(self) -> bool:
        return self.observed >= self.expected

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "event_type": self.event_type,
                "required": self.required, "expected": self.expected,
                "observed": self.observed, "covered": self.covered,
                "missing": max(0, self.expected - self.observed)}


def expected_from_trace(trace_path: str) -> Dict[str, int]:
    """Independent expectation of required operations, from the upstream trace."""
    with open(trace_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Only the drill-down/report responses count toward DRILL_DOWN; the earlier
    # "type" keys belong to other prompts.
    refine_responses = text.count(REFINE_OR_REPORT)
    drilldown = len(TYPE2.findall(text))

    return {
        "GRAPH_UPDATE": (int(INGEST_BASIC in text) + int(INGEST_L1 in text)
                         + text.count(GRAPH_UPDATE)),
        "PLAN": text.count(PLAN),
        "AGENT_CALL": text.count(EXPERT_RUN),
        "ANALYSIS": text.count(EXPERT_RUN),
        "TOOL_CALL": len(OBSERVATION.findall(text)),
        "EVIDENCE_READ": 0,
        "BACKTRACK": text.count(BACKTRACK),
        "DRILL_DOWN": drilldown if refine_responses else 0,
        "FINAL_REPORT": 1,
    }


def observed_from_events(events: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for e in events:
        counts[e["event_type"]] = counts.get(e["event_type"], 0) + 1
    return counts


def compare(task_id: str, trace_path: str,
            events: List[Dict[str, Any]]) -> List[CoverageRow]:
    expected = expected_from_trace(trace_path)
    observed = observed_from_events(events)
    rows: List[CoverageRow] = []
    for etype in sorted(set(expected) | set(observed)):
        rows.append(CoverageRow(
            task_id=task_id, event_type=etype,
            expected=expected.get(etype, 0), observed=observed.get(etype, 0),
            required=REQUIRED.get(etype, False)))
    return rows


def summarise(rows: List[CoverageRow]) -> Dict[str, Any]:
    req = [r for r in rows if r.required]
    exp = sum(r.expected for r in req)
    obs = sum(min(r.observed, r.expected) for r in req)
    return {
        "required_event_types": len(req),
        "expected_required_events": exp,
        "observed_required_events": obs,
        "coverage": round(obs / exp, 4) if exp else None,
        "all_covered": all(r.covered for r in req),
        "uncovered_types": [r.event_type for r in req if not r.covered],
    }


def find_trace(case_log_dir: str) -> Optional[str]:
    if not os.path.isdir(case_log_dir):
        return None
    logs = sorted(f for f in os.listdir(case_log_dir) if f.endswith(".txt"))
    return os.path.join(case_log_dir, logs[-1]) if logs else None
