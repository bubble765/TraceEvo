"""S3C-A §3.2: post-hoc outcome verification over the archived case 10-29.

No GoS process is started. §3.1 forbids re-running or modifying the case 10-29
reasoning, so this reads the FINAL_REPORT out of the sealed traces and nothing
else. The reference is read from the benchmark only after the corpus manifest
has been written (`s3c_archive.py`), preserving §4.1's ordering: the artifacts
are attested first, the label is read second.

§3.2 requires the verifier to be the S3B one *unchanged* — same judge model,
same prompt, same temperature, three runs, same 0/1/2 rubric, same
success/incorrect/uncertain rule. This module imports `verify_trace` and
`make_judge_fn` from `traceevo.evolution.outcome_verifier` rather than
reimplementing any part of it, so "same verifier" is a fact about the import,
not a claim about two similar files.

Usage:
    python traceevo/s3c_outcome.py --out S3C_experience_expansion
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
ARCHIVE = os.path.join(ROOT, "S3A_runs", "s3a_migration", "traceevo", "runs",
                       "migration_holdout")
# §2 Tier C. Dev-Small has several archived runs; this is the S3A ledger run,
# chosen because it is the latest, was produced after the S2 rule freeze, and
# carries the same instrumentation as everything else S3C reads. The choice is
# recorded rather than assumed, because "which Dev-Small run" is not something
# a reviewer can recover from the artifacts alone.
LEGACY_ARCHIVE = os.path.join(ROOT, "S3A_runs", "s3a_ledger", "ledger_on",
                              "traceevo", "runs", "ledger_on")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

CASES = list(range(10, 30))
LEGACY_CASES = list(range(0, 10))


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def final_report(case: int, archive: str = ARCHIVE) -> Dict[str, str]:
    p = os.path.join(archive, f"case-{case}_events.jsonl")
    if not os.path.exists(p):
        return {"prediction": "", "report": ""}
    for e in reversed(_load_jsonl(p)):
        if e.get("event_type") == "FINAL_REPORT":
            pl = e.get("payload") or {}
            return {"prediction": pl.get("prediction", ""),
                    "report": pl.get("report", "")}
    return {"prediction": "", "report": ""}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        ROOT, "S3C_experience_expansion"))
    ap.add_argument("--dry-run", action="store_true",
                    help="stub judge; exercises the plumbing with no model")
    ap.add_argument("--legacy", action="store_true",
                    help="§4.2 Tier C: verify the archived case 0-9 Dev-Small "
                         "traces as a legacy supplement")
    ap.add_argument("--archive", default="")
    ap.add_argument("--cases", default="")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    # The corpus must have been attested before this runs. §3.1's ordering is
    # the point; a verifier that runs first and attests later has it backwards.
    manifest = os.path.join(args.out, "corpus_manifest.json")
    if not os.path.exists(manifest):
        raise SystemExit(
            "run s3c_archive.py first: the corpus has to be attested before "
            "the label is read (§3.1)")
    with open(manifest, encoding="utf-8") as f:
        man = json.load(f)
    print(f"corpus manifest from {man['generated_at']}; "
          f"{man['chain_summary']['n_chain_ok_now']}/"
          f"{man['chain_summary']['n_traces']} chains verify")

    archive = args.archive or (LEGACY_ARCHIVE if args.legacy else ARCHIVE)
    cases = ([int(x) for x in args.cases.split(",")] if args.cases
             else (LEGACY_CASES if args.legacy else CASES))
    tag = "legacy_0_9" if args.legacy else "10_29"
    print(f"archive: {archive}")
    print(f"cases:   {cases[0]}-{cases[-1]} ({len(cases)})")

    from traceevo.s2_cases import load_env_file
    load_env_file()
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.evolution.outcome_verifier import (make_judge_fn,
                                                     verify_trace)
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}

    judge_fn = _stub if args.dry_run else make_judge_fn()
    raw_dir = os.path.join(args.out,
                           "legacy_judge_raw" if args.legacy
                           else "archive_judge_raw")
    os.makedirs(raw_dir, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    for c in cases:
        task = f"case-{c}"
        rep = final_report(c, archive)
        if not rep["prediction"]:
            rows.append({"case": c, "task_id": task,
                         "outcome_verdict": "uncertain",
                         "verdict_reason": "no FINAL_REPORT in the archive",
                         "judge_scores": "", "judge_unanimous": "",
                         "prediction": "", "reference_sha256": ""})
            print(f"  {task}: no FINAL_REPORT", flush=True)
            continue
        g = gold.get(c) or {}
        result = verify_trace(rep, g.get("final_diagnosis", ""), judge_fn, c,
                              raw_dir=raw_dir)
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

    out_csv = os.path.join(
        args.out, f"{'legacy_outcome_results_0_9' if args.legacy else 'archive_outcome_results_10_29'}.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["outcome_verdict"]] = counts.get(r["outcome_verdict"], 0) + 1
    unanimous = sum(1 for r in rows if r["judge_unanimous"] is True)
    summary = {"cases": f"{cases[0]}-{cases[-1]}", "n_cases": len(rows),
               "counts": counts, "archive": archive, "tier": tag,
               "headline_eligible": not args.legacy,
               "judge_unanimous": unanimous,
               "judge_unanimity_rate": round(unanimous / len(rows), 4),
               "judge_model_note": ("identical verifier to S3B §3.2: same "
                                    "model, prompt, temperature, 3 runs, same "
                                    "0/1/2 rubric"),
               "corpus_manifest_generated_at": man["generated_at"]}
    summary["corpus_source"] = "legacy_dev" if args.legacy else "s3a_holdout"
    with open(os.path.join(
            args.out, f"{'legacy_outcome_summary_0_9' if args.legacy else 'archive_outcome_summary'}.json"),
            "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n{counts}  unanimity {unanimous}/{len(rows)}")
    print(f"wrote {out_csv} and {raw_dir}/")
    return 0


def _stub(system_prompt: str, user_prompt: str):
    return "1. Case 1 - x: score 2;", {}


if __name__ == "__main__":
    raise SystemExit(main())
