"""S3B-A §4: the post-hoc verification pass over sealed Memory-Build traces.

This script is a separate process from `s3b_build.py` on purpose. §4.1's order

    1. GoS executes          4. artifact hashes computed
    2. FINAL_REPORT          5. OutcomeVerifier may read the gold label
    3. trace sealed          6. verdict

is only a guarantee if steps 1-4 cannot see the label, and the way that is
guaranteed here is that the *process* which ran them read a dataset file with
the gold columns removed (`blinding.py`). This script is a different process,
reading a different file, after the seal.

It writes `outcome_results.csv` and one `judge_raw/<task>.json` per case,
including every judge prompt and raw response, per §4.2 and §9.

Usage:
    python traceevo/s3b_verify.py --out S3B_runs/memory_build
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
# The judge transport lives in GoS (`utils.public_function`), so the
# working copy has to be importable from here too. Without it the
# judge raises ModuleNotFoundError, which verify_trace turns into
# `uncertain` -- safe, but it means no case is ever verified.
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

from traceevo.evolution.blinding import gold_by_case_index  # noqa: E402
from traceevo.evolution.outcome_verifier import (make_judge_fn,  # noqa: E402
                                                 verify_trace)


def _load_events(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _final_report(events: List[Dict[str, Any]]) -> Dict[str, str]:
    for e in reversed(events):
        if e.get("event_type") == "FINAL_REPORT":
            pl = e.get("payload") or {}
            return {"prediction": pl.get("prediction", ""),
                    "report": pl.get("report", "")}
    return {"prediction": "", "report": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_runs",
                                                  "memory_build"))
    ap.add_argument("--cases", default="30-39")
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise the pipeline with a stub judge, no model")
    args = ap.parse_args()

    cases = []
    for part in args.cases.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cases.extend(range(int(a), int(b) + 1))
        elif part:
            cases.append(int(part))

    from traceevo.s2_cases import load_env_file
    load_env_file()          # .env.local carries the gateway token
    seal_path = os.path.join(args.out, "sealed_artifacts.json")
    if not os.path.exists(seal_path):
        raise SystemExit(
            f"no seal at {seal_path}: §4.1 forbids verifying a trace that has "
            f"not been sealed first")
    with open(seal_path, encoding="utf-8") as f:
        sealed = json.load(f)
    print(f"seal taken at {sealed['sealed_at']}; gold may now be read")

    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    trace_dir = os.path.join(args.out, "gos", "traceevo", "runs",
                             "memory_build")
    judge_dir = os.path.join(args.out, "judge_raw")
    os.makedirs(judge_dir, exist_ok=True)

    judge_fn = _stub_judge if args.dry_run else make_judge_fn()

    rows: List[Dict[str, Any]] = []
    for c in cases:
        task = f"case-{c}"
        entry = sealed["traces"].get(task) or {}
        epath = os.path.join(trace_dir, f"{task}_events.jsonl")
        if not entry.get("complete") or not os.path.exists(epath):
            rows.append({"case": c, "task_id": task,
                         "outcome_verdict": "uncertain",
                         "verdict_reason": "trace did not complete",
                         "judge_scores": "", "prediction": ""})
            continue
        rep = _final_report(_load_events(epath))
        g = gold.get(c) or {}
        result = verify_trace(rep, g.get("final_diagnosis", ""), judge_fn, c,
                              raw_dir=os.path.join(args.out, "judge_raw"))
        # the redacted verdict; the full transcript is in <task>.raw.json
        with open(os.path.join(judge_dir, f"{task}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        rows.append({
            "case": c, "task_id": task,
            "outcome_verdict": result["outcome_verdict"],
            "verdict_reason": result["verdict_reason"],
            "judge_scores": "|".join(str(s) for s in result["judge_scores"]),
            "judge_unanimous": result["judge_unanimous"],
            "prediction": rep["prediction"],
            "reference_sha256": result["reference_sha256"],
        })
        print(f"  {task}: {result['outcome_verdict']:20} "
              f"scores={result['judge_scores']}", flush=True)

    out_csv = os.path.join(args.out, "outcome_results.csv")
    if not rows:
        raise SystemExit("no cases to verify")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["outcome_verdict"]] = counts.get(r["outcome_verdict"], 0) + 1
    with open(os.path.join(args.out, "outcome_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump({"cases": args.cases, "counts": counts,
                   "n_cases": len(rows)}, f, ensure_ascii=False, indent=2)
    print(f"\n{counts}")
    print(f"wrote {out_csv} and {judge_dir}/")
    return 0


def _stub_judge(system_prompt: str, user_prompt: str):
    """Deterministic stand-in for --dry-run: parses the reference out of the
    prompt and always calls it a match. Exercises the plumbing only."""
    return "1. Case 1 - x: score 2;", {}


if __name__ == "__main__":
    raise SystemExit(main())
