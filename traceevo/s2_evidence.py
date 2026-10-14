"""S2 §4 driver: evidence boundary analysis + threshold sensitivity.

    python3 traceevo/s2_evidence.py --runs <dir> --out S2_audit \
        --cases 0-9

Reads the per-task artifacts a T1 run produced (bindings / events / deltas),
writes `evidence_boundary_analysis.csv` for every node, and sweeps the
thresholds §4.2 requires to be reported rather than silently chosen.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "gos"))

from traceevo.audit.evidence_auditor import (  # noqa: E402
    DEFAULT_CFG, P3, audit_task, sensitivity, summarize, write_csv)


def parse_cases(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def find_run_dir(root: str) -> str:
    """Locate the directory holding the per-task artifacts."""
    if os.path.isfile(os.path.join(root, "case-0_bindings.json")):
        return root
    for dirpath, _dirs, files in os.walk(root):
        if "case-0_bindings.json" in files:
            return dirpath
    raise SystemExit(f"no per-task artifacts under {root}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True,
                    help="directory produced by the C (provenance-on) run")
    ap.add_argument("--out", default="S2_audit")
    ap.add_argument("--cases", default="0-9", help="e.g. 0-9 or 0,3,7")
    ap.add_argument("--csv-name", default="evidence_boundary_analysis.csv")
    args = ap.parse_args()

    run_dir = find_run_dir(args.runs)
    os.makedirs(args.out, exist_ok=True)
    cases = parse_cases(args.cases)
    print(f"artifacts : {run_dir}")
    print(f"cases     : {cases}")

    decisions = []
    per_case = {}
    for i in cases:
        task = f"case-{i}"
        if not os.path.exists(os.path.join(run_dir, f"{task}_bindings.json")):
            print(f"  {task}: missing artifacts, skipped")
            continue
        d = audit_task(run_dir, task)
        decisions += d
        per_case[task] = summarize(d)

    if not decisions:
        raise SystemExit("nothing audited")

    csv_path = os.path.join(args.out, args.csv_name)
    write_csv(decisions, csv_path)
    overall = summarize(decisions)

    # ------------------------------------------------------- sensitivity (§4.2)
    sens_rows = []
    for row in sensitivity():
        cfg = row["cfg"]
        ds = []
        for i in cases:
            task = f"case-{i}"
            if os.path.exists(os.path.join(run_dir, f"{task}_bindings.json")):
                ds += audit_task(run_dir, task, cfg=cfg)
        s = summarize(ds)
        sens_rows.append({"threshold": row["threshold"], "value": row["value"],
                          "p3_total": s["p3_total"], "p3_rate": s["p3_rate"],
                          "fpr": s["fpr"], "anomalies": s["anomalies"]})
    sens_path = os.path.join(args.out, "evidence_boundary_sensitivity.csv")
    with open(sens_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["threshold", "value", "p3_total",
                                          "p3_rate", "fpr", "anomalies"],
                           quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(sens_rows)

    summary = {
        "run_dir": run_dir,
        "cases": cases,
        "config": DEFAULT_CFG,
        "overall": overall,
        "per_case": per_case,
        "outputs": {"csv": csv_path, "sensitivity": sens_path},
        "p3_nodes": [
            {"case_id": d.case_id, "node_id": d.node_id, "label": d.label,
             "audit_label": d.audit_label, "used": d.used_in_reasoning,
             "token_coverage": d.token_coverage,
             "span_ratio": d.best_span_ratio,
             "analysis_coverage": d.analysis_coverage,
             "input_coverage": d.input_coverage}
            for d in decisions if d.provenance_tier == P3],
    }
    with open(os.path.join(args.out, "evidence_boundary_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(overall, ensure_ascii=False, indent=1))
    print(f"\nwrote {csv_path}")
    print(f"wrote {sens_path}")
    print(f"P3 nodes: {len(summary['p3_nodes'])}")
    for p in summary["p3_nodes"]:
        print(f"  {p['case_id']} {p['node_id']} [{p['audit_label']}] "
              f"cov={p['token_coverage']} span={p['span_ratio']} "
              f"an={p['analysis_coverage']} in={p['input_coverage']} | "
              f"{p['label'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
