"""Unified anomaly detection over one task's provenance (S2 §6).

Every detector in the package is run here and normalised into the one JSON
shape §6 specifies, so the metrics never have to know which detector fired:

    {"anomaly_id", "task_id", "type", "root_event_id", "root_node_id",
     "severity", "evidence", "affected_events", "affected_nodes",
     "path_to_conclusion", "detector_rule", "confidence"}

Detection rules
---------------
A0  `degraded_status`            an ANALYSIS the pipeline marked degraded
    `silent_fallback_text`      the fallback text with status=success
A1  `unsupported_assertion`      an ungrounded Evidence node that is used
A2  `record_tampering`           a record whose text no longer matches its hash
    `wrong_record_binding`       a binding whose record does not support it
A3  `invalid_expert_dispatch`    a dispatch the capability policy cannot resolve
    `tool_not_granted`           a role invoking outside its remit
A4  `ungrounded_analysis_quantity`  an ANALYSIS asserting an unsourced measurement
A5  `hash_chain_broken` / completeness failures (kept as their own class)
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

from . import dependency_graph as dep
from .cross_graph_binding import (RetrievalRecord, classify_evidence,
                                  content_tokens, norm, record_hash,
                                  token_coverage)


def _as_records(rows: Sequence[Dict[str, Any]]) -> List[RetrievalRecord]:
    """Rebuild RetrievalRecord objects from the persisted bindings file."""
    out = []
    for r in rows:
        out.append(RetrievalRecord(
            record_id=r.get("record_id", ""),
            retrieval_event_id=r.get("retrieval_event_id", ""),
            query=r.get("query", ""), text=r.get("text", ""),
            step_id=r.get("step_id", 0), expert=r.get("expert", ""),
            task_id=r.get("task_id", ""),
            record_hash=r.get("record_hash", "")))
    return out
from .evidence_auditor import (LBL_ANOMALY, P3, audit_task)
from .execution_auditor import detect_a0, detect_a4, detect_dag_issues
from .integrity_auditor import audit_integrity
from .policy import CapabilityPolicy

SEVERITY = {
    "A0": "medium", "A1": "high", "A2": "critical", "A3": "medium",
    "A4": "high", "A5": "critical",
}

# Confidence is the detector's own strength, not a probability the run is bad.
# A rule that reads a documented field is 1.0; a fuzzy similarity rule is not.
CONFIDENCE = {
    "degraded_status": 1.0,
    "silent_fallback_text": 0.9,
    "unsupported_assertion": 0.8,
    "record_tampering": 1.0,
    "wrong_record_binding": 0.85,
    "invalid_expert_dispatch": 1.0,
    "name_form_mismatch": 1.0,
    "tool_not_granted": 0.9,
    "ungrounded_analysis_quantity": 0.75,
    "ungrounded_analysis_finding": 0.7,
    "hash_chain_broken": 1.0,
    "missing_final_report": 1.0,
    "missing_graph_delta": 1.0,
    "evidence_record_missing": 1.0,
    "dangling_event_reference": 1.0,
    "node_creator_event_missing": 0.9,
    "final_report_not_last": 0.8,
    "empty_trace": 1.0,
}


def _anomaly(task_id: str, atype: str, rule: str, seq: int, *,
             root_event_id: str = "", root_node_id: str = "",
             evidence: Optional[List[Any]] = None,
             detector_rule: str = "", confidence: Optional[float] = None,
             severity: Optional[str] = None, **extra) -> Dict[str, Any]:
    return {
        "anomaly_id": f"{task_id}-{atype}-{seq:03d}",
        "task_id": task_id,
        "type": atype,
        "root_event_id": root_event_id,
        "root_node_id": root_node_id,
        "severity": severity or SEVERITY.get(atype, "medium"),
        "evidence": evidence or [],
        "affected_events": [],
        "affected_nodes": [],
        "path_to_conclusion": [],
        "detector_rule": detector_rule or rule,
        "confidence": CONFIDENCE.get(rule, 0.5) if confidence is None
        else confidence,
        **extra,
    }


# ------------------------------------------------------------------ A2 rules
def detect_a2(art, task_id: str) -> List[Dict[str, Any]]:
    """Record tampering and misbinding.

    `wrong_record_binding` must not fire on a clean trace, so the test is
    deliberately conservative: the bound record has to *fail* to justify the
    label while some other record justifies it clearly. A node whose binding
    is merely loose (paraphrase tier, coverage just over the threshold) is not
    reported -- otherwise the auditor would flag normal retrieval noise.
    """
    out: List[Dict[str, Any]] = []
    seq = 0
    for rec in art.records:
        stored = rec.get("record_hash", "")
        recomputed = record_hash(task_id, rec.get("query", ""),
                                 rec.get("text", ""), rec.get("expert", ""))
        if stored and stored != recomputed:
            seq += 1
            out.append(_anomaly(
                task_id, "A2", "record_tampering", seq,
                root_event_id=rec.get("retrieval_event_id", ""),
                evidence=[{"record_id": rec.get("record_id"),
                           "hash_recorded": stored,
                           "hash_recomputed": recomputed,
                           "text_chars": len(rec.get("text") or "")}],
                detector_rule="record_hash_mismatch"))

    for node in art.nodes:
        bound = node.get("matched_record_id")
        if not bound or node.get("node_type") == "Hypothesis":
            continue
        rec = art.record(bound)
        if rec is None:
            continue
        all_records = _as_records(art.records)
        bound_rec = next((r for r in all_records if r.record_id == bound), None)
        if bound_rec is None:
            continue
        # Reproduce the binding decision against the record it names, using
        # the same classifier that made it. A binding that claims
        # verbatim/paraphrase grounding the named record cannot support has
        # been repointed. Requiring "some *other* record matches better" (the
        # first version of this rule) missed 3 of 10 injections, because a
        # swapped-in record often has no competitor above the threshold.
        recorded_tier = node.get("match_tier", "")
        if recorded_tier not in ("verbatim", "paraphrase"):
            continue
        src, tier, _best, cov = classify_evidence(
            node.get("label", ""), [bound_rec],
            {"verbatim": True, "paraphrase": True,
             "paraphrase_min_token_coverage": 0.6,
             "min_verbatim_chars": 25, "min_content_tokens": 4})
        if src == "RETRIEVED_RECORD":
            continue
        _s2, _t2, better, cov2 = classify_evidence(
            node.get("label", ""), all_records,
            {"verbatim": True, "paraphrase": True,
             "paraphrase_min_token_coverage": 0.6,
             "min_verbatim_chars": 25, "min_content_tokens": 4})
        seq += 1
        out.append(_anomaly(
            task_id, "A2", "wrong_record_binding", seq,
            root_event_id=node.get("created_by_event", ""),
            root_node_id=node.get("node_id", ""),
            evidence=[{"bound_record": bound,
                       "recorded_tier": recorded_tier,
                       "bound_coverage": cov,
                       "recomputed_tier": tier,
                       "best_other_record": better.record_id if better and
                       better.record_id != bound else "",
                       "best_other_coverage": cov2 if better and
                       better.record_id != bound else 0.0,
                       "label": (node.get("label") or "")[:160]}],
            detector_rule="binding_not_reproducible_against_bound_record"))
    return out


# ------------------------------------------------------------------ A3 rules
def detect_a3(events: Sequence[Dict[str, Any]], task_id: str
              ) -> List[Dict[str, Any]]:
    policy = CapabilityPolicy.from_events(events)
    out: List[Dict[str, Any]] = []
    seq = 0
    seen = set()
    for e in events:
        if e.get("event_type") != "AGENT_CALL":
            continue
        requested = ((e.get("payload") or {}).get("expert")
                     or e.get("tool_or_model") or "")
        if requested in seen:
            continue
        check = policy.check_event(e)
        if check is None:
            continue
        seen.add(requested)
        seq += 1
        rule = ("name_form_mismatch" if check.detail.get("cause")
                == "name_form_mismatch" else "invalid_expert_dispatch")
        out.append(_anomaly(
            task_id, "A3", rule, seq,
            root_event_id=e["event_id"],
            evidence=[{"requested": requested,
                       "roster": policy.roster,
                       "near_miss": check.near_miss,
                       "recorded_status": e.get("status"),
                       "recorded_reason": e.get("reason_code"),
                       "detail": check.detail}],
            detector_rule=rule))
    return out


# ------------------------------------------------------- aborted-task audit
def detect_aborted_task(run_dir: str, task_id: str) -> List[Dict[str, Any]]:
    """Audit a task that died before `finish_task` could run.

    A crash leaves `_events.jsonl` (written and flushed per event) and nothing
    else: no bindings, no deltas, no summary, no FINAL_REPORT. That is exactly
    the A3 natural-failure shape, and it is auditable from the event log alone.

    The semantic detectors (A1/A2/A4) are deliberately not run: they read node
    bindings and graph usage, which do not exist. Running them on absent inputs
    would either crash or fabricate findings.
    """
    import json as _json
    import os as _os

    from .execution_auditor import detect_dag_issues
    from .integrity_auditor import audit_integrity
    from .policy import CapabilityPolicy

    path = _os.path.join(run_dir, f"{task_id}_events.jsonl")
    if not _os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        events = [_json.loads(l) for l in f if l.strip()]

    findings: List[Dict[str, Any]] = []
    for f in audit_integrity(events, None, None):
        findings.append(_anomaly(task_id, "A5", f.rule, len(findings) + 1,
                                 root_event_id=f.event_id,
                                 evidence=[f.detail], detector_rule=f.rule,
                                 severity=f.severity))
    # The integrity layer already reports the absent FINAL_REPORT as
    # `missing_final_report`; the DAG check reports the same fact as
    # `no_final_report`. One finding, not two.
    for f in detect_dag_issues(events, task_id):
        if f.rule == "dag_no_final_report" and any(
                x["detector_rule"] == "missing_final_report" for x in findings):
            continue
        findings.append(_anomaly(task_id, "A5", f.rule, len(findings) + 1,
                                 root_event_id=f.event_id,
                                 evidence=[f.detail], detector_rule=f.rule,
                                 severity="high"))

    # A3 is decidable from the event log alone: the roster and the requested
    # name are both in the trace, and the failed dispatch carries its reason.
    policy = CapabilityPolicy.from_events(events)
    seen = set()
    seq = 0
    for e in events:
        if e.get("event_type") != "AGENT_CALL":
            continue
        requested = ((e.get("payload") or {}).get("expert")
                     or e.get("tool_or_model") or "")
        if not requested or requested in seen:
            continue
        check = policy.check_event(e)
        if check is None:
            continue
        seen.add(requested)
        seq += 1
        rule = ("name_form_mismatch"
                if check.detail.get("cause") == "name_form_mismatch"
                else "invalid_expert_dispatch")
        findings.append(_anomaly(
            task_id, "A3", rule, seq, root_event_id=e["event_id"],
            evidence=[{"requested": requested, "roster": policy.roster,
                       "near_miss": check.near_miss,
                       "recorded_status": e.get("status"),
                       "recorded_reason": e.get("reason_code"),
                       "detail": check.detail}],
            detector_rule=rule))
    return findings


# ------------------------------------------------------------------ driver
def detect_task(run_dir: str, task_id: str,
                cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Run every detector over one task directory and return unified anomalies."""
    from .injector import TaskArtifacts
    art = TaskArtifacts.load(run_dir, task_id)
    events, bindings, records = art.events, art.nodes, art.records

    findings: List[Dict[str, Any]] = []

    # §5: A5 is reported as its own class and is *not* mixed into the A0-A4
    # population, because a trace whose log has been tampered with cannot
    # support trustworthy conclusions about what the model did: the node usage
    # and provenance the semantic rules read are themselves derived from that
    # log. Detecting the tampering and stopping is the correct behaviour, not
    # a convenience -- running A1/A4 over a truncated log produced four
    # spurious unsupported-evidence findings on the Dev-Small matrix.
    integrity = audit_integrity(events, art.deltas, art.bindings)
    for f in integrity:
        findings.append(_anomaly(
            task_id, "A5", f.rule, len(findings) + 1,
            root_event_id=f.event_id, evidence=[f.detail],
            detector_rule=f.rule, severity=f.severity))
    for f in detect_dag_issues(events, task_id):
        findings.append(_anomaly(
            task_id, "A5", f.rule, len(findings) + 1,
            root_event_id=f.event_id, evidence=[f.detail],
            detector_rule=f.rule, severity="high"))
    if findings:
        return attach_traces(findings, art, task_id)

    # A1 -- the evidence boundary (§4). Only the high-risk tier is an anomaly.
    for d in audit_task(run_dir, task_id, cfg=cfg):
        if not (d.provenance_tier == P3 and d.audit_label == LBL_ANOMALY):
            continue
        node = art.node(d.node_id) or {}
        findings.append(_anomaly(
            task_id, "A1", "unsupported_assertion", len(findings) + 1,
            root_event_id=node.get("created_by_event", ""),
            root_node_id=d.node_id,
            evidence=[{"label": d.label, "token_coverage": d.token_coverage,
                       "char_similarity": d.char_similarity,
                       "span_ratio": d.best_span_ratio,
                       "analysis_coverage": d.analysis_coverage,
                       "input_coverage": d.input_coverage,
                       "semantic_kind": d.semantic_kind,
                       "used_in_reasoning": d.used_in_reasoning}],
            detector_rule="evidence_tier_p3_used"))

    for f in detect_a0(events):
        findings.append(_anomaly(
            task_id, "A0", f.rule, len(findings) + 1,
            root_event_id=f.event_id, evidence=[f.detail],
            detector_rule=f.rule))

    findings += detect_a2(art, task_id)
    findings += detect_a3(events, task_id)

    for f in detect_a4(events, bindings, records):
        findings.append(_anomaly(
            task_id, "A4", f.rule, len(findings) + 1,
            root_event_id=f.event_id, evidence=[f.detail],
            detector_rule=f.rule))

    return attach_traces(findings, art, task_id)


def attach_traces(findings: List[Dict[str, Any]], art, task_id: str
                  ) -> List[Dict[str, Any]]:
    """Fill affected_events / affected_nodes / path_to_conclusion (§7).

    Detectors report *where* an anomaly is; the tracer says what it reaches.
    Keeping the two apart means a detector can never overstate propagation by
    writing the answer itself.
    """
    from .path_tracer import PathTracer
    edges = [[e["src"], e["dst"], e.get("type", "")]
             for e in art.final_state().get("edges", [])]
    tracer = PathTracer(art.events, art.nodes, edges)
    for f in findings:
        tr = tracer.trace(f.get("root_event_id", ""),
                          f.get("root_node_id", ""), task_id)
        f["affected_events"] = tr.affected_events
        f["affected_nodes"] = tr.affected_nodes
        f["affected_hypotheses"] = tr.affected_hypotheses
        f["path_to_conclusion"] = tr.conclusion_path
        f["reaches_report"] = tr.reaches_report
        f["graph_path"] = tr.graph_path
    return findings
