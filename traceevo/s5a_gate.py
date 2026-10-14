"""S5A-F §13: the Gate protocol drill on the validation block.

§13's branch is explicit and both halves matter:

    if there is a verified_success  ->  build correct-but-poisoned twins and
                                        compare Naive / OutcomeOnly /
                                        AuditOnly / IntegrityOnly / TraceEvo
    if there is not                 ->  GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION
                                        and stop; do not touch the judge, do
                                        not go looking for cases

The second half is the one that needs discipline. A validation block that
contains no verified success is a legitimate outcome, and the tempting
responses to it — widening the block, relaxing the judge, re-reading gold — are
all forbidden by §13 and §19. The correct output is a named absence, which is
what `GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION` is for.

The twin machinery is imported from `s3c_expand.poison_successes` and the
poisoner from `s3b_gate.poison`, exactly as S3D did, so "the Gate's behaviour
did not change with the corpus" is a claim about one implementation on new
inputs rather than about two similar ones.

Usage:
    python traceevo/s5a_gate.py --out S5A_protocol_validation
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5A_protocol_validation")
TAG = "core_v1"
CASES = list(range(70, 80))
SEED = 20260919
ABSENT = "GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION"

BASELINES = ("B0_Naive", "B1_OutcomeOnly", "B2_AuditOnly",
             "B3_IntegrityOnly", "TraceEvo")


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def candidates_by_class(out_dir: str) -> Dict[str, List[int]]:
    p = os.path.join(out_dir, "outcome_results_70_79.csv")
    by: Dict[str, List[int]] = collections.defaultdict(list)
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            by[r["outcome_verdict"]].append(int(r["case"]))
    return dict(by)


def baseline_table(out_dir: str, artifacts: str,
                   gold_safe_cases: List[int]) -> Dict[str, Any]:
    """§13's five-rule comparison over whatever the block produced.

    Retention is run on every candidate for every rule rather than filled from
    a placeholder — S3C FAILURE 4 was exactly that, and a retention column that
    reads 0.0 for four rules and 1.0 for the fifth is a claim about the corpus
    that the corpus did not make.
    """
    from traceevo.s3b_gate import audit_bundle, _integrity_ok
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import _trace_hashes
    import pandas as pd

    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_70_79.csv"))
    with open(os.path.join(out_dir, "outcome_results_70_79.csv"),
              encoding="utf-8") as f:
        verdicts = {int(r["case"]): r for r in csv.DictReader(f)}

    rows = []
    for c in gold_safe_cases:
        task = f"case-{c}"
        b = audit_bundle(artifacts, task)
        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        cand = build_candidate(task, _trace_hashes(artifacts, task), key,
                               verdicts[c]["outcome_verdict"], b["events"],
                               b["ledger"], b["summary"],
                               [a["type"] for a in b["anomalies"]])
        v = gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                          b["anomalies"], _integrity_ok(b),
                          b["semantic_complete"],
                          forbidden_hits=forbidden_content(cand, {}))
        rec = {"case": c, "task_id": task, "memory_id": cand.memory_id,
               "outcome_verdict": verdicts[c]["outcome_verdict"],
               "TraceEvo": v["eligibility"],
               "TraceEvo_admits_success": v["eligibility"] == gate.ELIGIBLE,
               "gate_reasons": "|".join(v["reasons"])}
        for name, fn in gate.BASELINES.items():
            try:
                r = fn(cand, audit={"decisions": b["decisions"]},
                       ledger_rows=b["ledger"], anomalies=b["anomalies"],
                       integrity_ok=_integrity_ok(b))
            except TypeError:
                r = fn(cand)
            rec[name] = r["eligibility"]
            rec[f"{name}_admits_success"] = r["eligibility"] == gate.ELIGIBLE
        rows.append(rec)
    return {"rows": rows}


def metrics(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for rule in BASELINES:
        n = len(rows)
        retained = sum(1 for r in rows if r.get(f"{rule}_admits_success")
                       or (rule == "TraceEvo" and r["TraceEvo_admits_success"]))
        out.append({
            "rule": rule,
            "n_clean_success_candidates": n,
            "retained": retained,
            "validated_success_retention": (retained / n) if n else None,
        })
    return out


def run(out_dir: str) -> Dict[str, Any]:
    artifacts = os.path.join(out_dir, "runs_70_79", "traceevo", "runs", TAG)
    by = candidates_by_class(out_dir)
    vs = by.get("verified_success", [])

    # §13's own definition of the positive class: a verified success that also
    # clears the six conditions. A verified success with an unsafe process is
    # not a reusable success and must not be poisoned as one.
    from traceevo.s3c_expand import gold_safe_conditions
    gold_safe = []
    for c in vs:
        g = gold_safe_conditions(artifacts, f"case-{c}")
        if g["gold_safe"]:
            gold_safe.append(c)

    base = {"block": "70-79", "n_cases": len(CASES),
            "outcome_counts": {k: len(v) for k, v in by.items()},
            "verified_success_cases": vs,
            "gold_safe_cases": gold_safe,
            "n_gold_safe": len(gold_safe)}

    if not gold_safe:
        # §13's second branch. Named, reported, and no case hunting.
        out = {
            **base,
            "verdict": ABSENT,
            "twins_built": 0,
            "why": ("§13: the validation block produced no verified success "
                    "that also clears the six gold-safe conditions, so there "
                    "is no positive class to build correct-but-poisoned twins "
                    "from."),
            "what_was_not_done": [
                "the judge was not modified",
                "no further cases were searched",
                "case 80-137 was not read",
                "no success candidate was constructed for a case that is not "
                "a gold-safe success",
            ],
            "prior_evidence": ("§13: S3C's 16 twins over case 10-29 remain the "
                               "development evidence for the two-counterexample "
                               "argument."),
            "baseline_table_on_gold_safe": [],
        }
        _write_csv(os.path.join(out_dir, "gate_validation_results.csv"),
                   [{"record": ABSENT, "n_cases": len(CASES),
                     "verified_success": len(vs), "gold_safe": 0,
                     "reason": out["why"]}])
        _write_csv(os.path.join(out_dir, "baseline_gate_metrics.csv"), [])
        with open(os.path.join(out_dir, "gate_drill.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return out

    # §13's first branch.
    from traceevo.s3c_expand import poison_successes
    entries = []
    for c in gold_safe:
        task = f"case-{c}"
        g = gold_safe_conditions(artifacts, task)
        b = g["bundle"]
        import pandas as pd
        blinded = pd.read_csv(os.path.join(out_dir,
                                           "dataset_blinded_70_79.csv"))
        from traceevo.evolution.memory_retriever import build_retrieval_key
        row = blinded.iloc[c]
        entries.append({
            "case": c, "task": task, "archive": artifacts,
            "headline_eligible": True,
            "candidate": _cand(artifacts, task, b, row, out_dir),
        })
    poison_successes(entries, out_dir, seed=SEED)

    twins = _jsonl(os.path.join(out_dir, "success_poisoned_candidates.jsonl"))
    applicable = [t for t in twins if t.get("applicable", True)]
    rows = []
    for t in applicable:
        rec = {"case": t.get("case"), "task_id": t.get("task_id"),
               "poison_kind": t.get("poison_kind"),
               "anomaly_type": t.get("anomaly_type"),
               "original_outcome": t.get("original_outcome")}
        for rule in BASELINES:
            key = (f"{rule}_admits_success" if rule != "TraceEvo"
                   else "traceevo_admits_success")
            rec[f"{rule}_admits"] = t.get(key)
        rows.append(rec)
    _write_csv(os.path.join(out_dir, "gate_validation_results.csv"), rows)

    bt = baseline_table(out_dir, artifacts, gold_safe)
    _write_csv(os.path.join(out_dir, "baseline_gate_metrics.csv"),
               metrics(bt["rows"]))

    out = {
        **base,
        "verdict": "GATE_DRILL_EXECUTED",
        "twins_built": len(applicable),
        "twin_admission": {rule: sum(1 for r in rows
                                     if r.get(f"{rule}_admits") == "True")
                           for rule in BASELINES},
        "baseline_table_on_gold_safe": bt["rows"],
        "retention": metrics(bt["rows"]),
        "note": ("§13. The retention column is run per candidate per rule "
                 "rather than filled from a placeholder (S3C FAILURE 4)."),
    }
    with open(os.path.join(out_dir, "gate_drill.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _cand(artifacts: str, task: str, b: Dict[str, Any], row, out_dir: str):
    from traceevo.evolution.experience import build_candidate
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import _trace_hashes
    with open(os.path.join(out_dir, "outcome_results_70_79.csv"),
              encoding="utf-8") as f:
        verdicts = {int(r["case"]): r for r in csv.DictReader(f)}
    c = int(task.split("-")[1])
    key = build_retrieval_key(row["Case Information"],
                              row["Physical Examination"])
    return build_candidate(task, _trace_hashes(artifacts, task), key,
                           verdicts[c]["outcome_verdict"], b["events"],
                           b["ledger"], b["summary"],
                           [a["type"] for a in b["anomalies"]])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    r = run(args.out)
    print(json.dumps({k: v for k, v in r.items()
                      if k not in ("baseline_table_on_gold_safe",)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
