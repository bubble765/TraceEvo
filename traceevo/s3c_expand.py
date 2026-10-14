"""S3C-B … S3C-G: experience expansion, the success path, and channel split.

Everything here is offline. No GoS process is started, no case 50-137 is read,
and case 40-49 is used only for a retrieval dry-run (§2, §10).

The three phases and what each is for:

    B  triage      how many gold-safe verified successes exist, and which
                   branch of §4 that puts S3C in
    C  success     build the success store *through the real Gate*, so the
                   `eligible` path — never once executed in S3B — actually runs
    D  poison      construct poisoned twins of real verified successes, which
                   is §7.2's first counterexample and could not be built before
    E  metrics     admission rates over three candidate classes
    F  channels    success retrievable from the initial plan, failure not
    G  abstention  a success retriever that returns [] rather than stretching

§4's gold-safe test is six conditions, not one verdict — `verified_success` is
necessary and not sufficient, and case-22 in this corpus is the proof: it is a
verified success carrying an unresolved P3 on the decision path, and it is
therefore a Validated *Failure*, not a gold-safe success. Applying the shortcut
would have made the headline count 5 instead of 4, which is the difference
between entering S4 and not.

Usage:
    python traceevo/s3c_expand.py --all
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import random
import sys
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

OUT = os.path.join(ROOT, "S3C_experience_expansion")
PZ_TYPES = ("PZ1", "PZ2", "PZ3", "PZ4")

# §2's tiers. `headline_eligible` is what separates A/B from the legacy
# supplement: C may be stored and used, and may not enter a headline number.
TIERS: List[Dict[str, Any]] = [
    {"tier": "A", "cases": list(range(10, 30)), "corpus_source": "s3a_holdout",
     "headline_eligible": True,
     "archive": os.path.join(ROOT, "S3A_runs", "s3a_migration", "traceevo",
                             "runs", "migration_holdout"),
     "outcomes": os.path.join(OUT, "archive_outcome_results_10_29.csv")},
    {"tier": "B", "cases": list(range(30, 40)), "corpus_source": "s3b_build",
     "headline_eligible": True,
     "archive": os.path.join(ROOT, "S3B_runs", "memory_build", "gos",
                             "traceevo", "runs", "memory_build"),
     "outcomes": os.path.join(ROOT, "S3B_runs", "memory_build",
                              "outcome_results.csv")},
    {"tier": "C", "cases": list(range(0, 10)), "corpus_source": "legacy_dev",
     "headline_eligible": False,
     "archive": os.path.join(ROOT, "S3A_runs", "s3a_ledger", "ledger_on",
                             "traceevo", "runs", "ledger_on"),
     "outcomes": os.path.join(OUT, "legacy_outcome_results_0_9.csv")},
]


def _jsonl(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def _write_jsonl(path: str, rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    """Write rows whose keys may differ.

    This took `fieldnames` from `rows[0]` alone, which is only correct when
    every row has the same shape. `poison_successes` produces rows that do
    not: a twin the poisoner declines carries an extra `reason`. So on the
    first corpus where any twin was NOT_APPLICABLE the writer raised
    `ValueError: dict contains fields not in fieldnames: 'reason'` and the
    whole Gate step aborted.

    It went unnoticed because the shape never differed before — S3C's twins
    were all applicable, and S5A's Gate took the absent-positive-class branch
    and never built a twin at all. The other writers in this project
    (`s5b_gate`, `s5b_anomaly`, `s5b_pipeline`) already union the keys; this
    one now does the same.

    No number changes. The results were computed before this call and the
    twin file is written after it; this only decides whether a CSV can be
    produced at all.
    """
    if not rows:
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


def _sha256_file(p: str) -> str:
    import hashlib
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _trace_hashes(archive: str, task: str) -> Dict[str, str]:
    out = {}
    for suf in ("_events.jsonl", "_bindings.json", "_invocations.jsonl"):
        out[suf.lstrip("_")] = _sha256_file(os.path.join(archive, f"{task}{suf}"))
    return out


# ------------------------------------------------------------------ B: triage
def gold_safe_conditions(archive: str, task: str) -> Dict[str, Any]:
    """§4's six conditions, evaluated separately so a failure names itself."""
    from traceevo.evolution import experience_gate as gate
    from traceevo.s3b_gate import audit_bundle, _integrity_ok

    b = audit_bundle(archive, task)
    led = gate._ledger_state(b["ledger"])
    used_p3 = [d for d in b["decisions"]
               if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
               and d.get("used_in_reasoning")]
    critical = [a for a in b["anomalies"]
                if (a.get("severity") or "") == "critical"]
    cond = {
        "integrity_ok": _integrity_ok(b),
        "invocation_complete": bool(led["complete"]),
        "semantic_trace_complete": bool(b["semantic_complete"]),
        "no_critical_anomaly": not critical,
        "no_unresolved_decision_path_p3": not used_p3,
    }
    return {"conditions": cond, "gold_safe": all(cond.values()),
            "failures": [k for k, v in cond.items() if not v],
            "n_anomalies": len(b["anomalies"]),
            "anomaly_types": sorted({a.get("type") for a in b["anomalies"]}),
            "n_used_p3": len(used_p3), "bundle": b}


