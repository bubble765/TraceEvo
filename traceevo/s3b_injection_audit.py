"""S3B §13, second pass: audit the injections that were actually sent.

`s3b_probe.plan()` guards what memory *would* inject — it re-renders the blocks
deterministically and scans them before any GoS process starts. That is the
right place for the gate, because a leak found after the runs would mean the
experiment had already happened on contaminated input.

But a prediction is not a record. `plan()` renders the block from the store and
the retrieval key; the run renders it again from the same inputs at
`hooks.on_plan_prompt` and writes what it produced to `{task}_injections.jsonl`.
If those two ever disagreed — a different store, a config that did not reach the
hook, a formatter change between plan and run — the guard would have scanned
something other than what the planner read, and still reported zero.

This module closes that gap. It compares the runtime injectate against the plan
and re-runs the leakage guard on the runtime text.

    python traceevo/s3b_injection_audit.py --probe S3B_runs/memory_probe \
        --out S3B_memory_gate

Two properties are checked, and they fail differently:

    coverage   every retrieved memory in the plan has a runtime injection, and
               vice versa. A shortfall means the run did not inject what was
               planned.
    identity   the runtime block equals the planned block for the same
               (arm, case). A mismatch means the guard scanned the wrong text.

Either one failing makes the §13 zero uninterpretable, so the verdict says so
rather than reporting the leak count alone.
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
sys.path.insert(0, ROOT)


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _runtime_injections(probe_dir: str, arm: str,
                        cases: List[int]) -> Dict[int, Dict[str, Any]]:
    """What the run actually put in front of the planner."""
    art = os.path.join(probe_dir, "probe", arm, "traceevo", "runs", arm)
    out: Dict[int, Dict[str, Any]] = {}
    for c in cases:
        p = os.path.join(art, f"case-{c}_injections.jsonl")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
        if rows:
            out[c] = rows[0]      # one injection per plan prompt
    return out


def _planned(probe_dir: str) -> Dict[str, Dict[int, Dict[str, Any]]]:
    p = os.path.join(probe_dir, "retrieval_results.csv")
    out: Dict[str, Dict[int, Dict[str, Any]]] = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["arm"], {})[int(r["case_index"])] = r
    return out


def audit(probe_dir: str, build_dir: str, out_dir: str,
          cases: List[int]) -> Dict[str, Any]:
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.evolution.leakage_guard import guard

    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    planned = _planned(probe_dir)

    # memory id -> source case, from the gate's own output rather than by
    # parsing the id (see failure_cases.md FAILURE 1)
    meta: Dict[str, Dict[str, Any]] = {}
    for fname in ("candidate_experiences.jsonl", "poisoned_candidates.jsonl"):
        p = os.path.join(probe_dir, fname)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for ln in f:
                if ln.strip():
                    r = json.loads(ln)
                    meta[r["memory_id"]] = r

    injections: List[Dict[str, Any]] = []
    coverage: List[Dict[str, Any]] = []
    identity: List[Dict[str, Any]] = []

    for arm in ("M1_NaiveMemory", "M2_TrustedMemory"):
        runtime = _runtime_injections(probe_dir, arm, cases)
        for c in cases:
            plan_row = planned.get(arm, {}).get(c, {})
            planned_ids = [m for m in plan_row.get("memory_ids", "").split("|") if m]
            rt = runtime.get(c)
            runtime_ids = list(rt.get("source_memory_ids", [])) if rt else []
            coverage.append({
                "arm": arm, "case": c,
                "planned_ids": planned_ids, "runtime_ids": runtime_ids,
                "match": sorted(planned_ids) == sorted(runtime_ids),
                "n_planned": len(planned_ids), "n_runtime": len(runtime_ids),
            })
            if not rt:
                continue
            # identity is only meaningful where the plan predicted a block
            if planned_ids:
                plan_block = plan_row.get("block_chars")
                identity.append({
                    "arm": arm, "case": c,
                    "planned_chars": int(plan_block) if plan_block else None,
                    "runtime_chars": len(rt.get("block", "")),
                    "scores_match": plan_row.get("scores", "").split("|") == [
                        str(s) for s in rt.get("scores", [])],
                })
            # the guard, on the text that was really sent
            for mid in runtime_ids:
                src = meta.get(mid, {}).get("source_case_id")
                injections.append({
                    "arm": arm, "target_case_index": c,
                    "source_memory_ids": runtime_ids,
                    "prompt": rt.get("block", ""),
                    "gold": gold.get(int(src), {}) if src is not None else {},
                    "source_predictions": [],
                    "source_case_text": "",
                })

    res = guard(injections)
    cov_bad = [r for r in coverage if not r["match"]]
    id_bad = [r for r in identity
              if r["planned_chars"] is not None
              and r["runtime_chars"] != r["planned_chars"]]

    result = {
        "scope": "runtime injections, %s" % ",".join(str(c) for c in cases),
        "n_injections_scanned": res["n_injections_scanned"],
        "gold_leakage_count": res["gold_leakage_count"],
        "coverage": {
            "cases_compared": len(coverage),
            "mismatches": len(cov_bad),
            "detail": cov_bad[:10],
        },
        "identity": {
            "cases_compared": len(identity),
            "block_length_mismatches": len(id_bad),
            "detail": id_bad[:10],
        },
        "passed": bool(res["passed"] and not cov_bad and not id_bad),
        "verdict": ("GO" if (res["passed"] and not cov_bad and not id_bad)
                    else "STOP: the runtime injectate does not match what the "
                         "plan phase guarded, or a leak was found"),
        "note": ("first-pass guard runs before any GoS process, on predicted "
                 "blocks; this second pass runs on the recorded ones. A zero "
                 "from the first pass is only meaningful if this one agrees."),
    }
    with open(os.path.join(out_dir, "leakage_scan_runtime.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "leakage_scan_runtime_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["arm", "target_case_index", "source_memory_ids", "n_leaks",
                    "kinds"])
        for r in res["results"]:
            w.writerow([r.get("arm", ""), r["target_case_index"],
                        "|".join(r["source_memory_ids"]), r["n_leaks"],
                        "|".join(sorted({l["kind"] for l in r["leaks"]}))])
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_probe"))
    ap.add_argument("--build", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_build"))
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_memory_gate"))
    ap.add_argument("--cases", default="40-49")
    args = ap.parse_args()
    from traceevo.s2_cases import load_env_file
    load_env_file()
    r = audit(args.probe, args.build, args.out, parse_cases(args.cases))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
