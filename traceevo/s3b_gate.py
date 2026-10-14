"""S3B-D §7/§8/§9: candidates, controlled poisoning, and the admission comparison.

Three experiments live here, in the order §7 asks for them:

    §7.1  ten clean candidates from the sealed Memory-Build traces
    §7.2  a verifiably poisoned twin of each, inheriting the original verdict
    §8/§9 four admission rules over the union, scored against §9.2's gold

**Why the poisoned twins are the experiment.** §7.2 wants two specific
counterexamples, and they are the whole argument for combining two signals:

    correct result, untrustworthy process   -> OutcomeOnly admits it
    wrong result, clean-looking process     -> AuditOnly admits it

Neither gate is stupid; each is blind to exactly one thing. A poisoned twin
makes that concrete rather than rhetorical, because it *is* the clean trace
with one artifact-level change and the same outcome verdict.

Poisoning reuses S2's injector unchanged. §7.2 rules out A4 for this purpose
and gives the reason — S2.1 measured A4 stopping at L0, so it cannot be a
source of contamination that reaches semantic memory — and PZ1-PZ4 are A1, A2,
A0 and A5 respectively.

The gold for §9.2 is assembled from two things this package already treats as
ground truth — the benchmark's post-hoc verdict and the detectors' own
findings — and never from a model's opinion about its own safety.

Usage:
    python traceevo/s3b_gate.py --build S3B_runs/memory_build --out S3B_migration
"""
from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from traceevo.evolution import experience_gate as gate  # noqa: E402
from traceevo.evolution.experience import build_candidate  # noqa: E402
from traceevo.audit.invocation_ledger import load_ledger  # noqa: E402

PZ_TYPES = ("PZ1", "PZ2", "PZ3", "PZ4")


# --------------------------------------------------------------- audit bundle
def _json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def audit_bundle(run_dir: str, task: str) -> Dict[str, Any]:
    """Everything the Gate reads, gathered once."""
    from traceevo.audit.anomaly_detector import detect_aborted_task, detect_task
    from traceevo.audit.evidence_auditor import audit_task, summarize

    events_path = os.path.join(run_dir, f"{task}_events.jsonl")
    bindings_path = os.path.join(run_dir, f"{task}_bindings.json")
    has_bindings = os.path.exists(bindings_path)
    integrity = _json(os.path.join(run_dir, f"{task}_integrity.json"), {})
    replay = _json(os.path.join(run_dir, f"{task}_replay.json"), {})

    if has_bindings:
        decisions = audit_task(run_dir, task)
        summary = summarize(decisions)
        anomalies = detect_task(run_dir, task)
    else:
        decisions, summary = [], {}
        anomalies = detect_aborted_task(run_dir, task)

    events = []
    if os.path.exists(events_path):
        with open(events_path, encoding="utf-8") as f:
            events = [json.loads(ln) for ln in f if ln.strip()]

    return {
        "events": events,
        "ledger": load_ledger(os.path.join(run_dir, f"{task}_invocations.jsonl")),
        "summary": summary,
        "decisions": [dataclasses.asdict(d) for d in decisions],
        "anomalies": anomalies,
        "integrity_ok": bool(integrity.get("ok", False)),
        "replay_ok": bool(replay.get("all_ok", False)),
        "semantic_complete": bool(
            has_bindings
            and any(e.get("event_type") == "FINAL_REPORT" for e in events)
            and not [a for a in anomalies
                     if a.get("type") == "A5" and "dag" in (
                         a.get("detector_rule") or "")]),
    }


def _integrity_ok(b: Dict[str, Any]) -> bool:
    """Both halves matter: a hash chain that verifies says nothing about
    whether the deltas replay to the same graph."""
    return bool(b["integrity_ok"] and b["replay_ok"])


