"""T1: prove the provenance instrumentation does not change GoS behaviour.

GoS samples at temperature 0.8, so two live runs of the same code legitimately
differ. Comparing them would prove nothing. Instead this driver makes the run
deterministic by recording every LLM response and replaying it:

    A. provenance OFF + record   -> live run, writes the recording
    B. provenance OFF + replay   -> serves A's responses (control)
    C. provenance ON  + replay   -> serves A's responses (treatment)

B must equal A exactly. If C equals B exactly, then instrumentation changed
nothing about what GoS did: same prompts (any change would break the replay
key), same control flow, same predictions, same final causal graph.

Usage:
    python -m traceevo.t1_noninvasiveness --cases 0-9 --tag t1
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
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable

RUNS = [
    # A uses `extend`, not `record`: it serves any already-recorded prefix (so a
    # run interrupted by a hung request resumes instead of starting over) and
    # appends live calls past the end.
    ("A_prov_off_record", {"TRACEEVO_ENABLED": "0", "TRACEEVO_REPLAY_MODE": "extend"}),
    ("B_prov_off_replay", {"TRACEEVO_ENABLED": "0", "TRACEEVO_REPLAY_MODE": "replay"}),
    ("C_prov_on_replay", {"TRACEEVO_ENABLED": "1", "TRACEEVO_REPLAY_MODE": "replay"}),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_one(name: str, env_overrides: Dict[str, str], cases: str,
            recording: str, workdir: str) -> Dict[str, Any]:
    run_dir = os.path.join(workdir, name)
    # Start from a clean output directory. main.py appends to the results CSV,
    # so a reused directory would silently concatenate several attempts into
    # one file and inflate the case count.
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_OUTPUT_DIR"] = os.path.join(run_dir, "traceevo")
    env["TRACEEVO_REPLAY_PATH"] = recording
    env.update(env_overrides)

    cmd = [PY, "-u", "main.py",
           "--cases", cases,
           "--out-csv", os.path.join(run_dir, "results.csv"),
           "--html-dir", os.path.join(run_dir, "html"),
           "--json-dir", os.path.join(run_dir, "graphs"),
           "--log-dir", os.path.join(run_dir, "logs"),
           "--tag", name]
    log_path = os.path.join(run_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=GOS, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    return {"name": name, "returncode": proc.returncode, "dir": run_dir,
            "log": log_path,
            "served": f"{recording}.served" if env_overrides.get(
                "TRACEEVO_REPLAY_MODE") != "record" else f"{recording}.served"}


def summarise_run(run: Dict[str, Any]) -> Dict[str, Any]:
    d = run["dir"]
    results = os.path.join(d, "results.csv")
    rows: List[Dict[str, str]] = []
    if os.path.exists(results):
        with open(results, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    graph_hashes = {}
    gdir = os.path.join(d, "graphs")
    if os.path.isdir(gdir):
        for fn in sorted(os.listdir(gdir)):
            if fn.endswith("_graphs.json"):
                case = fn.replace("_graphs.json", "")
                graph_hashes[case] = sha256_file(os.path.join(gdir, fn))[:16]

    served = []
    served_path = f"{RUNS_RECORDING}.served"
    if os.path.exists(served_path):
        with open(served_path, encoding="utf-8") as f:
            served = [ln.strip() for ln in f if ln.strip()]

    log_text = ""
    if os.path.exists(run["log"]):
        with open(run["log"], encoding="utf-8", errors="replace") as f:
            log_text = f.read()

    # The replay accounting line proves a replay consumed the whole recording;
    # otherwise "identical outputs" could mean "the run did less work".
    backend = None
    for line in log_text.splitlines():
        if line.startswith("[TraceEvo] llm backend:"):
            try:
                backend = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                backend = {"raw": line}

    return {
        "name": run["name"],
        "returncode": run["returncode"],
        "n_cases": len(rows),
        "predictions": {str(i): (rows[i]["prediction"] if i < len(rows) else None)
                        for i in range(len(rows))},
        "report_hashes": {str(i): sha256_text(rows[i].get("report", ""))[:16]
                          for i in range(len(rows))},
        "graph_hashes": graph_hashes,
        "llm_calls": log_text.count("Generation Completed"),
        "n_prompt_keys": len(served),
        "prompt_sequence_hash": sha256_text("\n".join(served))[:16] if served else "",
        "llm_backend": backend,
        "replay_exhausted": (backend or {}).get("exhausted"),
        "traceevo_lines": [ln for ln in log_text.splitlines()
                           if ln.startswith("[TraceEvo]")],
    }


RUNS_RECORDING = ""


def compare(a: Dict[str, Any], b: Dict[str, Any],
            fields: Optional[List[str]] = None) -> Dict[str, Any]:
    diffs = []
    for field in (fields or ("n_cases", "predictions", "report_hashes",
                             "graph_hashes", "llm_calls", "n_prompt_keys",
                             "prompt_sequence_hash")):
        if a[field] != b[field]:
            if isinstance(a[field], dict):
                keys = set(a[field]) | set(b[field])
                detail = {k: [a[field].get(k), b[field].get(k)] for k in sorted(keys)
                          if a[field].get(k) != b[field].get(k)}
                diffs.append({"field": field, "detail": detail})
            else:
                diffs.append({"field": field,
                              "detail": [a[field], b[field]]})
    return {"identical": not diffs, "differences": diffs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="0-9")
    ap.add_argument("--tag", default="t1")
    ap.add_argument("--workdir", default=os.path.join(ROOT, "S1_runs"))
    ap.add_argument("--recording", default=None)
    ap.add_argument("--fresh", action="store_true",
                    help="discard any existing recording and re-record run A "
                         "from scratch (default: resume an existing recording)")
    args = ap.parse_args()

    global RUNS_RECORDING
    workdir = os.path.join(args.workdir, args.tag)
    os.makedirs(workdir, exist_ok=True)
    recording = args.recording or os.path.join(workdir, "llm_responses.jsonl")
    RUNS_RECORDING = recording

    summaries = []
    for name, overrides in RUNS:
        print(f"\n=== {name} ===", flush=True)
        if name.startswith("A_"):
            existing = os.path.exists(recording)
            if args.fresh or not existing:
                for stale in (recording, f"{recording}.served"):
                    if os.path.exists(stale):
                        os.remove(stale)
            else:
                n = sum(1 for ln in open(recording, encoding="utf-8") if ln.strip())
                print(f"  resuming from an existing recording of {n} calls "
                      f"(use --fresh to re-record)", flush=True)
        run = run_one(name, overrides, args.cases, recording, workdir)
        summary = summarise_run(run)
        summaries.append(summary)
        print(f"  rc={summary['returncode']} cases={summary['n_cases']} "
              f"llm_calls={summary['llm_calls']} prompts={summary['n_prompt_keys']} "
              f"prompt_seq={summary['prompt_sequence_hash']}", flush=True)
        if summary["returncode"] != 0:
            print("  RUN FAILED; see", run["log"], flush=True)

    all_runs_ok = all(s["returncode"] == 0 for s in summaries)
    all_exhausted = all(s.get("replay_exhausted") in (None, True)
                        for s in summaries)
    report = {
        "cases": args.cases,
        "recording": recording,
        "runs": summaries,
        # A run that aborts makes the three runs trivially "identical" -- they
        # all stop at the same place. Without this guard the comparison would
        # report a vacuous pass, which is how the first extend-mode attempt
        # appeared to succeed while every run had actually crashed.
        "all_runs_completed": all_runs_ok,
        "replays_exhausted": all_exhausted,
        "verdict": ("PASS: provenance is non-invasive"
                    if (all_runs_ok and all_exhausted
                        and compare(summaries[1], summaries[2])["identical"])
                    else "INCONCLUSIVE: see runs[].returncode and differences"),
        # `replay_exhausted` only exists for replay runs, so it is excluded from
        # the record-vs-replay comparisons; it is reported separately.
        "A_vs_B": compare(summaries[0], summaries[1]),
        "B_vs_C": compare(summaries[1], summaries[2],
                          fields=("n_cases", "predictions", "report_hashes",
                                  "graph_hashes", "llm_calls", "n_prompt_keys",
                                  "prompt_sequence_hash", "replay_exhausted")),
        "A_vs_C": compare(summaries[0], summaries[2]),
    }
    out = os.path.join(workdir, "t1_noninvasiveness.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n=== T1 RESULT ===")
    print("run return codes:", [s["returncode"] for s in summaries])
    print(f"A vs B (replay reproduces the live run) : "
          f"{'IDENTICAL' if report['A_vs_B']['identical'] else 'DIFFERENT'}")
    print(f"B vs C (provenance changes nothing)     : "
          f"{'IDENTICAL' if report['B_vs_C']['identical'] else 'DIFFERENT'}")
    if not report["B_vs_C"]["identical"]:
        for d in report["B_vs_C"]["differences"]:
            print("  ", d["field"], str(d["detail"])[:400])
    print(f"VERDICT: {report['verdict']}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
