"""S3D-E §8: poisoned twins of the *new* successes, so the Gate's zero is
re-established on a corpus the Gate was not tuned against.

§8 asks for all four poison types against each new gold-safe success, and for
two numbers:

    OutcomeOnly correct-but-poisoned admission > 0
    TraceEvo   correct-but-poisoned admission = 0

The poisoning itself is `traceevo.s3b_gate.poison` and the twins are built and
judged by `traceevo.s3c_expand.poison_successes` — the same functions S3C used.
That is deliberate. If this module reimplemented the poisoning, "the Gate's
result did not change with the new corpus" would be a claim about two similar
implementations; by importing them it is a claim about one implementation run
on new inputs, which is the claim §8 actually makes.

§8 also says S3D does not need to re-run the full baseline experiment, only add
the new successes to the cumulative table. `poison_successes` evaluates every
baseline anyway, so the cumulative table is built from the same pass.

Usage:
    python traceevo/s3d_poison.py --out S3D_verified_expansion
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

OUT = os.path.join(ROOT, "S3D_verified_expansion")
TAG = "s3d_expansion"
S3C_OUT = os.path.join(ROOT, "S3C_experience_expansion")


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build(out_dir: str, seed: int = 20260919) -> Dict[str, Any]:
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import gold_safe_conditions, poison_successes
    from traceevo.s3d_store import new_gold_safe
    import pandas as pd

    cases = new_gold_safe(out_dir)
    if not cases:
        return {"n_new_gold_safe": 0, "skipped": True,
                "reason": "§8 applies only if case 50-59 produced a new "
                          "gold-safe success"}

    artifacts = os.path.join(out_dir, "runs_50_59", "traceevo", "runs", TAG)
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_50_59.csv"))
    with open(os.path.join(out_dir, "outcome_results_50_59.csv"),
              encoding="utf-8") as f:
        verdicts = {int(r["case"]): r for r in csv.DictReader(f)}

    entries = []
    for c in cases:
        task = f"case-{c}"
        g = gold_safe_conditions(artifacts, task)
        b = g["bundle"]
        row = blinded.iloc[c]
        entries.append({
            "case": c, "task": task, "archive": artifacts,
            "headline_eligible": True,
            "candidate": build_candidate(
                task, _trace_hashes(artifacts, task),
                build_retrieval_key(row["Case Information"],
                                    row["Physical Examination"]),
                verdicts[c]["outcome_verdict"], b["events"], b["ledger"],
                b["summary"], [a["type"] for a in b["anomalies"]]),
        })

    # The same call S3C made, on the new entries.
    result = poison_successes(entries, out_dir, seed=seed)

    src = os.path.join(out_dir, "success_poisoned_candidates.csv")
    with open(src, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    _write_jsonl(os.path.join(out_dir, "new_success_poisoned_candidates.jsonl"),
                 rows)

    applicable = [r for r in rows if r.get("applicable") == "True"]
    traceevo = sum(1 for r in applicable
                   if r.get("traceevo_admits_success") == "True")
    outcome_only = sum(1 for r in applicable
                       if r.get("B1_OutcomeOnly_admits_success") == "True")
    by_kind = collections.Counter(
        r["poison_kind"] for r in applicable
        if r.get("traceevo_admits_success") == "True")

    # §8's cumulative table: S3C's twins (case 10-29) plus S3D's (case 50-59).
    cumulative = _cumulative(out_dir, applicable, traceevo, outcome_only)
    with open(os.path.join(out_dir, "cumulative_gate_metrics.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(cumulative[0].keys()))
        w.writeheader()
        w.writerows(cumulative)

    out = {
        "n_new_gold_safe": len(cases), "cases": cases,
        "n_twins_applicable": len(applicable),
        "n_inapplicable": len(rows) - len(applicable),
        "traceevo_correct_but_poisoned_admission": traceevo,
        "outcome_only_correct_but_poisoned_admission": outcome_only,
        "s3d_requirement_traceevo_zero": traceevo == 0,
        "s3d_requirement_outcome_only_positive": outcome_only > 0,
        "traceevo_admissions_by_kind": dict(by_kind),
        "cumulative_table": os.path.join("S3D_verified_expansion",
                                         "cumulative_gate_metrics.csv"),
        "poisoner": "traceevo.s3b_gate.poison (imported, not reimplemented)",
        "twin_builder": ("traceevo.s3c_expand.poison_successes (imported, not "
                         "reimplemented)"),
    }
    with open(os.path.join(out_dir, "success_poison_summary_s3d.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _trace_hashes(archive: str, task: str) -> Dict[str, str]:
    from traceevo.s3c_expand import _trace_hashes as fn
    return fn(archive, task)


def _cumulative(out_dir: str, rows: List[Dict[str, Any]], traceevo: int,
                outcome_only: int) -> List[Dict[str, Any]]:
    """S3C's count plus S3D's, reported per block so the two are separable."""
    s3c_rows, s3c_counts = [], {}
    p = os.path.join(S3C_OUT, "success_poisoned_candidates.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            s3c_rows = [r for r in csv.DictReader(f)
                        if r.get("applicable") == "True"]
    out = [{
        "block": "10-29 (S3C)",
        "n_successes_poisoned": len({r["case"] for r in s3c_rows}),
        "n_twins": len(s3c_rows),
        "traceevo_admission": sum(
            1 for r in s3c_rows if r.get("traceevo_admits_success") == "True"),
        "outcome_only_admission": sum(
            1 for r in s3c_rows
            if r.get("B1_OutcomeOnly_admits_success") == "True"),
    }, {
        "block": "50-59 (S3D)",
        "n_successes_poisoned": len({r["case"] for r in rows}),
        "n_twins": len(rows),
        "traceevo_admission": traceevo,
        "outcome_only_admission": outcome_only,
    }]
    out.append({
        "block": "cumulative",
        "n_successes_poisoned": (out[0]["n_successes_poisoned"]
                                 + out[1]["n_successes_poisoned"]),
        "n_twins": out[0]["n_twins"] + out[1]["n_twins"],
        "traceevo_admission": (out[0]["traceevo_admission"]
                               + out[1]["traceevo_admission"]),
        "outcome_only_admission": (out[0]["outcome_only_admission"]
                                   + out[1]["outcome_only_admission"]),
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    print(json.dumps(build(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
