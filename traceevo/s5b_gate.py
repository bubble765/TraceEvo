"""S5B-G/H §10-§11: the Experience Gate's final held-out experiment.

§10 divides into three populations and §10.4 makes the third one conditional
on a fact about the corpus rather than on a preference:

    §10.1  every natural case          Naive / OutcomeOnly / AuditOnly /
                                       IntegrityOnly / TraceEvo
    §10.2  incorrect-clean             the negative class the block should have
    §10.3  correct-but-poisoned        only from verified_success AND clean
                                       trace AND eligible-or-reviewable
    §10.4  if verified_success is 0    HELDOUT_POSITIVE_CLASS_ABSENT, and the
                                       judge is not touched

The §10.4 branch is the one that needs discipline. A block with no verified
success is a legitimate result, and the tempting responses to it — relaxing
the judge, hunting for cases, re-labelling an uncertain as a success — are
forbidden in as many words. The correct output is a named absence, and the
paper's positive-branch claim is then attributed to the development
experiment rather than to this block.

The twin machinery is `s3c_expand.poison_successes` and the poisoner is
`s3b_gate.poison`, exactly as S3D and S5A used them, so "the Gate behaves the
same on a new corpus" is a claim about one implementation on new inputs rather
than about two similar implementations.

Usage:
    python traceevo/s5b_gate.py --out S5B_final_heldout
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"
SEED = 20260920
ABSENT = "HELDOUT_POSITIVE_CLASS_ABSENT"
SPARSE = "HELDOUT_POSITIVE_CLASS_SPARSE"
ENOUGH = "HELDOUT_POSITIVE_CLASS_ADEQUATE"

BASELINES = ("B0_Naive", "B1_OutcomeOnly", "B2_AuditOnly", "B3_IntegrityOnly",
             "TraceEvo")
RULE_COL = {"B0_Naive": "naive_rule", "B1_OutcomeOnly": "outcome_rule",
            "B2_AuditOnly": "audit_rule", "B3_IntegrityOnly": "integrity_rule",
            "TraceEvo": "gate_eligibility"}


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


def _csv(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _clear_outputs(out_dir: str) -> List[str]:
    """Delete this module's outputs before recomputing them.

    A stage that crashes leaves its previous outputs on disk, and the next
    reader cannot tell them from current ones — which is how a stale
    `gate_final.json` from a buggy run gets quoted as this run's result. The
    files are removed at the start so a failure leaves a gap rather than a
    wrong number.
    """
    removed = []
    for name in ("gate_final.json", "gate_results.csv",
                 "gate_baseline_metrics.csv",
                 "correct_but_poisoned_results.csv",
                 "incorrect_clean_results.csv"):
        p = os.path.join(out_dir, name)
        if os.path.exists(p):
            os.remove(p)
            removed.append(name)
    return removed


def wilson(k: int, n: int, z: float = 1.959963984540054
           ) -> List[Optional[float]]:
    if n <= 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 6), round(min(1.0, c + h), 6)]


def _ratio(k: int, n: int, label: str) -> Dict[str, Any]:
    """§11: every proportion is reported as numerator / denominator with CI."""
    return {"label": label, "numerator": k, "denominator": n,
            "rate": round(k / n, 6) if n else None,
            "ci95": wilson(k, n) if n else [None, None],
            "ci_method": "Wilson 95% (§16)",
            "note": ("no denominator, no rate: §11 forbids writing 0.0 / 1.0 "
                     "without the counts behind them")}


# ------------------------------------------------------------------ §10.1
def natural_table(out_dir: str) -> Dict[str, Any]:
    rows = list(csv.DictReader(open(
        os.path.join(out_dir, "natural_experience_summary.csv"),
        encoding="utf-8")))
    by_rule: Dict[str, collections.Counter] = {
        r: collections.Counter() for r in BASELINES}
    for r in rows:
        for rule in BASELINES:
            by_rule[rule][r[RULE_COL[rule]]] += 1
    n = len(rows)
    table = []
    for rule in BASELINES:
        c = by_rule[rule]
        admitted = c.get("eligible", 0)
        table.append({
            "rule": rule, "n_candidates": n,
            "eligible": admitted,
            "failure_experience": c.get("failure_experience", 0),
            "manual_review": c.get("manual_review", 0),
            "reject": c.get("reject", 0),
            "admission_rate": round(admitted / n, 6) if n else None,
            "admission_rate_ci95": wilson(admitted, n) if n else [None, None],
        })
    return {"n_natural_candidates": n, "rows": table}


# ------------------------------------------------------------------ §10.2
def incorrect_clean(out_dir: str) -> Dict[str, Any]:
    """The negative class with the sample size §10.2 expects.

    Definition, frozen: `outcome_verdict == verified_incorrect` AND the trace
    carries no audit finding at all (`n_anomalies == 0`). A wrong answer from
    a process that looks clean is exactly the population where AuditOnly
    admits and TraceEvo must not, so the comparison is only meaningful if
    "clean" means the audit found nothing rather than "found nothing
    serious".
    """
    rows = list(csv.DictReader(open(
        os.path.join(out_dir, "natural_experience_summary.csv"),
        encoding="utf-8")))
    ic = [r for r in rows
          if r["outcome_verdict"] == "verified_incorrect"
          and int(r["n_anomalies"] or 0) == 0]
    n = len(ic)
    out_rows = []
    for r in ic:
        out_rows.append({
            "case": r["case"], "task_id": r["task_id"],
            "outcome_verdict": r["outcome_verdict"], "n_anomalies": 0,
            **{f"{rule}_eligibility": r[RULE_COL[rule]] for rule in BASELINES},
        })
    admitted = {rule: sum(1 for r in out_rows
                          if r[f"{rule}_eligibility"] == "eligible")
                for rule in BASELINES}
    return {
        "definition": ("verified_incorrect with no audit finding on the trace "
                       "(§10.2, frozen)"),
        "n_incorrect_clean": n,
        "cases": [int(r["case"]) for r in rows
                  if r["outcome_verdict"] == "verified_incorrect"
                  and int(r["n_anomalies"] or 0) == 0],
        "by_rule": {rule: _ratio(admitted[rule], n,
                                 f"{rule} admission on incorrect-clean")
                    for rule in BASELINES},
        "rows": out_rows,
        "note": ("§10.2 calls this the negative-class evidence the held-out "
                 "block should carry most of. It is reported with its real "
                 "denominator; a zero denominator here is a fact about the "
                 "corpus and not a 0.0 admission rate."),
    }


# ------------------------------------------------------------------ §10.3/§10.4
def run(out_dir: str) -> Dict[str, Any]:
    from traceevo.s3c_expand import gold_safe_conditions

    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
    _clear_outputs(out_dir)
    outcomes = {int(r["case"]): r for r in csv.DictReader(open(
        os.path.join(out_dir, "outcome_results.csv"), encoding="utf-8"))}
    gates = {int(r["case"]): r for r in csv.DictReader(open(
        os.path.join(out_dir, "natural_experience_summary.csv"),
        encoding="utf-8"))}

    vs = [c for c, r in outcomes.items()
          if r["outcome_verdict"] == "verified_success"]
    # §10.3's own precondition: verified_success AND clean trace AND
    # eligible-or-reviewable according to the frozen protocol. A verified
    # success whose process is unsafe is not a reusable success and must not
    # be poisoned as one.
    gate_ok = []
    for c in vs:
        el = gates[c]["gate_eligibility"]
        if el in ("eligible", "manual_review") and int(
                gates[c]["n_anomalies"] or 0) == 0:
            gate_ok.append(c)
    gold_safe = []
    for c in gate_ok:
        g = gold_safe_conditions(artifacts, f"case-{c}")
        if g["gold_safe"]:
            gold_safe.append(c)

    n = len(gold_safe)
    if n == 0:
        branch = ABSENT
    elif n < 5:
        branch = SPARSE
    else:
        branch = ENOUGH

    base = {
        "block": "80-137", "n_cases": len(CASES),
        "verified_success_cases": sorted(vs),
        "n_verified_success": len(vs),
        "poison_eligible_cases": sorted(gate_ok),
        "n_poison_eligible": len(gate_ok),
        "gold_safe_cases": sorted(gold_safe),
        "n_gold_safe": n,
        "branch": branch,
        "branch_rule": ("§10.4: >=5 report positive-class metrics with CI; "
                        "1-4 report %s; 0 report %s" % (SPARSE, ABSENT)),
    }

    natural = natural_table(out_dir)
    ic = incorrect_clean(out_dir)
    _write_csv(os.path.join(out_dir, "incorrect_clean_results.csv"),
               ic["rows"])
    # §10.1 and §10.2 are reported by every branch, so they go into the file
    # the tables read rather than into a second summary that would be free to
    # drift from this one.
    base["natural"] = natural
    base["incorrect_clean"] = {k: v for k, v in ic.items() if k != "rows"}

    if n == 0:
        out = {
            **base,
            "twins_built": 0,
            "what_was_not_done": [
                "the judge was not modified",
                "score=1 was not promoted to a success",
                "uncertain was not relabelled as success",
                "development data was not mixed into the held-out denominator",
                "no further cases were searched",
            ],
            "paper_wording": (
                "The positive-branch conclusion for correct-but-poisoned is "
                "supported by the development controlled experiment; the "
                "final held-out block produced no positive class usable for "
                "independent replication."),
            "prior_evidence": ("S3C's 16 twins over case 10-29 remain the "
                               "development evidence for the two-"
                               "counterexample argument."),
            "positive_class_metrics": None,
        }
        _write_csv(os.path.join(out_dir, "correct_but_poisoned_results.csv"),
                   [{"record": ABSENT, "n_cases": len(CASES),
                     "verified_success": len(vs), "gold_safe": 0,
                     "reason": out["paper_wording"]}])
        _write_csv(os.path.join(out_dir, "gate_baseline_metrics.csv"),
                   metric_rows(build_population(out_dir, None)))
        _write_csv(os.path.join(out_dir, "gate_results.csv"),
                   _results_rows(ic, natural, None))
        with open(os.path.join(out_dir, "gate_final.json"), "w",
                  encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return out

    # ---- §10.3's first branch: build the frozen poison twins.
    import pandas as pd
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.s3c_expand import poison_successes

    blinded = pd.read_csv(os.path.join(out_dir,
                                       "dataset_blinded_80_137.csv"))
    entries = []
    for c in gold_safe:
        task = f"case-{c}"
        g = gold_safe_conditions(artifacts, task)
        b = g["bundle"]
        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        from traceevo.evolution.experience import build_candidate
        from traceevo.s3c_expand import _trace_hashes
        cand = build_candidate(task, _trace_hashes(artifacts, task), key,
                               "verified_success", b["events"], b["ledger"],
                               b["summary"],
                               [a["type"] for a in b["anomalies"]])
        entries.append({"case": c, "task": task, "archive": artifacts,
                        "headline_eligible": True, "candidate": cand})
    poison_successes(entries, out_dir, seed=SEED)

    # The rule decisions live in the CSV `poison_successes` writes, not in the
    # jsonl beside it. The jsonl holds the poisoned *candidates* — memory ids,
    # payloads, source hashes — and carries no `*_admits_success` column at
    # all. The first version read the jsonl, so every `t.get(key)` returned
    # None and every rule scored 0/19, including B0_Naive, which admits
    # everything by construction. A zero that Naive cannot produce is the
    # cheapest possible check on this number, and it is the one that caught
    # it.
    twin_rows = _csv(os.path.join(out_dir, "success_poisoned_candidates.csv"))
    applicable = [t for t in twin_rows
                  if str(t.get("applicable", "True")).lower() == "true"]
    rows = []
    for t in applicable:
        rec = {"case": t.get("case"), "task_id": t.get("task_id"),
               "poison_kind": t.get("poison_kind"),
               "anomaly_type": t.get("anomaly_type"),
               "variant": t.get("variant"),
               "original_outcome": t.get("original_outcome")}
        for rule in BASELINES:
            key = (f"{rule}_admits_success" if rule != "TraceEvo"
                   else "traceevo_admits_success")
            rec[f"{rule}_admits_success"] = t.get(key)
        rows.append(rec)
    # A rule that admits everything must admit something: the sanity check
    # that would have failed the first version at run time rather than at
    # reading time.
    naive_admits = sum(1 for r in rows
                       if str(r.get("B0_Naive_admits_success")).lower()
                       == "true")
    if rows and naive_admits == 0:
        raise SystemExit(
            f"B0_Naive admitted 0 of {len(rows)} poisoned twins. Naive "
            f"admits every candidate by construction, so this is a reading "
            f"error rather than a result — most likely the rule decisions "
            f"were read from a file that does not carry them.")
    _write_csv(os.path.join(out_dir, "correct_but_poisoned_results.csv"), rows)

    # CSV values arrive as strings, so `is True` would match nothing and
    # reproduce the same all-zero table from the other side.
    def _admits(r, rule):
        return str(r.get(f"{rule}_admits_success")).lower() == "true"

    admitted = {rule: sum(1 for r in rows if _admits(r, rule))
                for rule in BASELINES}
    positive = {
        "n_documents": len(gold_safe),
        "n_twins_applicable": len(applicable),
        "correct_but_poisoned_admission": {
            rule: _ratio(admitted[rule], len(applicable),
                         f"{rule} admission on correct-but-poisoned twins")
            for rule in BASELINES},
        "source": "success_poisoned_candidates.csv (the file that carries the "
                  "per-rule decisions)",
        "by_kind": {k: {"attempted": sum(1 for t in twin_rows
                                         if t.get("poison_kind") == k),
                        "applicable": sum(1 for t in applicable
                                          if t.get("poison_kind") == k)}
                    for k in ("PZ1", "PZ2", "PZ3", "PZ4")},
    }
    _write_csv(os.path.join(out_dir, "gate_baseline_metrics.csv"),
               metric_rows(build_population(out_dir, rows)))
    _write_csv(os.path.join(out_dir, "gate_results.csv"),
               _results_rows(ic, natural, rows))

    out = {**base, "twins_built": len(applicable),
           "positive_class_metrics": positive,
           "note": ("§10.4: verified_success >= 5, so the held-out Gate "
                    "positive-class metrics are reported with CIs.")}
    with open(os.path.join(out_dir, "gate_final.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


# ------------------------------------------------------------------ §11
def build_population(out_dir: str, twins: Optional[List[Dict[str, Any]]]
                     ) -> List[Dict[str, Any]]:
    """The population every §11 rate is computed over, assembled once.

    Each record carries what each rule decided and what the candidate *is*,
    kept in separate fields so no rate can be computed from the wrong side.
    The first version of this function computed an "admission precision" as
    `admitted / admitted` — a number that is 1.0 by construction and says
    nothing. Separating the label (`is_genuine_positive`) from the verdict
    (`rule_eligibility`) makes that unrepresentable.

        kind                 natural | poison
        is_genuine_positive  a clean verified success: the only thing an
                             admission is supposed to admit
        has_anomaly          the sealed trace carries a localized finding
        rule_eligibility     that rule's verdict for this candidate
    """
    rows = list(csv.DictReader(open(
        os.path.join(out_dir, "natural_experience_summary.csv"),
        encoding="utf-8")))
    pop: List[Dict[str, Any]] = []
    for r in rows:
        verdict = r["outcome_verdict"]
        n_anom = int(r["n_anomalies"] or 0)
        pop.append({
            "kind": "natural", "case": int(r["case"]),
            "task_id": r["task_id"], "outcome_verdict": verdict,
            "n_anomalies": n_anom,
            "is_genuine_positive": bool(verdict == "verified_success"
                                        and n_anom == 0),
            "has_anomaly": n_anom > 0,
            "rule_eligibility": {rule: r[RULE_COL[rule]]
                                 for rule in BASELINES},
        })
    for t in (twins or []):
        # The `*_admits_success` values arrive as the strings "True"/"False"
        # from a CSV, so `is True` matches nothing and every rule's poison
        # admission becomes 0 — including Naive's, which is the value that
        # cannot be 0. A rejected twin is REJECT; an admitted one is
        # ELIGIBLE; anything else is left as the empty string so a missing
        # reading never renders as a decision.
        elig = {}
        for rule in BASELINES:
            key = (f"{rule}_admits_success" if rule != "TraceEvo"
                   else "traceevo_admits_success")
            v = str(t.get(key)).lower()
            if t.get(key) in (None, ""):
                elig[rule] = ""
            else:
                elig[rule] = "eligible" if v == "true" else "reject"
        pop.append({
            "kind": "poison", "case": t.get("case"),
            "task_id": t.get("task_id"),
            "outcome_verdict": t.get("original_outcome"),
            "poison_kind": t.get("poison_kind"),
            "n_anomalies": None,
            "is_genuine_positive": False,
            "has_anomaly": True,
            "rule_eligibility": elig,
        })
    return pop


def metric_block(pop: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """§11's eight rates, per rule, each as numerator / denominator + CI."""
    out = []
    for rule in BASELINES:
        elig = lambda r: r["rule_eligibility"][rule]          # noqa: E731
        natural = [r for r in pop if r["kind"] == "natural"]
        poison = [r for r in pop if r["kind"] == "poison"]
        positives = [r for r in pop if r["is_genuine_positive"]]
        admitted = [r for r in pop if elig(r) == "eligible"]
        tp = [r for r in admitted if r["is_genuine_positive"]]
        ic = [r for r in natural
              if r["outcome_verdict"] == "verified_incorrect"
              and not r["has_anomaly"]]
        anomalous = [r for r in natural if r["has_anomaly"]
                     and elig(r) != "reject"]
        rec = {"rule": rule}
        rec["validated_success_retention"] = _ratio(
            sum(1 for r in positives if elig(r) == "eligible"),
            len(positives), f"{rule}: gold-safe verified successes admitted")
        rec["admission_precision"] = _ratio(
            len(tp), len(admitted), f"{rule}: admitted candidates that are "
                                    f"clean verified successes")
        rec["admission_recall"] = _ratio(
            len(tp), len(positives), f"{rule}: genuine positives admitted")
        rec["incorrect_clean_admission_rate"] = _ratio(
            sum(1 for r in ic if elig(r) == "eligible"), len(ic),
            f"{rule}: incorrect-clean candidates admitted")
        rec["poison_admission_rate"] = _ratio(
            sum(1 for r in poison if elig(r) == "eligible"), len(poison),
            f"{rule}: poisoned twins admitted")
        rec["correct_but_poisoned_admission_rate"] = _ratio(
            sum(1 for r in poison if elig(r) == "eligible"
                and r.get("poison_kind")),
            len([r for r in poison if r.get("poison_kind")]),
            f"{rule}: correct-but-poisoned twins admitted")
        rec["manual_review_rate"] = _ratio(
            sum(1 for r in natural if elig(r) == "manual_review"),
            len(natural), f"{rule}: natural candidates sent to review")
        rec["failure_capture_rate"] = _ratio(
            sum(1 for r in anomalous if elig(r) == "failure_experience"),
            len(anomalous),
            f"{rule}: traces carrying a localized finding routed to "
            f"failure_experience")
        rec["note"] = ("retention and admission recall share a numerator and "
                       "differ in reading: retention is what the rule kept of "
                       "what it should keep, recall is what it kept of "
                       "everything positive in the population. On a "
                       "population with no poisoned twins they coincide, and "
                       "that coincidence is a property of the population.")
        out.append(rec)
    return out


