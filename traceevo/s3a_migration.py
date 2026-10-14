"""S3A §6: the blind migration audit over cases 10-29.

The rules were frozen before this ran (`s3a_freeze.py --write`) and are verified
unchanged after it. Nothing here may tune them: §7 says a surprising number on a
new case is a `NEED_REVIEW`, never a reason to edit a threshold.

Two vocabularies are kept apart on purpose:

    false positive   a finding on a case whose ground truth is known
    alert            a finding on a case whose ground truth is not known

The holdout has no gold labels, so everything below is an *alert rate*. Writing
"FPR" for these numbers would import a claim the data cannot support.

Usage:
    python traceevo/s3a_migration.py \
      --run-dir S3A_runs/s3a_migration/traceevo/runs/migration_holdout \
      --out S3A_migration --cases 10-29
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from traceevo.audit.anomaly_detector import detect_aborted_task, detect_task  # noqa: E402
from traceevo.audit.evidence_auditor import (LBL_ANOMALY, LBL_REVIEW,  # noqa: E402
                                             P0, P1, P3, audit_task,
                                             summarize)
from traceevo.audit.invocation_ledger import load_ledger  # noqa: E402

REVIEW_FLAGS = {"clean_trace_alert_rate_above": 0.25, "p3_rate_above": 0.10}

# §6.2's review-queue columns, in order.
QUEUE_COLUMNS = ["case_id", "event_or_call_id", "alert_type", "text",
                 "source_candidate", "rule", "score", "context",
                 "suggested_review_question"]


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def _ledger_metrics(path: str) -> Dict[str, Any]:
    rows = load_ledger(path)
    n = len(rows)
    if not n:
        return {"calls": 0}
    status: Dict[str, int] = {}
    stage: Dict[str, int] = {}
    for r in rows:
        status[r["parse_status"]] = status.get(r["parse_status"], 0) + 1
        stage[r["stage"]] = stage.get(r["stage"], 0) + 1
    discarded = [r for r in rows if r["parse_status"] == "discarded"]
    return {
        "calls": n,
        "by_parse_status": status,
        "by_stage": stage,
        "uncommitted": sum(1 for r in rows if not r["committed_to_state"]),
        "uncommitted_rate": round(
            sum(1 for r in rows if not r["committed_to_state"]) / n, 4),
        "discarded": len(discarded),
        "discard_rate": round(len(discarded) / n, 4),
        "decision_content_discards": sum(
            1 for r in discarded
            if r["discard_reason"] == "DECISION_CONTENT_MISMATCH"),
        "text_bindings": sum(1 for r in rows if r["binding"] == "text"),
        "positional_bindings": sum(1 for r in rows
                                   if r["binding"] == "positional"),
        "unbound_commits": sum(1 for r in rows if r["stage"] == ""),
        "sources": _count(r["source"] for r in rows),
    }


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _context(records: List[Dict[str, Any]], node: Dict[str, Any]) -> str:
    """The record the node was bound to, trimmed, for a human to read."""
    rid = (node.get("record_id") or "")
    for r in records:
        if r.get("record_id") == rid:
            return (r.get("text") or "")[:280]
    return ""


def analyse_case(run_dir: str, case: int) -> Dict[str, Any]:
    task = f"case-{case}"
    events_path = os.path.join(run_dir, f"{task}_events.jsonl")
    bindings_path = os.path.join(run_dir, f"{task}_bindings.json")
    ledger_path = os.path.join(run_dir, f"{task}_invocations.jsonl")

    out: Dict[str, Any] = {"case_id": case, "task_id": task}
    out["ledger"] = _ledger_metrics(ledger_path)

    if not os.path.exists(events_path):
        out["status"] = "no_trace"
        out["error"] = "the task produced no event log at all"
        return out

    # A task that died before `finish_task` has no bindings, and the semantic
    # auditors cannot load it. That is a reportable outcome, not a crash.
    if not os.path.exists(bindings_path):
        anomalies = detect_aborted_task(run_dir, task)
        out.update({
            "status": "aborted",
            "n_anomalies": len(anomalies),
            "anomalies": [{"type": a["type"], "rule": a["detector_rule"],
                           "root": a["root_event_id"]} for a in anomalies],
            "n_evidence": 0, "by_tier": {}, "n_used_p3": 0,
            "externally_grounded": 0, "lineage_complete": 0,
            "review_queue": [{
                "case_id": case, "event_or_call_id": a["root_event_id"],
                "alert_type": a["type"], "text": "", "source_candidate": "",
                "rule": a["detector_rule"], "score": a["confidence"],
                "context": json.dumps(a.get("evidence") or [], ensure_ascii=False)[:280],
                "suggested_review_question":
                    "the task aborted -- is the failure cause visible and "
                    "correctly attributed?",
            } for a in anomalies],
        })
        return out

    decisions = audit_task(run_dir, task)
    summ = summarize(decisions)
    anomalies = detect_task(run_dir, task)
    # `summarize` aggregates over every node; the §6.2 rates that S2 reported
    # are Evidence-scoped, so they are recomputed here rather than inherited
    # from a denominator that changed.
    ev = [d for d in decisions if d.node_type == "Evidence"]
    ev_grounded = sum(1 for d in ev if d.externally_grounded)
    ev_lineage = sum(1 for d in ev if d.lineage_complete)
    ev_used_p3 = sum(1 for d in ev if d.provenance_tier == P3
                     and d.used_in_reasoning)

    from traceevo.audit.injector import TaskArtifacts
    art = TaskArtifacts.load(run_dir, task)

    queue: List[Dict[str, Any]] = []
    for d in decisions:
        if d.node_type != "Evidence":
            continue
        node = art.node(d.node_id) or {}
        if d.provenance_tier == P3:
            queue.append({
                "case_id": case, "event_or_call_id": node.get(
                    "created_by_event", d.node_id),
                "alert_type": "P3_unsupported_assertion",
                "text": d.label, "source_candidate": d.best_record_id,
                "rule": "evidence_tier_p3", "score": d.token_coverage,
                "context": _context(art.records, node),
                "suggested_review_question":
                    "is this really unsupported, or is the rule blind to a "
                    "kind of grounding it was never shown?",
            })
        elif d.provenance_tier == P1:
            queue.append({
                "case_id": case, "event_or_call_id": node.get(
                    "created_by_event", d.node_id),
                "alert_type": "P1_soft_grounded",
                "text": d.label, "source_candidate": d.best_record_id,
                "rule": "evidence_tier_p1", "score": d.token_coverage,
                "context": _context(art.records, node),
                "suggested_review_question":
                    "is this the same content as the candidate record, or a "
                    "coincidental overlap?",
            })
    for a in anomalies:
        queue.append({
            "case_id": case, "event_or_call_id": a["root_event_id"],
            "alert_type": f"{a['type']}_{a['detector_rule']}",
            "text": "", "source_candidate": a.get("root_node_id", ""),
            "rule": a["detector_rule"], "score": a["confidence"],
            "context": json.dumps(a.get("evidence") or [],
                                  ensure_ascii=False)[:280],
            "suggested_review_question":
                "this trace was never injected; is the finding real, or a "
                "rule that only fits Dev-Small?",
        })

    out.update({
        "status": "complete",
        "n_nodes": summ["n_nodes"],
        "n_evidence": summ["n_evidence"],
        "by_tier": summ["by_tier"],
        "by_derivation": summ.get("by_derivation", {}),
        "n_used_p3": ev_used_p3,
        "p3_rate": summ.get("p3_rate", 0.0),
        "externally_grounded": ev_grounded,
        "lineage_complete": ev_lineage,
        "n_anomalies": len(anomalies),
        "anomalies": [{"type": a["type"], "rule": a["detector_rule"],
                       "root": a["root_event_id"]} for a in anomalies],
        "review_queue": queue,
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cases", default="10-29")
    ap.add_argument("--freeze-manifest", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cases = parse_cases(args.cases)
    per_case = []
    queue: List[Dict[str, Any]] = []
    for c in cases:
        r = analyse_case(args.run_dir, c)
        queue.extend(r.pop("review_queue", []))
        per_case.append(r)
        print(f"  case-{c}: {r['status']} calls="
              f"{r.get('ledger', {}).get('calls', 0)} "
              f"evidence={r.get('n_evidence', 0)} "
              f"anomalies={r.get('n_anomalies', len(r.get('anomalies') or []))}",
              flush=True)

    # ------------------------------------------------------------- aggregate
    complete = [r for r in per_case if r["status"] == "complete"]
    aborted = [r for r in per_case if r["status"] == "aborted"]
    no_trace = [r for r in per_case if r["status"] == "no_trace"]
    n_cases = len(per_case)
    alerted = [r for r in per_case
               if (r.get("n_anomalies") or 0) > 0
               or r["status"] in ("aborted", "no_trace")]
    n_evidence = sum(r.get("n_evidence", 0) for r in complete)
    tiers: Dict[str, int] = {}
    for r in complete:
        for k, v in (r.get("by_tier") or {}).items():
            tiers[k] = tiers.get(k, 0) + v
    grounded = sum(r.get("externally_grounded", 0) for r in complete)
    lineage = sum(r.get("lineage_complete", 0) for r in complete)
    calls = sum(r.get("ledger", {}).get("calls", 0) for r in per_case)
    uncommitted = sum(r.get("ledger", {}).get("uncommitted", 0) for r in per_case)
    discarded = sum(r.get("ledger", {}).get("discarded", 0) for r in per_case)
    mismatches = sum(r.get("ledger", {}).get("decision_content_discards", 0)
                     for r in per_case)
    p3 = tiers.get(P3, 0)

    aggregate = {
        "cases": args.cases,
        "n_cases": n_cases,
        "status_counts": _count(r["status"] for r in per_case),
        "clean_trace_alert_rate": round(len(alerted) / n_cases, 4) if n_cases else 0.0,
        "n_traces_with_an_alert": len(alerted),
        "evidence_alert_rate": round(p3 / n_evidence, 4) if n_evidence else 0.0,
        "n_evidence_nodes": n_evidence,
        "tier_distribution": tiers,
        "externally_grounded": grounded,
        "externally_grounded_rate": (round(grounded / n_evidence, 4)
                                     if n_evidence else 0.0),
        "lineage_complete": lineage,
        "lineage_complete_rate": (round(lineage / n_evidence, 4)
                                  if n_evidence else 0.0),
        "uncommitted_call_rate": round(uncommitted / calls, 4) if calls else 0.0,
        "discarded_call_rate": round(discarded / calls, 4) if calls else 0.0,
        "decision_content_discard_rate": (round(mismatches / calls, 4)
                                          if calls else 0.0),
        "total_calls": calls,
        "aborted_cases": [r["case_id"] for r in aborted],
        "no_trace_cases": [r["case_id"] for r in no_trace],
    }

    flags = []
    if aggregate["clean_trace_alert_rate"] > REVIEW_FLAGS[
            "clean_trace_alert_rate_above"]:
        flags.append({
            "flag": "NEED_REVIEW",
            "what": "clean_trace_alert_rate",
            "value": aggregate["clean_trace_alert_rate"],
            "threshold": REVIEW_FLAGS["clean_trace_alert_rate_above"],
            "action": ("hand the review queue back to external review; §7 "
                       "forbids tuning the rules to bring this down"),
        })
    p3_rate = (p3 / n_evidence) if n_evidence else 0.0
    if p3_rate > REVIEW_FLAGS["p3_rate_above"]:
        flags.append({
            "flag": "NEED_REVIEW", "what": "p3_rate",
            "value": round(p3_rate, 4),
            "threshold": REVIEW_FLAGS["p3_rate_above"],
            "action": "as above: report, do not tune",
        })
    verdict = "NEED_REVIEW" if flags else "GO"
    aggregate["review_flags"] = flags
    aggregate["verdict"] = verdict

    freeze = None
    if args.freeze_manifest and os.path.exists(args.freeze_manifest):
        from traceevo.s3a_freeze import verify
        freeze = verify(args.freeze_manifest)
    aggregate["rule_freeze_verified"] = freeze

    report = {"aggregate": aggregate, "per_case": per_case,
              "review_queue_size": len(queue)}
    with open(os.path.join(args.out, "migration_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    _write_results(args.out, per_case, complete)
    _write_review_queue(args.out, queue)
    _write_evidence(args.out, args.run_dir, cases)
    _write_calls(args.out, per_case, args.run_dir)

    print("\n=== MIGRATION (cases %s) ===" % args.cases)
    for k in ("n_cases", "status_counts", "clean_trace_alert_rate",
              "evidence_alert_rate", "tier_distribution",
              "externally_grounded_rate", "lineage_complete_rate",
              "uncommitted_call_rate", "decision_content_discard_rate",
              "verdict"):
        print(f"  {k:32} {aggregate[k]}")
    if flags:
        for fl in flags:
            print(f"  FLAG {fl['what']} = {fl['value']} > {fl['threshold']}")
    print(f"\nwrote {args.out}/migration_*.csv, migration_summary.json")
    return 0


def _write_results(out: str, per_case: List[Dict[str, Any]],
                   complete: List[Dict[str, Any]]) -> None:
    cols = ["case_id", "status", "n_evidence", "n_nodes", "p0", "p1", "p2", "p3",
            "externally_grounded", "lineage_complete", "p3_rate", "n_anomalies",
            "calls", "uncommitted_rate", "discard_rate",
            "decision_content_discards", "text_bindings",
            "positional_bindings"]
    with open(os.path.join(out, "migration_results.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in per_case:
            t = r.get("by_tier") or {}
            led = r.get("ledger") or {}
            w.writerow([
                r["case_id"], r["status"], r.get("n_evidence", 0),
                r.get("n_nodes", 0), t.get(P0, 0), t.get(P1, 0),
                t.get("P2_DERIVED", 0), t.get(P3, 0),
                r.get("externally_grounded", 0), r.get("lineage_complete", 0),
                r.get("p3_rate", 0.0),
                r.get("n_anomalies", len(r.get("anomalies") or [])),
                led.get("calls", 0), led.get("uncommitted_rate", 0.0),
                led.get("discard_rate", 0.0),
                led.get("decision_content_discards", 0),
                led.get("text_bindings", 0), led.get("positional_bindings", 0),
            ])


def _write_review_queue(out: str, queue: List[Dict[str, Any]]) -> None:
    with open(os.path.join(out, "migration_review_queue.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS)
        w.writeheader()
        for row in queue:
            w.writerow({k: row.get(k, "") for k in QUEUE_COLUMNS})


def _write_evidence(out: str, run_dir: str, cases: List[int]) -> None:
    """One row per Evidence node, same columns as the S2 boundary CSV."""
    from traceevo.audit.evidence_auditor import CSV_COLUMNS
    path = os.path.join(out, "migration_evidence_analysis.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for c in cases:
            task = f"case-{c}"
            if not os.path.exists(os.path.join(run_dir, f"{task}_bindings.json")):
                continue
            for d in audit_task(run_dir, task):
                w.writerow({k: getattr(d, k, "") for k in CSV_COLUMNS})


def _write_calls(out: str, per_case: List[Dict[str, Any]],
                 run_dir: str) -> None:
    cols = ["case_id", "call_id", "step_id", "actor_id", "actor_role", "stage",
            "model_id", "prompt_hash", "response_hash", "parse_status",
            "discard_reason", "committed_to_state", "commit_target",
            "committed_event_ids", "binding", "source"]
    with open(os.path.join(out, "migration_call_analysis.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in per_case:
            task = r["task_id"]
            path = os.path.join(run_dir, f"{task}_invocations.jsonl")
            for row in load_ledger(path):
                row = dict(row)
                row["case_id"] = r["case_id"]
                row["committed_event_ids"] = "|".join(
                    row.get("committed_event_ids") or [])
                w.writerow({k: row.get(k, "") for k in cols})


if __name__ == "__main__":
    raise SystemExit(main())
