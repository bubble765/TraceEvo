"""S3B §7.1: the Memory-Build run over case 30-39, blinded.

Runs GoS + audit + ledger on ten cases with the gold columns physically absent
from the dataset the run reads (`blinding.py`), seals the traces, and only then
hands the traces to the OutcomeVerifier.

The order is enforced by construction, not by discipline: this script does the
run, and `s3b_verify.py` does the verification as a separate process reading a
different file.

Usage:
    python traceevo/s3b_build.py --cases 30-39 --out S3B_runs/memory_build
    python traceevo/s3b_build.py --verify-only --out S3B_runs/memory_build
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")

sys.path.insert(0, ROOT)


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def run_gos(cases: str, out_dir: str, blinded_csv: str,
            tag: str = "memory_build") -> Dict[str, Any]:
    run_dir = os.path.join(out_dir, "gos")
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    from traceevo.s2_cases import load_env_file
    load_env_file()          # .env.local carries the gateway token
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_REPLAY_MODE"] = "off"
    # The wall, in one line: the run's dataset has no gold column in it.
    # Absolute, because GoS runs with cwd=gos.
    env["TRACEEVO_DATASET_CSV"] = os.path.abspath(blinded_csv)
    env["TRACEEVO_OUTPUT_DIR"] = os.path.abspath(
        os.path.join(run_dir, "traceevo"))
    cmd = [PY, "-u", "main.py", "--cases", cases,
           "--out-csv", os.path.abspath(os.path.join(run_dir, "results.csv")),
           "--html-dir", os.path.abspath(os.path.join(run_dir, "html")),
           "--json-dir", os.path.abspath(os.path.join(run_dir, "graphs")),
           "--log-dir", os.path.abspath(os.path.join(run_dir, "logs")),
           "--tag", tag]
    log_path = os.path.join(run_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=GOS, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    return {"returncode": proc.returncode, "dir": run_dir, "log": log_path,
            "artifacts": os.path.join(run_dir, "traceevo", "runs", tag)}


def seal(trace_dir: str, cases: List[int]) -> Dict[str, Any]:
    """Compute the artifact hashes §4.1 step 4 asks for.

    This is the last thing that happens before the verifier is allowed to read
    a label, so it is recorded as a separate, dated artifact rather than
    computed on the fly during verification.
    """
    from traceevo.audit.event_schema import utc_now

    def _sha(p: str) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    sealed: Dict[str, Any] = {"sealed_at": utc_now(), "traces": {}}
    for c in cases:
        task = f"case-{c}"
        entry: Dict[str, Any] = {}
        for suffix in ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
                       "_integrity.json", "_invocations.jsonl"):
            p = os.path.join(trace_dir, f"{task}{suffix}")
            entry[suffix.lstrip("_")] = _sha(p) if os.path.exists(p) else ""
        entry["complete"] = bool(entry["events.jsonl"]
                                 and entry["bindings.json"])
        sealed["traces"][task] = entry
    return sealed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="30-39")
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_runs",
                                                  "memory_build"))
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--skip-run", action="store_true")
    args = ap.parse_args()
    cases = parse_cases(args.cases)
    os.makedirs(args.out, exist_ok=True)

    # ---------------------------------------------------------- §4.1 steps 1-4
    blind_manifest_path = os.path.join(args.out, "blinding_manifest.json")
    blinded_csv = os.path.join(args.out, "dataset_blinded.csv")
    if not args.verify_only:
        from traceevo.evolution.blinding import (make_blinded_dataset,
                                                 verify_blinded)
        manifest = make_blinded_dataset(DATASET_CSV, blinded_csv)
        check = verify_blinded(blinded_csv)
        if not check["blinded"]:
            raise SystemExit(
                f"blinding failed: {check['gold_columns_present']} still present")
        manifest["verified_blinded"] = check["blinded"]
        with open(blind_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        print(f"blinded dataset: {manifest['columns_removed']} removed, "
              f"{manifest['n_cases']} cases -> {blinded_csv}")

        if not args.skip_run:
            print(f"running GoS on cases {args.cases} (gold absent)...",
                  flush=True)
            run = run_gos(args.cases, args.out, blinded_csv)
            print(f"  rc={run['returncode']} log={run['log']}", flush=True)
            if run["returncode"] != 0:
                print("  RUN FAILED -- see the log; not proceeding to seal",
                      flush=True)
                return 1
        else:
            run = {"artifacts": os.path.join(args.out, "gos", "traceevo",
                                             "runs", "memory_build"),
                   "returncode": 0}

    # ------------------------------------------------------------ §4.1 step 4
    trace_dir = os.path.join(args.out, "gos", "traceevo", "runs",
                             "memory_build")
    if not os.path.isdir(trace_dir):
        raise SystemExit(f"no trace directory at {trace_dir}")
    sealed = seal(trace_dir, cases)
    seal_path = os.path.join(args.out, "sealed_artifacts.json")
    with open(seal_path, "w", encoding="utf-8") as f:
        json.dump(sealed, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for t in sealed["traces"].values() if t["complete"])
    print(f"sealed {n_ok}/{len(cases)} traces -> {seal_path}")
    print("the gold label has not been read by anything yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
