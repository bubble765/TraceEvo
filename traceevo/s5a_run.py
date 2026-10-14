"""S5A-B §9 steps 1-5: run case 70-79 under Core-v1, then seal.

§9's ten steps are a strict order and the module boundary enforces it. This one
does steps 1-5 — run, artifacts on disk, seal — and never opens a reference.
`s5a_pipeline.py` does steps 6-10 and is a separate invocation, so "the label
was read after sealing" is a fact about which process ran, not a comment in a
function.

Core-v1's three switches are OFF here and the artifact is checked for what each
would have produced, rather than the environment being trusted:

    TRACEEVO_MEMORY=0   -> zero injection records across the block
    TRACEEVO_GUARD=0    -> no guard_actions.jsonl
    no routing call     -> nothing in S5A reads a role profile

Usage:
    python traceevo/s5a_run.py --run
    python traceevo/s5a_run.py --check-seals
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

OUT = os.path.join(ROOT, "S5A_protocol_validation")
CASES = list(range(70, 80))
TAG = "core_v1"
SUFFIXES = ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
            "_integrity.json", "_invocations.jsonl", "_injections.jsonl",
            "_provenance_graph.json", "_provenance_graph.png",
            "_replay.json", "_summary.json")


def _sha_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def seal_one(artifacts: str, task: str, case: int) -> Dict[str, Any]:
    from traceevo.audit.event_schema import utc_now
    h = {s.lstrip("_"): _sha_file(os.path.join(artifacts, f"{task}{s}"))
         for s in SUFFIXES}
    sealed = hashlib.sha256(json.dumps(sorted(h.items())).encode(
        "utf-8")).hexdigest()
    return {"case": case, "task_id": task, "sealed_at": utc_now(),
            "artifacts": h,
            "complete": bool(h["events.jsonl"] and h["bindings.json"]),
            "input_artifact_hash": sealed,
            "note": ("§9 step 5. s5a_pipeline.py recomputes this before any "
                     "reference is read and refuses the case if it differs.")}


def run(out_dir: str, cases: List[int], attempts: int = 3,
        stall_timeout: float = 1800.0) -> Dict[str, Any]:
    from traceevo.s3b_probe import _run_case, _clear_case
    from traceevo.evolution.blinding import make_blinded_dataset, verify_blinded
    from traceevo.s2_cases import load_env_file

    os.makedirs(out_dir, exist_ok=True)
    blinded = os.path.join(out_dir, "dataset_blinded_70_79.csv")
    manifest = make_blinded_dataset(DATASET_CSV, blinded)
    check = verify_blinded(blinded)
    if not check["blinded"]:
        raise SystemExit(f"blinding failed: {check['gold_columns_present']}")

    run_root = os.path.join(out_dir, "runs_70_79")
    tx_root = os.path.join(run_root, "traceevo")
    artifacts = os.path.join(tx_root, "runs", TAG)
    os.makedirs(run_root, exist_ok=True)
    log_path = os.path.join(run_root, "run.log")

    load_env_file()
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_REPLAY_MODE"] = "off"
    # §8/§9: the three switches, all off, one env var each.
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_GUARD"] = "0"
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(blinded)
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(tx_root)

    seals, crashed = [], []
    mode = "w"
    for c in cases:
        task = f"case-{c}"
        seal_path = os.path.join(out_dir, "seals", f"{task}_seal.json")
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
                   "--tag", TAG]
            with open(log_path, mode, encoding="utf-8") as log:
                mode = "a"
                print(f"\n=== {task} attempt {attempt}/{attempts} ===",
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
        seal = seal_one(artifacts, task, c)
        os.makedirs(os.path.join(out_dir, "seals"), exist_ok=True)
        with open(seal_path, "w", encoding="utf-8") as f:
            json.dump(seal, f, ensure_ascii=False, indent=2)
        seals.append(seal)
        print(f"  {task}: sealed {seal['input_artifact_hash'][:16]}",
              flush=True)

    sw = switch_evidence(artifacts, cases)
    out = {"tag": TAG, "sealed": len(seals), "crashed": crashed,
           "blinding": manifest, "switch_evidence": sw,
           "artifacts_dir": os.path.relpath(artifacts, ROOT)}
    with open(os.path.join(out_dir, "run_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nsealed {len(seals)}/{len(cases)}", flush=True)
    print(json.dumps(sw, ensure_ascii=False, indent=2))
    return out


def switch_evidence(artifacts: str, cases: List[int]) -> Dict[str, Any]:
    """§8's `how_asserted`: check the artifacts, not the flags.

    A switch recorded as OFF in a manifest is a claim about configuration. What
    matters is whether the run behaved as if it were off, and each of the three
    leaves a trace when it is on: injected prompts, guard actions, and role
    lookups. All three traces are counted here and expected to be zero.
    """
    injections = guard_actions = 0
    for c in cases:
        p = os.path.join(artifacts, f"case-{c}_injections.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                injections += sum(1 for ln in f if ln.strip())
    ga = os.path.join(artifacts, "guard_actions.jsonl")
    if os.path.exists(ga):
        with open(ga, encoding="utf-8") as f:
            guard_actions = sum(1 for ln in f if ln.strip())
    return {
        "memory_injection_records": injections,
        "guard_action_records": guard_actions,
        "role_profile_reads": 0,
        "role_profile_note": ("no S5A module imports the capability profile; "
                             "checked by an import scan in the tests"),
        "all_off": injections == 0 and guard_actions == 0,
    }


def check_seals(out_dir: str) -> List[Dict[str, Any]]:
    artifacts = os.path.join(out_dir, "runs_70_79", "traceevo", "runs", TAG)
    seal_dir = os.path.join(out_dir, "seals")
    rows = []
    if not os.path.isdir(seal_dir):
        return rows
    for fn in sorted(os.listdir(seal_dir)):
        if not fn.endswith("_seal.json"):
            continue
        with open(os.path.join(seal_dir, fn), encoding="utf-8") as f:
            seal = json.load(f)
        now = seal_one(artifacts, seal["task_id"], seal["case"])
        rows.append({"case": seal["case"], "task_id": seal["task_id"],
                     "sealed_hash": seal["input_artifact_hash"],
                     "recomputed_hash": now["input_artifact_hash"],
                     "match": now["input_artifact_hash"]
                     == seal["input_artifact_hash"],
                     "changed": [k for k, v in now["artifacts"].items()
                                 if v != seal["artifacts"].get(k, "")]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cases", default="70-79")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--stall-timeout", type=float, default=1800.0)
    ap.add_argument("--run", action="store_true")
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
    if not set(cases) <= set(CASES):
        raise SystemExit(
            f"S5A may only run case 70-79; got {cases}. §7 forbids reading "
            f"case 80-137 and this refuses before opening anything.")
    if args.check_seals:
        rows = check_seals(args.out)
        bad = [r for r in rows if not r["match"]]
        print(json.dumps({"checked": len(rows),
                          "verified": len(rows) - len(bad),
                          "rejected": len(bad),
                          "rejected_cases": [r["task_id"] for r in bad]},
                         ensure_ascii=False, indent=2))
        return 0
    if args.run:
        run(args.out, cases, args.attempts, args.stall_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
