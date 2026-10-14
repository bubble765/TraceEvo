"""S2 §8.3 metrics over the injection matrix.

    python3 traceevo/s2_matrix.py --clean <run_dir> --injected S2A_inject \
        --out S2_audit

Every metric is defined here rather than left implicit, because several of them
are easy to state in a way that flatters the system:

**Detection.** Per injected type T, a trace is a true positive when the auditor
reports at least one anomaly of type T on it. A detection of type T on a trace
injected with T' != T (or on a clean trace) is a false positive for T. The full
injected-by-detected confusion matrix is written out, so A4 -> A1 propagation
(a corrupted analysis becoming an unsourced Evidence node downstream) is visible
as data rather than hidden inside a recall number.

**Root cause.** The injected root is compared against the reported root of the
matching-type anomaly. A1 roots on the fabricated *node*, the others on the
event the injector edited.

**Propagation.** `affected_event_recall` compares the detector's downstream set
to the injector's. Both are computed from the execution DAG, so this measures
that the *plumbing* is wired correctly, not that the DAG is the true causal
story -- §7 forbids reading it that way, and the report says so. The semantic
question ("did the corruption actually reach the graph and the report?") is
measured separately and independently by text, in `semantic_reach`.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "gos"))

from traceevo.audit.anomaly_detector import detect_task  # noqa: E402
from traceevo.audit.evidence_auditor import audit_task, summarize  # noqa: E402
from traceevo.audit.injector import TaskArtifacts  # noqa: E402

TYPES = ("A0", "A1", "A2", "A3", "A4", "A5")


# --------------------------------------------------------------------- utils
def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def load_ground_truth(path: str) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    """(anomaly_type, variant, case_id) -> injection record."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            out[(r["anomaly_type"], r.get("variant", ""), r["case_id"])] = r
    return out


def semantic_reach(inj: Dict[str, Any], art: TaskArtifacts) -> Dict[str, Any]:
    """Did the injected content actually surface in the graph and the report?

    Independent of the DAG: it looks for the injected text itself.
    """
    needle = ""
    detail = inj.get("detail") or {}
    needle = (detail.get("fabricated_label")
              or detail.get("fabricated_finding") or "")
    if not needle:
        return {"applicable": False}
    from traceevo.audit.cross_graph_binding import token_coverage
    nodes = [n for n in art.nodes
             if token_coverage(needle, n.get("label", "")) >= 0.5]
    report_text = " ".join(
        str((e.get("payload") or {}).get("report", "")) + " " +
        str((e.get("payload") or {}).get("prediction", ""))
        for e in art.events if e.get("event_type") == "FINAL_REPORT")
    return {"applicable": True,
            "reached_graph": bool(nodes),
            "nodes": [n["node_id"] for n in nodes],
            "reached_report": token_coverage(needle, report_text) >= 0.3}


