"""S4B-D §10: count decision-path P3 risk in case 60-69 **without reading gold**.

§10's branch depends entirely on this number and §9 forbids computing it from
the answer: "在不读取 gold diagnosis 的情况下统计". Both are satisfied
structurally here — this module opens `_events.jsonl`, `_bindings.json` and
`_deltas.jsonl`, and the only other file it touches is the *blinded* dataset
copy, which has no gold column in it. There is no code path in this module
that could read a diagnosis even by accident, which is a stronger statement
than a promise not to.

What counts as a decision-path P3 is the Gate's own definition, taken from
`experience_gate`: a node whose tier is `P3_UNSUPPORTED_ASSERTION` and whose
`used_in_reasoning` is true. Using the Gate's rule rather than a new one keeps
the trigger density comparable with the 10/40 cases S4B-A measured over
case 10-59.

Usage:
    python traceevo/s4b_risk.py --arm baseline
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S4B_adaptive_guard")
CASES = list(range(60, 70))
ARM_TAG = {"baseline": "atgg_off", "guard": "atgg_on"}

LOW_TRIGGER_DENSITY = "LOW_TRIGGER_DENSITY"
QUALITATIVE_ONLY = "QUALITATIVE_ONLY"
FULL_ARM = "FULL_ARM_ELIGIBLE"


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def risk_for_case(artifacts: str, case: int) -> Dict[str, Any]:
    from traceevo.s3b_gate import audit_bundle

    task = f"case-{case}"
    b = audit_bundle(artifacts, task)
    p3_used, p3_unused, derived_used = [], [], []
    for d in b["decisions"]:
        t = d.get("provenance_tier")
        if t == "P3_UNSUPPORTED_ASSERTION":
            (p3_used if d.get("used_in_reasoning") else p3_unused).append(d)
        elif d.get("used_in_reasoning") and t == "P2_DERIVED":
            derived_used.append(d)

    # §12.1 lists "Decision-path ungrounded Evidence count" separately from the
    # P3 rate. For Evidence nodes the two coincide by construction -- P3 *is*
    # "no source and no legitimate derivation" -- so the count below is the
    # same numerator as `n_p3_used` and is reported under the name §12.1 uses.
    #
    # The first version defined it as `used and not externally_grounded and not
    # lineage_complete`, which also catches P2 nodes whose derivation is
    # legitimate but self-declared (hypothesis, absence_claim, expert_synthesis
    # -- SELF_DECLARED, so `lineage_complete` is False). On case 60 that
    # reported 2 "ungrounded" nodes on the decision path, both of which were
    # P2 hypotheses. A number named "ungrounded evidence" that counts the
    # reasoning the pipeline is *supposed* to do overstates the risk it claims
    # to measure, and in a stage whose decision turns on how much P3 there is,
    # overstating it is the error that matters.
    ungrounded = list(p3_used)

    guard_log = os.path.join(artifacts, "guard_actions.jsonl")
    actions = _jsonl(guard_log)
    return {
        "case": case, "task_id": task,
        "n_decisions": len(b["decisions"]),
        "n_p3_used": len(p3_used),
        "n_p3_unused": len(p3_unused),
        "n_decision_path_ungrounded": len(ungrounded),
        "n_derived_used": len(derived_used),
        "derived_derivations": sorted({d.get("derivation", "")
                                       for d in derived_used}),
        "has_decision_path_p3": bool(p3_used),
        "p3_used_nodes": [d["node_id"] for d in p3_used],
        "p3_used_labels": [(d.get("label") or "")[:90] for d in p3_used],
        "p3_used_derivations": [d.get("derivation", "") for d in p3_used],
        "anomaly_types": sorted({a.get("type") for a in b["anomalies"]}),
        "n_anomalies": len(b["anomalies"]),
        "guard_events": len(actions),
        "guard_triggers": sum(1 for a in actions
                              if a.get("guard_event") == "GUARD_TRIGGER"),
        "guard_results": [a.get("guard_result") for a in actions
                          if a.get("guard_result")],
    }


def survey(out_dir: str, arm: str, cases: List[int]) -> Dict[str, Any]:
    tag = ARM_TAG[arm]
    artifacts = os.path.join(out_dir, f"{arm}_60_69", "traceevo", "runs", tag)
    rows = [risk_for_case(artifacts, c) for c in cases]
    triggered = [r for r in rows if r["has_decision_path_p3"]]
    branch = (LOW_TRIGGER_DENSITY if not triggered
              else QUALITATIVE_ONLY if len(triggered) == 1
              else FULL_ARM)

    with open(os.path.join(out_dir, f"baseline_risk_summary_{tag}.csv"), "w",
              newline="", encoding="utf-8") as f:
        cols = [k for k in rows[0] if not isinstance(rows[0][k], list)]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in cols})

    out = {
        "arm": arm, "cases": cases,
        "n_cases": len(rows),
        "n_cases_with_decision_path_p3": len(triggered),
        "triggered_cases": [r["case"] for r in triggered],
        "total_p3_used": sum(r["n_p3_used"] for r in rows),
        "total_p3_unused": sum(r["n_p3_unused"] for r in rows),
        "total_decision_path_ungrounded": sum(
            r["n_decision_path_ungrounded"] for r in rows),
        "total_derived_used": sum(r["n_derived_used"] for r in rows),
        "metric_note": ("`total_decision_path_ungrounded` counts P3 evidence "
                        "used in reasoning, which is §12.1's 'decision-path "
                        "ungrounded Evidence'; P2 nodes on the decision path "
                        "are legitimate derivations and are reported "
                        "separately as `total_derived_used`"),
        "guard_triggers": sum(r["guard_triggers"] for r in rows),
        "§10_branch": branch,
        "branch_rule": ("0 cases -> LOW_TRIGGER_DENSITY (stop); 1 -> "
                        "QUALITATIVE_ONLY (run ATGG on that case only); >=2 -> "
                        "full Guard arm"),
        "gold_read": False,
        "gold_note": ("computed from events/bindings/deltas and the blinded "
                      "dataset only; no outcome file is opened"),
        "per_case": rows,
    }
    with open(os.path.join(out_dir, f"baseline_risk_summary_{tag}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--arm", choices=("baseline", "guard"), default="baseline")
    ap.add_argument("--cases", default="60-69")
    args = ap.parse_args()
    cases: List[int] = []
    for part in args.cases.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cases.extend(range(int(a), int(b) + 1))
        elif part:
            cases.append(int(part))
    s = survey(args.out, args.arm, cases)
    print(f"{'case':6} {'P3used':7} {'P3unused':9} {'P2used':7} "
          f"{'anomalies':10} {'guard':6}")
    for r in s["per_case"]:
        print(f"{r['case']:<6} {r['n_p3_used']:<7} {r['n_p3_unused']:<9} "
              f"{r['n_derived_used']:<7} "
              f"{','.join(r['anomaly_types']) or '-':10} {r['guard_triggers']:<6}")
    print(json.dumps({k: v for k, v in s.items()
                      if k not in ("per_case",)}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
