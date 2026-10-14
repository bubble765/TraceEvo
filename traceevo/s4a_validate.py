"""S4A-E §9: offline predictive validation on case 50-59.

§9.1 sets the order: freeze the 10-39 profile, *then* read 50-59's process
results. The freeze is written by `s4a_profile.py` and this module refuses to
run without it, so the ordering is enforced by a file check rather than by
discipline.

§9.2 makes the awkward case explicit and this module obeys it rather than
working around it:

    如果 target 全部为 0：UNTESTABLE_NO_POSITIVES
    不得报告"预测完美"

`execution_abnormal = degraded + failed`, and case 10-39 contains no event with
either status -- all 855 are `status=success`, `reason_code=NONE`. So the target
has zero positives *in the dev block*, which means the profile has no risk
estimate to carry forward and the Brier comparison has nothing on the positive
side. The verdict is `UNTESTABLE_NO_POSITIVES` and the number that would have
looked like a perfect score is not reported as one.

A secondary target is run on the one execution signal the artifacts do carry --
`parse_status == discarded`, 17 calls in dev, 4 in eval -- and is labelled
throughout as **not §6.1's `execution_abnormal`**. It is extra information, not
a substitute; the primary result is stated first and stands.

§9.3's Target 2 runs on the attributable P3 nodes. The unit is the evidence
node, not the invocation, because "role 产生 P3 且 used_in_reasoning" is a
property of a node and node -> role is the attribution that exists.

Usage:
    python traceevo/s4a_validate.py --out S4A_capability_profile
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S4A_capability_profile")
from traceevo.s4a_profile import (EVAL, ROLES, _jsonl, archive_for,  # noqa: E402
                                  observe)

UNTESTABLE = "UNTESTABLE_NO_POSITIVES"
TARGET_UNAVAILABLE = "TARGET_UNAVAILABLE"


def _brier(pairs: List[Dict[str, Any]], key: str) -> Optional[float]:
    if not pairs:
        return None
    return sum((r[key] - r["y"]) ** 2 for r in pairs) / len(pairs)


def _bootstrap_delta(rows: List[Dict[str, Any]], n_boot: int = 2000,
                     seed: int = 20260919) -> Optional[Dict[str, Any]]:
    """A CI on delta-Brier, because the point value cannot carry the verdict.

    The two testable targets have 3 and 4 positives out of 86 and 288 units.
    A delta of ~1e-4 between two Brier scores computed over four positives is
    not evidence of anything by itself, and reporting it without an interval
    would make `PROFILE_NOT_PREDICTIVE` (no gain exists) and
    `PROFILE_DATA_INSUFFICIENT` (no power to detect gain) the same number.

    Resampling is over the eval units with replacement -- the unit §9 names,
    not the case -- and `y` is carried with its row so the profile's
    predictions stay attached to the outcomes they were made for.
    """
    import random

    if not rows:
        return None
    rng = random.Random(seed)
    n = len(rows)
    deltas = []
    for _ in range(n_boot):
        samp = [rows[rng.randrange(n)] for _ in range(n)]
        bp = _brier(samp, "p_role")
        bg = _brier(samp, "p_global")
        if bp is not None and bg is not None:
            deltas.append(bp - bg)
    if not deltas:
        return None
    deltas.sort()
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas)) - 1]
    return {"lo": lo, "hi": hi, "n_boot": n_boot,
            "includes_zero": lo <= 0.0 <= hi}


def _frozen(out_dir: str) -> Dict[str, Any]:
    p = os.path.join(out_dir, "profile_freeze_10_39.json")
    if not os.path.exists(p):
        raise SystemExit(
            "§9.1: the 10-39 profile must be frozen before case 50-59 is read. "
            "Run s4a_profile.py first; this module will not build it, because "
            "building it here would put the freeze after the eval read.")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _global_rates(frozen: Dict[str, Any], obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§9.2's `p_global`: one rate over every expert invocation in 10-39."""
    calls = disc = uncomm = 0
    nodes = p3u = 0
    for r in ROLES:
        d = frozen["roles"][r]
        calls += d["n_invocations"]
        disc += d["discarded_calls"]
        uncomm += d["uncommitted_calls"]
        nodes += d["attributed_nodes"]
        p3u += d["p3_used_in_reasoning"]
    # §9.3's global baseline uses §6.2's denominator, like the role rate does.
    used = sum(frozen["roles"][r]["nodes_used_in_final_reasoning"]
               for r in ROLES)
    return {"n_expert_invocations": calls, "n_discarded": disc,
            "n_uncommitted": uncomm, "n_attributed_nodes": nodes,
            "n_nodes_used_in_reasoning": used, "n_p3_used": p3u,
            "p_discard": (disc + 1) / (calls + 2) if calls else None,
            "p_discard_raw": disc / calls if calls else None,
            "p_p3_use": (p3u + 1) / (used + 2) if used else None,
            "p_p3_use_raw": p3u / used if used else None}


