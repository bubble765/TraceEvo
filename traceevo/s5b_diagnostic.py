"""S5B-D §6.1/§6.3: held-out diagnostic metrics and clean alert rates.

§6.1 keeps two different things apart and forbids merging them:

    GoS-style Match / Relevant      the frozen evaluator's scores
    Experience Outcome categories   verified_success / incorrect / uncertain

They answer different questions, and §6.1 says in as many words that
`verified_incorrect` must not be quoted as `Match = 0`.

The Match/Relevant half does not reimplement the metric. It imports
`build_group_prompt` and `parse_scores` from the frozen
`gos/evaluate_Medical_scoring.py` and drives them, so the prompt the judge
sees is constructed by the same code that produced every earlier stage's
number. A second implementation here would be free to disagree with the first
about what a group is.

§16 fixes the statistics: case-level bootstrap, 10,000 resamples, 95%,
seed 20260920. §6.3 fixes the clean alert interval as Wilson.

Usage:
    python traceevo/s5b_diagnostic.py --out S5B_final_heldout
    python traceevo/s5b_diagnostic.py --skip-judge
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
SEED = 20260920
BOOTSTRAP = 10000
GROUP_SIZE = 5


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


def wilson(k: int, n: int, z: float = 1.959963984540054) -> List[Optional[float]]:
    """§16's Wilson 95% interval. Returns [lo, hi], or [None, None] if n=0.

    §16 forbids reporting a bare 0.0 / 1.0 without its denominator, and Wilson
    is the reason: at k=0, n=10 the upper bound is 0.28, not 0.
    """
    if n <= 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, centre - half), 6), round(min(1.0, centre + half), 6)]


def bootstrap_mean(values: List[float], seed: int = SEED,
                   n: int = BOOTSTRAP) -> Dict[str, Any]:
    """§16's case-level bootstrap. The sampling unit is the case."""
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "ci95": [None, None]}
    rng = random.Random(seed)
    m = len(clean)
    means = []
    for _ in range(n):
        s = 0.0
        for _ in range(m):
            s += clean[rng.randrange(m)]
        means.append(s / m)
    means.sort()
    lo = means[int(0.025 * n)]
    hi = means[min(n - 1, int(0.975 * n))]
    return {"n": m, "mean": round(sum(clean) / m, 6),
            "ci95": [round(lo, 6), round(hi, 6)],
            "method": (f"case-level bootstrap, {n} resamples, 95%, seed "
                       f"{seed} (§16)")}


