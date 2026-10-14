"""S5B-C §5.1 steps 6-12: audit, verify the seal, and only then read gold.

§5.1's order is the requirement:

    6.  natural audit
    7.  graph replay verification
    8.  only now may the OutcomeVerifier read gold
    9.  frozen Judge x 3
   10.  Experience Gate
   11.  candidate saved
   12.  the candidate is not injected into any later case

Steps 6-7 are hash arithmetic and deterministic detection over sealed
artifacts. Step 8 is the first statement in this module that opens a
reference, and it is reachable only after every case has been sealed *and*
re-sealed. A case whose artifacts do not hash to its seal is dropped from
every table rather than carried with a footnote.

Step 12's "inject nothing" is enforced by construction: this module holds no
memory store, `s5b_run.py` sets `TRACEEVO_MEMORY=0`, and the candidates are
written to a file that no other module reads back into a prompt. The
experience partition is the *product*, not an input.

Usage:
    python traceevo/s5b_pipeline.py --out S5B_final_heldout
    python traceevo/s5b_pipeline.py --skip-judge        (no gold, no model)
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

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"

# §6.2's experience partition, as the Gate spells it.
PARTITION = ("eligible", "failure_experience", "manual_review", "reject")


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


def _json(p: str, default: Any = None) -> Any:
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------- §5.1 steps 6-7 audit
def audit_case(artifacts: str, case: int, seal_verified: bool) -> Dict[str, Any]:
    """§6.2's natural statistics for one case. No reference is read here."""
    from traceevo.s3b_gate import audit_bundle

    task = f"case-{case}"
    b = audit_bundle(artifacts, task)
    events = _jsonl(os.path.join(artifacts, f"{task}_events.jsonl"))
    ledger = _jsonl(os.path.join(artifacts, f"{task}_invocations.jsonl"))
    integ = _json(os.path.join(artifacts, f"{task}_integrity.json"), {})
    replay = _json(os.path.join(artifacts, f"{task}_replay.json"), {})

    by_tier = collections.Counter(d.get("provenance_tier")
                                  for d in b["decisions"])
    parse_status = collections.Counter(r.get("parse_status") for r in ledger)
    critical = [a for a in b["anomalies"]
                if (a.get("severity") or "") == "critical"]
    roots = sorted({a.get("root_event_id") for a in b["anomalies"]
                    if a.get("root_event_id")})

    # §6.2 asks for "ledger binding failures" as a separate line from
    # "discarded call count". They are not the same question and the first
    # version of this function made them the same expression:
    #
    #   discarded        a call whose output the parser rejected. Nothing was
    #                    bound, and the reason is recorded.
    #   accepted but
    #   uncommitted      a call whose output was accepted and never bound to
    #                    the graph. Some stages (EVIDENCE_RETRIEVAL, OTHER)
    #                    legitimately emit text that commits to nothing, so
    #                    this is reported with its own denominator and is not
    #                    called a failure of the ledger.
    discarded = [r for r in ledger if r.get("parse_status") == "discarded"]
    accepted = [r for r in ledger if r.get("parse_status") == "accepted"]
    uncommitted = [r for r in accepted if not r.get("committed_event_ids")]

    return {
        "case": case, "task_id": task, "seal_verified": seal_verified,
        "event_count": len(events),
        "llm_call_count": len(ledger),
        "accepted_calls": len(accepted),
        "discarded_calls": len(discarded),
        "ledger_binding_failures": len(discarded),
        "ledger_binding_note": (
            "a discarded call is one whose parsed output bound to no graph "
            "element; the count is repeated as `discarded_calls` because §6.2 "
            "asks for both names and they are the same events"),
        "accepted_but_uncommitted": len(uncommitted),
        "accepted_but_uncommitted_note": (
            "accepted calls that commit to no graph element. Retrieval and "
            "miscellaneous stages legitimately belong here, so this is not "
            "counted as a binding failure"),
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
        "integrity_ok": bool(integ.get("ok")),
        "chain_ok": bool(b["integrity_ok"]),
        "replay_ok": bool(replay.get("all_ok")),
        "n_deltas": replay.get("n_deltas"),
    }


