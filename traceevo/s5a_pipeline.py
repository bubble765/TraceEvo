"""S5A-B/C §9-§10 steps 6-10: audit, verify the seal, and only then read gold.

§9's order is the requirement:

    6. audit
    7. seal verification
    8. OutcomeVerifier may read gold
    9. Experience Gate
   10. produce a candidate; inject nothing into later cases

Steps 6-7 are hash arithmetic over sealed artifacts. Step 8 is the first
statement in this module that opens a reference, and it is reachable only after
`check_seals` has verified every case. A case whose artifacts no longer hash to
its seal is dropped from every table rather than carried with a footnote — the
same rule as S3D §3.3 and for the same reason.

Step 10's "inject nothing" is enforced by construction rather than by
discipline: this module holds no store, and `s5a_run.py` sets
`TRACEEVO_MEMORY=0`. The candidates are written to a file and read by nothing.

Usage:
    python traceevo/s5a_pipeline.py --out S5A_protocol_validation
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5A_protocol_validation")
CASES = list(range(70, 80))
TAG = "core_v1"
TIERS = ("P0_DIRECT_GROUNDED", "P1_SOFT_GROUNDED", "P2_DERIVED",
         "P3_UNSUPPORTED_ASSERTION")


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


def audit_case(artifacts: str, case: int) -> Dict[str, Any]:
    """§10's natural statistics for one case. No reference is read here."""
    from traceevo.s3b_gate import audit_bundle

    task = f"case-{case}"
    b = audit_bundle(artifacts, task)
    events = _jsonl(os.path.join(artifacts, f"{task}_events.jsonl"))
    ledger = _jsonl(os.path.join(artifacts, f"{task}_invocations.jsonl"))
    with open(os.path.join(artifacts, f"{task}_integrity.json"),
              encoding="utf-8") as f:
        integ = json.load(f)
    with open(os.path.join(artifacts, f"{task}_replay.json"),
              encoding="utf-8") as f:
        replay = json.load(f)

    by_tier = collections.Counter(d.get("provenance_tier")
                                  for d in b["decisions"])
    used_tier = collections.Counter(
        d.get("provenance_tier") for d in b["decisions"]
        if d.get("used_in_reasoning"))
    parse_status = collections.Counter(r.get("parse_status") for r in ledger)
    critical = [a for a in b["anomalies"]
                if (a.get("severity") or "") == "critical"]
    # §10's "observable root": the anomaly detector's own localization
    roots = sorted({a.get("root_event_id") for a in b["anomalies"]
                    if a.get("root_event_id")})
    return {
        "case": case, "task_id": task,
        "event_count": len(events),
        "llm_call_count": len(ledger),
        "accepted_calls": parse_status.get("accepted", 0),
        "discarded_calls": parse_status.get("discarded", 0),
        "p0": by_tier.get("P0_DIRECT_GROUNDED", 0),
        "p1": by_tier.get("P1_SOFT_GROUNDED", 0),
        "p2": by_tier.get("P2_DERIVED", 0),
        "p3": by_tier.get("P3_UNSUPPORTED_ASSERTION", 0),
        "p3_used": sum(1 for d in b["decisions"]
                       if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
                       and d.get("used_in_reasoning")),
        "p1_used": sum(1 for d in b["decisions"]
                       if d.get("provenance_tier") == "P1_SOFT_GROUNDED"
                       and d.get("used_in_reasoning")),
        "p2_used": sum(1 for d in b["decisions"]
                       if d.get("provenance_tier") == "P2_DERIVED"
                       and d.get("used_in_reasoning")),
        "n_decisions": len(b["decisions"]),
        "audit_alerts": len(b["anomalies"]),
        "critical_alerts": len(critical),
        "anomaly_types": "|".join(sorted({a.get("type")
                                          for a in b["anomalies"]})),
        "observable_roots": "|".join(roots),
        "n_observable_roots": len(roots),
        "trace_complete": bool(b["semantic_complete"]),
        "integrity_ok": bool(integ.get("integrity_ok")),
        "chain_ok": bool(b["integrity_ok"]),
        "replay_ok": bool(replay.get("all_ok")),
        "n_deltas": replay.get("n_deltas"),
    }


