"""S4B-D §10: run case 60-69, baseline arm first, seal before anything else.

§10 splits the experiment in two so the Guard arm is not run when the block
cannot test it. Phase 1 is the No-Guard baseline:

    case 60-69, Memory OFF, adaptive routing OFF, ATGG OFF

and then, **without reading gold**, count how many cases carry a decision-path
P3. §10's branches are 0 / 1 / >=2 and the run stops before the guard arm on the
first two.

The gold-blinding is the same mechanism S3D used: `TRACEEVO_DATASET_CSV` points
at a copy of the benchmark with `Final Diagnosis` and `Right Option` physically
removed, so during the run the label is not in the process's inputs at all.
That is what lets §10 say "在不读取 gold diagnosis 的情况下统计" and have it be
checkable rather than promised.

Sealing follows S3D §3.3 exactly — per-case manifest, sealed hash, and the
OutcomeVerifier refuses any case whose artifacts no longer hash to it.

Usage:
    python traceevo/s4b_run.py --arm baseline
    python traceevo/s4b_run.py --arm guard
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

OUT = os.path.join(ROOT, "S4B_adaptive_guard")
CASES = list(range(60, 70))
SUFFIXES = ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
            "_integrity.json", "_invocations.jsonl", "_injections.jsonl",
            "_provenance_graph.json", "_provenance_graph.png",
            "_replay.json", "_summary.json")

ARM_TAG = {"baseline": "atgg_off", "guard": "atgg_on"}


def _sha_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def seal_one(artifacts: str, task: str, case: int, arm: str) -> Dict[str, Any]:
    from traceevo.audit.event_schema import utc_now
    h = {s.lstrip("_"): _sha_file(os.path.join(artifacts, f"{task}{s}"))
         for s in SUFFIXES}
    sealed = hashlib.sha256(json.dumps(sorted(h.items())).encode(
        "utf-8")).hexdigest()
    return {"case": case, "task_id": task, "arm": arm, "sealed_at": utc_now(),
            "artifacts": h,
            "complete": bool(h["events.jsonl"] and h["bindings.json"]),
            "input_artifact_hash": sealed}


def run_arm(out_dir: str, arm: str, cases: List[int], attempts: int = 3,
            stall_timeout: float = 1800.0) -> Dict[str, Any]:
    from traceevo.s3b_probe import _run_case, _clear_case
    from traceevo.evolution.blinding import make_blinded_dataset, verify_blinded
    from traceevo.s2_cases import load_env_file

    tag = ARM_TAG[arm]
    os.makedirs(out_dir, exist_ok=True)
    blinded = os.path.join(out_dir, "dataset_blinded_60_69.csv")
    manifest = make_blinded_dataset(DATASET_CSV, blinded)
    check = verify_blinded(blinded)
    if not check["blinded"]:
        raise SystemExit(f"blinding failed: {check['gold_columns_present']}")

    run_root = os.path.join(out_dir, f"{arm}_60_69")
    tx_root = os.path.join(run_root, "traceevo")
    artifacts = os.path.join(tx_root, "runs", tag)
    os.makedirs(run_root, exist_ok=True)
    log_path = os.path.join(run_root, "run.log")

    load_env_file()
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_REPLAY_MODE"] = "off"
    env["TRACEEVO_MEMORY"] = "0"                    # §10: Memory OFF
    env["TRACEEVO_GUARD"] = "1" if arm == "guard" else "0"
    env["TRACEEVO_GUARD_MAX_RETRY"] = "1"           # §8.2: max_retry = 1
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(blinded)
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(tx_root)

    seals, crashed = [], []
    mode = "w"
    for c in cases:
        task = f"case-{c}"
        seal_path = os.path.join(out_dir, f"seals_{tag}", f"{task}_seal.json")
        if os.path.exists(seal_path):
            print(f"  {task}: already sealed, skipped", flush=True)
            continue
        for attempt in range(1, attempts + 1):
            _clear_case(artifacts, task)
            cmd = [PY, "-u", "main.py", "--cases", str(c),
                   "--out-csv", os.path.abspath(
                       os.path.join(run_root, "results.csv")),
                   "--html-dir", os.path.abspath(
                       os.path.join(run_root, "html")),
                   "--json-dir", os.path.abspath(
                       os.path.join(run_root, "graphs")),
                   "--log-dir", os.path.abspath(
                       os.path.join(run_root, "logs")),
                   "--tag", tag]
            with open(log_path, mode, encoding="utf-8") as log:
                mode = "a"
                print(f"\n=== {arm} {task} attempt {attempt}/{attempts} ===",
                      file=log, flush=True)
                _proc, stalled = _run_case(cmd, env, log, log_path,
                                           stall_timeout)
            if stalled:
                print(f"  {task} attempt {attempt}: STALLED, killed",
                      flush=True)
            if os.path.exists(os.path.join(artifacts,
                                           f"{task}_integrity.json")):
                break
        if not os.path.exists(os.path.join(artifacts, f"{task}_integrity.json")):
            crashed.append(task)
            print(f"  {task}: INCOMPLETE after {attempts} attempts", flush=True)
            continue
        seal = seal_one(artifacts, task, c, arm)
        os.makedirs(os.path.dirname(seal_path), exist_ok=True)
        with open(seal_path, "w", encoding="utf-8") as f:
            json.dump(seal, f, ensure_ascii=False, indent=2)
        seals.append(seal)
        # guard activity is itself a result, so report it as it happens
        ga = os.path.join(artifacts, "guard_actions.jsonl")
        n_actions = 0
        if os.path.exists(ga):
            with open(ga, encoding="utf-8") as f:
                n_actions = sum(1 for ln in f if ln.strip())
        print(f"  {task}: sealed {seal['input_artifact_hash'][:16]} "
              f"guard_actions={n_actions}", flush=True)

    out = {"arm": arm, "tag": tag, "sealed": len(seals),
           "crashed": crashed, "blinding": manifest,
           "artifacts_dir": os.path.relpath(artifacts, ROOT)}
    with open(os.path.join(out_dir, f"run_summary_{tag}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n{arm}: sealed {len(seals)}/{len(cases)}", flush=True)
    return out


def check_seals(out_dir: str, arm: str) -> List[Dict[str, Any]]:
    """S3D §3.3's check, reused: artifacts must still hash to their seal."""
    tag = ARM_TAG[arm]
    artifacts = os.path.join(out_dir, f"{arm}_60_69", "traceevo", "runs", tag)
    seal_dir = os.path.join(out_dir, f"seals_{tag}")
    rows = []
    if not os.path.isdir(seal_dir):
        return rows
    for fn in sorted(os.listdir(seal_dir)):
        if not fn.endswith("_seal.json"):
            continue
        with open(os.path.join(seal_dir, fn), encoding="utf-8") as f:
            seal = json.load(f)
        now = seal_one(artifacts, seal["task_id"], seal["case"], arm)
        rows.append({"case": seal["case"], "task_id": seal["task_id"],
                     "arm": arm, "sealed_hash": seal["input_artifact_hash"],
                     "recomputed_hash": now["input_artifact_hash"],
                     "match": now["input_artifact_hash"]
                     == seal["input_artifact_hash"],
                     "changed": [k for k, v in now["artifacts"].items()
                                 if v != seal["artifacts"].get(k, "")]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--arm", choices=("baseline", "guard"), required=True)
    ap.add_argument("--cases", default="60-69")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--stall-timeout", type=float, default=1800.0)
    ap.add_argument("--check-seals", action="store_true")
    args = ap.parse_args()

    cases: List[int] = []
    for part in args.cases.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cases.extend(range(int(a), int(b) + 1))
        elif part:
            cases.append(int(part))
    allowed = set(CASES) if args.arm == "baseline" else set(CASES)
    if not set(cases) <= allowed:
        raise SystemExit(
            f"S4B may only run case 60-69; got {cases}. §5 forbids reading "
            f"case 70-137 and this refuses before opening anything.")
    if args.check_seals:
        rows = check_seals(args.out, args.arm)
        bad = [r for r in rows if not r["match"]]
        print(json.dumps({"checked": len(rows), "verified": len(rows) - len(bad),
                          "rejected": len(bad),
                          "rejected_cases": [r["task_id"] for r in bad]},
                         ensure_ascii=False, indent=2))
        return 0
    run_arm(args.out, args.arm, cases, args.attempts, args.stall_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