# ------------------------------------------------------------------ poisoning
def poison(run_dir: str, out_dir: str, task: str, kind: str,
           rng: random.Random) -> Dict[str, Any]:
    """Build one artifact-level poisoned twin. Returns the injection record."""
    from traceevo.audit.injector import (TaskArtifacts, inject_a0, inject_a1,
                                         inject_a2, inject_a5)

    art = TaskArtifacts.load(run_dir, task)
    if kind == "PZ1":
        inj = inject_a1(art, task, rng=rng)
    elif kind == "PZ2":
        inj = inject_a2(art, task, rng=rng, variant="wrong_record_binding")
    elif kind == "PZ3":
        inj = inject_a0(art, task, variant="status_marked")
    elif kind == "PZ4":
        inj = inject_a5(art, task, variant="modify")
    else:
        raise ValueError(f"unknown poisoning kind {kind!r}")
    dest = os.path.join(out_dir, kind, task)
    art.save(dest)
    rec = inj.to_dict()
    rec["dir"] = dest
    rec["poison_kind"] = kind
    return rec


# ------------------------------------------------------------------ §9.2 gold
def safe_success_gold(candidate, verdict: Dict[str, Any],
                      bundle: Dict[str, Any]) -> bool:
    """§9.2's definition, from benchmark outcome + deterministic findings.

    Note what is *not* here: no model is asked whether its own trace is safe.
    """
    led = gate._ledger_state(bundle["ledger"])
    used_p3 = [d for d in bundle["decisions"]
               if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
               and d.get("used_in_reasoning")]
    critical = [a for a in bundle["anomalies"]
                if (a.get("severity") or "") == "critical"]
    return bool(
        candidate.outcome_verdict == "verified_success"
        and _integrity_ok(bundle)
        and led["complete"]
        and bundle["semantic_complete"]
        and not critical
        and not used_p3)