def outcome_and_gate(out_dir: str, artifacts: str,
                     cases: List[int], dry_run: bool = False
                     ) -> Dict[str, Any]:
    """§9 steps 8-10. The reference is opened inside here and nowhere earlier."""
    from traceevo.s2_cases import load_env_file
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.evolution.outcome_verifier import make_judge_fn, verify_trace
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3b_gate import _integrity_ok
    from traceevo.s3c_expand import _trace_hashes
    import pandas as pd

    load_env_file()
    judge_fn = _stub if dry_run else make_judge_fn()
    raw_dir = os.path.join(out_dir, "judge_raw")
    os.makedirs(raw_dir, exist_ok=True)
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_70_79.csv"))

    # §9 step 8's gate. Everything before this line is hash arithmetic.
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}

    outcome_rows, gate_rows, candidates = [], [], []
    for c in cases:
        task = f"case-{c}"
        b = audit_bundle_cached(artifacts, task)
        events = _jsonl(os.path.join(artifacts, f"{task}_events.jsonl"))
        rep = {"prediction": "", "report": ""}
        for e in reversed(events):
            if e.get("event_type") == "FINAL_REPORT":
                pl = e.get("payload") or {}
                rep = {"prediction": pl.get("prediction", ""),
                       "report": pl.get("report", "")}
                break
        if not rep["prediction"]:
            outcome_rows.append({"case": c, "task_id": task,
                                 "outcome_verdict": "uncertain",
                                 "verdict_reason": "no FINAL_REPORT",
                                 "judge_scores": ""})
            continue
        g = gold.get(c) or {}
        res = verify_trace(rep, g.get("final_diagnosis", ""), judge_fn, c,
                           raw_dir=raw_dir)
        outcome_rows.append({
            "case": c, "task_id": task,
            "outcome_verdict": res["outcome_verdict"],
            "verdict_reason": res["verdict_reason"],
            "judge_scores": "|".join(str(s) for s in res["judge_scores"]),
            "judge_unanimous": res["judge_unanimous"],
            "prediction": rep["prediction"],
            "reference_sha256": res["reference_sha256"]})

        # §9 step 9
        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        cand = build_candidate(task, _trace_hashes(artifacts, task), key,
                               res["outcome_verdict"], b["events"], b["ledger"],
                               b["summary"],
                               [a["type"] for a in b["anomalies"]])
        v = gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                          b["anomalies"], _integrity_ok(b),
                          b["semantic_complete"],
                          forbidden_hits=forbidden_content(cand, {}))
        gate_rows.append({
            "case": c, "task_id": task, "outcome_verdict": res["outcome_verdict"],
            "gate_eligibility": v["eligibility"],
            "gate_reasons": "|".join(v["reasons"]),
            "memory_id": cand.memory_id,
            "audit_summary_hash_present": bool(cand.audit_summary_hash),
            "source_trace_hashes_present": bool(cand.source_trace_hashes),
            "extractor_version": cand.extractor_version})
        # §9 step 10: the candidate is written, and nothing reads it back.
        if v["eligibility"] in (gate.ELIGIBLE, gate.FAILURE_EXPERIENCE):
            candidates.append(dict(cand.to_dict(),
                                   gate_eligibility=v["eligibility"]))

    _write_csv(os.path.join(out_dir, "outcome_results_70_79.csv"), outcome_rows)
    _write_csv(os.path.join(out_dir, "gate_validation_results.csv"), gate_rows)
    with open(os.path.join(out_dir, "experience_candidates_70_79.jsonl"), "w",
              encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    return {"outcomes": outcome_rows, "gates": gate_rows,
            "n_candidates": len(candidates)}


_BUNDLE_CACHE: Dict[str, Any] = {}


def audit_bundle_cached(artifacts: str, task: str):
    key = f"{artifacts}|{task}"
    if key not in _BUNDLE_CACHE:
        from traceevo.s3b_gate import audit_bundle
        _BUNDLE_CACHE[key] = audit_bundle(artifacts, task)
    return _BUNDLE_CACHE[key]


def _stub(system_prompt: str, user_prompt: str) -> str:
    return "SCORE: 1"


def run(out_dir: str, dry_run: bool = False) -> Dict[str, Any]:
    from traceevo.s5a_run import check_seals
    from traceevo.s3c_expand import gold_safe_conditions

    artifacts = os.path.join(out_dir, "runs_70_79", "traceevo", "runs", TAG)
    seals = check_seals(out_dir)
    if not seals:
        raise SystemExit("no seals found; run s5a_run.py --run first (§9 step 5)")
    bad = [s["task_id"] for s in seals if not s["match"]]
    print(f"§9 step 7: {len(seals)} seals checked, "
          f"{len(seals) - len(bad)} verified, {len(bad)} rejected")
    for s in seals:
        if not s["match"]:
            print(f"  {s['task_id']}: MISMATCH {s['changed']}")

    # only seal-verified cases proceed; §9's consequence, applied
    ok_cases = [s["case"] for s in seals if s["match"]]

    rows = [audit_case(artifacts, c) for c in ok_cases]
    _write_csv(os.path.join(out_dir, "natural_audit_summary.csv"), rows)

    og = outcome_and_gate(out_dir, artifacts, ok_cases, dry_run)
    _write_csv(os.path.join(out_dir, "natural_experience_summary.csv"),
               og["gates"])

    # §9 step 6's audit is also what the run manifest reports
    man = []
    for r in rows:
        man.append({
            "case": r["case"], "task_id": r["task_id"],
            "seal_verified": True,
            "events": r["event_count"], "llm_calls": r["llm_call_count"],
            "accepted": r["accepted_calls"], "discarded": r["discarded_calls"],
            "p3": r["p3"], "p3_used": r["p3_used"],
            "alerts": r["audit_alerts"], "critical": r["critical_alerts"],
            "trace_complete": r["trace_complete"],
            "chain_ok": r["chain_ok"], "replay_ok": r["replay_ok"],
        })
    _write_csv(os.path.join(out_dir, "run_manifest_70_79.csv"), man)

    counts = collections.Counter(r["outcome_verdict"] for r in og["outcomes"])
    gates = collections.Counter(r["gate_eligibility"] for r in og["gates"])
    trap = sum(r["p3"] for r in rows)
    summary = {
        "n_sealed": len(seals), "n_seal_verified": len(ok_cases),
        "n_seal_rejected": len(bad),
        "audit": {
            "total_events": sum(r["event_count"] for r in rows),
            "total_llm_calls": sum(r["llm_call_count"] for r in rows),
            "total_discarded": sum(r["discarded_calls"] for r in rows),
            "p0": sum(r["p0"] for r in rows), "p1": sum(r["p1"] for r in rows),
            "p2": sum(r["p2"] for r in rows), "p3": trap,
            "p3_used": sum(r["p3_used"] for r in rows),
            "p1_used": sum(r["p1_used"] for r in rows),
            "p2_used": sum(r["p2_used"] for r in rows),
            "alerts": sum(r["audit_alerts"] for r in rows),
            "critical_alerts": sum(r["critical_alerts"] for r in rows),
            "all_traces_complete": all(r["trace_complete"] for r in rows),
            "all_chains_ok": all(r["chain_ok"] for r in rows),
            "all_replays_ok": all(r["replay_ok"] for r in rows),
        },
        "outcome_counts": dict(counts),
        "gate_counts": dict(gates),
        "n_candidates": og["n_candidates"],
        "p3_note": ("§18 Q7: reported only. ATGG is not enabled in S5A, so a "
                    "P3 appearing here is an observation and not a trigger."),
    }
    with open(os.path.join(out_dir, "pipeline_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    s = run(args.out, args.dry_run)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
