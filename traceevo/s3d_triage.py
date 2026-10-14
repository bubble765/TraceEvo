"""S3D-C/D §5-§6: the six-condition gate, and the headline count.

§5 fixes six conditions and says not to change them. Five are evaluated here;
the sixth, `verified_success`, is the filter that decides which cases reach
them. They are reported as six separate booleans rather than one, so a case
that fails is named by its failing condition instead of disappearing into a
count — S3C's `case-22` is the reason, and it is the reason §6 says a
verified-success-but-unsafe case is a case study and not a success.

§6 then adds up gold-safe successes over case 10-29, 30-39 and 50-59 and
nothing else. `case 0-9 legacy` and `case 40-49 probe` are not in the sum: the
first by §2's split, the second because §6 names only the three blocks. The
count of 4 that S3C produced for 10-39 is read back from S3C's own triage
rather than recomputed here, because recomputing it would be a second
implementation of a frozen rule and the two could disagree.

Usage:
    python traceevo/s3d_triage.py --out S3D_verified_expansion
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
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S3D_verified_expansion")
TAG = "s3d_expansion"
S3C = os.path.join(ROOT, "S3C_experience_expansion")
MIN_HEADLINE = 5

CONDITIONS = ("verified_success", "integrity_ok", "invocation_complete",
              "semantic_trace_complete", "no_critical_anomaly",
              "no_unresolved_decision_path_p3")


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _n_eligible(out_dir: str) -> int:
    """How many of the new gold-safe successes the frozen Gate actually admits."""
    p = os.path.join(out_dir, "expanded_store_admission.csv")
    if not os.path.exists(p):
        return 0
    with open(p, encoding="utf-8") as f:
        return sum(1 for r in csv.DictReader(f)
                   if r.get("headline_admitted") == "True")


def s3c_headline() -> Dict[str, Any]:
    """The 10-29 and 30-39 halves of §6, read from S3C's frozen triage."""
    with open(os.path.join(S3C, "triage_summary.json"), encoding="utf-8") as f:
        t = json.load(f)
    return {
        "10-29": {"n_cases": t["per_tier"]["A"]["n_cases"],
                  "n_verified_success": t["per_tier"]["A"]["n_verified_success"],
                  "gold_safe_cases": t["per_tier"]["A"]["gold_safe_cases"],
                  "n_gold_safe": t["per_tier"]["A"]["n_gold_safe"]},
        "30-39": {"n_cases": t["per_tier"]["B"]["n_cases"],
                  "n_verified_success": t["per_tier"]["B"]["n_verified_success"],
                  "gold_safe_cases": t["per_tier"]["B"]["gold_safe_cases"],
                  "n_gold_safe": t["per_tier"]["B"]["n_gold_safe"]},
    }


