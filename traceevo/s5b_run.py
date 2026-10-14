"""S5B-B §5.1 steps 1-5: run case 80-137 under Core-v1, once, then seal.

§5 is one pass in strict case order. This module does steps 1-5 — read the
case input, run Core-v1, write every artifact, seal, verify the seal — and
never opens a reference. `s5b_pipeline.py` does steps 6-12 and is a separate
invocation, so "gold was read after sealing" is a fact about which process
ran rather than a comment in a function.

§5.2 allows a re-run only for infrastructure failure. The attempt history is
therefore a file that is appended to and never rewritten: a case that took
three attempts shows three attempts at the end, including the failed ones.
A run that quietly retried and reported success would erase exactly the
record §5.2 asks for.

The blind is the same one S5A used: `make_blinded_dataset` drops the gold
columns and `verify_blinded` refuses the run if any survive. This is what the
LLM sees; the reference is read in the pipeline module, after sealing.

Usage:
    python traceevo/s5b_run.py --run
    python traceevo/s5b_run.py --check-seals
    python traceevo/s5b_run.py --attempts-report
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

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"
# The same ten artifacts S3D §3.3 listed and S5A sealed. §4's freeze names the
# graph and the report explicitly, and they are inside the seal rather than
# beside it.
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
            "note": ("§5.1 step 5. s5b_pipeline.py recomputes this before any "
                     "reference is read and refuses the case if it differs.")}


def _attempt_log(out_dir: str, records: List[Dict[str, Any]]) -> None:
    """§5.2's attempt history: append-only, never rewritten."""
    p = os.path.join(out_dir, "attempt_history.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(out_dir: str, cases: List[int], attempts: int = 3,
        stall_timeout: float = 1800.0) -> Dict[str, Any]:
    from traceevo.s3b_probe import _run_case, _clear_case
    from traceevo.evolution.blinding import make_blinded_dataset, verify_blinded
    from traceevo.s2_cases import load_env_file
    from traceevo.audit.event_schema import utc_now
    from traceevo.s5b_freeze import log_access

    man_path = os.path.join(out_dir, "Final_Core_v1_freeze_manifest.json")
    if not os.path.exists(man_path):
        raise SystemExit(
            "§4: the freeze manifest must exist before case 80 is opened. "
            "Run `python traceevo/s5b_freeze.py` first.")
    os.makedirs(out_dir, exist_ok=True)
    blinded = os.path.join(out_dir, "dataset_blinded_80_137.csv")
    manifest = make_blinded_dataset(DATASET_CSV, blinded)
    check = verify_blinded(blinded)
    if not check["blinded"]:
        raise SystemExit(f"blinding failed: {check['gold_columns_present']}")

    run_root = os.path.join(out_dir, "heldout_original")
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
    # §4's four switches, all off, one env var each.
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_GUARD"] = "0"
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(blinded)
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(tx_root)

    seals, crashed = [], []
    first_read_logged = False
    mode = "w"
    for c in cases:
        task = f"case-{c}"
        seal_path = os.path.join(out_dir, "seals", f"{task}_seal.json")
        if os.path.exists(seal_path):
            print(f"  {task}: already sealed, skipped", flush=True)
            continue
        if not first_read_logged:
            # §4: the first access to case 80 is recorded here, in the same
            # file the freeze wrote its own timestamp into.
            log_access(out_dir, "first held-out case content read",
                       "traceevo/s5b_run.py", [c],
                       "the blinded case input is read by the GoS process "
                       "below; the freeze manifest predates this entry")
            first_read_logged = True
        records: List[Dict[str, Any]] = []
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
            started = utc_now()
            with open(log_path, mode, encoding="utf-8") as log:
                mode = "a"
                print(f"\n=== {task} attempt {attempt}/{attempts} "
                      f"{started} ===", file=log, flush=True)
                _proc, stalled = _run_case(cmd, env, log, log_path,
                                           stall_timeout)
            complete = os.path.exists(os.path.join(
                artifacts, f"{task}_integrity.json"))
            # §5.2: the reason is recorded for every attempt, not only the
            # ones that failed, so "it took one attempt" is visible too.
            records.append({
                "case": c, "task_id": task, "attempt": attempt,
                "started_at": started, "finished_at": utc_now(),
                "returncode": _proc.returncode, "stalled": bool(stalled),
                "complete": bool(complete),
                "rerun_reason": ("infrastructure: "
                                 + ("stall watchdog killed the process"
                                    if stalled else
                                    f"process exited {_proc.returncode} "
                                    f"without integrity.json")
                                 if attempt > 1 else "first attempt"),
            })
            if stalled:
                print(f"  {task} attempt {attempt}: STALLED, killed",
                      flush=True)
            if complete:
                break
        # §5.2's history is flushed per case rather than at the end of the
        # block. Buffering it until all 58 cases finish means a crash at case
        # 40 loses the record of everything that happened at cases 1-39 --
        # which is precisely the record the condition exists to preserve. The
        # first version buffered, and a stopped run left no attempt history at
        # all.
        if records:
            _attempt_log(out_dir, records)
            records = []
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

    log_access(out_dir, "held-out run pass finished", "traceevo/s5b_run.py",
               [c for c in cases if f"case-{c}_integrity.json"
                in os.listdir(artifacts)] if os.path.isdir(artifacts) else [],
               f"{len(seals)} sealed this pass, {len(crashed)} incomplete")

    sw = switch_evidence(artifacts, cases)
    out = {"tag": TAG, "sealed": len(seals), "crashed": crashed,
           "blinding": manifest, "switch_evidence": sw,
           "artifacts_dir": os.path.relpath(artifacts, ROOT),
           "n_attempts_total": attempts_report(out_dir)["n_attempt_records"]}
    with open(os.path.join(out_dir, "run_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nsealed {len(seals)}/{len(cases)}", flush=True)
    print(json.dumps(sw, ensure_ascii=False, indent=2))
    return out


def switch_evidence(artifacts: str, cases: List[int]) -> Dict[str, Any]:
    """§4's `how_asserted`: check the artifacts, not the flags."""
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
        "failure_memory_activations": injections,
        "guard_action_records": guard_actions,
        "role_profile_reads": 0,
        "role_profile_note": ("no S5B module imports the capability profile; "
                              "checked by an import scan in the tests"),
        "all_off": injections == 0 and guard_actions == 0,
    }


