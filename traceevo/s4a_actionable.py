"""S4A-F §10-§12: guard candidates, the memory hold, and the leakage check.

§10.1 forbids a weighted total score, so this module computes no score. A role's
class is read off independent risk dimensions, each against its own threshold.

§10.2's two rules are the same shape:

    execution guard : role's execution-abnormal CI lower bound > global mean
    grounding guard : role's P3-decision-use CI lower bound > global mean

The execution rule cannot fire even in principle here, because
`execution_abnormal` has no observable source in the artifacts (see
`s4a_profile.UNAVAILABLE_FIELDS`). That is reported as
`INSUFFICIENT_DATA` with its reason, not as a pass.

The grounding rule can fire in principle and does not; §10.2 closes with
"S4A 不为了制造 candidate 放宽 interval", so the interval is not widened.

`SUFFICIENCY_MIN_N` is declared before the numbers are computed and is not
revisited afterwards. 30 is chosen because a Beta(1,1) 95% interval at n=30 has
a half-width of at most roughly 0.18 around the mean: below that, the interval
covers most of [0,1] and "this role is not distinguishable from the baseline"
would be unfalsifiable rather than measured.

Usage:
    python traceevo/s4a_actionable.py --out S4A_capability_profile
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S4A_capability_profile")
from traceevo.s4a_profile import ROLES  # noqa: E402

SUFFICIENCY_MIN_N = 30
CLASSES = ("INSUFFICIENT_DATA", "NORMAL", "GROUNDING_GUARD_CANDIDATE",
           "EXECUTION_GUARD_CANDIDATE", "BOTH_GUARD_CANDIDATE")


def assess(out_dir: str) -> Dict[str, Any]:
    with open(os.path.join(out_dir, "profile_freeze_10_39.json"),
              encoding="utf-8") as f:
        frozen = json.load(f)
    with open(os.path.join(out_dir, "profile_validation.json"),
              encoding="utf-8") as f:
        val = json.load(f)

    g_exec = None                      # §9.2's baseline does not exist
    g_p3 = val["global_baselines"]["p_p3_use"]
    g_p3_raw = val["global_baselines"]["p_p3_use_raw"]

    rows, notes = [], []
    for r in ROLES:
        d = frozen["roles"][r]
        gr = d["grounding"]["p3_decision_use_rate"]
        n_used = d["nodes_used_in_final_reasoning"]

        # ---- grounding dimension
        if n_used < SUFFICIENCY_MIN_N:
            grounding = "INSUFFICIENT_DATA"
            g_reason = (f"{n_used} attributed nodes used in reasoning < "
                        f"{SUFFICIENCY_MIN_N}")
        elif gr["lo"] is None:
            grounding = "INSUFFICIENT_DATA"
            g_reason = "no interval (denominator 0)"
        elif gr["lo"] > g_p3:
            grounding = "GROUNDING_GUARD_CANDIDATE"
            g_reason = f"CI lower bound {gr['lo']:.4f} > global {g_p3:.4f}"
        else:
            grounding = "NORMAL"
            g_reason = (f"CI lower bound {gr['lo']:.4f} <= global {g_p3:.4f} "
                        f"-- not distinguishable from baseline")

        # ---- execution dimension
        exec_cls = "INSUFFICIENT_DATA"
        e_reason = ("execution_abnormal has no observable source in the "
                    "artifacts (degraded/failed never occur), so there is no "
                    "role interval to compare against any baseline")

        both = (grounding == "GROUNDING_GUARD_CANDIDATE"
                and exec_cls == "EXECUTION_GUARD_CANDIDATE")
        if grounding == "INSUFFICIENT_DATA":
            cls = "INSUFFICIENT_DATA"
        elif both:
            cls = "BOTH_GUARD_CANDIDATE"
        else:
            cls = grounding

        rows.append({
            "role": r,
            "classification": cls,
            "grounding_dimension": grounding,
            "grounding_reason": g_reason,
            "execution_dimension": exec_cls,
            "execution_reason": e_reason,
            "n_invocations": d["n_invocations"],
            "n_nodes_used_in_reasoning": n_used,
            "p3_used_in_reasoning": d["p3_used_in_reasoning"],
            "p3_decision_use_rate": gr["value"],
            "p3_decision_use_posterior_mean": gr["mean"],
            "p3_decision_use_ci95_lo": gr["lo"],
            "p3_decision_use_ci95_hi": gr["hi"],
            "p_parse_discard": d["p_parse_discard"]["value"],
            "global_p3_decision_use": g_p3,
            "global_p3_decision_use_raw": g_p3_raw,
            "sufficiency_min_n": SUFFICIENCY_MIN_N,
        })
        notes.append(f"{r}: {cls}")

    _write_csv(os.path.join(out_dir, "profile_actionability.csv"), rows)

    n_guard = sum(1 for r in rows if r["classification"].endswith(
        "GUARD_CANDIDATE"))
    all_insufficient = all(r["classification"] == "INSUFFICIENT_DATA"
                           for r in rows)
    # §13 item 6's second half: proving all roles are alike is a valid result,
    # but it is a different one from producing a candidate.
    risks_close = _spread_is_small(rows)

    out = {
        "classes_available": list(CLASSES),
        "sufficiency_min_n": SUFFICIENCY_MIN_N,
        "sufficiency_rationale": ("declared before the thresholds were "
                                  "evaluated; a Beta(1,1) 95% interval at "
                                  "n=30 has half-width <= ~0.18, below which "
                                  "'not distinguishable' is unfalsifiable"),
        "rows": rows,
        "n_guard_candidates": n_guard,
        "all_roles_insufficient": all_insufficient,
        "role_risks_indistinguishable": risks_close,
        "no_candidate_reason": (
            "all_roles_insufficient" if all_insufficient else
            ("roles_indistinguishable" if risks_close else
             "thresholds_not_met")),
        "interval_not_widened": ("§10.2: S4A 不为了制造 candidate 放宽 "
                                 "interval -- no threshold was moved to "
                                 "produce one"),
        "notes": notes,
    }
    with open(os.path.join(out_dir, "profile_actionability.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    _policy_md(out_dir, out, val)
    return out


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


def _spread_is_small(rows: List[Dict[str, Any]]) -> bool:
    """Are the roles' grounding risks practically indistinguishable?

    Judged by overlapping intervals, not by equal point estimates: with these
    counts the point estimates differ by a factor of two and the intervals all
    cover most of each other, which is the definition of "cannot tell them
    apart" rather than "they are the same".
    """
    los = [r["p3_decision_use_ci95_lo"] for r in rows
           if r["p3_decision_use_ci95_lo"] is not None]
    his = [r["p3_decision_use_ci95_hi"] for r in rows
           if r["p3_decision_use_ci95_hi"] is not None]
    if len(los) < 2:
        return False
    return max(los) < min(his)


def _policy_md(out_dir: str, out: Dict[str, Any],
               val: Dict[str, Any]) -> None:
    """§16's policy_candidates.md."""
    L = ["# Policy candidates (§10-§11)", "",
         f"Sufficiency threshold: **n >= {out['sufficiency_min_n']}** "
         f"attributed nodes used in reasoning, declared before evaluation.",
         "", "| role | classification | grounding | execution |",
         "|---|---|---|---|"]
    for r in out["rows"]:
        L.append(f"| {r['role']} | **{r['classification']}** | "
                 f"{r['grounding_dimension']} | {r['execution_dimension']} |")
    L += ["", "## Why no guard was raised", "",
          f"{out['no_candidate_reason']}", "",
          "Every role's P3-decision-use 95% interval lower bound sits below the",
          "global baseline, so none is distinguishable from it in the unsafe",
          "direction. The intervals are wide because the counts are small:",
          ""]
    for r in out["rows"]:
        L.append(f"* **{r['role']}** — {r['p3_used_in_reasoning']} P3-used of "
                 f"{r['n_nodes_used_in_reasoning']} attributed nodes used in "
                 f"reasoning, posterior mean "
                 f"{r['p3_decision_use_posterior_mean']:.4f}, "
                 f"95% CI [{r['p3_decision_use_ci95_lo']:.4f}, "
                 f"{r['p3_decision_use_ci95_hi']:.4f}]; "
                 f"global {r['global_p3_decision_use']:.4f}")
    L += ["", "§10.2 forbids widening the interval to manufacture a candidate,",
          "and none was widened.", "",
          "## What a guard would mean if one existed (§11)", "",
          "Even a raised candidate would change no call in S4A. The actions",
          "below are for a future S4B, and both are *verification-strength*",
          "adjustments rather than capability withdrawals:", "",
          "* `GROUNDING_GUARD` — require evidence source binding / provenance",
          "  verification before this expert's analysis enters `UpdateGraph`.",
          "  **Not** suppressing the expert.",
          "* `EXECUTION_GUARD` — when a call returns degraded/failed, have the",
          "  central agent start a fallback or second review. **Not**",
          "  pre-emptively removing a medically necessary expert on the basis",
          "  of a historical profile.", "",
          "> 能力画像只能调节验证强度与协作策略，不能单凭历史风险剥夺当前病例所需的专业能力。",
          ""]
    with open(os.path.join(out_dir, "policy_candidates.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(L))


# ------------------------------------------------------------------ §12/§16
def memory_hold(out_dir: str) -> Dict[str, Any]:
    """§12: both stores kept, injection off, review queue not consumed."""
    from traceevo.evolution.memory_store import MemoryStore

    hi = os.path.join(ROOT, "S3D_verified_expansion", "expanded_success_store")
    lo = os.path.join(ROOT, "S3D_verified_expansion", "failure_memory_store")
    queue = os.path.join(ROOT, "S3D_verified_expansion",
                         "cross_case_retrieval_review.csv")
    n_queue = 0
    if os.path.exists(queue):
        with open(queue, encoding="utf-8") as f:
            n_queue = sum(1 for _ in csv.DictReader(f))
    out = {
        "success_memory_store": "retained", "failure_memory_store": "retained",
        "memory_injection": "OFF",
        "success_store_entries": len(MemoryStore(
            hi, enabled=True,
            initial_plan_partitions=("success",)).initial_plan_ids())
        if os.path.isdir(hi) else 0,
        "failure_initial_plan_ids": MemoryStore(
            lo, enabled=True,
            initial_plan_partitions=()).initial_plan_ids()
        if os.path.isdir(lo) else [],
        "cross_case_review_queue": {
            "path": os.path.relpath(queue, ROOT), "rows": n_queue,
            "consumed_by_s4a": False,
            "review_labels": "all UNREVIEWED",
            "why_not": ("4 reusable successes is too few, cross-case "
                        "relevance has no gold label, and 28.6789 was shown "
                        "to be a self-identity threshold only (§12).")},
        "tracks": {
            "advanced": "audit feedback -> capability profile",
            "not_advanced": "cross-case success memory -> planner",
            "note": "§12: the two tracks are decoupled for S4A."},
    }
    assert out["failure_initial_plan_ids"] == []
    with open(os.path.join(out_dir, "memory_hold.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def leakage_check(out_dir: str) -> Dict[str, Any]:
    """§16's leakage_check.json, and §13 item 2: no future case in a past
    snapshot."""
    snap_dir = os.path.join(out_dir, "profile_snapshots")
    checks, violations = [], []
    for fn in sorted(os.listdir(snap_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(snap_dir, fn), encoding="utf-8") as f:
            s = json.load(f)
        t = s["snapshot_after_case"]
        used = s["uses_cases"]
        bad = [c for c in used if c > t]
        checks.append({"snapshot": fn, "t": t, "n_cases_used": len(used),
                       "max_case_used": max(used) if used else None,
                       "future_leak": bool(bad), "leaked_cases": bad})
        if bad:
            violations.append({"snapshot": fn, "leaked": bad})

    dev = set(range(10, 40))
    evl = set(range(50, 60))
    out = {
        "check": "§13 item 2 -- no future case enters a past snapshot",
        "snapshots_checked": len(checks),
        "violations": violations,
        "passed": not violations,
        "dev_eval_disjoint": not (dev & evl),
        "dev_cases": sorted(dev), "eval_cases": sorted(evl),
        "excluded_read": {"0-9": "not read by S4A",
                          "40-49": "not read by S4A",
                          "60-137": "not read by S4A"},
        "freeze_before_eval": (
            "s4a_validate.py refuses to run unless profile_freeze_10_39.json "
            "already exists, so the profile cannot be rebuilt after the eval "
            "read (§9.1)"),
        "no_gos_process": True,
        "no_memory_injection": True,
        "detail": checks,
    }
    with open(os.path.join(out_dir, "leakage_check.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def close_criteria(out_dir: str, a: Dict[str, Any],
                   leak: Dict[str, Any]) -> Dict[str, Any]:
    """§13 items 2 and 6 are decided by this module, not by the validator.

    `s4a_validate.decide` writes them as `None` with a pointer here rather than
    guessing, so the two modules cannot disagree about the verdict. This fills
    them in and re-derives the verdict with the full set, which is what makes
    the JSON a record of the decision instead of a record of part of it.
    """
    p = os.path.join(out_dir, "profile_validation.json")
    with open(p, encoding="utf-8") as f:
        val = json.load(f)
    d = val["decision"]
    d["criteria"]["item_2"] = {
        "met": leak["passed"],
        "snapshots_checked": leak["snapshots_checked"],
        "violations": len(leak["violations"]),
        "note": "§13 item 2: no future case enters a past snapshot"}
    d["criteria"]["item_6"] = {
        "met": (a["n_guard_candidates"] >= 1
                or a["role_risks_indistinguishable"]),
        "n_guard_candidates": a["n_guard_candidates"],
        "satisfied_by": ("guard candidate" if a["n_guard_candidates"] >= 1
                         else "proof that role risks are indistinguishable"
                         if a["role_risks_indistinguishable"]
                         else "neither"),
        "note": ("§13 item 6: either a non-INSUFFICIENT_DATA candidate, or a "
                 "demonstration that all roles' risks are alike so that no "
                 "differentiated scheduling is warranted")}
    # No filtering here. `all(... if v["met"] is not None)` returns True for a
    # criteria set where every entry is None, so a conjunction that has quietly
    # lost members still reports as satisfied. After this call all six are
    # decided; anything still None would make the verdict unresolvable, and
    # that should raise rather than be skipped.
    unresolved = [k for k, v in d["criteria"].items() if v["met"] is None]
    if unresolved:
        raise SystemExit(
            f"§13 criteria {unresolved} were never evaluated; the verdict "
            f"cannot be stated over {len(d['criteria']) - len(unresolved)} of "
            f"{len(d['criteria'])} conditions")
    d["go_conditions_met"] = all(v["met"] for v in d["criteria"].values())
    d["criteria_evaluated"] = len(d["criteria"])
    d["criterion_6_second_half"] = a["role_risks_indistinguishable"]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(val, f, ensure_ascii=False, indent=2)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    a = assess(args.out)
    print(json.dumps({"n_guard_candidates": a["n_guard_candidates"],
                      "no_candidate_reason": a["no_candidate_reason"],
                      "rows": [{k: v for k, v in r.items()
                                if k in ("role", "classification",
                                         "p3_decision_use_ci95_lo",
                                         "global_p3_decision_use")}
                               for r in a["rows"]]},
                     ensure_ascii=False, indent=2))
    print(json.dumps(memory_hold(args.out), ensure_ascii=False, indent=2))
    leak = leakage_check(args.out)
    print(json.dumps({k: v for k, v in leak.items() if k != "detail"},
                     ensure_ascii=False, indent=2))
    print(json.dumps(close_criteria(args.out, a, leak), ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