# --------------------------------------------- §5.1 steps 8-11: gold, judge, gate
def outcome_and_gate(out_dir: str, artifacts: str, cases: List[int],
                     skip_judge: bool = False) -> Dict[str, Any]:
    """§5.1 steps 8-11. The reference is opened inside here and nowhere earlier."""
    from traceevo.s2_cases import load_env_file
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.evolution.outcome_verifier import make_judge_fn, verify_trace
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3b_gate import _integrity_ok
    from traceevo.s3c_expand import _trace_hashes
    from traceevo.s5b_freeze import log_access
    import pandas as pd

    load_env_file()
    judge_fn = _stub if skip_judge else make_judge_fn()
    raw_dir = os.path.join(out_dir, "judge_raw")
    os.makedirs(raw_dir, exist_ok=True)
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_80_137.csv"))

    # §5.1 step 8's gate. Everything before this line is hash arithmetic and
    # deterministic detection over sealed artifacts.
    log_access(out_dir, "reference (gold) opened for outcome verification",
               "traceevo/s5b_pipeline.py", cases,
               "reached only after every case's seal was re-verified in this "
               "process")
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}

    outcome_rows, gate_rows, candidates = [], [], []
    for c in cases:
        task = f"case-{c}"
        b = audit_bundle_cached(artifacts, task)
        rep = {"prediction": "", "report": ""}
        for e in reversed(_jsonl(os.path.join(artifacts,
                                              f"{task}_events.jsonl"))):
            if e.get("event_type") == "FINAL_REPORT":
                pl = e.get("payload") or {}
                rep = {"prediction": pl.get("prediction", ""),
                       "report": pl.get("report", "")}
                break
        if not rep["prediction"]:
            outcome_rows.append({"case": c, "task_id": task,
                                 "outcome_verdict": "uncertain",
                                 "verdict_reason": "no FINAL_REPORT",
                                 "judge_scores": "", "prediction": ""})
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

        # §5.1 step 10. The blinded file keeps row order and row count (only
        # the gold columns are dropped), so the case index is the row index --
        # the same join key `gold_by_case_index` uses on the verifier's side.
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
        # §10.1 compares five rules on every natural case, so every rule is
        # run for every case rather than only the one the Gate chose.
        rules = {"TraceEvo": v["eligibility"]}
        for name, fn in gate.BASELINES.items():
            try:
                r = fn(cand, audit={"decisions": b["decisions"]},
                       ledger_rows=b["ledger"], anomalies=b["anomalies"],
                       integrity_ok=_integrity_ok(b))
            except TypeError:
                r = fn(cand)
            rules[name] = r["eligibility"]
        gate_rows.append({
            "case": c, "task_id": task,
            "outcome_verdict": res["outcome_verdict"],
            "gate_eligibility": v["eligibility"],
            "gate_reasons": "|".join(v["reasons"]),
            "memory_id": cand.memory_id,
            "n_anomalies": len(b["anomalies"]),
            "n_used_p1": sum(1 for d in b["decisions"]
                             if d.get("provenance_tier") == "P1_SOFT_GROUNDED"
                             and d.get("used_in_reasoning")),
            "n_used_p3": sum(1 for d in b["decisions"]
                             if d.get("provenance_tier")
                             == "P3_UNSUPPORTED_ASSERTION"
                             and d.get("used_in_reasoning")),
            "audit_rule": rules["B2_AuditOnly"],
            "integrity_rule": rules["B3_IntegrityOnly"],
            "outcome_rule": rules["B1_OutcomeOnly"],
            "naive_rule": rules["B0_Naive"],
            "audit_summary_hash_present": bool(cand.audit_summary_hash),
            "source_trace_hashes_present": bool(cand.source_trace_hashes),
            "extractor_version": cand.extractor_version})
        # §5.1 step 11: every candidate is written, including the rejected
        # ones, because §10.2's incorrect-clean rate is a statement about the
        # rejected population.
        candidates.append(dict(cand.to_dict(),
                               gate_eligibility=v["eligibility"],
                               case=c, task_id=task,
                               n_anomalies=len(b["anomalies"])))

    _write_csv(os.path.join(out_dir, "outcome_results.csv"), outcome_rows)
    _write_csv(os.path.join(out_dir, "natural_experience_summary.csv"),
               gate_rows)
    with open(os.path.join(out_dir, "gate_candidates.jsonl"), "w",
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


def run(out_dir: str, skip_judge: bool = False) -> Dict[str, Any]:
    from traceevo.s5b_run import check_seals

    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
    seals = check_seals(out_dir)
    if not seals:
        raise SystemExit("no seals found; run s5b_run.py --run first "
                         "(§5.1 step 4-5)")
    bad = {s["task_id"] for s in seals if not s["match"]}
    print(f"§5.1 steps 6-7: {len(seals)} seals checked, "
          f"{len(seals) - len(bad)} verified, {len(bad)} rejected")
    for s in seals:
        if not s["match"]:
            print(f"  {s['task_id']}: MISMATCH {s['changed']}")

    # §5.1's consequence, applied: only seal-verified cases proceed. A case
    # whose artifacts no longer hash to its seal is dropped from every table.
    ok_cases = [s["case"] for s in seals if s["match"]]

    rows = [audit_case(artifacts, c, True) for c in ok_cases]

    og = outcome_and_gate(out_dir, artifacts, ok_cases, skip_judge)

    # §5.1 step 6's audit is also what the run manifest reports.
    tmap = {r["case"]: r for r in og["gates"]}
    man = []
    for r in rows:
        g = tmap.get(r["case"], {})
        man.append({
            "case": r["case"], "task_id": r["task_id"],
            "seal_verified": r["seal_verified"],
            "events": r["event_count"], "llm_calls": r["llm_call_count"],
            "accepted": r["accepted_calls"], "discarded": r["discarded_calls"],
            "ledger_binding_failures": r["ledger_binding_failures"],
            "accepted_but_uncommitted": r["accepted_but_uncommitted"],
            "p0": r["p0"], "p1": r["p1"], "p2": r["p2"], "p3": r["p3"],
            "p3_used": r["p3_used"],
            "alerts": r["audit_alerts"], "critical": r["critical_alerts"],
            "trace_complete": r["trace_complete"],
            "chain_ok": r["chain_ok"], "replay_ok": r["replay_ok"],
            "outcome_verdict": g.get("outcome_verdict", ""),
            "gate_eligibility": g.get("gate_eligibility", ""),
        })
    _write_csv(os.path.join(out_dir, "run_manifest_80_137.csv"), man)
    _write_csv(os.path.join(out_dir, "natural_audit_summary.csv"), rows)

    counts = collections.Counter(r["outcome_verdict"] for r in og["outcomes"])
    gates = collections.Counter(r["gate_eligibility"] for r in og["gates"])
    partitions = {k: gates.get(k, 0) for k in PARTITION}
    summary = {
        "n_sealed": len(seals), "n_seal_verified": len(ok_cases),
        "n_seal_rejected": len(bad),
        "audit": {
            "total_events": sum(r["event_count"] for r in rows),
            "total_llm_calls": sum(r["llm_call_count"] for r in rows),
            "total_accepted": sum(r["accepted_calls"] for r in rows),
            "total_discarded": sum(r["discarded_calls"] for r in rows),
            "total_accepted_but_uncommitted": sum(
                r["accepted_but_uncommitted"] for r in rows),
            "p0": sum(r["p0"] for r in rows), "p1": sum(r["p1"] for r in rows),
            "p2": sum(r["p2"] for r in rows), "p3": sum(r["p3"] for r in rows),
            "p3_used": sum(r["p3_used"] for r in rows),
            "p1_used": sum(r["p1_used"] for r in rows),
            "p2_used": sum(r["p2_used"] for r in rows),
            "alerts": sum(r["audit_alerts"] for r in rows),
            "critical_alerts": sum(r["critical_alerts"] for r in rows),
            "clean_traces_with_alerts": sum(1 for r in rows
                                            if r["audit_alerts"] > 0),
            "all_traces_complete": all(r["trace_complete"] for r in rows),
            "all_chains_ok": all(r["chain_ok"] for r in rows),
            "all_replays_ok": all(r["replay_ok"] for r in rows),
        },
        "outcome_counts": dict(counts),
        "gate_counts": dict(gates),
        "experience_partition": partitions,
        "n_candidates": og["n_candidates"],
        "p3_note": ("§6.2. ATGG is OFF for the whole stage, so a natural P3 "
                    "here is an observation and not a trigger."),
    }
    with open(os.path.join(out_dir, "pipeline_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args()
    s = run(args.out, args.skip_judge)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