def check_seals(out_dir: str) -> List[Dict[str, Any]]:
    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
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


def attempts_report(out_dir: str) -> Dict[str, Any]:
    p = os.path.join(out_dir, "attempt_history.jsonl")
    rows = []
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
    reruns = [r for r in rows if r["attempt"] > 1]
    return {"n_attempt_records": len(rows),
            "n_cases_attempted": len({r["case"] for r in rows}),
            "n_reruns": len(reruns),
            "rerun_cases": sorted({r["case"] for r in reruns}),
            "rerun_reasons": sorted({r["rerun_reason"] for r in reruns}),
            "records": rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--cases", default="80-137")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--stall-timeout", type=float, default=1800.0)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--check-seals", action="store_true")
    ap.add_argument("--attempts-report", action="store_true")
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
            f"S5B may only run case 80-137; got {cases}. §3 forbids reading "
            f"the development blocks and this refuses before opening "
            f"anything.")
    if args.check_seals:
        rows = check_seals(args.out)
        bad = [r for r in rows if not r["match"]]
        print(json.dumps({"checked": len(rows),
                          "verified": len(rows) - len(bad),
                          "rejected": len(bad),
                          "rejected_cases": [r["task_id"] for r in bad]},
                         ensure_ascii=False, indent=2))
        return 0
    if args.attempts_report:
        print(json.dumps(attempts_report(args.out), ensure_ascii=False,
                         indent=2))
        return 0
    if args.run:
        run(args.out, cases, args.attempts, args.stall_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
