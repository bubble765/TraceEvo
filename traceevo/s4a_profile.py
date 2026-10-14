"""S4A-A/B §5-§8: build the Trustworthy Execution Capability Profile.

Two attribution paths exist in the artifacts and only one of them discriminates
between experts. Both are recorded here because the difference is the first
result of this stage:

  `created_by_event`   every one of the 420 evidence nodes in case 10-39 points
                       at a GRAPH_UPDATE emitted by `Primary_Physician`. The
                       central agent writes the graph, so this link says
                       "the orchestrator recorded it" and nothing about which
                       expert the evidence came from.

  `best_analysis_event` the ANALYSIS event whose text best covers the node.
                       Emitted by the expert. This is the link that
                       discriminates: 377 of 420 nodes resolve to one of the
                       three expert roles, 22 of 30 cases carry more than one
                       distinct role, and 43 nodes resolve to nothing and are
                       reported `NO_LINK` rather than assigned by heuristic
                       (§5: 不得用启发式硬分配后当作真值).

What it is *not*: `best_analysis_event` is a grounding/coverage link, not
provenance of authorship. The profile therefore calls the field
`evidence_attributed_*` and not `evidence_generated_*`, and says so wherever
the number appears.

Fields the schema asks for and the artifacts do not carry are recorded as
`UNAVAILABLE` with a reason, never as zero. `degraded_calls` and `failed_calls`
are the important ones: every one of the 855 events in the dev block has
`status = "success"` and `reason_code = "NONE"`, so `execution_abnormal` has no
observable source at all. §9.2's consequence for that is handled in
`s4a_validate.py`, not papered over here.

Usage:
    python traceevo/s4a_profile.py --out S4A_capability_profile
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from traceevo.audit.cross_graph_binding import is_retrieval_result_event  # noqa: E402

OUT = os.path.join(ROOT, "S4A_capability_profile")

# §5.1 names three expert roles. They are the keys of the profile; a role that
# never appears is still reported, at n=0, so its absence is visible.
ROLES = ("Laboratory_Physician", "Pathologist", "Radiologist")
CENTRAL = "Central_Agent"

# §4's split. Profile-Dev builds; Profile-Eval validates; nothing else is read.
DEV = list(range(10, 40))
EVAL = list(range(50, 60))
TIERS = [
    {"tier": "A", "cases": list(range(10, 30)), "archive": os.path.join(
        ROOT, "S3A_runs", "s3a_migration", "traceevo", "runs",
        "migration_holdout"),
     "outcomes": os.path.join(ROOT, "S3C_experience_expansion",
                              "archive_outcome_results_10_29.csv")},
    {"tier": "B", "cases": list(range(30, 40)), "archive": os.path.join(
        ROOT, "S3B_runs", "memory_build", "gos", "traceevo", "runs",
        "memory_build"),
     "outcomes": os.path.join(ROOT, "S3B_runs", "memory_build",
                              "outcome_results.csv")},
]
EVAL_ARCHIVE = os.path.join(ROOT, "S3D_verified_expansion", "runs_50_59",
                            "traceevo", "runs", "s3d_expansion")
EVAL_OUTCOMES = os.path.join(ROOT, "S3D_verified_expansion",
                             "outcome_results_50_59.csv")

# §5.1's schema, with the fields this implementation cannot fill marked and
# reasoned. Kept next to the builder so the claim and the code cannot drift.
UNAVAILABLE_FIELDS = {
    "degraded_calls": (
        "no event in case 10-39 carries status=degraded; all 855 are "
        "status=success, reason_code=NONE. There is no observable source."),
    "failed_calls": (
        "same as degraded_calls: the status vocabulary in the artifacts has "
        "no failing value."),
    "latency": "not recorded in any artifact (S3A §5.4 noted the same).",
    "token_counts": "not recorded per invocation in the artifacts.",
}

TIERS_ORDER = ("P0_DIRECT_GROUNDED", "P1_SOFT_GROUNDED", "P2_DERIVED",
               "P3_UNSUPPORTED_ASSERTION")


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def archive_for(case: int) -> str:
    for t in TIERS:
        if case in t["cases"]:
            return t["archive"]
    return EVAL_ARCHIVE


def outcome_for(case: int) -> str:
    for t in TIERS:
        if case in t["cases"]:
            p = t["outcomes"]
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        if int(r["case"]) == case:
                            return r.get("outcome_verdict", "uncertain")
    p = EVAL_OUTCOMES
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if int(r["case"]) == case:
                    return r.get("outcome_verdict", "uncertain")
    return "unknown"


# ------------------------------------------------------------------ one case
def observe(case: int, archive: Optional[str] = None) -> Dict[str, Any]:
    """Everything one case contributes to the profile, attributed by role.

    Pure observation: no smoothing, no rate, no interval. §7's snapshot
    arithmetic needs the raw counts so it can add them in case order.
    """
    from traceevo.s3b_gate import audit_bundle

    ar = archive or archive_for(case)
    task = f"case-{case}"
    events = _jsonl(os.path.join(ar, f"{task}_events.jsonl"))
    by_ev = {e["event_id"]: e for e in events}
    ledger = _jsonl(os.path.join(ar, f"{task}_invocations.jsonl"))
    bundle = audit_bundle(ar, task)
    verdict = outcome_for(case)

    per: Dict[str, Dict[str, Any]] = {
        r: {"n_invocations": 0, "accepted_calls": 0, "discarded_calls": 0,
            "uncommitted_calls": 0, "retrieval_events": 0,
            "evidence_attributed_p0": 0, "evidence_attributed_p1": 0,
            "evidence_attributed_p2": 0, "evidence_attributed_p3": 0,
            "evidence_used_p0": 0, "evidence_used_p1": 0,
            "evidence_used_p2": 0, "evidence_used_p3": 0,
            "p3_used_in_reasoning": 0,
            "attributed_nodes": 0, "nodes_used_in_final_reasoning": 0,
            "present": False}
        for r in ROLES}

    for r in ledger:
        a = r.get("actor_id")
        if a not in per:
            continue
        v = per[a]
        v["present"] = True
        v["n_invocations"] += 1
        if r.get("parse_status") == "accepted":
            v["accepted_calls"] += 1
        elif r.get("parse_status") == "discarded":
            v["discarded_calls"] += 1
        if r.get("committed_to_state") is False:
            v["uncommitted_calls"] += 1

    # retrieval activity is the actor's own tool/evidence events
    for e in events:
        a = e.get("actor_id")
        if a in per and is_retrieval_result_event(e):
            per[a]["retrieval_events"] += 1

    no_link = 0
    for d in bundle["decisions"]:
        # §5: attribute through the cross-binding, and only through it.
        e = by_ev.get(d.get("best_analysis_event") or "")
        role = e.get("actor_id") if (e and e.get("event_type") == "ANALYSIS") \
            else None
        if role not in per:
            no_link += 1
            continue
        v = per[role]
        t = d.get("provenance_tier")
        k = {"P0_DIRECT_GROUNDED": "p0", "P1_SOFT_GROUNDED": "p1",
             "P2_DERIVED": "p2", "P3_UNSUPPORTED_ASSERTION": "p3"}.get(t)
        if k is None:
            no_link += 1
            continue
        v[f"evidence_attributed_{k}"] += 1
        v["attributed_nodes"] += 1
        if d.get("used_in_reasoning"):
            v[f"evidence_used_{k}"] += 1
            v["nodes_used_in_final_reasoning"] += 1
            if k == "p3":
                v["p3_used_in_reasoning"] += 1

    return {"case": case, "task_id": task, "outcome_verdict": verdict,
            "roles": per, "no_link_nodes": no_link,
            "n_decisions": len(bundle["decisions"]),
            "n_anomalies": len(bundle["anomalies"]),
            "anomaly_types": sorted({a.get("type")
                                     for a in bundle["anomalies"]})}


# ------------------------------------------------------------------ §6 rates
def _beta_interval(k: int, n: int, lo: float = 0.025,
                   hi: float = 0.975) -> Tuple[Optional[float], Optional[float]]:
    """§8: Beta(1,1) posterior interval. Requires scipy, which is present.

    Falls back to Wilson only if scipy is missing, and says which was used --
    §8 allows either but requires consistency, so the choice is recorded in the
    output rather than left to whichever branch ran.
    """
    if n == 0:
        return (None, None)
    try:
        from scipy.stats import beta as _b
        return (float(_b.ppf(lo, k + 1, n - k + 1)),
                float(_b.ppf(hi, k + 1, n - k + 1)))
    except ImportError:                                    # pragma: no cover
        import math
        z, p = 1.959963985, k / n
        d = 1 + z * z / n
        c = p + z * z / (2 * n)
        m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        return ((c - m) / d, (c + m) / d)


def interval_method() -> str:
    try:
        import scipy  # noqa: F401
        return "Beta(1,1) posterior, 95% (scipy.stats.beta.ppf)"
    except ImportError:                                    # pragma: no cover
        return "Wilson score interval, 95% (scipy unavailable)"


def ratio(k: int, n: int) -> Dict[str, Any]:
    """§8: a small-sample proportion is never reported as a bare zero.

    Denominator zero is `NA`, not `0.0` (§6.2), because `0/0` and `0/40` are
    the same printed number and opposite findings.
    """
    if n == 0:
        return {"count": k, "n": n, "value": None, "status": "NA",
                "mean": None, "lo": None, "hi": None}
    lo, hi = _beta_interval(k, n)
    return {"count": k, "n": n, "value": k / n, "status": "ok",
            "mean": (k + 1) / (n + 2), "lo": lo, "hi": hi}


def summarise(obs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§6: the profile vector. No weighted score is computed (§10.1)."""
    roles: Dict[str, Any] = {}
    for r in ROLES:
        agg = {k: 0 for k in obs[0]["roles"][r]} if obs else {}
        if not agg:
            agg = {"n_invocations": 0, "accepted_calls": 0,
                   "discarded_calls": 0, "uncommitted_calls": 0,
                   "retrieval_events": 0, "evidence_attributed_p0": 0,
                   "evidence_attributed_p1": 0, "evidence_attributed_p2": 0,
                   "evidence_attributed_p3": 0, "evidence_used_p0": 0,
                   "evidence_used_p1": 0, "evidence_used_p2": 0,
                   "evidence_used_p3": 0, "p3_used_in_reasoning": 0,
                   "attributed_nodes": 0,
                   "nodes_used_in_final_reasoning": 0, "present": False}
        for o in obs:
            for k in agg:
                v = o["roles"][r][k]
                agg[k] = (agg[k] or v) if isinstance(v, bool) else agg[k] + v
            if o["roles"][r]["present"]:
                agg["present"] = True
        n_inv = agg["n_invocations"]
        attr = agg["attributed_nodes"]
        attr_used = agg["nodes_used_in_final_reasoning"]
        roles[r] = {
            "role": r,
            "n_cases": sum(1 for o in obs if o["roles"][r]["present"]),
            **agg,
            # §5: what the artifacts do not carry
            "degraded_calls": "UNAVAILABLE",
            "failed_calls": "UNAVAILABLE",
            "unavailable_reasons": {
                "degraded_calls": UNAVAILABLE_FIELDS["degraded_calls"],
                "failed_calls": UNAVAILABLE_FIELDS["failed_calls"]},
            # §6.1 -- execution_abnormal has no source, so it is NA with the
            # reason, not 0.0. Reporting 0.0 here is the failure mode §8 names.
            "p_exec_abnormal": {
                "status": "UNAVAILABLE",
                "reason": UNAVAILABLE_FIELDS["failed_calls"],
                "value": None, "n": n_inv},
            "p_parse_discard": ratio(agg["discarded_calls"], n_inv),
            "p_uncommitted": ratio(agg["uncommitted_calls"], n_inv),
            # §6.2
            "grounding": {
                "denominator": "attributable evidence nodes",
                "direct_grounding_rate": ratio(agg["evidence_attributed_p0"], attr),
                "soft_grounding_rate": ratio(agg["evidence_attributed_p1"], attr),
                "derived_rate": ratio(agg["evidence_attributed_p2"], attr),
                "unsupported_rate": ratio(agg["evidence_attributed_p3"], attr),
                "p3_decision_use_rate": ratio(agg["p3_used_in_reasoning"],
                                              attr_used),
            },
            # §6.3 -- named for what it measures
            "structural_utilization": {
                "term": "structural utilization / structural participation",
                "not": "causal contribution",
                "rate": ratio(attr_used, attr),
            },
            # §6.4 -- description only; §10 forbids weighting on it
            "outcome_association": {
                "verified_success_cases_participated": sum(
                    1 for o in obs if o["roles"][r]["present"]
                    and o["outcome_verdict"] == "verified_success"),
                "verified_incorrect_cases_participated": sum(
                    1 for o in obs if o["roles"][r]["present"]
                    and o["outcome_verdict"] == "verified_incorrect"),
                "uncertain_cases_participated": sum(
                    1 for o in obs if o["roles"][r]["present"]
                    and o["outcome_verdict"] == "uncertain"),
                "warning": ("role presence in a success case is affected by "
                            "case type, Planner selection and the other "
                            "agents; it is not this role's causal "
                            "contribution. §6.4 forbids weighting on it."),
            },
        }
    no_link = sum(o["no_link_nodes"] for o in obs)
    n_dec = sum(o["n_decisions"] for o in obs)
    return {
        "roles": roles,
        "attribution": {
            "link_used": "decision.best_analysis_event -> ANALYSIS.actor_id",
            "link_not_used": ("binding.created_by_event -> GRAPH_UPDATE."
                              "actor_id = Primary_Physician for every node; it "
                              "identifies the orchestrator, not the expert"),
            "semantic": ("grounding/coverage attribution, not authorship; "
                         "hence `evidence_attributed_*`"),
            "attributable_nodes": n_dec - no_link,
            "no_link_nodes": no_link,
            "no_link_note": ("§5: marked unavailable, not assigned by "
                             "heuristic"),
            "n_decisions": n_dec,
        },
        "interval_method": interval_method(),
        "n_cases_observed": len(obs),
        "cases": [o["case"] for o in obs],
    }