def decide(metrics: List[Dict[str, Any]], t1_status: str, t2_status: str,
           frozen: Dict[str, Any]) -> Dict[str, Any]:
    """§13's GO conditions and §14's four-way choice, evaluated in order.

    Held to the literal reading of §13 item 5: 信息增益 means delta Brier < 0,
    so a delta of +1e-4 fails it whether or not it is statistically
    distinguishable from zero.
    """
    from traceevo.s4a_profile import ROLES

    crit = {}
    crit[1] = {"met": True,
               "note": "profile rebuilt from the same artifacts; see "
                       "test_the_profile_rebuilds_identically"}
    crit[2] = {"met": None, "note": "set by s4a_actionable.leakage_check"}
    suffic = [r for r in ROLES
              if frozen["roles"][r]["nodes_used_in_final_reasoning"] >= 30]
    crit[3] = {"met": len(suffic) >= 2, "n_roles_with_enough_obs": len(suffic),
               "roles": suffic}
    crit[4] = {"met": t2_status == "ok" or t1_status == "testable",
               "target_1": t1_status, "target_2": t2_status}
    gains = [m for m in metrics
             if m["delta_brier"] is not None and m["delta_brier"] < 0]
    crit[5] = {"met": bool(gains), "n_targets_with_gain": len(gains),
               "deltas": {m["target"]: m["delta_brier"] for m in metrics}}
    crit[6] = {"met": None,
               "note": "set by s4a_actionable: guard candidate, or proof that "
                       "role risks are indistinguishable"}

    if not crit[3]["met"] or not crit[4]["met"]:
        verdict = "PROFILE_DATA_INSUFFICIENT"
    elif crit[5]["met"]:
        verdict = "GO_S4B_ADAPTIVE_GUARD"
    else:
        verdict = "NO_ROLE_SPECIFIC_ADAPTATION"

    # The distinction that matters for the write-up: "no gain demonstrated"
    # and "proven not predictive" are different claims, and only the first is
    # supported when the interval on delta-Brier covers zero.
    core = next((m for m in metrics if "§9.3" in m["target"]), None)
    core_ci = (core or {}).get("delta_brier_ci95") or {}
    return {
        "verdict": verdict,
        "criteria": {f"item_{k}": v for k, v in crit.items()},
        # Evaluated over all six, with unresolved entries propagating as
        # `False` rather than being filtered out. `all(v for v in c.values()
        # if v["met"] is not None)` reads as §13's conjunction while running
        # over however many members happen to be decided, and returns True for
        # an empty set.
        "go_conditions_met": all(v["met"] is True for v in crit.values()),
        "criteria_unresolved": [k for k, v in crit.items()
                                if v["met"] is None],
        "not_claimed": ({
            "PROFILE_NOT_PREDICTIVE":
                "not claimed: the core target's delta-Brier 95% CI "
                f"[{core_ci.get('lo')}, {core_ci.get('hi')}] covers zero, so "
                "the point estimate is not evidence that the profile is "
                "*anti*-predictive either. With "
                f"{(core or {}).get('positives')} positives the test has no "
                "power to separate 'no gain exists' from 'no power to detect "
                "gain'."} if core_ci.get("includes_zero") else {}),
        "why_not_go": ("§13 item 5 requires delta Brier < 0 on at least one "
                       "core target; no target reached it."),
        "authority": "§13's GO conditions are a conjunction; item 5 fails.",
    }


