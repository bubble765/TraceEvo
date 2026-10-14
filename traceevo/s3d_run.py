"""S3D-A §3.1/§3.3: run case 50-59 with no memory, and seal before outcome.

Two phases, and the gap between them is the point:

    --run      blind dataset, Memory=OFF, Capability Profile=OFF. Per case:
               GoS -> artifacts on disk -> per-case manifest -> seal.
               Gold is not readable by this phase at all.
    --verify   separate process, separate invocation. Reads the reference,
               and refuses any case whose artifacts do not hash to the seal.

§3.3 asks for `case-XX_seal.json` per case and for the verifier to check
`input_artifact_hash == sealed_hash`, with the consequence spelled out: a case
that fails that check **does not enter the experience corpus**. That check is
implemented as a hard refusal in `s3d_verify.py`, not a warning.

S3A did not seal and S3C could not prove provenance as a result
(`S3C_experience_expansion/failure_cases.md` FAILURE 1). This is the stage that
fixes it.

Usage:
    python traceevo/s3d_run.py --run
    python traceevo/s3d_run.py --verify
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

OUT = os.path.join(ROOT, "S3D_verified_expansion")
CASES = list(range(50, 60))
TAG = "s3d_expansion"
# Every artifact the runner leaves on disk for a case, not a curated subset.
#
# The first version listed five of these ten, chosen by asking "which ones does
# verification read?" -- and that question has the wrong answer. §3.3 step 2
# names events / ledger / deltas / graph / prediction / report, so the graph
# (`_provenance_graph.json`) and the run summary that carries
# `final_graph_hash` were outside the seal that exists to bind them. A subset
# seal is also the version that quietly stops covering a new artifact when the
# runner starts writing one. Hashing all ten removes the judgment call.
SUFFIXES = ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
            "_integrity.json", "_invocations.jsonl", "_injections.jsonl",
            "_provenance_graph.json", "_provenance_graph.png",
            "_replay.json", "_summary.json")


def _sha256_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_hashes(artifacts: str, task: str) -> Dict[str, str]:
    return {s.lstrip("_"): _sha256_file(os.path.join(artifacts, f"{task}{s}"))
            for s in SUFFIXES}


def seal_one(artifacts: str, task: str, case: int) -> Dict[str, Any]:
    """§3.3 step 3-4: per-case manifest, then the seal."""
    from traceevo.audit.event_schema import utc_now
    h = _artifact_hashes(artifacts, task)
    complete = bool(h["events.jsonl"] and h["bindings.json"])
    # The sealed value is the digest of the artifact digest map, so a reviewer
    # can recompute it from the same files without trusting this file.
    sealed_hash = hashlib.sha256(json.dumps(sorted(h.items())).encode(
        "utf-8")).hexdigest()
    return {
        "case": case, "task_id": task, "sealed_at": utc_now(),
        "artifacts": h, "complete": complete,
        "input_artifact_hash": sealed_hash,
        "note": ("§3.3 seal. s3d_verify.py recomputes input_artifact_hash from "
                 "the artifacts on disk and refuses the case if it differs."),
    }


def _write_seal(out_dir: str, seal: Dict[str, Any]) -> str:
    d = os.path.join(out_dir, "seals")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{seal['task_id']}_seal.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(seal, f, ensure_ascii=False, indent=2)
    return p


# ------------------------------------------------------------------- --run
def run_phase(out_dir: str, cases: List[int], attempts: int = 3,
              stall_timeout: float = 1800.0) -> Dict[str, Any]:
    from traceevo.s3b_probe import _run_case, _clear_case
    from traceevo.evolution.blinding import make_blinded_dataset, verify_blinded
    from traceevo.s2_cases import load_env_file

    os.makedirs(out_dir, exist_ok=True)
    blinded_csv = os.path.join(out_dir, "dataset_blinded_50_59.csv")
    manifest = make_blinded_dataset(DATASET_CSV, blinded_csv)
    check = verify_blinded(blinded_csv)
    if not check["blinded"]:
        raise SystemExit(f"blinding failed: {check['gold_columns_present']}")
    print(f"blinded: {manifest['columns_removed']} removed, "
          f"{manifest['n_cases']} rows -> {blinded_csv}")

    run_root = os.path.join(out_dir, "runs_50_59")
    # `hooks.py:207` builds the artifact dir as `output_dir/runs`, so the env
    # var is the parent and the tag names the leaf. Passing the artifacts path
    # itself as output_dir would put the traces one level deeper than this
    # module then looks for them -- the run would succeed and the seal step
    # would find nothing, which is S3C FAILURE 6's shape.
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
    # §3.1: Memory OFF, Capability Profile OFF. Nothing else touches these.
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(blinded_csv)
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(tx_root)

    seals, crashed = [], []
    mode = "w"
    for c in cases:
        task = f"case-{c}"
        if os.path.exists(os.path.join(out_dir, "seals",
                                       f"{task}_seal.json")):
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
                proc, stalled = _run_case(cmd, env, log, log_path,
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
        # §3.3 steps 3-4: manifest and seal, before anything reads a label
        seal = seal_one(artifacts, task, c)
        _write_seal(out_dir, seal)
        seals.append(seal)
        print(f"  {task}: sealed input_artifact_hash="
              f"{seal['input_artifact_hash'][:16]}", flush=True)

    # §3.2's freeze must survive the run
    from traceevo.s3d_freeze import verify as verify_freeze
    fz = verify_freeze(out_dir)
    out = {"sealed": len(seals), "crashed": crashed,
           "freeze_unchanged": fz["unchanged"], "freeze_drift": fz["drift"],
           "blinding": manifest}
    print(f"\nsealed {len(seals)}/{len(cases)}; freeze unchanged: "
          f"{fz['unchanged']}")
    with open(os.path.join(out_dir, "run_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--cases", default="50-59")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--stall-timeout", type=float, default=1800.0)
    args = ap.parse_args()
    cases: List[int] = []
    for part in args.cases.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cases.extend(range(int(a), int(b) + 1))
        elif part:
            cases.append(int(part))
    if not set(cases) <= set(range(50, 60)):
        raise SystemExit(
            f"S3D may only run case 50-59; got {cases}. §D2 forbids reading "
            f"case 60-137 and this refuses before opening anything.")
    if args.run:
        run_phase(args.out, cases, args.attempts, args.stall_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