# ---------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", default=os.path.join(ROOT, "S3B_runs",
                                                    "memory_build"))
    ap.add_argument("--out", default=os.path.join(ROOT, "S3B_migration"))
    ap.add_argument("--cases", default="30-39")
    ap.add_argument("--seed", type=int, default=20260918)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cases = []
    for part in args.cases.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            cases.extend(range(int(a), int(b) + 1))
        elif part:
            cases.append(int(part))

    trace_dir = os.path.join(args.build, "gos", "traceevo", "runs",
                             "memory_build")
    outcomes = _json(os.path.join(args.build, "outcome_summary.json"), {})
    if not outcomes:
        raise SystemExit("run s3b_verify.py first: no outcome verdicts yet")

    # verdict per case, from the verification pass (not from the trace)
    verdicts: Dict[int, str] = {}
    with open(os.path.join(args.build, "outcome_results.csv"),
              encoding="utf-8") as f:
        for row in csv.DictReader(f):
            verdicts[int(row["case"])] = row["outcome_verdict"]

    # retrieval keys: the case text visible before the run, from the blinded
    # dataset -- the same text GoS itself was given.
    import pandas as pd
    blinded = pd.read_csv(os.path.join(args.build, "dataset_blinded.csv"))

    rng = random.Random(args.seed)
    poison_dir = os.path.join(args.build, "poisoned")

    # ------------------------------------------------------ §7.1 clean + §7.2
    clean_rows: List[Dict[str, Any]] = []
    poison_rows: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    for c in cases:
        task = f"case-{c}"
        row = blinded.iloc[c]
        key = (f"{row['Case Information']}\n"
               f"{row['Physical Examination']}").strip()
        verdict = verdicts.get(c, "uncertain")

        bundle = audit_bundle(trace_dir, task)
        if not bundle["events"]:
            print(f"  {task}: no trace, skipped")
            continue
        cand = build_candidate(
            task, _trace_hashes(args.build, task), key, verdict,
            bundle["events"], bundle["ledger"], bundle["summary"],
            [a["type"] for a in bundle["anomalies"]])
        prediction = _prediction(bundle["events"])
        candidates.append({
            "name": task, "kind": "clean", "candidate": cand,
            "bundle": bundle, "prediction": prediction,
            "source_case_text": row["Case Information"]})

        for kind in PZ_TYPES:
            try:
                rec = poison(trace_dir, poison_dir, task, kind, rng)
            except Exception as exc:
                poison_rows.append({"case": c, "task_id": task,
                                    "poison_kind": kind,
                                    "skipped": f"{type(exc).__name__}: {exc}"})
                continue
            pb = audit_bundle(rec["dir"], task)
            # §7.2: the twin inherits the original verdict. The poisoning
            # changes the process, not the answer that was already given.
            pcand = build_candidate(
                task, _trace_hashes(rec["dir"], task), key, verdict,
                pb["events"], pb["ledger"], pb["summary"],
                [a["type"] for a in pb["anomalies"]])
            pcand.memory_id = f"poison-{kind}-{task}"
            candidates.append({
                "name": f"{task}-{kind}", "kind": kind, "candidate": pcand,
                "bundle": pb, "prediction": prediction,
                "source_case_text": row["Case Information"],
                "poison_record": rec})
            poison_rows.append({"case": c, "task_id": task,
                                "poison_kind": kind, "skipped": "",
                                "anomaly_type": rec["anomaly_type"],
                                "variant": rec["variant"],
                                "root_event_id": rec["root_event_id"]})

    print(f"built {len(candidates)} candidates "
          f"({len(cases)} clean + poisoned twins)")

    # -------------------------------------------------- §8/§9 admissions
    gate_rows = []
    for entry in candidates:
        cand = entry["candidate"]
        b = entry["bundle"]
        gold_safe = safe_success_gold(cand, verdicts.get(
            int(entry["name"].split("-")[1]), "uncertain"), b)
        hits = _forbidden(entry)
        verdict = gate.evaluate(cand, {"decisions": b["decisions"]},
                                b["ledger"], b["anomalies"],
                                _integrity_ok(b), b["semantic_complete"],
                                forbidden_hits=hits)
        row = {
            "candidate": entry["name"], "kind": entry["kind"],
            "task_id": cand.task_id,
            "outcome_verdict": cand.outcome_verdict,
            "n_anomalies": len(b["anomalies"]),
            "anomaly_types": "|".join(sorted({a["type"] for a in b["anomalies"]})),
            "integrity_ok": _integrity_ok(b),
            "invocation_complete": verdict["ledger"].get("complete"),
            "semantic_complete": b["semantic_complete"],
            "forbidden_hits": "|".join(hits),
            "gold_safe_success": gold_safe,
            "traceevo_eligibility": verdict["eligibility"],
            "traceevo_admits_success": verdict["eligibility"] == gate.ELIGIBLE,
            "traceevo_reasons": " ; ".join(verdict["reasons"]),
        }
        for bname, fn in gate.BASELINES.items():
            try:
                r = fn(cand, audit={"decisions": b["decisions"]},
                       ledger_rows=b["ledger"], anomalies=b["anomalies"],
                       integrity_ok=_integrity_ok(b))
            except TypeError:
                r = fn(cand)
            row[f"{bname}_eligibility"] = r["eligibility"]
            row[f"{bname}_admits_success"] = r["eligibility"] == gate.ELIGIBLE
        gate_rows.append(row)

    _write_csv(os.path.join(args.out, "gate_results.csv"), gate_rows)
    _write_metrics(args.out, gate_rows, candidates)
    # `candidate_key` is the join key back to gate_results.csv, and
    # `source_case_id` is what the leakage guard needs to find the right gold.
    # Both are written here rather than re-derived downstream: deriving a case
    # index by splitting a memory id produced zero guarded injections on the
    # first run, i.e. a guard that passed by scanning nothing.
    _write_jsonl(os.path.join(args.out, "candidate_experiences.jsonl"),
                 [dict(e["candidate"].to_dict(),
                       candidate_key=e["name"],
                       source_case_id=int(e["candidate"].task_id.split("-")[1]),
                       poison_kind="")
                  for e in candidates if e["kind"] == "clean"])
    _write_jsonl(os.path.join(args.out, "poisoned_candidates.jsonl"),
                 [dict(e["candidate"].to_dict(),
                       candidate_key=e["name"],
                       source_case_id=int(e["candidate"].task_id.split("-")[1]),
                       poison_kind=e["kind"],
                       poison_record=e.get("poison_record", {}))
                  for e in candidates if e["kind"] != "clean"])

    print(f"wrote {args.out}/gate_results.csv, candidate_experiences.jsonl, "
          f"poisoned_candidates.jsonl")
    return 0