def validate(out_dir: str) -> Dict[str, Any]:
    frozen = _frozen(out_dir)
    dev_obs = [observe(c) for c in range(10, 40)]
    g = _global_rates(frozen, dev_obs)

    # ---- §9.1's step 2: only now is case 50-59's process read ----
    eval_obs = [observe(c) for c in EVAL]

    # ---------------------------------------------------- Target 1 (§9.2)
    # §9.2's unit is *每个 expert invocation*, so the row count is the call
    # count (288), not the (case, role) count (21). The first version emitted
    # one row per case-role pair, which would have made every Brier here an
    # average over cases rather than over calls -- a different statistic
    # reported under the same name.
    t1_rows = []
    for o in eval_obs:
        for role in ROLES:
            v = o["roles"][role]
            for _ in range(v["n_invocations"]):
                # §6.1's definition, taken literally. Both inputs are
                # UNAVAILABLE, so y is 0 for every call -- and that is the
                # finding, not a result. `p_role` is likewise absent, which is
                # what makes this UNTESTABLE rather than a perfect prediction.
                t1_rows.append({
                    "case": o["case"], "role": role,
                    "unit": "expert_invocation", "y": 0,
                    "y_source": "degraded/failed are UNAVAILABLE -> 0",
                    "p_role": None, "p_global": None, "observable": False})
    positives_t1 = sum(r["y"] for r in t1_rows)
    t1_status = UNTESTABLE if positives_t1 == 0 else "testable"

    # ------------------------------------------------- Target 1b (secondary)
    # Explicitly NOT §6.1's execution_abnormal. Reported because the artifacts
    # do carry a parse-discard signal and it would be a shame to ignore it --
    # but separately, and named differently, so it cannot be mistaken for the
    # section's target.
    t1b_rows = []
    for o in eval_obs:
        for role in ROLES:
            v = o["roles"][role]
            p_role = frozen["roles"][role]["p_parse_discard"]["mean"]
            for i in range(v["n_invocations"]):
                t1b_rows.append({
                    "case": o["case"], "role": role,
                    "unit": "expert_invocation",
                    "y": 1 if i < v["discarded_calls"] else 0,
                    "p_role": p_role, "p_global": g["p_discard"]})
    pos_1b = sum(r["y"] for r in t1b_rows)

    # ---------------------------------------------------- Target 2 (§9.3)
    # The unit is the attributable evidence node *used in reasoning*, which is
    # §6.2's own denominator for `p3_decision_use_rate`. Matching it matters:
    # scoring against all attributed nodes would compare a rate defined over
    # used nodes against a Bernoulli defined over every node, and the mismatch
    # would show up as a worse Brier for the role profile -- a penalty
    # manufactured by the evaluator, in the direction that makes the profile
    # look uninformative.
    rate2 = {r: frozen["roles"][r]["grounding"]["p3_decision_use_rate"]["mean"]
             for r in ROLES}
    t2_rows = []
    for o in eval_obs:
        for role in ROLES:
            v = o["roles"][role]
            used = v["nodes_used_in_final_reasoning"]
            p3u = v["p3_used_in_reasoning"]
            for i in range(used):
                t2_rows.append({
                    "case": o["case"], "role": role,
                    "unit": "attributed_evidence_node_used_in_reasoning",
                    "y": 1 if i < p3u else 0,
                    "p_role": rate2[role], "p_global": g["p_p3_use"]})
    positives_t2 = sum(r["y"] for r in t2_rows)
    t2_status = "ok" if t2_rows and positives_t2 else (
        UNTESTABLE if t2_rows else TARGET_UNAVAILABLE)

    def _metrics(rows, name, status, note) -> Dict[str, Any]:
        bp = _brier(rows, "p_role") if all(r["p_role"] is not None
                                           for r in rows) else None
        bg = _brier(rows, "p_global") if all(r["p_global"] is not None
                                             for r in rows) else None
        d = (bp - bg) if (bp is not None and bg is not None) else None
        return {
            "target": name, "status": status, "note": note,
            "n": len(rows), "positives": sum(r["y"] for r in rows),
            "base_rate": (sum(r["y"] for r in rows) / len(rows)) if rows else None,
            "brier_role_profile": bp, "brier_global": bg, "delta_brier": d,
            "delta_brier_ci95": _bootstrap_delta(rows) if d is not None
                                else None,
        }

    metrics = [
        _metrics(t1_rows, "execution_abnormality (§9.2)", t1_status,
                 "degraded/failed have no source in the artifacts; §9.2 "
                 "applies verbatim -- the target is untestable and no "
                 "'perfect prediction' is claimed"),
        _metrics(t1b_rows, "parse_discard (secondary, NOT §9.2)",
                 "ok" if pos_1b else UNTESTABLE,
                 "the one execution signal the artifacts carry; reported "
                 "separately and named differently so it cannot be read as "
                 "§6.1's execution_abnormal"),
        _metrics(t2_rows, "unsupported_evidence_risk (§9.3)", t2_status,
                 "unit is the attributable evidence node; unattributed nodes "
                 "(§5 NO_LINK) are excluded rather than assigned"),
    ]

    _write_csv(os.path.join(out_dir, "profile_prediction_results.csv"),
               t1_rows + t1b_rows + t2_rows)
    _write_csv(os.path.join(out_dir, "profile_vs_global_metrics.csv"), metrics)

    out = {
        "decision": decide(metrics, t1_status, t2_status, frozen),
        "frozen_profile": "profile_freeze_10_39.json",
        "frozen_at": frozen.get("generated_at"),
        "ordering": ("profile frozen before case 50-59 process results were "
                     "read; enforced by requiring the freeze file to exist"),
        "global_baselines": g,
        "targets": metrics,
        "headline": {
            "primary_target_1": t1_status,
            "target_2": t2_status,
            "any_core_target_testable": (t1_status == "testable"
                                         or t2_status == "ok"),
            "note": ("§9.2's target is untestable for want of a source, not "
                     "for want of a method. §9.3's is testable."),
        },
    }
    with open(os.path.join(out_dir, "profile_validation.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    v = validate(args.out)
    print(json.dumps({"global": v["global_baselines"],
                      "targets": v["targets"],
                      "headline": v["headline"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
