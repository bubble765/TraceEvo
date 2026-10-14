"""S3A §8: prove the Model Invocation Ledger only observes.

T1 already shows that provenance does not change GoS. This asks the narrower
question §8 asks about the *second* observation channel: with the ledger off
and the ledger on, over the same recording, do the runs agree byte for byte?

    ledger_off  TRACEEVO_ENABLED=1 TRACEEVO_LEDGER=0 replay
    ledger_on   TRACEEVO_ENABLED=1 TRACEEVO_LEDGER=1 replay

Both replay the same recording, so any difference in what GoS did would have to
be caused by the ledger. Six things are compared, which is what §8 lists:

    prompt sequence hash      what GoS sent, in order (from the served log)
    response sequence hash    what the model returned, in order
    prediction                results.csv, per case
    report                    results.csv, per case (hashed)
    final causal graph        graphs/<case>_graphs.json, per case (hashed)
    semantic provenance       <task>_events.jsonl digests (wall-clock
                              timestamps excluded) and _deltas.jsonl hashes

A run that aborts would make the two trivially "identical" -- both stop at the
same place -- so completion and replay exhaustion are checked first, as in T1.

Usage:
    python traceevo/s3a_regression.py --cases 0-9 \
      --recording S1_runs/t1_dev10/llm_responses.jsonl --tag s3a_ledger
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
    ("ledger_off", {"TRACEEVO_ENABLED": "1", "TRACEEVO_LEDGER": "0",
                    "TRACEEVO_REPLAY_MODE": "replay"}),
    ("ledger_on", {"TRACEEVO_ENABLED": "1", "TRACEEVO_LEDGER": "1",
                   "TRACEEVO_REPLAY_MODE": "replay"}),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_one(name: str, overrides: Dict[str, str], cases: str,
            recording: str, workdir: str) -> Dict[str, Any]:
    run_dir = os.path.join(workdir, name)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_OUTPUT_DIR"] = os.path.join(run_dir, "traceevo")
    env["TRACEEVO_REPLAY_PATH"] = recording
    env.update(overrides)
    cmd = [PY, "-u", "main.py", "--cases", cases,
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
            "log": log_path}


# An event's `timestamp` is the wall clock at emission, so two runs of the same
# replay never have byte-identical event files and never could. Its `event_hash`
# and `prev_hash` are derived from it, so they move with it. Hashing the events
# with those three fields removed compares what §8 actually cares about -- the
# semantic provenance -- and is still strict: every other field, including the
# whole payload and the parent links, is covered by the digest.
_VOLATILE_EVENT_FIELDS = ("timestamp", "event_hash", "prev_hash")


def _events_digest(path: str) -> str:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            for k in _VOLATILE_EVENT_FIELDS:
                row.pop(k, None)
            rows.append(row)
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _mapping_by_key(recording: str) -> Dict[str, str]:
    """request key -> response, so a served key can be turned back into a
    response and the response *sequence* compared, not just the prompt one."""
    out: Dict[str, str] = {}
    if not os.path.exists(recording):
        return out
    with open(recording, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            out[row["key"]] = row.get("response", "")
    return out


def summarise_run(run: Dict[str, Any], recording: str) -> Dict[str, Any]:
    d = run["dir"]
    rows: List[Dict[str, str]] = []
    results = os.path.join(d, "results.csv")
    if os.path.exists(results):
        with open(results, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    graph_hashes = {}
    gdir = os.path.join(d, "graphs")
    if os.path.isdir(gdir):
        for fn in sorted(os.listdir(gdir)):
            if fn.endswith("_graphs.json"):
                graph_hashes[fn.replace("_graphs.json", "")] = \
                    sha256_file(os.path.join(gdir, fn))[:16]

    served = []
    served_path = f"{recording}.served"
    if os.path.exists(served_path):
        with open(served_path, encoding="utf-8") as f:
            served = [ln.strip() for ln in f if ln.strip()]
    by_key = _mapping_by_key(recording)
    responses = [by_key.get(k, "") for k in served]

    # Semantic provenance: the events the audit consumes, byte for byte, plus
    # the graph-delta log that Replay(G_t, delta_t) == G_(t+1) is checked on.
    prov = os.path.join(d, "traceevo", "runs", run["name"])
    event_hashes, delta_hashes, invocation_counts = {}, {}, {}
    if os.path.isdir(prov):
        for fn in sorted(os.listdir(prov)):
            p = os.path.join(prov, fn)
            if fn.endswith("_events.jsonl"):
                event_hashes[fn[:-len("_events.jsonl")]] = _events_digest(p)
            elif fn.endswith("_deltas.jsonl"):
                delta_hashes[fn[:-len("_deltas.jsonl")]] = sha256_file(p)[:16]
            elif fn.endswith("_invocations.jsonl"):
                with open(p, encoding="utf-8") as f:
                    invocation_counts[fn[:-len("_invocations.jsonl")]] = \
                        sum(1 for ln in f if ln.strip())

    log_text = ""
    if os.path.exists(run["log"]):
        with open(run["log"], encoding="utf-8", errors="replace") as f:
            log_text = f.read()
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
        "event_hashes": event_hashes,
        "delta_hashes": delta_hashes,
        "ledger_call_counts": invocation_counts,
        "llm_calls": log_text.count("Generation Completed"),
        "n_prompt_keys": len(served),
        "prompt_sequence_hash": sha256_text("\n".join(served))[:16] if served else "",
        "response_sequence_hash": sha256_text("\n".join(responses))[:16] if served else "",
        "llm_backend": backend,
        "replay_exhausted": (backend or {}).get("exhausted"),
    }


FIELDS = ("n_cases", "predictions", "report_hashes", "graph_hashes",
          "event_hashes", "delta_hashes", "llm_calls", "n_prompt_keys",
          "prompt_sequence_hash", "response_sequence_hash",
          "replay_exhausted")


def compare(a: Dict[str, Any], b: Dict[str, Any],
            fields=FIELDS) -> Dict[str, Any]:
    diffs = []
    for field in fields:
        if a[field] != b[field]:
            if isinstance(a[field], dict):
                keys = set(a[field]) | set(b[field])
                detail = {k: [a[field].get(k), b[field].get(k)]
                          for k in sorted(keys)
                          if a[field].get(k) != b[field].get(k)}
            else:
                detail = [a[field], b[field]]
            diffs.append({"field": field, "detail": detail})
    return {"identical": not diffs, "differences": diffs}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="0-9")
    ap.add_argument("--tag", default="s3a_ledger")
    ap.add_argument("--workdir", default=os.path.join(ROOT, "S3A_runs"))
    ap.add_argument("--recording", required=True,
                    help="an existing recording to replay under both arms")
    args = ap.parse_args()

    workdir = os.path.join(args.workdir, args.tag)
    os.makedirs(workdir, exist_ok=True)

    summaries = []
    for name, overrides in RUNS:
        print(f"\n=== {name} ===", flush=True)
        run = run_one(name, overrides, args.cases, args.recording, workdir)
        s = summarise_run(run, args.recording)
        summaries.append(s)
        print(f"  rc={s['returncode']} cases={s['n_cases']} "
              f"calls={s['llm_calls']} prompt_seq={s['prompt_sequence_hash']} "
              f"resp_seq={s['response_sequence_hash']}", flush=True)
        if s["returncode"] != 0:
            print("  RUN FAILED; see", run["log"], flush=True)

    off, on = summaries
    all_ok = all(s["returncode"] == 0 for s in summaries)
    exhausted = all(s.get("replay_exhausted") in (None, True) for s in summaries)
    verdict = compare(off, on)

    report = {
        "cases": args.cases,
        "recording": args.recording,
        "arms": {s["name"]: {"ledger_enabled": s["name"] == "ledger_on",
                             "n_ledger_calls": sum(
                                 s["ledger_call_counts"].values()),
                             "ledger_calls_per_case":
                                 s["ledger_call_counts"]}
                 for s in summaries},
        "all_runs_completed": all_ok,
        "replays_exhausted": exhausted,
        "identical": bool(all_ok and exhausted and verdict["identical"]),
        "differences": verdict["differences"],
        "runs": [{k: v for k, v in s.items() if k != "llm_backend"}
                 for s in summaries],
        "verdict": ("PASS: the ledger observes without changing GoS"
                    if (all_ok and exhausted and verdict["identical"])
                    else "FAIL: the ledger changed the run, or a run did not "
                         "complete -- see differences"),
    }
    out = os.path.join(workdir, "s3a_regression.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # regression_results.csv -- §9's one-line-per-field form of the same thing.
    with open(os.path.join(workdir, "regression_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["field", "ledger_off", "ledger_on", "identical"])
        for field in FIELDS:
            w.writerow([field, _flat(off.get(field)), _flat(on.get(field)),
                        off.get(field) == on.get(field)])

    print("\n=== S3A §8 REGRESSION ===")
    print(f"return codes              : {[s['returncode'] for s in summaries]}")
    print(f"replays exhausted         : {exhausted}")
    print(f"ledger calls (off/on)     : "
          f"{sum(off['ledger_call_counts'].values())} / "
          f"{sum(on['ledger_call_counts'].values())}")
    print(f"all compared fields equal : {verdict['identical']}")
    if not verdict["identical"]:
        for d in verdict["differences"]:
            print("  ", d["field"], str(d["detail"])[:400])
    print(f"VERDICT: {report['verdict']}")
    print(f"\nwrote {out}")
    return 0 if report["identical"] else 1


def _flat(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 120 else s[:117] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
