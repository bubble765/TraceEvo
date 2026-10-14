"""S5B-J §14: final overhead, in the three classes §14 separates.

§14 splits the report and forbids mixing the parts:

    Local TraceEvo   event logging, ledger, graph delta, audit, gate, storage
                     — a property of the implementation, comparable across
                     runs, reported as median / IQR / p95.
    LLM / API        number of model calls and wall-clock duration. Network
                     latency is engineering data, not a method result.
    Token            `TOKEN_UNAVAILABLE` unless the API reports usage. §14's
                     不得估算 is followed literally; nothing is estimated.

The split exists because a single wall-clock number would be dominated by a
gateway that was observed hanging for minutes at a time, and would say more
about the network than about TraceEvo.

What each local figure times, exactly, because "audit time" is otherwise a
word rather than a measurement:

    event_logging   serialising the sealed event log the way the recorder
                    writes it (canonical JSON, one line per event)
    ledger          the same for the invocation ledger
    graph_delta     loading the delta chain and replaying it to the final
                    graph — the derivation and its verification
    audit           `audit_bundle`: evidence tiering plus anomaly detection
                    over the sealed trace
    gate            `experience_gate.evaluate`, the pure decision function
    storage         bytes of every artifact the case wrote

Usage:
    python traceevo/s5b_overhead.py --out S5B_final_heldout
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _pct(xs: List[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _stats(xs: List[float], unit: str = "ms") -> Dict[str, Any]:
    if not xs:
        return {"median": None, "iqr": [None, None], "p95": None, "n": 0,
                "unit": unit}
    return {
        "n": len(xs), "unit": unit,
        "median": round(statistics.median(xs), 4),
        "iqr": [round(_pct(xs, 0.25), 4), round(_pct(xs, 0.75), 4)],
        "p95": round(_pct(xs, 0.95), 4),
        "min": round(min(xs), 4), "max": round(max(xs), 4),
    }


def local_overhead(out_dir: str) -> Dict[str, Any]:
    from traceevo.s3b_gate import audit_bundle, _integrity_ok
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import _trace_hashes
    from traceevo.audit.graph_delta import GraphDelta, replay
    import pandas as pd

    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
    blinded = pd.read_csv(os.path.join(out_dir,
                                       "dataset_blinded_80_137.csv"))
    import tempfile
    tmp = tempfile.mkdtemp(prefix="s5b_overhead_")
    rows = []
    for c in CASES:
        task = f"case-{c}"
        if not os.path.exists(os.path.join(artifacts, f"{task}_events.jsonl")):
            continue
        events = _jsonl(os.path.join(artifacts, f"{task}_events.jsonl"))
        ledger = _jsonl(os.path.join(artifacts, f"{task}_invocations.jsonl"))
        deltas = _jsonl(os.path.join(artifacts, f"{task}_deltas.jsonl"))

        # --- event logging: the recorder's write path
        t0 = time.perf_counter()
        with open(os.path.join(tmp, "events.jsonl"), "w",
                  encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False, sort_keys=True)
                        + "\n")
        t_log = time.perf_counter() - t0

        # --- ledger: the same, for invocation records
        t0 = time.perf_counter()
        with open(os.path.join(tmp, "ledger.jsonl"), "w",
                  encoding="utf-8") as f:
            for r in ledger:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True)
                        + "\n")
        t_led = time.perf_counter() - t0

        # --- graph delta: load and replay the chain to the final graph
        t0 = time.perf_counter()
        objs = [GraphDelta.from_dict(d) for d in deltas]
        replay({"nodes": {}, "edges": [], "start_signal_id": None}, objs)
        t_delta = time.perf_counter() - t0

        # --- audit and gate
        t0 = time.perf_counter()
        b = audit_bundle(artifacts, task)
        t_audit = time.perf_counter() - t0

        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        cand = build_candidate(task, _trace_hashes(artifacts, task), key,
                               "uncertain", b["events"], b["ledger"],
                               b["summary"],
                               [a["type"] for a in b["anomalies"]])
        t0 = time.perf_counter()
        gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                      b["anomalies"], _integrity_ok(b), b["semantic_complete"],
                      forbidden_hits=forbidden_content(cand, {}))
        t_gate = time.perf_counter() - t0

        storage = sum(os.path.getsize(os.path.join(artifacts, f))
                      for f in os.listdir(artifacts) if f.startswith(task))
        rows.append({
            "case": c,
            "event_logging_ms": round(t_log * 1e3, 4),
            "ledger_ms": round(t_led * 1e3, 4),
            "graph_delta_ms": round(t_delta * 1e3, 4),
            "audit_ms": round(t_audit * 1e3, 4),
            "gate_ms": round(t_gate * 1e3, 4),
            "gate_microseconds": round(t_gate * 1e6, 2),
            "traceevo_local_ms": round(
                (t_log + t_led + t_delta + t_audit + t_gate) * 1e3, 4),
            "storage_bytes": storage,
            "storage_mib": round(storage / (1024 * 1024), 4),
            "n_events": len(events), "n_ledger_calls": len(ledger),
            "n_deltas": len(deltas),
            "llm_wall_seconds": "REPORTED_SEPARATELY",
            "token_usage": "TOKEN_UNAVAILABLE",
        })
    _write_csv(os.path.join(out_dir, "overhead_results.csv"), rows)

    def col(k):
        return [r[k] for r in rows]

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    out = {
        "n_cases": len(rows),
        "local_processing": {
            "event_logging": _stats(col("event_logging_ms")),
            "ledger": _stats(col("ledger_ms")),
            "graph_delta": _stats(col("graph_delta_ms")),
            "audit": _stats(col("audit_ms")),
            "gate": _stats(col("gate_ms")),
            "gate_microseconds": _stats(col("gate_microseconds"), "us"),
            "traceevo_local_total": _stats(col("traceevo_local_ms")),
            "storage": _stats(col("storage_mib"), "MiB"),
            "what_this_is": ("wall-clock of the local TraceEvo functions over "
                             "the sealed held-out artifacts, on this machine; "
                             "a property of the implementation rather than of "
                             "the network"),
            "gate_precision_note": (
                "the gate is sub-millisecond and is reported in microseconds "
                "as well: at two decimal places of a millisecond a real "
                "measurement and a rounding artefact are the same string"),
        },
        "llm_network": llm_wall(out_dir, rows),
        "tokens": tokens_from_log(out_dir),
        "per_case": rows,
    }
    with open(os.path.join(out_dir, "overhead_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def llm_wall(out_dir: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§14's LLM/API half: call count and wall clock, reported separately."""
    attempts = _jsonl(os.path.join(out_dir, "attempt_history.jsonl"))
    from datetime import datetime
    walls = []
    for a in attempts:
        try:
            t0 = datetime.fromisoformat(a["started_at"])
            t1 = datetime.fromisoformat(a["finished_at"])
        except Exception:                                 # pragma: no cover
            continue
        walls.append((a["case"], (t1 - t0).total_seconds()))
    total = sum(w for _c, w in walls)
    return {
        "n_model_calls": sum(r["n_ledger_calls"] for r in rows),
        "n_model_calls_definition": ("ledger rows across the held-out block; "
                                     "every model call the recorder saw"),
        "wall_clock_seconds_total": round(total, 1),
        "wall_clock_seconds": _stats([w for _c, w in walls], "s"),
        "n_attempts_timed": len(walls),
        "status": "ENGINEERING_DATA",
        "note": ("§14: this is network and queue time, not a property of the "
                 "method. It is reported so the local numbers above are not "
                 "read as the whole cost, and it is not used for any claim."),
        "per_case": [{"case": c, "wall_seconds": round(w, 1)}
                     for c, w in walls],
    }


