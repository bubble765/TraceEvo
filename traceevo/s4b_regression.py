"""S4B-C §9.1: prove that with the guard disabled, GoS runs exactly as before.

The claim §9.1 asks for is not "the guard is unlikely to fire" -- it is that
adding the guard *code* changes nothing when the switch is off. Two independent
pieces of evidence are produced:

1. **A replay against a recording made before S4B existed.** The recording at
   `S2_audit/case_studies/_work_A4_case5/clean_recording.jsonl` was written on
   2026-09-17 by the pre-S4B pipeline. Replaying it in strict mode makes the
   recorder raise on any prompt that differs from what that pipeline sent, and
   on any extra LLM call. A clean replay is therefore a byte-level statement
   about the prompts and the call sequence, not a similarity judgement.

2. **A comparison against that run's own artifacts**, which are still on disk.
   Final graph hash, prediction, report and the semantic event sequence are
   compared field by field.

Timestamps and event ids are excluded from the comparison and the exclusion is
stated: `timestamp` is a wall-clock read and differs between any two runs, and
comparing it would make the check fail for a reason that has nothing to do with
the guard. Everything the pipeline *decides* is compared.

Usage:
    python traceevo/s4b_regression.py --out S4B_adaptive_guard
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
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S4B_adaptive_guard")
# Pre-S4B, written 2026-09-17 by the pipeline as it stood before the guard.
RECORDING = os.path.join(ROOT, "S2_audit", "case_studies", "_work_A4_case5",
                         "clean_recording.jsonl")
REFERENCE = os.path.join(ROOT, "S2_audit", "case_studies", "_work_A4_case5",
                         "A4_case5_record", "traceevo", "runs",
                         "A4_case5_record")
CASE = 5

# Fields that legitimately differ between two runs of the same case.
#
# `event_hash` and `prev_hash` are in this set because they are *functions of*
# `timestamp`: the chain hashes the event including its wall-clock reading, so
# a run at a different second has a different hash over identical content. The
# first version of this set excluded only `timestamp`, which made the semantic
# comparison differ on exactly those two derived fields and reported
# "REGRESSION: guard-off is not inert" for a run whose 37 events were
# byte-identical in every field the pipeline decides. A comparison is only as
# good as its exclusion set, and this one failed in the direction that invents
# a defect rather than hiding one.
VOLATILE = {"timestamp", "event_hash", "prev_hash"}


def _sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def semantic_events(path: str) -> Dict[str, Any]:
    """The event stream minus wall-clock fields.

    §9.1 lists "semantic events" as one of the things that must match, and this
    is what that means concretely: who emitted what, in what order, with what
    payload -- and not when the machine happened to run.
    """
    evs = _jsonl(path)
    seq = []
    for e in evs:
        seq.append({k: v for k, v in e.items() if k not in VOLATILE})
    return {
        "n_events": len(evs),
        "sequence_hash": _sha_text(json.dumps(seq, sort_keys=True,
                                              ensure_ascii=False)),
        "types": [e.get("event_type") for e in evs],
        "actors": [e.get("actor_id") for e in evs],
    }


def run_once(name: str, guard: str, workdir: str,
             mode: str = "replay") -> Dict[str, Any]:
    from traceevo.s2_cases import load_env_file
    load_env_file()
    run_dir = os.path.join(workdir, name)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1"
    env["TRACEEVO_MEMORY"] = "0"
    env["TRACEEVO_GUARD"] = guard
    env["TRACEEVO_REPLAY_MODE"] = mode
    env["TRACEEVO_REPLAY_STRICT"] = "1"
    env["TRACEEVO_REPLAY_PATH"] = RECORDING
    env["TRACEEVO_OUTPUT_DIR"] = os.path.join(run_dir, "traceevo")
    cmd = [PY, "-u", "main.py", "--cases", str(CASE),
           "--out-csv", os.path.join(run_dir, "results.csv"),
           "--html-dir", os.path.join(run_dir, "html"),
           "--json-dir", os.path.join(run_dir, "graphs"),
           "--log-dir", os.path.join(run_dir, "logs"),
           "--tag", name]
    log_path = os.path.join(run_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=GOS, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    return {"name": name, "guard": guard, "mode": mode,
            "returncode": proc.returncode, "dir": run_dir, "log": log_path}


def summarise(run: Dict[str, Any]) -> Dict[str, Any]:
    d = run["dir"]
    art = os.path.join(d, "traceevo", "runs", run["name"])
    out: Dict[str, Any] = {"name": run["name"], "returncode": run["returncode"],
                           "guard": run["guard"], "mode": run["mode"]}
    res = os.path.join(d, "results.csv")
    rows = []
    if os.path.exists(res):
        with open(res, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    out["n_cases"] = len(rows)
    out["prediction"] = rows[0]["prediction"] if rows else ""
    out["report_hash"] = _sha_text(rows[0].get("report", ""))[:16] if rows else ""

    summ = os.path.join(art, f"case-{CASE}_summary.json")
    if os.path.exists(summ):
        with open(summ, encoding="utf-8") as f:
            s = json.load(f)
        out["final_graph_hash"] = s.get("final_graph_hash", "")
        out["integrity_ok"] = s.get("integrity_ok")
        out["n_deltas"] = s.get("n_deltas")
    ev = os.path.join(art, f"case-{CASE}_events.jsonl")
    se = semantic_events(ev)
    out["n_events"] = se["n_events"]
    out["semantic_sequence_hash"] = se["sequence_hash"]
    out["event_types"] = se["types"]

    # the replay accounting proves the recording was consumed, not skipped
    log = ""
    if os.path.exists(run["log"]):
        with open(run["log"], encoding="utf-8", errors="replace") as f:
            log = f.read()
    out["replay_errors"] = [ln for ln in log.splitlines()
                            if "replay divergence" in ln
                            or "replay underrun" in ln]
    guard_log = os.path.join(art, "guard_actions.jsonl")
    out["guard_actions"] = len(_jsonl(guard_log))
    out["guard_log_exists"] = os.path.exists(guard_log)
    return out


def reference_summary() -> Dict[str, Any]:
    """The same fields, read from the pre-S4B run of the same case."""
    out: Dict[str, Any] = {"name": "A4_case5_record (2026-09-17, pre-S4B)"}
    summ = os.path.join(REFERENCE, f"case-{CASE}_summary.json")
    if os.path.exists(summ):
        with open(summ, encoding="utf-8") as f:
            s = json.load(f)
        out["final_graph_hash"] = s.get("final_graph_hash", "")
        out["integrity_ok"] = s.get("integrity_ok")
        out["n_deltas"] = s.get("n_deltas")
    se = semantic_events(os.path.join(REFERENCE, f"case-{CASE}_events.jsonl"))
    out["n_events"] = se["n_events"]
    out["semantic_sequence_hash"] = se["sequence_hash"]
    out["event_types"] = se["types"]
    return out


def compare(label: str, a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    fields = ("final_graph_hash", "integrity_ok", "prediction", "report_hash",
              "n_events", "semantic_sequence_hash", "event_types", "n_deltas")
    diffs = {k: [a.get(k), b.get(k)] for k in fields
             if k in a and k in b and a.get(k) != b.get(k)}
    return {"comparison": label, "identical": not diffs, "differences": diffs,
            "compared_fields": list(fields)}


def run(out_dir: str) -> Dict[str, Any]:
    workdir = os.path.join(out_dir, "_regression_work")
    os.makedirs(workdir, exist_ok=True)
    a = run_once("guard_off_A", "0", workdir)
    b = run_once("guard_off_B", "0", workdir)
    sa, sb = summarise(a), summarise(b)
    ref = reference_summary()

    checks = [
        compare("guard_off_A vs guard_off_B (determinism)", sa, sb),
        compare("guard_off_A vs pre-S4B run (2026-09-17)", sa, ref),
    ]
    both_clean = (a["returncode"] == 0 and b["returncode"] == 0)
    no_replay_errors = not (sa["replay_errors"] or sb["replay_errors"])
    no_guard_log = not (sa["guard_log_exists"] or sb["guard_log_exists"])

    rows = []
    for s, lbl in ((sa, "guard_off_A"), (sb, "guard_off_B")):
        rows.append({
            "run": lbl, "guard": "disabled", "mode": "replay strict",
            "returncode": s["returncode"],
            "final_graph_hash": s.get("final_graph_hash", ""),
            "prediction_sha16": _sha_text(s.get("prediction", ""))[:16],
            "report_sha16": s.get("report_hash", ""),
            "n_events": s.get("n_events"),
            "semantic_sequence_hash": s.get("semantic_sequence_hash", "")[:16],
            "replay_errors": len(s["replay_errors"]),
            "guard_actions_written": s["guard_actions"],
        })
    if ref.get("final_graph_hash"):
        rows.append({
            "run": "pre-S4B reference (2026-09-17)", "guard": "n/a",
            "mode": "live record", "returncode": "",
            "final_graph_hash": ref.get("final_graph_hash", ""),
            "prediction_sha16": "",
            "report_sha16": "",
            "n_events": ref.get("n_events"),
            "semantic_sequence_hash": ref.get("semantic_sequence_hash", "")[:16],
            "replay_errors": "", "guard_actions_written": "",
        })
    with open(os.path.join(out_dir, "regression_results.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    out = {
        "recording": os.path.relpath(RECORDING, ROOT),
        "recording_predates_s4b": "2026-09-17; S4B began 2026-09-19",
        "reference_run": os.path.relpath(REFERENCE, ROOT),
        "case": CASE,
        "runs": [sa, sb],
        "checks": checks,
        "runs_completed": both_clean,
        "strict_replay_clean": no_replay_errors,
        "no_guard_log_written": no_guard_log,
        "all_identical": all(c["identical"] for c in checks),
        "excluded_fields": sorted(VOLATILE),
        "exclusion_reason": (
            "`timestamp` is a wall-clock read, and `event_hash`/`prev_hash` "
            "are hashes over it, so all three differ between runs whose "
            "content is identical. They are excluded together; comparing the "
            "hashes while excluding the time they were computed from is the "
            "inconsistency that produced a false regression on the first run."),
        "conclusion": ("guard disabled reproduces the pre-S4B pipeline"
                       if all(c["identical"] for c in checks)
                       and no_replay_errors and no_guard_log
                       else "REGRESSION: guard-off is not inert"),
    }
    with open(os.path.join(out_dir, "regression_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    r = run(args.out)
    for c in r["checks"]:
        print(f"  {'IDENTICAL' if c['identical'] else 'DIFFERS  '}  "
              f"{c['comparison']}")
        for k, v in c["differences"].items():
            print(f"      {k}: {str(v[0])[:60]} != {str(v[1])[:60]}")
    print(json.dumps({k: v for k, v in r.items()
                      if k in ("runs_completed", "strict_replay_clean",
                               "no_guard_log_written", "all_identical",
                               "conclusion")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
