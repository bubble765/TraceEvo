"""S4B-C §9.2: the guard's positive controls, run before case 60 is touched.

§9.2 asks for two things and the second is the one that matters:

    a P3 unsupported assertion supporting a hypothesis must trigger
    a P0 retrieved evidence and a P2 derived statement must not
    「证明"没有触发"不是 guard 坏了」

The controls here isolate one condition each, which the first version of them
did not. That version used a retrieved troponin record as background and then
claimed "Troponin is markedly elevated" was P3 -- but with that record present
the label *is* a paraphrase of it, and the frozen classifier returned P1. The
probe read `trigger = false` and the honest conclusion was "the control was not
P3", not "the guard is broken". A control that does not test the condition it
names is worse than none, because its failure is attributable to either the
mechanism or the control and the output does not say which.

So each row below varies exactly one thing:

    A/B   P3, no source anywhere, SUPPORT / REFUTE edge   -> trigger
    C     same P3, but only a `refines` edge              -> no trigger
          (isolates §8's edge condition: tier alone is not enough)
    D     verbatim retrieved record present               -> no trigger
          (isolates the tier condition: a support edge alone is not enough)
    E     expert-synthesis text, P2                       -> no trigger
    F     Hypothesis node, no Evidence at all             -> no trigger

Usage:
    python traceevo/s4b_controls.py --out S4B_adaptive_guard
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

RECORD_TEXT = "Troponin I was 0.02 ng/mL, within the reference range."
ANALYSIS_TEXT = ("The ECG shows ST elevation in leads II, III and aVF; this is "
                 "consistent with an inferior myocardial infarction.")
INPUT_POOL = ("A 68-year-old man presented with chest pain radiating to the "
              "left arm.")
UNSUPPORTED = "Troponin is markedly elevated"


def _records():
    from traceevo.audit.cross_graph_binding import RetrievalRecord
    return [RetrievalRecord(record_id="rec-1", retrieval_event_id="evt-1",
                            query="troponin", step_id=1,
                            expert="Laboratory_Physician", text=RECORD_TEXT)]


def _analyses():
    return [{"event_id": "evt-a1", "analysis": ANALYSIS_TEXT}]


def _cand(label: str, node_type: str, edge: str) -> Dict[str, Any]:
    hyp = {"id": "n2", "node_type": "Hypothesis", "label": "Acute MI",
           "confidence": 0.8, "why": "leading hypothesis"}
    node = {"id": "n1", "node_type": node_type, "label": label}
    nodes = [node, hyp] if edge else [node]
    edges = [{"src": "n1", "dst": "n2", "relation": edge}] if edge else []
    return {"nodes": nodes, "edges": edges}


def controls() -> List[Dict[str, Any]]:
    """Each entry: what it varies, what it holds fixed, what §9.2 requires."""
    return [
        {"id": "A", "name": "P3 unsupported assertion, SUPPORT edge",
         "candidate": _cand(UNSUPPORTED, "Evidence", "support"),
         "records": [], "analyses": [], "input_pool": "",
         "varies": "the edge is support and no source exists",
         "holds": "nothing (baseline trigger case)",
         "expected_trigger": True,
         "spec": "§9.2 first requirement"},
        {"id": "B", "name": "P3 unsupported assertion, REFUTE edge",
         "candidate": _cand(UNSUPPORTED, "Evidence", "refute"),
         "records": [], "analyses": [], "input_pool": "",
         "varies": "the offending edge is refute rather than support",
         "holds": "same unsupported label",
         "expected_trigger": True,
         "spec": "§8 names both SUPPORT and REFUTE"},
        {"id": "C", "name": "P3 unsupported assertion, refines edge only",
         "candidate": _cand(UNSUPPORTED, "Evidence", "refines"),
         "records": [], "analyses": [], "input_pool": "",
         "varies": "the relation, from support to refines",
         "holds": "the node is still P3 with no source",
         "expected_trigger": False,
         "spec": "§8's edge condition: a P3 alone does not trigger"},
        {"id": "D", "name": "P0 verbatim retrieved record, SUPPORT edge",
         "candidate": _cand(RECORD_TEXT, "Evidence", "support"),
         "records": "records", "analyses": [], "input_pool": "",
         "varies": "the label, from unsupported to a verbatim record quote",
         "holds": "there is still a support edge",
         "expected_trigger": False,
         "spec": "§8's tier condition: a support edge alone does not trigger"},
        {"id": "E", "name": "P2 expert synthesis, SUPPORT edge",
         "candidate": _cand(ANALYSIS_TEXT, "Evidence", "support"),
         "records": [], "analyses": "analyses", "input_pool": "",
         "varies": "the label, to text the analysis stage actually produced",
         "holds": "there is still a support edge",
         "expected_trigger": False,
         "spec": "§9.2's P2 requirement"},
        {"id": "F", "name": "Hypothesis node carrying a support edge",
         "candidate": _cand("Acute coronary syndrome", "Hypothesis", "support"),
         "records": [], "analyses": [], "input_pool": "",
         "varies": "the node type, Evidence to Hypothesis",
         "holds": "there is still a support edge",
         "expected_trigger": False,
         "spec": "§8's node-type condition"},
    ]


def run(out_dir: str) -> Dict[str, Any]:
    from traceevo.evolution import grounding_guard as gg

    recs, ans = _records(), _analyses()
    rows = []
    for c in controls():
        r = [] if c["records"] == [] else recs
        a = [] if c["analyses"] == [] else ans
        d = gg.evaluate(c["candidate"], r, a, c["input_pool"])
        tiers = [t["provenance_tier"] for t in d["tiers"]]
        deriv = [t["derivation"] for t in d["tiers"]]
        rows.append({
            "control_id": c["id"], "name": c["name"],
            "expected_trigger": c["expected_trigger"],
            "actual_trigger": d["trigger"],
            "passed": d["trigger"] == c["expected_trigger"],
            "varies": c["varies"], "holds_fixed": c["holds"],
            "spec": c["spec"],
            "n_candidate_evidence": d["n_candidate_evidence"],
            "n_support_refute_edges": d["n_support_refute_edges"],
            "tiers": "|".join(tiers), "derivations": "|".join(deriv),
            "offending": "|".join(o["candidate_id"] for o in d["offenders"]),
            "candidate_hash": d["candidate_hash"][:16],
        })

    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "positive_control_results.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    positive = [r for r in rows if r["expected_trigger"]]
    negative = [r for r in rows if not r["expected_trigger"]]
    out = {
        "n_controls": len(rows),
        "n_positive": len(positive), "n_negative": len(negative),
        "all_passed": all(r["passed"] for r in rows),
        "positive_controls_fire": all(r["actual_trigger"] for r in positive),
        "negative_controls_silent": not any(r["actual_trigger"]
                                            for r in negative),
        "isolates": {
            "edge_condition": "C (P3 but refines -> silent)",
            "tier_condition": "D (P0 with support -> silent)",
            "node_type_condition": "F (Hypothesis with support -> silent)",
        },
        "why_this_matters": (
            "§9.2: '证明没有触发不是 guard 坏了'. The negative controls are what "
            "make the guard's silence in case 60-69 interpretable; without "
            "them a run with no triggers would be indistinguishable from a "
            "guard that never fires."),
        "first_version_note": (
            "The first version of these controls used a retrieved troponin "
            "record as background and called 'Troponin is markedly elevated' "
            "P3. With that record present the label is a paraphrase of it and "
            "the frozen classifier returns P1, so the probe measured the "
            "control's error rather than the guard's behaviour."),
    }
    with open(os.path.join(out_dir, "positive_control_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    s = run(args.out)
    for r in csv.DictReader(open(os.path.join(args.out,
                                              "positive_control_results.csv"))):
        print(f"  {r['control_id']} {'PASS' if r['passed']=='True' else 'FAIL'}  "
              f"{r['name']:46} tier={r['tiers']:24} trigger={r['actual_trigger']}")
    print(json.dumps({k: v for k, v in s.items()
                      if k not in ("isolates", "why_this_matters",
                                   "first_version_note")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