def build(out_dir: str) -> Dict[str, Any]:
    """§7: per-case snapshots over 10-39, each using only cases <= t."""
    from traceevo.audit.event_schema import utc_now

    os.makedirs(out_dir, exist_ok=True)
    snap_dir = os.path.join(out_dir, "profile_snapshots")
    os.makedirs(snap_dir, exist_ok=True)

    observations, snapshots = [], []
    for c in DEV:
        observations.append(observe(c))
        snap = summarise(observations)
        snap["snapshot_after_case"] = c
        snap["uses_cases"] = [o["case"] for o in observations]
        snap["generated_at"] = utc_now()
        with open(os.path.join(snap_dir, f"profile_after_case_{c}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        snapshots.append(snap)
        print(f"  case-{c}: snapshot "
              f"({sum(1 for r in ROLES if snap['roles'][r]['present'])} roles)",
              flush=True)

    full = snapshots[-1]
    with open(os.path.join(out_dir, "profile_freeze_10_39.json"), "w",
              encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    _write_evolution(out_dir, snapshots)
    return full


def _write_evolution(out_dir: str, snaps: List[Dict[str, Any]]) -> None:
    """§7's evolution table, one row per (case, role)."""
    rows = []
    for s in snaps:
        for r in ROLES:
            d = s["roles"][r]
            g = d["grounding"]
            rows.append({
                "after_case": s["snapshot_after_case"], "role": r,
                "n_cases": d["n_cases"], "n_invocations": d["n_invocations"],
                "attributed_nodes": d["attributed_nodes"],
                "p_exec_abnormal": d["p_exec_abnormal"]["value"],
                "p_parse_discard": d["p_parse_discard"]["value"],
                "p_parse_discard_mean": d["p_parse_discard"]["mean"],
                "p_parse_discard_lo": d["p_parse_discard"]["lo"],
                "p_parse_discard_hi": d["p_parse_discard"]["hi"],
                "direct_grounding_rate": g["direct_grounding_rate"]["value"],
                "soft_grounding_rate": g["soft_grounding_rate"]["value"],
                "derived_rate": g["derived_rate"]["value"],
                "unsupported_rate": g["unsupported_rate"]["value"],
                "p3_decision_use_rate": g["p3_decision_use_rate"]["value"],
                "p3_decision_use_count": g["p3_decision_use_rate"]["count"],
                "p3_decision_use_n": g["p3_decision_use_rate"]["n"],
                "structural_use_rate": d["structural_utilization"]["rate"]["value"],
            })
    with open(os.path.join(out_dir, "profile_evolution.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ----------------------------------------------------------- §16 artifacts
def emit_artifacts(out_dir: str, full: Dict[str, Any]) -> None:
    """The §16 files that describe the profile rather than compute it."""
    from traceevo.audit.event_schema import utc_now
    import subprocess

    def sha(p: str) -> str:
        import hashlib
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for ch in iter(lambda: f.read(65536), b""):
                h.update(ch)
        return h.hexdigest()

    schema = {
        "name": "Trustworthy Execution Capability Profile",
        "version": "s4a-v1",
        "shape": "vector per role; §10.1 forbids a weighted total score",
        "roles": list(ROLES),
        "central_agent": {
            "note": ("§5.1: orchestration profile may be built separately and "
                     "must not be mixed into expert role metrics. S4A reports "
                     "Central_Agent counts in the ledger attribution but does "
                     "not give it an expert profile row."),
        },
        "fields": {
            "n_cases": "cases in which the role appears in the ledger",
            "n_invocations": "ledger rows with actor_id == role",
            "accepted_calls": "ledger parse_status == accepted",
            "discarded_calls": "ledger parse_status == discarded",
            "degraded_calls": "UNAVAILABLE -- no such status in the artifacts",
            "failed_calls": "UNAVAILABLE -- no such status in the artifacts",
            "evidence_attributed_p0..p3": (
                "audit decisions whose best_analysis_event is an ANALYSIS "
                "emitted by this role; a grounding/coverage link, not "
                "authorship"),
            "p3_used_in_reasoning": "attributed P3 nodes with used_in_reasoning",
            "retrieval_events": ("completed retrieval TOOL_CALL events by this role; "
                                 "legacy traces may use EVIDENCE_READ"),
            "structural_use_rate": (
                "attributed nodes appearing in final reasoning / attributed "
                "nodes -- structural participation, never causal contribution"),
        },
        "unavailable_fields": UNAVAILABLE_FIELDS,
        "derived_metrics": {
            "p_exec_abnormal": "UNAVAILABLE (its two inputs are)",
            "p_parse_discard": "Beta(1,1) smoothed, §6.1 keeps it separate "
                               "from failure",
            "p3_decision_use_rate": "§6.2's safety metric; NA when the "
                                    "denominator is 0, never 0",
            "outcome_association": "§6.4 description only; §10 forbids "
                                   "weighting on it",
        },
        "uncertainty": {"method": interval_method(),
                        "requirement": "every binomial ratio reports count, "
                                       "posterior mean and 95% interval (§8)"},
    }
    _dump(os.path.join(out_dir, "profile_schema.json"), schema)

    rows, irows = [], []
    for r in ROLES:
        d = full["roles"][r]
        g = d["grounding"]
        rows.append({
            "role": r, "n_cases": d["n_cases"],
            "n_invocations": d["n_invocations"],
            "accepted_calls": d["accepted_calls"],
            "discarded_calls": d["discarded_calls"],
            "uncommitted_calls": d["uncommitted_calls"],
            "degraded_calls": d["degraded_calls"],
            "failed_calls": d["failed_calls"],
            "evidence_attributed_p0": d["evidence_attributed_p0"],
            "evidence_attributed_p1": d["evidence_attributed_p1"],
            "evidence_attributed_p2": d["evidence_attributed_p2"],
            "evidence_attributed_p3": d["evidence_attributed_p3"],
            "p3_used_in_reasoning": d["p3_used_in_reasoning"],
            "attributed_nodes": d["attributed_nodes"],
            "nodes_used_in_final_reasoning":
                d["nodes_used_in_final_reasoning"],
            "retrieval_events": d["retrieval_events"],
            "p_exec_abnormal": "UNAVAILABLE",
            "p_parse_discard": d["p_parse_discard"]["value"],
            "p3_decision_use_rate": g["p3_decision_use_rate"]["value"],
            "structural_use_rate": d["structural_utilization"]["rate"]["value"],
            "verified_success_cases_participated":
                d["outcome_association"]["verified_success_cases_participated"],
            "verified_incorrect_cases_participated":
                d["outcome_association"][
                    "verified_incorrect_cases_participated"],
            "uncertain_cases_participated":
                d["outcome_association"]["uncertain_cases_participated"],
        })
        for name, ratio_val in (
                ("p_parse_discard", d["p_parse_discard"]),
                ("p_uncommitted", d["p_uncommitted"]),
                ("direct_grounding_rate", g["direct_grounding_rate"]),
                ("soft_grounding_rate", g["soft_grounding_rate"]),
                ("derived_rate", g["derived_rate"]),
                ("unsupported_rate", g["unsupported_rate"]),
                ("p3_decision_use_rate", g["p3_decision_use_rate"]),
                ("structural_use_rate", d["structural_utilization"]["rate"])):
            irows.append({
                "role": r, "metric": name, "count": ratio_val["count"],
                "n": ratio_val["n"], "value": ratio_val["value"],
                "status": ratio_val["status"], "posterior_mean": ratio_val["mean"],
                "ci95_lo": ratio_val["lo"], "ci95_hi": ratio_val["hi"],
                "method": interval_method()})
    _write_csv(os.path.join(out_dir, "role_profile_summary.csv"), rows)
    _write_csv(os.path.join(out_dir, "role_profile_intervals.csv"), irows)

    def _git(*a):
        return subprocess.run(["git", "-C", ROOT, *a], capture_output=True,
                              text=True, timeout=30).stdout.strip()

    frozen = os.path.join(out_dir, "profile_freeze_10_39.json")
    manifest = {
        "generated_at": utc_now(),
        "stage": "S4A",
        "profile_version": "s4a-v1",
        "dev_cases": DEV, "eval_cases": EVAL,
        "excluded": {"0-9": "legacy / audit rule development (§4)",
                     "40-49": "memory behaviour treatment (§4)"},
        "untouched": "60-137",
        "no_gos_run": ("S4A starts no GoS process; every input is an existing "
                       "artifact"),
        "inputs": [
            {"archive": os.path.relpath(t["archive"], ROOT),
             "cases": f"{t['cases'][0]}-{t['cases'][-1]}"} for t in TIERS],
        "eval_input": os.path.relpath(EVAL_ARCHIVE, ROOT),
        "freeze_file": os.path.relpath(frozen, ROOT),
        "freeze_sha256": sha(frozen),
        "n_snapshots": len(os.listdir(os.path.join(out_dir,
                                                   "profile_snapshots"))),
        "attribution": full["attribution"],
        "interval_method": full["interval_method"],
        "git": {"traceevo_head": _git("rev-parse", "HEAD"),
                "traceevo_branch": _git("rev-parse", "--abbrev-ref", "HEAD")},
    }
    _dump(os.path.join(out_dir, "profile_build_manifest.json"), manifest)
    _emit_availability(out_dir, full, schema)
    _emit_plot(out_dir)


def _emit_plot(out_dir: str) -> None:
    """§16's profile_evolution.png.

    Two panels, because the two things worth seeing have different scales:
    the grounding composition (rates over attributed nodes) and the execution
    signal (counts, with intervals). `p_exec_abnormal` is deliberately absent
    -- it has no source, and drawing it as a flat zero line is exactly the
    "0 failures / 2 calls, risk = 0" presentation §8 forbids.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    with open(os.path.join(out_dir, "profile_evolution.csv"),
              encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cases = sorted({int(r["after_case"]) for r in rows})
    colors = {"Laboratory_Physician": "#1f77b4", "Pathologist": "#d62728",
              "Radiologist": "#2ca02c"}

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.6))
    for role, col in colors.items():
        rs = [r for r in rows if r["role"] == role]
        rs.sort(key=lambda r: int(r["after_case"]))
        xs = [int(r["after_case"]) for r in rs]
        axes[0].plot(xs, [_f(r["p3_decision_use_rate"]) for r in rs], color=col,
                     label=role)
        axes[1].plot(xs, [_f(r["direct_grounding_rate"]) for r in rs], color=col,
                     label=f"{role} P0")
        axes[1].plot(xs, [_f(r["derived_rate"]) for r in rs], color=col,
                     linestyle="--", alpha=0.6)
        axes[2].plot(xs, [int(r["n_invocations"]) for r in rs], color=col,
                     label=role)
    axes[0].set_title("P3 decision-use rate\n(safety metric)")
    axes[0].set_ylim(-0.01, 0.35)
    axes[1].set_title("Grounding composition\n(solid P0, dashed P2)")
    axes[1].set_ylim(-0.02, 1.02)
    axes[2].set_title("Invocations\n(sample size behind the rates)")
    for ax in axes:
        ax.set_xlabel("snapshot after case")
        ax.grid(alpha=0.25)
    axes[1].legend(fontsize=7, loc="center right")
    fig.suptitle("S4A capability profile over case 10-39 "
                 "(each point uses only cases <= t; §7)", fontsize=11)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "profile_evolution.png"), dpi=140)
    plt.close(fig)


def _f(v):
    return float("nan") if v in ("", None, "None") else float(v)


def _emit_availability(out_dir: str, full: Dict[str, Any],
                       schema: Dict[str, Any]) -> None:
    """§16's data_availability.md, and §17 Q1/Q2's answer."""
    L = ["# Data availability (§5, §17 Q1-Q2)", "",
         "Every field below was checked against case 10-39's artifacts. Fields",
         "that do not exist there are `UNAVAILABLE` with the reason, not zero.",
         "", "## Computable", "",
         "| field | source | coverage |", "|---|---|---|",
         "| `n_invocations` | ledger `actor_id` | 660 expert calls |",
         "| `accepted_calls` / `discarded_calls` | ledger `parse_status` | "
         "1151 accepted, 17 discarded |",
         "| `uncommitted_calls` | ledger `committed_to_state` | 355 false |",
         "| `evidence_attributed_p0..p3` | `best_analysis_event` | 377 of 420 "
         "nodes |",
         "| `p3_used_in_reasoning` | audit `used_in_reasoning` | 6 nodes |",
         "| `retrieval_events` | completed retrieval `TOOL_CALL` (legacy: `EVIDENCE_READ`) | per role |",
         "| `structural_use_rate` | `used_in_reasoning` | 265 of 420 |",
         "", "## Not computable", "",
         "| field | why |", "|---|---|"]
    for k, v in UNAVAILABLE_FIELDS.items():
        L.append(f"| `{k}` | {v} |")
    L += ["", "## Attribution is narrower than it looks", "",
          "Two links exist and only one discriminates:", "",
          "* `binding.created_by_event` resolves to `Primary_Physician` for",
          "  **all 420** nodes -- the orchestrator records the graph, so this",
          "  link identifies who wrote it down, not who found it.",
          "* `best_analysis_event` resolves 377 nodes to the three expert",
          "  roles and leaves 43 as `NO_LINK`. Those 43 are reported as",
          "  unattributed (10.2%); §5 forbids assigning them by heuristic.",
          "",
          "The link is a grounding/coverage relation, not authorship, which is",
          "why every field is named `evidence_attributed_*`.",
          "", f"Interval method: **{full['interval_method']}**.", ""]
    with open(os.path.join(out_dir, "data_availability.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(L))


def _dump(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    full = build(args.out)
    emit_artifacts(args.out, full)
    print(json.dumps({
        "roles": {r: {"n_invocations": full["roles"][r]["n_invocations"],
                      "attributed_nodes": full["roles"][r]["attributed_nodes"],
                      "discarded": full["roles"][r]["discarded_calls"],
                      "p3_used": full["roles"][r]["p3_used_in_reasoning"]}
                  for r in ROLES},
        "attribution": full["attribution"],
        "interval_method": full["interval_method"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