def evaluate_trace(run_dir: str, task_id: str,
                   injected_type: str = "clean",
                   inj: Optional[Dict[str, Any]] = None
                   ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    anomalies = detect_task(run_dir, task_id)
    art = TaskArtifacts.load(run_dir, task_id)
    row: Dict[str, Any] = {"task_id": task_id, "injected_type": injected_type,
                           "n_anomalies": len(anomalies),
                           "detected_types": sorted({a["type"] for a in anomalies})}

    if inj and "skipped" not in inj:
        root_event = inj.get("root_event_id", "")
        row["latent_root_event_id"] = inj.get("latent_root_event_id", root_event)
        row["observable_root_event_id"] = inj.get(
            "observable_root_event_id", root_event)
        row["latent_root_is_observable"] = (
            row["latent_root_event_id"] == row["observable_root_event_id"])
        root_node = inj.get("root_node_id", "")
        matching = [a for a in anomalies if a["type"] == injected_type]
        row["detected"] = bool(matching)
        row["root_event_match"] = any(
            a.get("root_event_id") == root_event for a in matching)
        row["root_node_match"] = any(
            a.get("root_node_id") == root_node for a in matching
        ) if root_node else row["root_event_match"]
        # Top-1: the auditor's most confident explanation must name the true
        # root. Per-rule confidences are coarse constants, so several findings
        # routinely tie; a tie is not a ranking, and the check is applied to
        # the tied top group rather than to list order.
        if matching:
            top = max(a.get("confidence", 0.0) for a in matching)
            leaders = [a for a in matching
                       if a.get("confidence", 0.0) == top]
            row["root_top1"] = any(
                a.get("root_event_id") == root_event and
                (not root_node or a.get("root_node_id") == root_node)
                for a in leaders)
        else:
            row["root_top1"] = False
        gt_events = set(inj.get("affected_events") or [])
        got_events = set()
        for a in matching:
            got_events |= set(a.get("affected_events") or [])
        row["affected_event_recall"] = (len(gt_events & got_events) / len(gt_events)
                                        if gt_events else None)
        row["reaches_report"] = any(a.get("reaches_report") for a in matching)
        row["semantic"] = semantic_reach(inj, art)
    else:
        row.update({"detected": None, "root_event_match": None,
                    "root_node_match": None, "root_top1": None,
                    "affected_event_recall": None, "reaches_report": None,
                    "semantic": None})

    # A5 is also checked on every trace: an untampered trace must not raise one.
    row["integrity_flagged"] = any(a["type"] == "A5" for a in anomalies)
    return anomalies, row


def per_class_metrics(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Three quantities per anomaly type, kept apart on purpose (S2.1 §2.1).

    The single-label view mixes two different things and produces a number that
    is worse than either. So:

    **Primary detection.** Did the auditor find *the anomaly that was
    injected*? `tp`/`fn` over the traces injected with this type; `fp` counts
    only detections on **clean** traces. This is the detection performance.

    **Secondary consequences.** A detection of type T on a trace injected with
    T' != T. Degrading an ANALYSIS (A0) or tampering a record (A2) genuinely
    leaves downstream Evidence nodes unsourced, so the A1 rule firing there is
    correct behaviour observed on a multi-anomaly trace -- it is not a false
    positive, and calling it one made A1 look broken when it was not.
    Reported as its own column and excluded from `precision`.

    **Strict view.** `precision_strict` keeps the old single-label arithmetic
    for continuity. It is not the headline number.
    """
    out = []
    for t in TYPES:
        tp = sum(1 for r in rows if r["injected_type"] == t and r.get("detected"))
        fn = sum(1 for r in rows if r["injected_type"] == t
                 and r.get("detected") is False)
        fp_clean = sum(1 for r in rows if r["injected_type"] == "clean"
                       and t in (r.get("detected_types") or []))
        secondary = sum(1 for r in rows if r["injected_type"] not in ("clean", t)
                        and t in (r.get("detected_types") or []))
        n_clean = sum(1 for r in rows if r["injected_type"] == "clean")
        # Primary precision: false positives are clean-trace detections only.
        precision = tp / (tp + fp_clean) if (tp + fp_clean) else None
        precision_strict = (tp / (tp + fp_clean + secondary)
                            if (tp + fp_clean + secondary) else None)
        recall = tp / (tp + fn) if (tp + fn) else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else None)
        f1_strict = (2 * precision_strict * recall
                     / (precision_strict + recall)
                     if precision_strict and recall else None)
        out.append({
            "type": t, "n_injected": tp + fn, "tp": tp, "fn": fn,
            "fp_on_clean": fp_clean,
            "clean_fp_rate": round(fp_clean / n_clean, 4) if n_clean else None,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 else None,
            "secondary_consequence_detections": secondary,
            "precision_strict": (round(precision_strict, 4)
                                 if precision_strict is not None else None),
            "f1_strict": round(f1_strict, 4) if f1_strict else None,
        })
    def _mean(key):
        vals = [r[key] for r in out if isinstance(r.get(key), float)]
        return round(sum(vals) / len(vals), 4) if vals else None

    out.append({"type": "MACRO", "n_injected": "",
                "tp": "", "fn": "", "fp_on_clean": "", "clean_fp_rate": "",
                "precision": _mean("precision"), "recall": _mean("recall"),
                "f1": _mean("f1"),
                "secondary_consequence_detections": sum(
                    r["secondary_consequence_detections"] for r in out
                    if isinstance(r["secondary_consequence_detections"], int)),
                "precision_strict": _mean("precision_strict"),
                "f1_strict": _mean("f1_strict")})
    return out


# -------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", required=True)
    ap.add_argument("--injected", default="S2A_inject")
    ap.add_argument("--out", default="S2_audit")
    ap.add_argument("--cases", default="0-9")
    ap.add_argument("--gt", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cases = [f"case-{i}" for i in parse_cases(args.cases)]
    gt = load_ground_truth(args.gt or os.path.join(
        args.injected, "injection_ground_truth.jsonl"))

    all_rows: List[Dict[str, Any]] = []
    all_anomalies: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- clean
    clean_decisions = []
    for task in cases:
        path = os.path.join(args.clean, f"{task}_bindings.json")
        if not os.path.exists(path):
            continue
        anomalies, row = evaluate_trace(args.clean, task, "clean")
        all_rows.append(row)
        all_anomalies += anomalies
        clean_decisions += audit_task(args.clean, task)
    clean_summary = summarize(clean_decisions)

    # ------------------------------------------------------------- injected
    if os.path.isdir(args.injected):
        for atype in sorted(os.listdir(args.injected)):
            tdir = os.path.join(args.injected, atype)
            if not os.path.isdir(tdir):
                continue
            for variant in sorted(os.listdir(tdir)):
                vdir = os.path.join(tdir, variant)
                if not os.path.isdir(vdir):
                    continue
                for task in sorted(os.listdir(vdir)):
                    rundir = os.path.join(vdir, task)
                    if not os.path.exists(
                            os.path.join(rundir, f"{task}_bindings.json")):
                        continue          # skipped injections have no artifacts
                    inj = gt.get((atype, variant, task))
                    anomalies, row = evaluate_trace(rundir, task, atype, inj)
                    row["variant"] = variant
                    all_rows.append(row)
                    all_anomalies += anomalies

    metrics = per_class_metrics(all_rows)
    injected_rows = [r for r in all_rows if r["injected_type"] != "clean"]

    # ------------------------------------------------------------- outputs
    def write_csv(name, rows, cols):
        path = os.path.join(args.out, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
            w.writeheader()
            for r in rows:
                w.writerow({c: (json.dumps(r.get(c), ensure_ascii=False)
                                if isinstance(r.get(c), (dict, list))
                                else r.get(c, "")) for c in cols})
        return path

    clean_cols = ["task_id", "injected_type", "n_anomalies", "detected_types",
                  "integrity_flagged"]
    write_csv("clean_audit_results.csv",
              [r for r in all_rows if r["injected_type"] == "clean"], clean_cols)

    anom_cols = ["task_id", "injected_type", "variant", "detected", "root_top1",
                 "root_event_match", "root_node_match", "affected_event_recall",
                 "reaches_report", "integrity_flagged", "semantic",
                 "latent_root_event_id", "observable_root_event_id",
                 "latent_root_is_observable"]
    write_csv("anomaly_results.csv", injected_rows, anom_cols)

    write_csv("per_class_metrics.csv", metrics,
              ["type", "n_injected", "tp", "fp", "fn", "fp_on_clean",
               "prevalence_on_clean", "fp_on_other_injections",
               "n_traces_other_types", "precision", "precision_clean",
               "recall", "f1"])

    root_rows = [r for r in injected_rows if r.get("detected")]
    write_csv("root_cause_results.csv",
              [{"task_id": r["task_id"], "injected_type": r["injected_type"],
                "variant": r.get("variant", ""),
                "root_top1": r["root_top1"],
                "root_event_match": r["root_event_match"],
                "root_node_match": r["root_node_match"],
                "latent_root_event_id": r.get("latent_root_event_id", ""),
                "observable_root_event_id": r.get("observable_root_event_id", ""),
                "latent_root_is_observable": r.get("latent_root_is_observable")}
               for r in root_rows],
              ["task_id", "injected_type", "variant", "root_top1",
               "root_event_match", "root_node_match", "latent_root_event_id",
               "observable_root_event_id", "latent_root_is_observable"])

    prop_rows = [{"task_id": r["task_id"], "injected_type": r["injected_type"],
                  "variant": r.get("variant", ""),
                  "affected_event_recall": r.get("affected_event_recall"),
                  "reaches_report": r.get("reaches_report"),
                  "semantic_reached_graph": (r.get("semantic") or {}).get("reached_graph"),
                  "semantic_reached_report": (r.get("semantic") or {}).get("reached_report")}
                 for r in injected_rows]
    write_csv("propagation_results.csv", prop_rows,
              ["task_id", "injected_type", "variant", "affected_event_recall",
               "reaches_report", "semantic_reached_graph",
               "semantic_reached_report"])

    with open(os.path.join(args.out, "anomalies.jsonl"), "w",
              encoding="utf-8") as f:
        for a in all_anomalies:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------- summary
    conf = Counter((r["injected_type"], t)
                   for r in all_rows for t in (r.get("detected_types") or []))
    clean_task_hits = sum(1 for r in all_rows
                          if r["injected_type"] == "clean" and r["n_anomalies"])
    detected = [r for r in injected_rows if r.get("detected")]
    a5_gt = [r for r in injected_rows if r["injected_type"] == "A5" and r.get("detected") is not None]
    summary = {
        "cases": cases,
        "clean": {
            "n_traces": sum(1 for r in all_rows if r["injected_type"] == "clean"),
            "traces_with_findings": clean_task_hits,
            "trace_fpr": (round(clean_task_hits / max(1, sum(
                1 for r in all_rows if r["injected_type"] == "clean")), 4)),
            "evidence": clean_summary,
        },
        "detection": {r["type"]: {k: r.get(k) for k in
                                  ("n_injected", "tp", "fn", "fp_on_clean",
                                   "clean_fp_rate", "precision", "recall", "f1",
                                   "secondary_consequence_detections",
                                   "precision_strict", "f1_strict")}
                      for r in metrics},
        "confusion_injected_to_detected": {f"{a}->{b}": n
                                           for (a, b), n in sorted(conf.items())},
        # S2.1 §2.2: named for what it measures. Detection is scored against
        # the *observable* root, because for A5 delete/truncate the latent
        # root no longer exists in the trace and no detector could name it.
        "observable_root_localization": {
            "metric": "Observable-root Top-1 localization accuracy",
            "n_detected": len(detected),
            "top1": sum(1 for r in detected if r.get("root_top1")),
            "accuracy": (round(sum(1 for r in detected if r.get("root_top1"))
                               / len(detected), 4) if detected else None),
            "root_event_present": sum(1 for r in detected
                                      if r.get("root_event_match")),
            "caveat": ("latent injection root and observable failure seam are "
                       "distinct concepts; this metric scores the latter"),
        },
        "propagation": {
            "affected_event_recall_mean": _mean(
                [r.get("affected_event_recall") for r in detected]),
            "reaches_report_rate": _mean(
                [1.0 if r.get("reaches_report") else 0.0 for r in detected]),
        },
        "dag_consistency": _dag_consistency(args.clean, args.injected, cases),
        "integrity": {
            "a5_injected": len(a5_gt),
            "a5_detected": sum(1 for r in a5_gt if r["detected"]),
            "a5_detection_rate": (round(sum(1 for r in a5_gt if r["detected"])
                                        / len(a5_gt), 4) if a5_gt else None),
            "false_integrity_alarms_on_correct_traces": sum(
                1 for r in all_rows
                if r["injected_type"] in ("clean", "A0", "A1", "A2", "A3", "A4")
                and r["integrity_flagged"]),
        },
    }
    with open(os.path.join(args.out, "matrix_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nwrote metrics to {args.out}/")
    return 0


def _dag_consistency(clean_dir: str, injected_dir: str,
                     cases: Sequence[str]) -> Dict[str, Any]:
    """§9: the §3.2 dependency DAG must check out on every trace we accept.

    A5 traces are excluded by construction -- their logs are deliberately
    damaged, and "the DAG is inconsistent" is the correct finding there.
    """
    from traceevo.audit.dependency_graph import check
    ok = bad = a5 = 0
    examples = []
    def visit(rundir: str, task: str, is_a5: bool):
        nonlocal ok, bad, a5
        path = os.path.join(rundir, f"{task}_events.jsonl")
        if not os.path.exists(path):
            return
        events = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        _dag, rep = check(events, task)
        if is_a5 or rep.by_kind().get("missing_final_report"):
            a5 += 1
            return
        if rep.ok:
            ok += 1
        else:
            bad += 1
            examples.append({"task": task, "dir": rundir,
                             "issues": rep.by_kind()})

    for task in cases:
        visit(clean_dir, task, False)
    if os.path.isdir(injected_dir):
        for atype in sorted(os.listdir(injected_dir)):
            tdir = os.path.join(injected_dir, atype)
            if not os.path.isdir(tdir):
                continue
            for variant in sorted(os.listdir(tdir)):
                vdir = os.path.join(tdir, variant)
                if not os.path.isdir(vdir):
                    continue
                for task in sorted(os.listdir(vdir)):
                    visit(os.path.join(vdir, task), task, atype == "A5")
    total = ok + bad
    return {"traces_checked": total, "consistent": ok, "inconsistent": bad,
            "excluded_tampered_or_truncated": a5,
            "rate": round(ok / total, 4) if total else None,
            "examples": examples[:5]}


def _mean(xs: Iterable[Optional[float]]) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


if __name__ == "__main__":
    raise SystemExit(main())