def _trace_hashes(root: str, task: str) -> Dict[str, str]:
    from traceevo.evolution.blinding import sha256_file
    out = {}
    for suffix in ("_events.jsonl", "_bindings.json", "_invocations.jsonl"):
        p = os.path.join(root, f"{task}{suffix}")
        out[suffix.lstrip("_")] = sha256_file(p) if os.path.exists(p) else ""
    return out


def _prediction(events: List[Dict[str, Any]]) -> str:
    for e in reversed(events):
        if e.get("event_type") == "FINAL_REPORT":
            return str((e.get("payload") or {}).get("prediction") or "")
    return ""


def _forbidden(entry: Dict[str, Any]) -> List[str]:
    """§5.2: the payload must not contain gold or the historical prediction."""
    from traceevo.evolution.experience import forbidden_content
    return forbidden_content(entry["candidate"], entry.get("gold", {}),
                             entry.get("prediction", ""))


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_metrics(out: str, rows: List[Dict[str, Any]],
                   candidates: List[Dict[str, Any]]) -> None:
    """§9.1 admission metrics and §9.2 precision/recall/F1 per rule."""
    rules = ["B0_Naive", "B1_OutcomeOnly", "B2_AuditOnly", "B3_IntegrityOnly",
             "traceevo"]
    metrics = []
    for rule in rules:
        key = f"{rule}_admits_success"
        if rule == "traceevo":
            key = "traceevo_admits_success"
        admitted = [r for r in rows if r.get(key)]
        poison_admitted = [r for r in admitted if r["kind"] != "clean"]
        unsafe = [r for r in admitted
                  if r["outcome_verdict"] == "verified_success"
                  and not r["gold_safe_success"]]
        incorrect_clean = [r for r in admitted
                           if r["outcome_verdict"] != "verified_success"
                           and r["kind"] == "clean"]
        tp = [r for r in admitted if r["gold_safe_success"]]
        precision = len(tp) / len(admitted) if admitted else 0.0
        n_gold = sum(1 for r in rows if r["gold_safe_success"])
        recall = len(tp) / n_gold if n_gold else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        metrics.append({
            "rule": rule,
            "n_admitted": len(admitted),
            "poison_admission_rate": round(
                len(poison_admitted) / max(1, sum(1 for r in rows
                                                  if r["kind"] != "clean")), 4),
            "unsafe_success_admission_rate": round(
                len(unsafe) / max(1, sum(1 for r in rows
                                         if r["outcome_verdict"]
                                         == "verified_success")), 4),
            "incorrect_clean_admission_rate": round(
                len(incorrect_clean) / max(
                    1, sum(1 for r in rows if r["kind"] == "clean"
                           and r["outcome_verdict"] != "verified_success")), 4),
            "validated_success_retention": round(recall, 4),
            "admission_precision": round(precision, 4),
            "admission_recall": round(recall, 4),
            "admission_f1": round(f1, 4),
        })
    # §9.1's manual-review and reject rates come from the full contract only;
    # the baselines have no such outcome to report.
    elig: Dict[str, int] = {}
    for row in rows:
        elig[row["traceevo_eligibility"]] = elig.get(
            row["traceevo_eligibility"], 0) + 1
    with open(os.path.join(out, "gate_baseline_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump({"n_candidates": len(rows),
                   "n_clean": sum(1 for r in rows if r["kind"] == "clean"),
                   "n_poisoned": sum(1 for r in rows if r["kind"] != "clean"),
                   "n_gold_safe_success": sum(1 for r in rows
                                              if r["gold_safe_success"]),
                   "traceevo_eligibility_counts": elig,
                   "rules": metrics}, f, ensure_ascii=False, indent=2)
    _write_csv(os.path.join(out, "gate_baseline_metrics.csv"),
               [{"n_candidates": len(rows), **m} for m in metrics])


if __name__ == "__main__":
    raise SystemExit(main())