# §11's rate names, in the order the table reports them. The flat column form
# exists because a nested dict written into a CSV becomes its own repr, and a
# rate that has to be parsed out of a Python literal is a rate nobody
# re-derives.
RATE_NAMES = ("validated_success_retention", "admission_precision",
              "admission_recall", "incorrect_clean_admission_rate",
              "poison_admission_rate", "correct_but_poisoned_admission_rate",
              "manual_review_rate", "failure_capture_rate")


def metric_rows(pop: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`metric_block` flattened for CSV: one column per rate component."""
    rows = []
    for rec in metric_block(pop):
        flat: Dict[str, Any] = {"rule": rec["rule"]}
        for name in RATE_NAMES:
            v = rec.get(name) or {}
            flat[f"{name}_numerator"] = v.get("numerator")
            flat[f"{name}_denominator"] = v.get("denominator")
            flat[f"{name}_rate"] = v.get("rate")
            flat[f"{name}_ci95_lo"] = (v.get("ci95") or [None, None])[0]
            flat[f"{name}_ci95_hi"] = (v.get("ci95") or [None, None])[1]
        flat["note"] = rec.get("note", "")
        rows.append(flat)
    return rows


def _results_rows(ic: Dict[str, Any], natural: Dict[str, Any],
                  twins: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Per-candidate rows for both populations, in one file.

    `twins` here is the normalised row list built above, so its keys are the
    `*_admits_success` columns rather than a candidate's fields.
    """
    out = []
    for rule in BASELINES:
        out.append({"population": "incorrect_clean", "rule": rule,
                    **ic["by_rule"][rule]})
    for t in (twins or []):
        out.append({"population": "correct_but_poisoned",
                    "rule": t.get("poison_kind"),
                    "case": t.get("case"),
                    **{r: t.get(f"{r}_admits_success") for r in BASELINES}})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    r = run(args.out)
    print(json.dumps({k: v for k, v in r.items()
                      if k != "positive_class_metrics"}, ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