def _json(p: str, default: Any = None) -> Any:
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ §6.1
def diagnostic(out_dir: str, skip_judge: bool = False) -> Dict[str, Any]:
    """Match / Relevant on the held-out block, plus the outcome categories."""
    from traceevo.evolution.blinding import gold_by_case_index

    outcomes = list(csv.DictReader(
        open(os.path.join(out_dir, "outcome_results.csv"), encoding="utf-8")))
    by_case = {int(r["case"]): r for r in outcomes}
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    cases = sorted(by_case)

    rows = []
    if skip_judge:
        rows = [{"case": c, "judge_score": None, "prediction":
                 by_case[c]["prediction"]} for c in cases]
    else:
        rows = _run_match_judge(out_dir, cases, by_case, gold)
    _write_csv(os.path.join(out_dir, "diagnostic_metrics.csv"), rows)

    scores = [r["judge_score"] for r in rows]
    match = [1.0 if s == 2 else 0.0 for s in scores if s is not None]
    relevant = [1.0 if s is not None and s >= 1 else 0.0
                for s in scores if s is not None]
    dist = {str(k): sum(1 for s in scores if s == k) for k in (0, 1, 2)}
    dist["unparsed"] = sum(1 for s in scores if s is None)

    counts: Dict[str, int] = {}
    for r in outcomes:
        counts[r["outcome_verdict"]] = counts.get(r["outcome_verdict"], 0) + 1

    out = {
        "n_cases": len(rows),
        "n_scored": len(match),
        "Match": bootstrap_mean(match),
        "Relevant": bootstrap_mean(relevant),
        "score_distribution": dist,
        "case_level": [{"case": r["case"], "judge_score": r["judge_score"]}
                       for r in rows],
        "experience_outcomes": counts,
        "separate_metric_warning": (
            "§6.1: `verified_incorrect` is NOT GoS `Match = 0`. The Outcome "
            "Verifier checks whether the prediction is the reference "
            "diagnosis; the Match evaluator scores exact / broad-category / "
            "neither, and the two judges are different models with different "
            "prompts. The counters above are reported next to each other and "
            "are not derived from each other."),
        "evaluator": ("frozen `gos/evaluate_Medical_scoring.py` — its "
                      "`build_group_prompt` and `parse_scores` are imported "
                      "and driven, not reimplemented"),
    }
    with open(os.path.join(out_dir, "diagnostic_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _run_match_judge(out_dir: str, cases: List[int], by_case: Dict[int, Any],
                     gold: Dict[int, Any]) -> List[Dict[str, Any]]:
    """Drive the frozen evaluator over the held-out block."""
    import importlib.util
    from traceevo.s2_cases import load_env_file
    from utils.public_function import llm_generate_response, \
        default_model_path

    load_env_file()
    spec = importlib.util.spec_from_file_location(
        "frozen_medical_scoring",
        os.path.join(GOS, "evaluate_Medical_scoring.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    model = default_model_path()
    raw_path = os.path.join(out_dir, "match_judge_raw.jsonl")
    per_case: Dict[int, Optional[int]] = {}
    with open(raw_path, "w", encoding="utf-8") as raw_f:
        for start in range(0, len(cases), GROUP_SIZE):
            chunk = cases[start:start + GROUP_SIZE]
            combined = [f"{by_case[c]['prediction']}, " for c in chunk]
            answers = [gold.get(c, {}).get("final_diagnosis", "") for c in chunk]
            # `end` is the group's last *position*, which is what the frozen
            # prompt builder indexes its "Cases {start+1} to {end}" label
            # with. Positions here are positions in the held-out block.
            user_prompt = mod.build_group_prompt(combined, answers, start,
                                                 start + len(chunk))
            rec = {"group": start // GROUP_SIZE + 1,
                   "cases": chunk, "judge_model": model,
                   "system_prompt": mod.JUDGE_SYSTEM_PROMPT,
                   "user_prompt": user_prompt}
            try:
                response, meta = llm_generate_response(
                    user_prompt=user_prompt, model_path=model,
                    temperature=1.0, max_tokens=8192,
                    system_prompt=mod.JUDGE_SYSTEM_PROMPT, return_meta=True)
                rec["response"] = response
                rec["usage"] = {k: meta.get(k) for k in
                                ("prompt_tokens", "completion_tokens",
                                 "total_tokens")}
                parsed = mod.parse_scores(response, len(chunk), start,
                                          len(cases))
                rec["parsed_scores"] = {str(k): v for k, v in parsed.items()}
                for pos, score in parsed.items():
                    per_case[cases[pos - 1]] = score
            except Exception as exc:              # keep going, unparsed drops
                rec["error"] = f"{type(exc).__name__}: {exc}"
            raw_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"  match judge group {rec['group']} "
                  f"cases {chunk[0]}-{chunk[-1]}: "
                  f"{rec.get('parsed_scores', rec.get('error'))}", flush=True)
    return [{"case": c, "judge_score": per_case.get(c),
             "prediction": by_case[c]["prediction"],
             "reference_sha256": by_case[c].get("reference_sha256", "")}
            for c in cases]


# ------------------------------------------------------------- §6.3 clean alert
def clean_alerts(out_dir: str) -> Dict[str, Any]:
    """§6.2/§6.3's clean alert rates, with Wilson 95% intervals.

    §6.3 is explicit that a real audit finding in a natural trace still counts
    as an alert: it is not to be removed from the denominator on the grounds
    that it might be a genuine problem. The denominator is therefore every
    held-out case, and the point estimate is reported with its numerator.
    """
    rows = list(csv.DictReader(open(
        os.path.join(out_dir, "natural_audit_summary.csv"), encoding="utf-8")))
    # §6.3's evidence alert: a clean trace carrying an evidence-tier finding
    # (A1 unsupported evidence / A3 invalid capability) — a statement about
    # nodes rather than about the trace as a whole.
    n = len(rows)
    trace_hits = [r for r in rows if int(r["audit_alerts"] or 0) > 0]
    ev_hits = [r for r in rows
               if {"A1", "A3"} & set((r.get("anomaly_types") or "").split("|"))]
    types: Dict[str, int] = {}
    for r in rows:
        for t in (r.get("anomaly_types") or "").split("|"):
            if t:
                types[t] = types.get(t, 0) + 1
    out = {
        "n_cases": n,
        "clean_trace_alert": {
            "numerator": len(trace_hits), "denominator": n,
            "rate": round(len(trace_hits) / n, 6) if n else None,
            "wilson95": wilson(len(trace_hits), n),
            "cases": [int(r["case"]) for r in trace_hits],
        },
        "clean_evidence_alert": {
            "numerator": len(ev_hits), "denominator": n,
            "rate": round(len(ev_hits) / n, 6) if n else None,
            "wilson95": wilson(len(ev_hits), n),
            "cases": [int(r["case"]) for r in ev_hits],
            "definition": ("a clean trace carrying an A1 or A3 evidence-tier "
                           "finding"),
        },
        "detected_type_counts": types,
        "denominator_note": (
            "§6.3: a real audit finding on a natural trace still counts as an "
            "alert. Nothing was removed from the denominator for looking like "
            "a genuine problem."),
        "interval": "Wilson 95% (§16)",
    }
    with open(os.path.join(out_dir, "clean_alert_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args()
    d = diagnostic(args.out, args.skip_judge)
    c = clean_alerts(args.out)
    print(json.dumps({"Match": d["Match"], "Relevant": d["Relevant"],
                      "score_distribution": d["score_distribution"],
                      "experience_outcomes": d["experience_outcomes"],
                      "clean_trace_alert": c["clean_trace_alert"],
                      "clean_evidence_alert": c["clean_evidence_alert"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
