"""S3D-B/C §3.3 steps 5-6: verify outcome, but only through the seal.

§3.3's ordering is the requirement, and the ordering is what this module
enforces:

    1. GoS completes                     (s3d_run.py --run)
    2. artifacts land on disk            (s3d_run.py --run)
    3. per-case artifact manifest        (s3d_run.py --run)
    4. sealed hash written               (s3d_run.py --run)
    5. only now may the verifier read the reference   <- this module
    6. Gate judges on outcome + audit                 <- this module

The consequence §3.3 attaches to a failed check is explicit — "该 case 不得进入
经验语料" — so the seal check is a refusal, not a warning. A case whose artifacts
no longer hash to its seal is dropped from every table below rather than
carried with a footnote.

The verifier itself is imported, not reimplemented, exactly as in S3C: same
judge prompt, same model, same temperature, three runs, same 0/1/2 rubric, same
verdict rule. §4 also asks for `score_1_unanimous` and `score_1_mixed`
separately, because S3C found eight of ten uncertains were a unanimous `1|1|1`
and S3D has to say whether that shape recurs.

Usage:
    python traceevo/s3d_verify.py --out S3D_verified_expansion
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

OUT = os.path.join(ROOT, "S3D_verified_expansion")
TAG = "s3d_expansion"
# Imported, not restated. The sealer and the verifier must hash the same set or
# the check is between two different questions.
from traceevo.s3d_run import SUFFIXES  # noqa: E402


def _sha256_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def recompute_seal(artifacts: str, task: str) -> str:
    """The same digest `s3d_run.seal_one` wrote, from the files as they are now."""
    h = {s.lstrip("_"): _sha256_file(os.path.join(artifacts, f"{task}{s}"))
         for s in SUFFIXES}
    return hashlib.sha256(json.dumps(sorted(h.items())).encode(
        "utf-8")).hexdigest()


def check_seals(out_dir: str, artifacts: str) -> List[Dict[str, Any]]:
    """§3.3: `input_artifact_hash == sealed_hash`, per case."""
    seal_dir = os.path.join(out_dir, "seals")
    rows = []
    for name in sorted(os.listdir(seal_dir)):
        if not name.endswith("_seal.json"):
            continue
        with open(os.path.join(seal_dir, name), encoding="utf-8") as f:
            seal = json.load(f)
        task = seal["task_id"]
        now = recompute_seal(artifacts, task)
        # Per-file comparison too, so a mismatch says *which* artifact moved
        # rather than only that the digest changed.
        per_file = {}
        for s in SUFFIXES:
            k = s.lstrip("_")
            cur = _sha256_file(os.path.join(artifacts, f"{task}{s}"))
            if cur != seal["artifacts"].get(k, ""):
                per_file[k] = {"sealed": seal["artifacts"].get(k, ""),
                               "now": cur}
        rows.append({
            "case": seal["case"], "task_id": task,
            "sealed_hash": seal["input_artifact_hash"],
            "recomputed_hash": now,
            "match": now == seal["input_artifact_hash"],
            "changed_artifacts": sorted(per_file),
            "sealed_at": seal["sealed_at"],
        })
    return rows


def score_shape(scores: List[int]) -> str:
    """§4: the `1|1|1` shape S3C found, told apart from a mixed 1."""
    if not scores:
        return "none"
    s = set(scores)
    if s == {1}:
        return "score_1_unanimous"
    if 1 in s:
        return "score_1_mixed"
    return "no_score_1"


def verify(out_dir: str, dry_run: bool = False) -> Dict[str, Any]:
    from traceevo.s2_cases import load_env_file
    from traceevo.evolution.outcome_verifier import (make_judge_fn,
                                                     verify_trace)
    from traceevo.s3c_expand import gold_safe_conditions

    artifacts = os.path.join(out_dir, "runs_50_59", "traceevo", "runs", TAG)
    seals = check_seals(out_dir, artifacts)
    if not seals:
        raise SystemExit("no seals found; run s3d_run.py --run first (§3.3)")

    bad = [s["task_id"] for s in seals if not s["match"]]
    print(f"seals: {len(seals)} checked, {len(seals) - len(bad)} verified, "
          f"{len(bad)} rejected")
    for s in seals:
        print(f"  {s['task_id']}: "
              f"{'OK' if s['match'] else 'MISMATCH ' + ','.join(s['changed_artifacts'])}")

    # The reference is not opened until every seal above has been checked. The
    # rejected cases are excluded from `cases` first, so a failed seal cannot
    # be rescued by a correct answer.
    cases = [s["case"] for s in seals if s["match"]]

    load_env_file()
    judge_fn = _stub if dry_run else make_judge_fn()
    raw_dir = os.path.join(out_dir, "judge_raw")
    os.makedirs(raw_dir, exist_ok=True)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from traceevo.s3c_outcome import final_report
    from traceevo.evolution.blinding import gold_by_case_index

    # §3.3 step 5. Everything above this line is hash arithmetic on artifacts;
    # this is the first statement in the module that opens the reference, and
    # it is reachable only after `check_seals` has run for every case.
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    rows: List[Dict[str, Any]] = []

    for c in cases:
        task = f"case-{c}"
        rep = final_report(c, artifacts)
        if not rep["prediction"]:
            rows.append({"case": c, "task_id": task, "seal_verified": True,
                         "outcome_verdict": "uncertain",
                         "verdict_reason": "no FINAL_REPORT in the artifacts",
                         "judge_scores": "", "judge_unanimous": "",
                         "score_shape": "none", "prediction": "",
                         "reference_sha256": ""})
            continue
        g = gold.get(c) or {}
        result = verify_trace(rep, g.get("final_diagnosis", ""), judge_fn, c,
                              raw_dir=raw_dir)
        rows.append({
            "case": c, "task_id": task, "seal_verified": True,
            "outcome_verdict": result["outcome_verdict"],
            "verdict_reason": result["verdict_reason"],
            "judge_scores": "|".join(str(s) for s in result["judge_scores"]),
            "judge_unanimous": result["judge_unanimous"],
            "score_shape": score_shape(result["judge_scores"]),
            "prediction": rep["prediction"],
            "reference_sha256": result["reference_sha256"],
        })
        print(f"  {task}: {result['outcome_verdict']:20} "
              f"scores={result['judge_scores']}", flush=True)

    # cases whose seal failed are recorded, and only there
    for s in seals:
        if not s["match"]:
            rows.append({
                "case": s["case"], "task_id": s["task_id"],
                "seal_verified": False,
                "outcome_verdict": "excluded_seal_mismatch",
                "verdict_reason": f"artifacts changed after sealing: "
                                  f"{','.join(s['changed_artifacts'])}",
                "judge_scores": "", "judge_unanimous": "", "score_shape": "none",
                "prediction": "", "reference_sha256": ""})

    rows.sort(key=lambda r: r["case"])
    out_csv = os.path.join(out_dir, "outcome_results_50_59.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    counts = collections.Counter(r["outcome_verdict"] for r in rows)
    shapes = collections.Counter(r["score_shape"] for r in rows)
    summary = {"n_sealed": len(seals), "n_seal_verified": len(cases),
               "n_seal_rejected": len(bad), "verdict_counts": dict(counts),
               "score_shapes": dict(shapes),
               "judge_raw_dir": os.path.relpath(raw_dir, ROOT)}
    with open(os.path.join(out_dir, "s3d_outcome_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _stub(system_prompt: str, user_prompt: str) -> str:
    return "SCORE: 1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = verify(args.out, args.dry_run)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