def triage(out_dir: str) -> Dict[str, Any]:
    from traceevo.s3c_expand import gold_safe_conditions

    artifacts = os.path.join(out_dir, "runs_50_59", "traceevo", "runs", TAG)
    with open(os.path.join(out_dir, "outcome_results_50_59.csv"),
              encoding="utf-8") as f:
        outcomes = list(csv.DictReader(f))

    counts = collections.Counter(r["outcome_verdict"] for r in outcomes)
    rows, unsafe_studies, gold_safe = [], [], []
    for r in outcomes:
        c = int(r["case"])
        v = r["outcome_verdict"]
        cond = {k: False for k in CONDITIONS}
        cond["verified_success"] = (v == "verified_success")
        if not cond["verified_success"]:
            continue
        g = gold_safe_conditions(artifacts, f"case-{c}")
        cond.update({k: bool(val) for k, val in g["conditions"].items()})
        safe = all(cond.values())
        rows.append({
            "case": c, "outcome_verdict": v, "gold_safe": safe,
            "failed_conditions": "|".join(k for k in CONDITIONS if not cond[k]),
            **{k: cond[k] for k in CONDITIONS},
            "n_anomalies": g["n_anomalies"],
            "anomaly_types": "|".join(g["anomaly_types"]),
            "n_used_p3": g["n_used_p3"],
            "judge_scores": r["judge_scores"],
            "score_shape": r["score_shape"],
        })
        if safe:
            gold_safe.append(c)
        else:
            # §5: a verified success with an unsafe process is a case study.
            unsafe_studies.append({
                "case": c, "failed_conditions": rows[-1]["failed_conditions"],
                "n_anomalies": g["n_anomalies"],
                "anomaly_types": g["anomaly_types"],
                "n_used_p3": g["n_used_p3"],
                "judge_scores": r["judge_scores"],
                "same_shape_as_s3c_case_22": bool(
                    "no_unresolved_decision_path_p3"
                    in rows[-1]["failed_conditions"]),
            })

    _write_csv(os.path.join(out_dir, "gold_safe_triage_50_59.csv"), rows)
    with open(os.path.join(out_dir, "verified_success_but_unsafe_50_59.json"),
              "w", encoding="utf-8") as f:
        json.dump(unsafe_studies, f, ensure_ascii=False, indent=2)

    prior = s3c_headline()
    prior_total = prior["10-29"]["n_gold_safe"] + prior["30-39"]["n_gold_safe"]
    headline = prior_total + len(gold_safe)

    # §6 counts gold-safe successes; §7 can only store ones the Gate admits.
    # Those are different numbers whenever a case is gold-safe and not
    # eligible, and on this block they are 5 and 4. Reporting only the first
    # would be reporting the number §6 asks for as though it were the corpus.
    n_new_eligible = _n_eligible(out_dir)
    eligible_total = prior_total + n_new_eligible

    if headline < MIN_HEADLINE:
        verdict = "VERIFIED_SUCCESS_SCARCITY_PERSISTS"
    elif eligible_total < headline:
        # The count passed and the corpus did not grow. Neither of §15's
        # named verdicts describes that, so this does not claim either.
        verdict = "NEEDS_GATE_SCOPE_DECISION"
    else:
        verdict = "GO_S4A_CANDIDATE"

    summary = {
        "block_50_59": {
            "n_cases": len(outcomes),
            "verdict_counts": dict(counts),
            "n_verified_success": counts.get("verified_success", 0),
            "gold_safe_cases": gold_safe,
            "n_gold_safe": len(gold_safe),
            "n_verified_success_but_unsafe": len(unsafe_studies),
            "unsafe_cases": [u["case"] for u in unsafe_studies],
        },
        "headline": {
            "blocks": ["10-29", "30-39", "50-59"],
            "excluded": {"0-9": "legacy_dev, headline_eligible=false (§2)",
                         "40-49": "behavior probe, not in §6's sum"},
            "per_block": {**prior, "50-59": {
                "n_cases": len(outcomes),
                "n_verified_success": counts.get("verified_success", 0),
                "gold_safe_cases": gold_safe, "n_gold_safe": len(gold_safe)}},
            "headline_gold_safe": headline,
            "headline_eligible": eligible_total,
            "headline_gold_safe_but_not_eligible": headline - eligible_total,
            "requires": MIN_HEADLINE,
            "met": headline >= MIN_HEADLINE,
            "eligible_met": eligible_total >= MIN_HEADLINE,
            "note": ("§6 counts gold-safe successes (the number above); §7 can "
                     "only store ones the frozen Gate admits. They differ by "
                     "the cases that are gold-safe and not eligible, and the "
                     "difference is the corpus, not the headline."),
        },
        "decision": verdict,
        "decision_note": (
            "NEEDS_GATE_SCOPE_DECISION is not one of §15's three options. It "
            "is used because the count reached 5 while the store gained "
            "nothing: VERIFIED_SUCCESS_SCARCITY_PERSISTS would misdescribe a "
            "count of 5, and GO_S4A would misdescribe a store with no new "
            "entry in it.") if verdict == "NEEDS_GATE_SCOPE_DECISION" else "",
        "stop_condition": ("§6: headline_gold_safe < 5 -> stop and return "
                           "VERIFIED_SUCCESS_SCARCITY_PERSISTS; do not consume "
                           "case 60+."),
    }
    with open(os.path.join(out_dir, "headline_success_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # One row per case, uniform shape. The first version wrote a case row and
    # then a totals row with extra keys, and `DictWriter` rejected it -- the
    # wide-row-per-block shape is what summary.json is for.
    rows_out = []
    for block, d in summary["headline"]["per_block"].items():
        for c in d["gold_safe_cases"]:
            rows_out.append({
                "block": block, "case": c, "gold_safe": True,
                "headline_eligible": block != "0-9",
                "n_cases": d["n_cases"],
                "n_verified_success": d["n_verified_success"],
                "n_gold_safe": d["n_gold_safe"]})
    _write_csv(os.path.join(out_dir, "headline_success_summary.csv"), rows_out)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    s = triage(args.out)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