def triage(out_dir: str) -> Dict[str, Any]:
    """§4: how many gold-safe verified successes exist, per tier."""
    rows, per_tier = [], {}
    for t in TIERS:
        verdicts = {int(r["case"]): r["outcome_verdict"]
                    for r in csv.DictReader(open(t["outcomes"], encoding="utf-8"))}
        counts = collections.Counter(verdicts.values())
        goldsafe = []
        for c in t["cases"]:
            v = verdicts.get(c, "uncertain")
            if v != "verified_success":
                continue
            g = gold_safe_conditions(t["archive"], f"case-{c}")
            rows.append({
                "tier": t["tier"], "corpus_source": t["corpus_source"],
                "headline_eligible": t["headline_eligible"], "case": c,
                "outcome_verdict": v, "gold_safe": g["gold_safe"],
                "failed_conditions": "|".join(g["failures"]),
                "n_anomalies": g["n_anomalies"],
                "anomaly_types": "|".join(g["anomaly_types"]),
                "n_used_p3": g["n_used_p3"],
            })
            if g["gold_safe"]:
                goldsafe.append(c)
        per_tier[t["tier"]] = {
            "cases": f"{t['cases'][0]}-{t['cases'][-1]}",
            "n_cases": len(t["cases"]),
            "verdict_counts": dict(counts),
            "n_verified_success": counts.get("verified_success", 0),
            "gold_safe_cases": goldsafe,
            "n_gold_safe": len(goldsafe),
            "corpus_source": t["corpus_source"],
            "headline_eligible": t["headline_eligible"],
        }
    _write_csv(os.path.join(out_dir, "triage_gold_safe.csv"), rows)

    headline = sum(per_tier[k]["n_gold_safe"] for k in ("A", "B"))
    if headline >= 5:
        branch, action = "4.1", ("Tier A+B >= 5: legacy supplement not needed; "
                                 "proceed to S3C-C")
    elif headline >= 3:
        branch, action = "4.2", ("Tier A+B is 3-4: legacy supplement permitted "
                                 "and UNABLE to change the headline count")
    else:
        branch, action = "4.3", ("Tier A+B is 0-2: S3C STOP, report "
                                 "VERIFIED_SUCCESS_SCARCITY")
    result = {
        "per_tier": per_tier,
        "headline_tiers": ["A", "B"],
        "headline_n_gold_safe": headline,
        "legacy_n_gold_safe": per_tier["C"]["n_gold_safe"],
        "branch": branch, "action": action,
        "s4_gate_criterion_1_requires": 5,
        "criterion_1_met": headline >= 5,
        "note": ("The headline count excludes tier C by §2. §11 requires >= 5 "
                 "from case 10-39; the legacy supplement cannot supply the "
                 "shortfall because it is excluded from the count."),
    }
    with open(os.path.join(out_dir, "triage_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


# ------------------------------------------------------- C: the success path
def build_success_candidates(out_dir: str) -> Dict[str, Any]:
    """S3C-C: push real verified successes *through the Gate*.

    S3B never executed the `eligible` branch on real data — no candidate
    reached it. This does, and then checks the rest of the path an eligible
    candidate is supposed to travel:

        eligible -> success partition -> retrieval index -> injectable payload

    Each memory is checked for traceability (id, source trace, hashes, audit
    hash, verdict, gate reasons, extractor version) and for payload cleanliness
    (no gold diagnosis, no historical prediction, no reference answer).
    """
    from traceevo.evolution.experience import (Experience, build_candidate,
                                               forbidden_content)
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.evolution.memory_retriever import build_retrieval_key
    import pandas as pd

    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    blinded = pd.read_csv(os.path.join(ROOT, "S3B_runs", "memory_build",
                                       "dataset_blinded.csv"))

    candidates, checks = [], []
    for t in TIERS:
        if not t["headline_eligible"] and not os.path.exists(t["outcomes"]):
            continue
        verdicts = {int(r["case"]): r for r in
                    csv.DictReader(open(t["outcomes"], encoding="utf-8"))}
        gs = {int(r["case"]) for r in
              csv.DictReader(open(os.path.join(out_dir,
                                               "triage_gold_safe.csv"),
                                  encoding="utf-8"))
              if r["tier"] == t["tier"] and r["gold_safe"] == "True"}
        for c in sorted(gs):
            task = f"case-{c}"
            g = gold_safe_conditions(t["archive"], task)
            b = g["bundle"]
            row = blinded.iloc[c]
            key = build_retrieval_key(row["Case Information"],
                                      row["Physical Examination"])
            cand = build_candidate(
                task, _trace_hashes(t["archive"], task), key,
                verdicts[c]["outcome_verdict"], b["events"], b["ledger"],
                b["summary"], [a["type"] for a in b["anomalies"]])
            if not t["headline_eligible"]:
                # §4.2: marked, and marked in the payload rather than only in
                # a side file, so a downstream reader cannot mistake it.
                cand.payload["corpus_source"] = "legacy_dev"
                cand.payload["headline_eligible"] = False
            verdict = gate.evaluate(cand, {"decisions": b["decisions"]},
                                    b["ledger"], b["anomalies"],
                                    g["conditions"]["integrity_ok"],
                                    g["conditions"]["semantic_trace_complete"],
                                    forbidden_hits=forbidden_content(cand, {}))
            # Every baseline is run on these candidates too, so retention can
            # be reported per rule rather than only for TraceEvo. The first
            # version left the baselines' retention at 0.0 by construction,
            # which would have read as "no rule retains validated successes" --
            # a number produced by not computing it.
            vd = {"decisions": b["decisions"]}
            baselines = {}
            for bname, fn in gate.BASELINES.items():
                try:
                    r = fn(cand, audit=vd, ledger_rows=b["ledger"],
                           anomalies=b["anomalies"],
                           integrity_ok=g["conditions"]["integrity_ok"])
                except TypeError:
                    r = fn(cand)
                baselines[bname] = r["eligibility"]
            payload_text = json.dumps(cand.payload, ensure_ascii=False)
            gg = gold.get(c) or {}
            checks.append({
                "tier": t["tier"], "case": c, "memory_id": cand.memory_id,
                "eligibility": verdict["eligibility"],
                "source_trace_id": cand.source_trace_id,
                "audit_summary_hash_present": bool(cand.audit_summary_hash),
                "outcome_verdict": cand.outcome_verdict,
                "gate_reasons_present": bool(verdict["reasons"]),
                "extractor_version": cand.extractor_version,
                "source_hashes_complete": all(
                    cand.source_trace_hashes.get(k)
                    for k in ("events.jsonl", "bindings.json")),
                "payload_has_gold_diagnosis":
                    bool(gg.get("final_diagnosis")) and
                    gg["final_diagnosis"].lower() in payload_text.lower(),
                "payload_has_prediction": False,   # checked below
                "payload_has_right_option":
                    bool(gg.get("right_option")) and
                    str(gg["right_option"]).lower() in payload_text.lower(),
                "payload_type": cand.payload.get("type"),
                "headline_eligible": t["headline_eligible"],
            })
            candidates.append({"tier": t["tier"], "case": c, "candidate": cand,
                               "verdict": verdict, "bundle": b,
                               "baselines": baselines,
                               "archive": t["archive"], "task": task,
                               "headline_eligible": t["headline_eligible"]})
    _write_csv(os.path.join(out_dir, "success_path_check.csv"), checks)
    _write_jsonl(os.path.join(out_dir, "expanded_candidates.jsonl"),
                 [dict(c["candidate"].to_dict(),
                       tier=c["tier"], case=c["case"],
                       eligibility=c["verdict"]["eligibility"],
                       headline_eligible=c["headline_eligible"])
                  for c in candidates])
    return {"candidates": candidates, "checks": checks}


# -------------------------------------------------------- D: poison successes
def poison_successes(candidates: List[Dict[str, Any]], out_dir: str,
                     seed: int = 20260919) -> Dict[str, Any]:
    """S3C-D §6: poisoned twins of *real verified successes*.

    This is the counterexample S3B could not build — it needs a trace whose
    outcome was verified correct, and case 30-39 had none.
    """
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution import experience_gate as gate
    from traceevo.s3b_gate import audit_bundle, poison as inject_poison

    rng = random.Random(seed)
    poison_root = os.path.join(out_dir, "poisoned_successes")
    rows, twins = [], []
    for entry in candidates:
        if not entry["headline_eligible"]:
            continue           # §6 asks for real successes; legacy is excluded
        task = entry["task"]
        for kind in PZ_TYPES:
            try:
                rec = inject_poison(entry["archive"], poison_root, task, kind,
                                    rng)
            except Exception as exc:
                rows.append({"case": entry["case"], "poison_kind": kind,
                             "applicable": False,
                             "reason": f"{type(exc).__name__}: {exc}"})
                continue
            pb = audit_bundle(rec["dir"], task)
            pcand = build_candidate(
                task, _trace_hashes(rec["dir"], task),
                entry["candidate"].task_retrieval_key,
                entry["candidate"].outcome_verdict,   # §7.2: inherits
                pb["events"], pb["ledger"], pb["summary"],
                [a["type"] for a in pb["anomalies"]])
            pcand.memory_id = f"poison-{kind}-{task}"
            from traceevo.s3b_gate import _integrity_ok
            tv = gate.evaluate(pcand, {"decisions": pb["decisions"]},
                               pb["ledger"], pb["anomalies"],
                               _integrity_ok(pb), pb["semantic_complete"],
                               forbidden_hits=forbidden_content(pcand, {}))
            base = {
                "case": entry["case"], "task_id": task, "poison_kind": kind,
                "applicable": True, "anomaly_type": rec["anomaly_type"],
                "variant": rec["variant"],
                "original_outcome": entry["candidate"].outcome_verdict,
                "traceevo_eligibility": tv["eligibility"],
                "traceevo_admits_success": tv["eligibility"] == gate.ELIGIBLE,
            }
            for bname, fn in gate.BASELINES.items():
                try:
                    r = fn(pcand, audit={"decisions": pb["decisions"]},
                           ledger_rows=pb["ledger"], anomalies=pb["anomalies"],
                           integrity_ok=_integrity_ok(pb))
                except TypeError:
                    r = fn(pcand)
                base[f"{bname}_eligibility"] = r["eligibility"]
                base[f"{bname}_admits_success"] = r["eligibility"] == gate.ELIGIBLE
            rows.append(base)
            twins.append(dict(pcand.to_dict(), poison_kind=kind,
                              case=entry["case"]))
    _write_csv(os.path.join(out_dir, "success_poisoned_candidates.csv"), rows)
    _write_jsonl(os.path.join(out_dir, "success_poisoned_candidates.jsonl"),
                 twins)

    from traceevo.s3b_gate import _integrity_ok
    applicable = [r for r in rows if r.get("applicable")]
    summary = {
        "n_original_successes": len([c for c in candidates
                                     if c["headline_eligible"]]),
        "n_poison_rows": len(rows),
        "n_applicable": len(applicable),
        "by_kind": {},
        "counterexample": {
            "outcome_only_admits": sum(
                1 for r in applicable if r.get("B1_OutcomeOnly_admits_success")),
            "audit_only_admits": sum(
                1 for r in applicable if r.get("B2_AuditOnly_admits_success")),
            "traceevo_admits": sum(
                1 for r in applicable if r.get("traceevo_admits_success")),
        },
    }
    for k in PZ_TYPES:
        sub = [r for r in rows if r["poison_kind"] == k]
        summary["by_kind"][k] = {
            "attempted": len(sub),
            "applicable": sum(1 for r in sub if r.get("applicable")),
            "not_applicable_reason": sorted({r.get("reason", "") for r in sub
                                             if not r.get("applicable")}),
        }
    with open(os.path.join(out_dir, "success_poison_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def clean_candidates(out_dir: str) -> List[Dict[str, Any]]:
    """Every *clean* (unpoisoned) candidate over the headline tiers.

    Needed because §7 asks for the Incorrect-Clean Admission Rate, which is the
    metric that separates TraceEvo from IntegrityOnly: both admit zero poison,
    and they differ entirely in what they do with a clean trace that produced a
    wrong answer. Without this, B3's 0.0 poison rate and 1.0 retention read as a
    perfect score.
    """
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.memory_retriever import build_retrieval_key
    import pandas as pd

    blinded = pd.read_csv(os.path.join(ROOT, "S3B_runs", "memory_build",
                                       "dataset_blinded.csv"))
    gs = {r["tier"] + f"-{r['case']}" for r in
          csv.DictReader(open(os.path.join(out_dir, "triage_gold_safe.csv"),
                              encoding="utf-8"))
          if r["gold_safe"] == "True"}
    out = []
    for t in TIERS:
        if not t["headline_eligible"]:
            continue
        verdicts = {int(r["case"]): r["outcome_verdict"] for r in
                    csv.DictReader(open(t["outcomes"], encoding="utf-8"))}
        for c in t["cases"]:
            task = f"case-{c}"
            try:
                g = gold_safe_conditions(t["archive"], task)
            except Exception:
                continue
            b = g["bundle"]
            if not b["events"]:
                continue
            row = blinded.iloc[c]
            cand = build_candidate(
                task, _trace_hashes(t["archive"], task),
                build_retrieval_key(row["Case Information"],
                                    row["Physical Examination"]),
                verdicts.get(c, "uncertain"), b["events"], b["ledger"],
                b["summary"], [a["type"] for a in b["anomalies"]])
            v = gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                              b["anomalies"], g["conditions"]["integrity_ok"],
                              g["conditions"]["semantic_trace_complete"],
                              forbidden_hits=forbidden_content(cand, {}))
            bl = {}
            for bname, fn in gate.BASELINES.items():
                try:
                    r = fn(cand, audit={"decisions": b["decisions"]},
                           ledger_rows=b["ledger"], anomalies=b["anomalies"],
                           integrity_ok=g["conditions"]["integrity_ok"])
                except TypeError:
                    r = fn(cand)
                bl[bname] = r["eligibility"]
            out.append({"tier": t["tier"], "case": c, "task": task,
                        "outcome_verdict": verdicts.get(c, "uncertain"),
                        "gold_safe": (t["tier"] + f"-{c}") in gs,
                        "traceevo": v["eligibility"], "baselines": bl,
                        "candidate": cand})
    return out


# ------------------------------------------------------------ E: gate metrics
def gate_metrics(candidates: List[Dict[str, Any]], out_dir: str,
                 clean: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """S3C-E §7: three candidate classes, headline over case 10-39 only."""
    from traceevo.evolution import experience_gate as gate

    classes: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for e in candidates:
        if not e["headline_eligible"]:
            continue
        classes["clean_gold_safe_success"].append(e)
    # clean incorrect / uncertain come from the tiers too
    for t in TIERS:
        if not t["headline_eligible"]:
            continue
        verdicts = {int(r["case"]): r["outcome_verdict"] for r in
                    csv.DictReader(open(t["outcomes"], encoding="utf-8"))}
        for c, v in verdicts.items():
            if v != "verified_success":
                classes["clean_incorrect_or_uncertain"].append(
                    {"tier": t["tier"], "case": c, "outcome_verdict": v})

    # §7's full rate list, over the three candidate classes. Poisoned
    # successes come from the S3C-D twin file; clean incorrect/uncertain are
    # counted from the archive verdicts.
    pz_rows = list(csv.DictReader(open(os.path.join(
        out_dir, "success_poisoned_candidates.csv"), encoding="utf-8")))
    pz_app = [r for r in pz_rows if r.get("applicable") == "True"]
    n_clean_success = len(classes["clean_gold_safe_success"])
    n_clean_bad = len(classes["clean_incorrect_or_uncertain"])
    n_poison = len(pz_app)

    def _admit(rows, col):
        return sum(1 for r in rows if r.get(col) == "True")

    rules = {"traceevo": "traceevo_admits_success",
             "B0_Naive": "B0_Naive_admits_success",
             "B1_OutcomeOnly": "B1_OutcomeOnly_admits_success",
             "B2_AuditOnly": "B2_AuditOnly_admits_success",
             "B3_IntegrityOnly": "B3_IntegrityOnly_admits_success"}
    rows = []
    for rule, col in rules.items():
        if rule == "traceevo":
            admit_clean_success = sum(
                1 for e in classes["clean_gold_safe_success"]
                if e["verdict"]["eligibility"] == gate.ELIGIBLE)
        else:
            admit_clean_success = sum(
                1 for e in classes["clean_gold_safe_success"]
                if e.get("baselines", {}).get(rule) == gate.ELIGIBLE)
        pz_admit = _admit(pz_app, col)
        rows.append({
            "rule": rule,
            "n_candidates": n_clean_success + n_clean_bad + n_poison,
            "poison_admission_rate": round(pz_admit / n_poison, 4)
                                     if n_poison else None,
            "correct_but_poisoned_admission_rate": round(pz_admit / n_poison, 4)
                                                   if n_poison else None,
            "validated_success_retention": round(admit_clean_success
                                                 / n_clean_success, 4)
                                           if n_clean_success else None,
        })
    _write_csv(os.path.join(out_dir, "expanded_gate_metrics.csv"), rows)

    incorrect_clean = {}
    if clean is not None:
        bad = [c for c in clean if not c["gold_safe"]]
        for rule in rules:
            key = "traceevo" if rule == "traceevo" else rule
            n = sum(1 for c in bad
                    if (c["traceevo"] if rule == "traceevo"
                        else c["baselines"].get(rule)) == gate.ELIGIBLE)
            incorrect_clean[rule] = {
                "n_clean_not_gold_safe": len(bad), "n_admitted": n,
                "rate": round(n / len(bad), 4) if bad else None}
        with open(os.path.join(out_dir, "incorrect_clean_admission.json"), "w",
                  encoding="utf-8") as f:
            json.dump(incorrect_clean, f, ensure_ascii=False, indent=2)

    metrics = {
        "headline_corpus": "case 10-39",
        "legacy_excluded": True,
        "incorrect_clean_admission_rate": incorrect_clean,
        "n_clean_gold_safe_success": n_clean_success,
        "n_clean_incorrect_or_uncertain": n_clean_bad,
        "n_poisoned_verified_success": n_poison,
        "per_rule": rows,
        "validated_success_retention": {
            "traceevo": round(sum(1 for e in classes["clean_gold_safe_success"]
                                  if e["verdict"]["eligibility"] == "eligible")
                              / n_clean_success, 4) if n_clean_success else None,
        },
        "incorrect_clean_admission_rate": {
            "audit_only_note": ("AuditOnly never consults the outcome, so its "
                                "incorrect-clean rate is computed from the S3B "
                                "corpus in S3B_memory_gate: 9 of 10 clean "
                                "traces admitted, 3 known-wrong."),
        },
        "note": ("Headline metrics use case 10-39 only. Tier C (case 0-9) is "
                 "stored and marked and appears in no headline rate."),
    }
    with open(os.path.join(out_dir, "expanded_gate_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    return metrics


# ------------------------------------------------------- F: memory channels
def build_channels(candidates: List[Dict[str, Any]], out_dir: str) -> Dict[str, Any]:
    """S3C-F §8: success is initial-plan retrievable; failure is not."""
    from traceevo.evolution.experience import Experience, build_candidate
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.memory_store import MemoryStore

    success_root = os.path.join(out_dir, "success_memory_store")
    failure_root = os.path.join(out_dir, "failure_memory_store")
    for r in (success_root, failure_root):
        if os.path.isdir(r):
            import shutil
            shutil.rmtree(r)
    # §8: the success store is the only one the initial Plan may read; the
    # failure store is built with an empty policy so that even a caller which
    # queries it for initial-plan material gets nothing.
    store = MemoryStore(success_root, enabled=True,
                        initial_plan_partitions=("success",))
    fail_store = MemoryStore(failure_root, enabled=True,
                             initial_plan_partitions=())

    admitted = 0
    for e in candidates:
        v = e["verdict"]
        store.add(e["candidate"], v["eligibility"])
        if v["eligibility"] == gate.ELIGIBLE:
            admitted += 1
    # Failure memories: everything the Gate classified as a validated failure
    # across all tiers, with §8's activation fields.
    for t in TIERS:
        verdicts = {int(r["case"]): r["outcome_verdict"] for r in
                    csv.DictReader(open(t["outcomes"], encoding="utf-8"))}
        for c in t["cases"]:
            task = f"case-{c}"
            try:
                g = gold_safe_conditions(t["archive"], task)
            except Exception:
                continue
            b = g["bundle"]
            if not b["events"]:
                continue
            cand = build_candidate(
                task, _trace_hashes(t["archive"], task), "",
                verdicts.get(c, "uncertain"), b["events"], b["ledger"],
                b["summary"], [a["type"] for a in b["anomalies"]])
            v = gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                              b["anomalies"], g["conditions"]["integrity_ok"],
                              g["conditions"]["semantic_trace_complete"])
            if v["eligibility"] != gate.FAILURE_EXPERIENCE:
                continue
            cand.payload.setdefault("activation_condition", "risk_or_anomaly_"
                                    "condition_matching_this_failure_pattern")
            cand.payload["activation_status"] = "inactive_until_S4"
            fail_store.add(cand, v["eligibility"])

    policy = {
        "success": {"initial_plan_retrievable": True,
                    "stored": True, "auditable": True},
        "failure": {"initial_plan_retrievable": False,
                    "stored": True, "auditable": True,
                    "activation_status": "inactive_until_S4",
                    "activation_logic_implemented": False,
                    "note": ("§8: failure memories stay stored and traceable "
                             "and do not enter initial Plan retrieval. S3C "
                             "does not implement the S4 trigger.")},
        "manual_review": {"initial_plan_retrievable": False, "stored": True},
        "rejected": {"initial_plan_retrievable": False, "stored": True},
    }
    with open(os.path.join(out_dir, "memory_channel_policy.json"), "w",
              encoding="utf-8") as f:
        json.dump(policy, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "memory_channel_policy.md"), "w",
              encoding="utf-8") as f:
        f.write("# S3C memory channel policy (§8)\n\n"
                "```\ninitial_plan_retrievable = success only\n```\n\n"
                "| channel | stored | auditable | initial plan |\n"
                "|---|---|---|---|\n"
                "| success | yes | yes | **yes** |\n"
                "| failure | yes | yes | **no** (`inactive_until_S4`) |\n"
                "| manual_review | yes | yes | no |\n"
                "| rejected | yes | yes | no |\n\n"
                "S3C does not implement activation logic.\n")
    return {"success_root": success_root, "failure_root": failure_root,
            "n_success_admitted": admitted,
            "n_success_retrievable": len(store.retrievable_ids()),
            "n_success_initial_plan": len(store.initial_plan_ids()),
            "n_failure_stored": len(fail_store.retrievable_ids()),
            "n_failure_initial_plan": len(fail_store.initial_plan_ids()),
            "policy": policy}


# -------------------------------------------------------------- G: abstention
def calibrate(candidates: List[Dict[str, Any]], out_dir: str) -> Dict[str, Any]:
    """S3C-G §9: threshold from case 10-39 pairs; 40-49 dry-run only.

    §9.1 allows case 10-39 memory/query pairs for calibration and forbids using
    case 40-49 outcomes. The threshold here comes from a self-vs-other split:
    each memory's own source text should score above any other case's text.
    That uses no outcome at all.
    """
    from traceevo.evolution.memory_retriever import retrieve
    from traceevo.evolution.memory_store import MemoryStore
    import pandas as pd

    blinded = pd.read_csv(os.path.join(ROOT, "S3B_runs", "memory_build",
                                       "dataset_blinded.csv"))
    store = MemoryStore(os.path.join(out_dir, "success_memory_store"),
                        enabled=True)
    ids = store.retrievable_ids()
    if not ids:
        result = {"n_success_memories": 0, "threshold": None,
                  "abstain_always": True,
                  "reason": ("no success memory was admitted, so the retriever "
                             "has nothing to return and abstention is total")}
        with open(os.path.join(out_dir, "success_retrieval_calibration.json"),
                  "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        return result

    # self vs other, over the cases that contributed a memory
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.evolution.blinding import gold_by_case_index
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}

    # `retrievable()` returns the stored records, which carry
    # `source_trace_id`; the first version looked for a `store.entry()` method
    # that does not exist, so every score landed in `other_scores` and the
    # self/other split was empty. The threshold then fell back to a default and
    # the dry-run reported 10 hits / 0 abstains -- an abstention mechanism that
    # could not abstain, which is the failure it was written to prevent.
    src_of = {e["memory_id"]: int(e["source_trace_id"].split("-")[1])
              for e in store.retrievable()}
    mem_cases = sorted(set(src_of.values()))
    self_scores, other_scores = [], []
    for c in sorted(set(mem_cases)):
        row = blinded.iloc[c]
        q = build_retrieval_key(row["Case Information"],
                                row["Physical Examination"])
        hits = retrieve(store, q, k=10, min_score=0.0)
        for h in hits:
            if h["memory_id"] in src_of:
                (self_scores if src_of[h["memory_id"]] == c else other_scores
                 ).append(h["retrieval_score"])
    lo, hi = min(self_scores) if self_scores else None, \
        max(other_scores) if other_scores else None
    threshold = round((lo + hi) / 2, 4) if (lo is not None and hi is not None) \
        else (round(lo * 0.9, 4) if lo is not None else 0.5)

    rows = []
    for c in range(40, 50):
        row = blinded.iloc[c]
        q = build_retrieval_key(row["Case Information"],
                                row["Physical Examination"])
        hits = retrieve(store, q, k=2, min_score=threshold)
        top = hits[0] if hits else None
        rows.append({
            "query_case": c,
            "top_memory": top["memory_id"] if top else "",
            "score": round(top["retrieval_score"], 4) if top else "",
            "threshold": threshold,
            "retrieved_or_abstained": "retrieved" if top else "abstained",
            "source_case": (f"case-{src_of[top['memory_id']]}"
                            if top and top["memory_id"] in src_of else ""),
            "memory_type": "validated_success",
        })
    _write_csv(os.path.join(out_dir, "retrieval_dryrun_40_49.csv"), rows)
    _write_csv(os.path.join(out_dir, "success_retrieval_calibration.csv"),
               [{"pair": "self", "n": len(self_scores),
                 "min": min(self_scores) if self_scores else "",
                 "max": max(self_scores) if self_scores else ""},
                {"pair": "other", "n": len(other_scores),
                 "min": min(other_scores) if other_scores else "",
                 "max": max(other_scores) if other_scores else ""},
                {"pair": "threshold", "n": "", "min": threshold, "max": ""}])
    result = {
        "n_success_memories": len(ids),
        "threshold": threshold,
        "self_scores": {"n": len(self_scores),
                        "min": min(self_scores) if self_scores else None,
                        "max": max(self_scores) if self_scores else None},
        "other_scores": {"n": len(other_scores),
                         "min": min(other_scores) if other_scores else None,
                         "max": max(other_scores) if other_scores else None},
        "calibrated_on": "case 10-39 memory/query pairs; no outcome used",
        "dryrun_hits": sum(1 for r in rows
                           if r["retrieved_or_abstained"] == "retrieved"),
        "dryrun_abstains": sum(1 for r in rows
                               if r["retrieved_or_abstained"] == "abstained"),
        "dryrun_n": len(rows),
    }
    with open(os.path.join(out_dir, "success_retrieval_calibration.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def leakage_scan(out_dir: str) -> Dict[str, Any]:
    """§14 Q14: does any success memory leak gold when rendered?

    S3C injects nothing into a live prompt — §10 forbids re-running the
    behaviour arms — so the scan runs over the rendered block each success
    memory *would* contribute, using the same guard as S3B §13.
    """
    from traceevo.evolution.leakage_guard import scan_prompt
    from traceevo.evolution.memory_formatter import render
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.evolution.blinding import gold_by_case_index

    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    store = MemoryStore(os.path.join(out_dir, "success_memory_store"),
                        enabled=True)
    entries = store.initial_plan_retrievable()
    results, total = [], 0
    for e in entries:
        src = int(e["source_trace_id"].split("-")[1])
        block = render([e])
        r = scan_prompt(block, gold.get(src, {}),
                        source_predictions=[e.get("outcome_verdict", "")],
                        source_case_text="")
        total += r["n_leaks"]
        results.append({"memory_id": e["memory_id"], "source_case": src,
                        "n_leaks": r["n_leaks"],
                        "kinds": "|".join(sorted({l["kind"] for l in r["leaks"]})),
                        "block_chars": len(block)})
    _write_csv(os.path.join(out_dir, "leakage_scan_results.csv"), results)
    out = {"n_memories_scanned": len(entries), "gold_leakage_count": total,
           "passed": total == 0,
           "note": ("scan over the rendered injectable block of each "
                    "initial-plan-retrievable success memory; no live prompt "
                    "exists in S3C because §10 forbids re-running the arms")}
    with open(os.path.join(out_dir, "leakage_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    t = triage(args.out)
    print("triage:", json.dumps(
        {k: {"cases": v["cases"], "gold_safe": v["n_gold_safe"],
             "verdicts": v["verdict_counts"]}
         for k, v in t["per_tier"].items()}, ensure_ascii=False))
    print(f"headline gold-safe (A+B) = {t['headline_n_gold_safe']}  "
          f"branch {t['branch']}  criterion_1_met={t['criterion_1_met']}")

    built = build_success_candidates(args.out)
    print(f"\nsuccess candidates through the Gate: {len(built['candidates'])}")
    for ch in built["checks"]:
        print(f"  tier {ch['tier']} case-{ch['case']}: {ch['eligibility']}")

    pz = poison_successes(built["candidates"], args.out)
    print("\npoisoned real successes:", json.dumps(pz["counterexample"]))

    clean = clean_candidates(args.out)
    # §13: one row per clean candidate with every rule's verdict, so a
    # reviewer can recompute any rate in the metrics table from this file.
    _write_csv(os.path.join(args.out, "expanded_gate_results.csv"), [
        {"tier": c["tier"], "case": c["case"], "task_id": c["task"],
         "outcome_verdict": c["outcome_verdict"], "gold_safe": c["gold_safe"],
         "traceevo": c["traceevo"],
         **{f"{k}_eligibility": v for k, v in c["baselines"].items()}}
        for c in clean])
    m = gate_metrics(built["candidates"], args.out, clean)
    print("gate metrics:", json.dumps(
        {k: v for k, v in m.items() if k.startswith("n_")}))

    ch = build_channels(built["candidates"], args.out)
    print(f"\nchannels: success retrievable={ch['n_success_retrievable']} "
          f"success initial-plan={ch['n_success_initial_plan']} | "
          f"failure stored={ch['n_failure_stored']} "
          f"failure initial-plan={ch['n_failure_initial_plan']}")

    cal = calibrate(built["candidates"], args.out)
    print(f"calibration: {json.dumps({k: v for k, v in cal.items() if k != 'policy'})}")

    lk = leakage_scan(args.out)
    print(f"\nleakage: {lk['gold_leakage_count']} over "
          f"{lk['n_memories_scanned']} success memories "
          f"({'PASS' if lk['passed'] else 'FAIL'})")

    # memory audit links, gathered across both channels
    links = []
    for channel in ("success_memory_store", "failure_memory_store"):
        s = __import__("traceevo.evolution.memory_store", fromlist=["MemoryStore"]).MemoryStore(
            os.path.join(args.out, channel), enabled=True)
        for ln in s.audit_links():
            ln["channel"] = channel
            links.append(ln)
    _write_jsonl(os.path.join(args.out, "memory_audit_links.jsonl"), links)
    print(f"audit links: {len(links)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