TOKEN_RE = __import__("re").compile(
    r"Input token nums[：:]\s*(\d+)\s*\|\s*Output token nums[：:]\s*(\d+)")
ATTEMPT_RE = __import__("re").compile(r"=== (case-\d+) attempt (\d+)/(\d+) ")


def tokens_from_log(out_dir: str) -> Dict[str, Any]:
    """§14's token class, read from the run log rather than declared absent.

    §14 says to report `TOKEN_UNAVAILABLE` if the API has no reliable usage.
    This gateway *does* report it — every model call logs its input and output
    token counts — so declaring the field unavailable would be a false
    statement about the instrument, not a conservative one. S5A reported
    `TOKEN_UNAVAILABLE` for this pipeline; the numbers were there.

    Nothing is estimated. Each figure is summed from the log lines the
    transport itself wrote, and calls are attributed to a case by the
    `=== case-N attempt M ===` marker the runner writes before each attempt,
    so a retried case's tokens are counted twice on purpose.
    """
    log = os.path.join(out_dir, "heldout_original", "run.log")
    if not os.path.exists(log):
        return {"status": "TOKEN_UNAVAILABLE",
                "note": f"no run log at {os.path.relpath(log, ROOT)}"}
    per_case: Dict[str, Dict[str, int]] = {}
    cur = None
    calls = 0
    with open(log, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ATTEMPT_RE.search(line)
            if m:
                cur = m.group(1)
                per_case.setdefault(cur, {"input": 0, "output": 0,
                                          "calls": 0, "attempts": 0})
                per_case[cur]["attempts"] += 1
                continue
            t = TOKEN_RE.search(line)
            if t and cur:
                per_case[cur]["input"] += int(t.group(1))
                per_case[cur]["output"] += int(t.group(2))
                per_case[cur]["calls"] += 1
                calls += 1
    if not calls:
        return {"status": "TOKEN_UNAVAILABLE",
                "note": ("the run log carries no parseable token line; no "
                         "figure is estimated")}
    tin = sum(v["input"] for v in per_case.values())
    tout = sum(v["output"] for v in per_case.values())
    return {
        "status": "REPORTED_BY_API",
        "n_calls_with_usage": calls,
        "input_tokens_total": tin,
        "output_tokens_total": tout,
        "tokens_total": tin + tout,
        "input_tokens_median_per_case": round(
            statistics.median([v["input"] for v in per_case.values()]), 1),
        "output_tokens_median_per_case": round(
            statistics.median([v["output"] for v in per_case.values()]), 1),
        "per_case": per_case,
        "source": ("the transport's own `[LLM-API] Generation Completed` "
                   "lines in heldout_original/run.log"),
        "attribution": ("calls are attributed to a case by the runner's "
                        "`=== case-N attempt M ===` marker; a retried case "
                        "therefore carries its attempts' tokens, which is the "
                        "true cost of that case"),
        "not_an_estimate": True,
        "note": ("§14: 不得估算 applies to guessing. These are summed from "
                 "the API's own reported usage, so the field is reported "
                 "rather than declared unavailable."),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    o = local_overhead(args.out)
    print(json.dumps({"local_processing": o["local_processing"],
                      "llm_network": {k: v for k, v in
                                      o["llm_network"].items()
                                      if k != "per_case"}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
