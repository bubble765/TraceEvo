"""S5A-G §14: non-invasiveness by record/replay, and local processing overhead.

§14 says not to spend another full live GoS run proving non-invasiveness, and
to use record/replay instead. So the comparison is three replays of one
recording:

    A  Core OFF, replay      the plain-GoS arm
    B  Core ON,  replay      the instrumented arm
    C  Core ON,  replay      a repeat, to show B is not a one-off

Strict replay raises on a differing prompt or an extra call, so "the prompt
sequence matches" is enforced rather than measured; what the comparison adds is
that the two arms were handed the *same* recording and produced the same
prediction, report and final graph from it.

The overhead half is deliberately split, because §14 says the two numbers must
not be mixed:

* **Local TraceEvo processing** — event logging, graph delta, audit, gate,
  storage. Measured by timing the real functions over the sealed artifacts, and
  by a timed replay for the in-line parts. This is a property of the
  implementation and is comparable across runs.
* **LLM network time** — reported separately from the run logs, and not used
  for any claim: S4B's run showed the gateway hanging for minutes at a time,
  so a wall-clock figure that includes it measures the network as much as the
  method.

Token usage is reported as `TOKEN_UNAVAILABLE` unless the ledger carries it.
§14's "不得估算" is followed literally.

Usage:
    python traceevo/s5a_overhead.py --out S5A_protocol_validation
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5A_protocol_validation")
TAG = "core_v1"
CASES = list(range(70, 80))
# A recording made before S5A, so neither arm can have influenced it.
RECORDING = os.path.join(ROOT, "S2_audit", "case_studies", "_work_A4_case5",
                         "clean_recording.jsonl")
REC_CASE = 5


def _sha_text(t: str) -> str:
    import hashlib
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _run(name: str, core_on: bool, workdir: str,
         extra_env: Optional[Dict[str, str]] = None,
         time_it: bool = False) -> Dict[str, Any]:
    import shutil
    import subprocess
    from traceevo.s2_cases import load_env_file

    load_env_file()
    run_dir = os.path.join(workdir, name)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1" if core_on else "0"
    env["TRACEEVO_LEDGER"] = "1" if core_on else "0"
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_GUARD"] = "0"
    env["TRACEEVO_REPLAY_MODE"] = "replay"
    env["TRACEEVO_REPLAY_STRICT"] = "1"
    env["TRACEEVO_REPLAY_PATH"] = RECORDING
    env["TRACEEVO_OUTPUT_DIR"] = os.path.join(run_dir, "traceevo")
    env.update(extra_env or {})
    cmd = [PY, "-u", "main.py", "--cases", str(REC_CASE),
           "--out-csv", os.path.join(run_dir, "results.csv"),
           "--html-dir", os.path.join(run_dir, "html"),
           "--json-dir", os.path.join(run_dir, "graphs"),
           "--log-dir", os.path.join(run_dir, "logs"),
           "--tag", name]
    log_path = os.path.join(run_dir, "run.log")
    t0 = time.perf_counter()
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=GOS, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    wall = time.perf_counter() - t0
    return {"name": name, "core_on": core_on, "returncode": proc.returncode,
            "dir": run_dir, "log": log_path,
            "wall_seconds": round(wall, 3) if time_it else None}


def _summarise(run: Dict[str, Any]) -> Dict[str, Any]:
    d = run["dir"]
    rows = []
    res = os.path.join(d, "results.csv")
    if os.path.exists(res):
        with open(res, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    art = os.path.join(d, "traceevo", "runs", run["name"])
    out: Dict[str, Any] = {
        "name": run["name"], "core_on": run["core_on"],
        "returncode": run["returncode"],
        "wall_seconds": run["wall_seconds"],
        "prediction": rows[0]["prediction"] if rows else "",
        "report_hash": _sha_text(rows[0].get("report", ""))[:16] if rows else "",
    }
    # §14's "final graph" is GoS's own graph output, which both arms produce,
    # not the TraceEvo summary's `final_graph_hash` -- that field is absent by
    # construction in the Core-OFF arm, so comparing it reported a difference
    # that only says the instrumented arm wrote an instrumentation file. The
    # first version did exactly that and failed §15 condition 5 on a
    # non-difference.
    g = os.path.join(d, "graphs", f"case{REC_CASE}_graphs.json")
    out["gos_final_graph_hash"] = _sha_text(
        open(g, encoding="utf-8").read())[:16] if os.path.exists(g) else ""
    s = os.path.join(art, f"case-{REC_CASE}_summary.json")
    if os.path.exists(s):
        with open(s, encoding="utf-8") as f:
            sj = json.load(f)
        out["traceevo_final_graph_hash"] = sj.get("final_graph_hash", "")
        out["integrity_ok"] = sj.get("integrity_ok")
    log = ""
    if os.path.exists(run["log"]):
        with open(run["log"], encoding="utf-8", errors="replace") as f:
            log = f.read()
    out["replay_errors"] = sum(1 for ln in log.splitlines()
                               if "replay divergence" in ln
                               or "replay underrun" in ln)
    out["llm_generation_lines"] = log.count("Generation Completed")
    ev = os.path.join(art, f"case-{REC_CASE}_events.jsonl")
    evs = _jsonl(ev)
    VOLATILE = {"timestamp", "event_hash", "prev_hash"}
    out["n_events"] = len(evs)
    out["semantic_sequence_hash"] = _sha_text(json.dumps(
        [{k: v for k, v in e.items() if k not in VOLATILE} for e in evs],
        sort_keys=True, ensure_ascii=False))[:16] if evs else ""
    return out


def noninvasive(out_dir: str) -> Dict[str, Any]:
    workdir = os.path.join(out_dir, "_replay_work")
    os.makedirs(workdir, exist_ok=True)
    runs = [_run("A_core_off", False, workdir, time_it=True),
            _run("B_core_on", True, workdir, time_it=True),
            _run("C_core_on_repeat", True, workdir, time_it=True)]
    sums = [_summarise(r) for r in runs]
    a, b, c = sums
    # Compared across arms: things both arms produce. `n_events` and
    # `semantic_sequence_hash` are compared Core-ON-to-Core-ON (the repeat),
    # because a Core-OFF run writes no provenance events at all.
    fields = ("prediction", "report_hash", "gos_final_graph_hash")
    on_only = ("n_events", "semantic_sequence_hash",
               "traceevo_final_graph_hash")
    diffs_on = {k: [a.get(k), b.get(k)] for k in fields
                if a.get(k) != b.get(k)}
    diffs_repeat = {k: [b.get(k), c.get(k)] for k in fields + on_only
                    if b.get(k) != c.get(k)}
    diffs_off_on_only = {k: [a.get(k), b.get(k)] for k in on_only
                         if a.get(k) != b.get(k)}

    rows = [{"run": s["name"], "core": "ON" if s["core_on"] else "OFF",
             "returncode": s["returncode"], "wall_seconds": s["wall_seconds"],
             "prediction_sha16": _sha_text(s["prediction"])[:16],
             "report_sha16": s["report_hash"],
             "gos_final_graph_hash": s.get("gos_final_graph_hash", ""),
             "n_events": s["n_events"],
             "semantic_sequence_hash": s["semantic_sequence_hash"],
             "replay_errors": s["replay_errors"]}
            for s in sums]
    with open(os.path.join(out_dir, "replay_nonintrusive_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    out = {
        "recording": os.path.relpath(RECORDING, ROOT),
        "recording_case": REC_CASE,
        "comparison": "Core ON vs Core OFF, same recording, strict replay",
        "core_off_vs_on_identical": not diffs_on,
        "differences_off_vs_on": diffs_on,
        "differences_on_only_fields": diffs_off_on_only,
        "on_only_note": ("`n_events`, `semantic_sequence_hash` and the "
                         "TraceEvo graph hash exist only when Core is ON; they "
                         "are compared between the two Core-ON runs, and their "
                         "absence in the Core-OFF arm is the definition of "
                         "Core being off rather than a difference"),
        "core_on_repeatable": not diffs_repeat,
        "differences_on_vs_repeat": diffs_repeat,
        "runs_completed": all(s["returncode"] == 0 for s in sums),
        "strict_replay_clean": all(s["replay_errors"] == 0 for s in sums),
        "excluded_fields": ["timestamp", "event_hash", "prev_hash"],
        "exclusion_reason": ("the two hashes are computed over the event "
                             "including its timestamp, so they differ between "
                             "runs whose content is identical"),
        "note": ("strict replay raises on a differing prompt or an extra LLM "
                 "call, so matching prompt sequences are enforced by the "
                 "recorder rather than measured here; this comparison adds "
                 "that both arms produced the same prediction, report and "
                 "graph from the same recording"),
        "runs": sums,
    }
    with open(os.path.join(out_dir, "nonintrusive_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def local_overhead(out_dir: str) -> Dict[str, Any]:
    """§14's local processing costs, timed over the sealed block."""
    from traceevo.s3b_gate import audit_bundle, _integrity_ok
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import _trace_hashes
    import pandas as pd

    artifacts = os.path.join(out_dir, "runs_70_79", "traceevo", "runs", TAG)
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_70_79.csv"))
    rows = []
    for c in CASES:
        task = f"case-{c}"
        if not os.path.exists(os.path.join(artifacts, f"{task}_events.jsonl")):
            continue
        t0 = time.perf_counter()
        b = audit_bundle(artifacts, task)
        t_audit = time.perf_counter() - t0

        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        cand = build_candidate(task, _trace_hashes(artifacts, task), key,
                               "uncertain", b["events"], b["ledger"],
                               b["summary"], [a["type"] for a in b["anomalies"]])
        t0 = time.perf_counter()
        gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                      b["anomalies"], _integrity_ok(b), b["semantic_complete"],
                      forbidden_hits=forbidden_content(cand, {}))
        t_gate = time.perf_counter() - t0

        storage = sum(os.path.getsize(os.path.join(artifacts, f))
                      for f in os.listdir(artifacts)
                      if f.startswith(task))
        # Microsecond precision on the gate: at four decimal places it rounds
        # to 0.0, and a reported 0.0 reads as "not measured" rather than as
        # "fast". The audit is milliseconds-scale and a 4-decimal figure is
        # honest there; the gate is not, so it gets its own unit.
        rows.append({
            "case": c,
            "audit_seconds": round(t_audit, 4),
            "gate_seconds": round(t_gate, 6),
            "gate_microseconds": round(t_gate * 1e6, 1),
            "traceevo_seconds": round(t_audit + t_gate, 6),
            "storage_bytes": storage,
            "storage_kib": round(storage / 1024, 1),
            "n_events": len(b["events"]),
            "n_ledger_calls": len(b["ledger"]),
            "llm_network_seconds": "REPORTED_SEPARATELY",
            "token_usage": "TOKEN_UNAVAILABLE",
        })
    with open(os.path.join(out_dir, "overhead_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def med(k, nd=4):
        xs = [r[k] for r in rows]
        return round(statistics.median(xs), nd) if xs else None

    out = {
        "n_cases": len(rows),
        "local_processing": {
            "audit_seconds_per_case_median": med("audit_seconds"),
            "gate_seconds_per_case_median": med("gate_seconds", 6),
            "gate_microseconds_per_case_median": med("gate_microseconds", 1),
            "gate_precision_note": ("reported in microseconds as well, because "
                                    "the gate is sub-millisecond and a "
                                    "4-decimal seconds figure would round to "
                                    "0.0 and read as unmeasured"),
            "traceevo_seconds_per_case_median": med("traceevo_seconds", 6),
            "storage_kib_per_case_median": med("storage_kib"),
            "what_this_is": ("wall-clock of the local TraceEvo functions over "
                             "sealed artifacts, on this machine; a property "
                             "of the implementation rather than of the "
                             "network"),
        },
        "llm_network": {
            "status": "REPORTED_SEPARATELY",
            "note": ("§14: not mixed with local audit time. The gateway was "
                     "observed hanging for minutes during S4B, so a wall-clock "
                     "figure including it measures the network as much as the "
                     "method."),
        },
        "tokens": {
            "status": "TOKEN_UNAVAILABLE",
            "note": "§14: 不得估算 — the API does not report per-call usage.",
        },
        "per_case": rows,
    }
    with open(os.path.join(out_dir, "overhead_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip-replay", action="store_true")
    ap.add_argument("--skip-overhead", action="store_true")
    args = ap.parse_args()
    if not args.skip_replay:
        n = noninvasive(args.out)
        print(json.dumps({k: v for k, v in n.items()
                          if k in ("core_off_vs_on_identical",
                                   "core_on_repeatable", "runs_completed",
                                   "strict_replay_clean", "differences_off_vs_on")},
                         ensure_ascii=False, indent=2))
    if not args.skip_overhead:
        o = local_overhead(args.out)
        print(json.dumps(o["local_processing"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
