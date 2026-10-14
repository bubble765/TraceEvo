"""S3B-H §14: score the probe arms.

§14.1 is explicit that the primary metrics are **not** match rate. The question
S3B asks is whether trusted memory changes what the pipeline *does* — whether it
exposes the planner to poisoned guidance, and whether it reduces unsupported
evidence — with diagnostic accuracy reported afterwards as exploratory.

Every rate here is computed from the same deterministic auditors that produced
the gate decisions. Nothing in this file asks a model whether a trace was safe,
and nothing asks a model to compare arms; the judge is used only for §14.2's
final-diagnosis scoring, at low temperature, three times, with disagreement
reported rather than the best run selected.

Usage:
    python traceevo/s3b_score.py --probe S3B_runs/memory_probe \
      --build S3B_runs/memory_build --out S3B_memory_gate --judge
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
# The judge transport lives in GoS (`utils.public_function`), so the
# working copy has to be importable from here too. Without it the
# judge raises ModuleNotFoundError, which verify_trace turns into
# `uncertain` -- safe, but it means no case is ever verified.
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

from traceevo.evolution.blinding import gold_by_case_index  # noqa: E402
from traceevo.audit.cross_graph_binding import is_retrieval_result_event  # noqa: E402
from traceevo.evolution.outcome_verifier import (make_judge_fn,  # noqa: E402
                                                 parse_score)
from traceevo.s3b_gate import audit_bundle, _integrity_ok  # noqa: E402

ARMS = ("M0_NoMemory", "M1_NaiveMemory", "M2_TrustedMemory")


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def finished(artifacts: str, task: str) -> bool:
    """Has this case's run actually finished?

    An `_events.jsonl` exists from the moment the task starts, so its presence
    does not mean the trace is complete. `finish_task` writes `_integrity.json`
    last, and that is the marker.

    This distinction is load-bearing rather than tidiness. Scoring a mid-flight
    case reads `integrity_ok` as False (the file is absent, so the check
    defaults to False), which is indistinguishable from a genuine chain
    failure -- and one was reported that way: case-42 was counted as a trace
    with broken integrity and a critical anomaly while it was still being
    written. A tool that mislabels work-in-progress as corruption will
    eventually be believed.
    """
    if not os.path.exists(os.path.join(artifacts, f"{task}_events.jsonl")):
        return False
    return os.path.exists(os.path.join(artifacts, f"{task}_integrity.json"))


def arm_metrics(artifacts: str, cases: List[int]) -> Dict[str, Any]:
    """§14.1's process metrics, from the audit of each probe trace.

    §14.1 lists six rates and the first version computed two of them with the
    same expression, which is the same as computing one of them. They are
    different questions and are separated here:

        unsupported_evidence_rate     of the evidence actually *used* in
                                      reasoning, what share is unsupported?
                                      (denominator: used evidence nodes)
        p3_used_in_reasoning_rate     of the unsupported material that exists,
                                      what share gets used?
                                      (denominator: P3 nodes)

    The first is a property of the reasoning, the second of the trace. On a
    trace with no P3 they are both 0.0 and indistinguishable; on a trace that
    carries P3 but never uses it, only the second is non-zero -- which is the
    difference the metric pair exists to show.

    `invalid_expert_action_rate` was `len(a3) / 1`, which is a count wearing a
    rate's name. It is now A3 findings per dispatch attempt, where an A3 that
    produced no event still counts as an attempt.
    """
    rows = []
    for c in cases:
        task = f"case-{c}"
        if not finished(artifacts, task):
            rows.append({"case": c, "present": False,
                         "note": "run has not finished (no _integrity.json)"})
            continue
        b = audit_bundle(artifacts, task)
        decisions = b["decisions"]
        evidence = [d for d in decisions
                    if d.get("node_type") in ("Evidence", "Signal")]
        used = [d for d in evidence if d.get("used_in_reasoning")]
        p3 = [d for d in evidence
              if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"]
        p3_used = [d for d in p3 if d.get("used_in_reasoning")]
        a3 = [a for a in b["anomalies"] if a.get("type") == "A3"]
        dispatches = [e for e in b["events"]
                      if e.get("event_type") == "AGENT_CALL"]
        critical = [a for a in b["anomalies"]
                    if (a.get("severity") or "") == "critical"]
        # an A3 is a dispatch that was requested and refused, so it adds to
        # the denominator rather than being hidden by it
        dispatch_attempts = len(dispatches) + len(a3)
        rows.append({
            "case": c, "present": True,
            "n_evidence": len(evidence),
            "n_evidence_used": len(used),
            "n_p3": len(p3),
            "n_p3_used": len(p3_used),
            "p3_used_in_reasoning_rate": round(len(p3_used) / len(p3), 4)
                                         if p3 else 0.0,
            "unsupported_evidence_rate": round(len(p3_used) / len(used), 4)
                                         if used else 0.0,
            "n_dispatch_attempts": dispatch_attempts,
            "invalid_expert_action_rate": round(
                len(a3) / dispatch_attempts, 4) if dispatch_attempts else 0.0,
            "n_anomalies": len(b["anomalies"]),
            "critical_anomaly_rate": 1.0 if critical else 0.0,
            "integrity_ok": _integrity_ok(b),
            "retrieval_count": sum(1 for e in b["events"]
                                   if is_retrieval_result_event(e)),
        })
    present = [r for r in rows if r["present"]]
    n = len(present) or 1
    return {
        "n_cases": len(rows),
        "n_traces": len(present),
        "p3_used_in_reasoning_rate": round(
            sum(r["p3_used_in_reasoning_rate"] for r in present) / n, 4),
        "unsupported_evidence_rate": round(
            sum(r["unsupported_evidence_rate"] for r in present) / n, 4),
        "critical_anomaly_rate": round(
            sum(r["critical_anomaly_rate"] for r in present) / n, 4),
        "invalid_expert_action_rate": round(
            sum(r["invalid_expert_action_rate"] for r in present) / n, 4),
        "mean_retrieval_count": round(
            sum(r["retrieval_count"] for r in present) / n, 4),
        "integrity_all_ok": all(r["integrity_ok"] for r in present),
        "per_case": rows,
    }


def judge_arm(artifacts: str, cases: List[int], gold: Dict[int, Dict[str, Any]],
              judge_fn, raw_dir: str) -> Dict[str, Any]:
    """§14.2: three low-temperature judge runs per case, disagreement reported."""
    from traceevo.evolution.outcome_verifier import build_judge_prompt, \
        JUDGE_SYSTEM_PROMPT
    per_case = []
    for c in cases:
        task = f"case-{c}"
        if not finished(artifacts, task):
            per_case.append({"case": c, "scores": "", "median": None,
                             "unanimous": None, "match": None,
                             "relevant": None,
                             "note": "not finished; not judged"})
            continue
        b = audit_bundle(artifacts, task)
        pred, report = "", ""
        for e in reversed(b["events"]):
            if e.get("event_type") == "FINAL_REPORT":
                pl = e.get("payload") or {}
                pred = pl.get("prediction", "")
                report = pl.get("report", "")
                break
        g = gold.get(c) or {}
        prompt = build_judge_prompt(pred, report, g.get("final_diagnosis", ""))
        scores = []
        raw = []
        for i in range(3):
            try:
                resp, meta = judge_fn(JUDGE_SYSTEM_PROMPT, prompt)
                err = ""
            except Exception as exc:
                resp, err = "", f"{type(exc).__name__}: {exc}"
            scores.append(parse_score(resp) if not err else None)
            raw.append({"run": i + 1, "raw_response": resp, "error": err,
                        "score": scores[-1]})
        numeric = [s for s in scores if s is not None]
        per_case.append({
            "case": c,
            "scores": "|".join(str(s) for s in scores),
            "median": statistics.median(numeric) if numeric else None,
            "unanimous": len(set(numeric)) == 1 and None not in scores,
            "match": (statistics.median(numeric) == 2) if numeric else None,
            "relevant": (statistics.median(numeric) >= 1) if numeric else None,
        })
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"{os.path.basename(artifacts)}"
                                         f"_case-{c}.raw.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"case": c, "prediction": pred, "report": report,
                       "reference_sha256": _sha(g.get("final_diagnosis", "")),
                       "note": "raw judge transcript; evaluation only",
                       "runs": raw}, f, ensure_ascii=False, indent=2)
    n = len(per_case) or 1
    return {
        "match": round(sum(1 for r in per_case if r["match"]) / n, 4),
        "relevant": round(sum(1 for r in per_case if r["relevant"]) / n, 4),
        "unanimity_rate": round(sum(1 for r in per_case
                                    if r["unanimous"]) / n, 4),
        "disagreement_rate": round(sum(1 for r in per_case
                                       if len(set(r["scores"].split("|"))) > 1)
                                   / n, 4),
        "per_case": per_case,
    }


def _sha(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_probe"))
    ap.add_argument("--build", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_build"))
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_memory_gate"))
    ap.add_argument("--probe-cases", default="40-49")
    ap.add_argument("--judge", action="store_true")
    args = ap.parse_args()
    cases = parse_cases(args.probe_cases)

    from traceevo.s2_cases import load_env_file
    load_env_file()          # .env.local carries the gateway token
    results: Dict[str, Any] = {}
    for arm in ARMS:
        artifacts = os.path.join(args.probe, "probe", arm, "traceevo", "runs",
                                 arm)
        if not os.path.isdir(artifacts):
            print(f"  {arm}: no artifacts at {artifacts}")
            continue
        m = arm_metrics(artifacts, cases)
        results[arm] = m
        print(f"  {arm}: traces={m['n_traces']} "
              f"p3_used={m['p3_used_in_reasoning_rate']} "
              f"unsupported={m['unsupported_evidence_rate']} "
              f"critical={m['critical_anomaly_rate']}")

    if args.judge:
        gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
        judge_fn = make_judge_fn(temperature=0.0)
        for arm in ARMS:
            artifacts = os.path.join(args.probe, "probe", arm, "traceevo",
                                     "runs", arm)
            if not os.path.isdir(artifacts):
                continue
            print(f"  judging {arm}...", flush=True)
            results[arm]["judge"] = judge_arm(
                artifacts, cases, gold, judge_fn,
                os.path.join(args.out, "probe_judge_results_raw"))
            print(f"    match={results[arm]['judge']['match']} "
                  f"relevant={results[arm]['judge']['relevant']} "
                  f"unanimity={results[arm]['judge']['unanimity_rate']}")

    # ------------------------------------------------------------- §14.1 table
    rows = []
    for arm, m in results.items():
        row = {"arm": arm}
        for k in ("n_traces", "p3_used_in_reasoning_rate",
                  "unsupported_evidence_rate", "critical_anomaly_rate",
                  "invalid_expert_action_rate", "mean_retrieval_count",
                  "integrity_all_ok"):
            row[k] = m.get(k)
        j = m.get("judge") or {}
        row["match"] = j.get("match")
        row["relevant"] = j.get("relevant")
        row["judge_unanimity"] = j.get("unanimity_rate")
        row["judge_disagreement"] = j.get("disagreement_rate")
        rows.append(row)
    if rows:
        with open(os.path.join(args.out, "probe_results.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    with open(os.path.join(args.out, "probe_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    if args.judge:
        jr = []
        for arm, m in results.items():
            for r in (m.get("judge") or {}).get("per_case", []):
                jr.append({"arm": arm, **r})
        if jr:
            with open(os.path.join(args.out, "probe_judge_results.csv"), "w",
                      newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(jr[0].keys()))
                w.writeheader()
                w.writerows(jr)
    print(f"wrote {args.out}/probe_results.csv, probe_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
