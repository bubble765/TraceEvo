"""S4B §12/§15/§17: close out the stage on the Phase-1 stop.

§10's first branch fired: zero of ten cases carry a decision-path P3, so the
Guard arm is not run and §15's `LOW_TRIGGER_DENSITY` is the conclusion.

§17 says that when Phase 1 stops, the Guard-arm files may be empty but the
report must say why. This module writes each of them as an explicit
`NOT_PRODUCED` record rather than a zero-byte file, because an empty file and
a file nobody wrote are indistinguishable to a later reader, and the reason is
the deliverable here.

One decision this module makes and the report defends: **the gold is not read.**
§11 conditions outcome verification on both arms being sealed, and there is no
second arm. Verifying the baseline now would produce diagnostic numbers that
no part of S4B's decision uses, at the cost of spending case 60-69's
gold-unopened status — which is the only remaining value the block has if the
review decides to re-scope it.

Usage:
    python traceevo/s4b_finalize.py --out S4B_adaptive_guard
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "S4B_adaptive_guard")
VERDICT = "LOW_TRIGGER_DENSITY"

ARM_REASON = (
    "§10 branch 1: 0 of 10 cases in case 60-69 carry a decision-path P3, so "
    "the Guard arm is not run and this file is not produced. §17 permits it "
    "to be empty provided the report explains why; writing the file with no "
    "content would look the same as a run that failed to write it.")


def _dump(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _sha_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def guard_schema(out_dir: str) -> Dict[str, Any]:
    """§17's guard_schema.json: what a guard action record contains."""
    from traceevo.evolution.grounding_guard import (G_P3_SOURCE, MAX_RETRY,
                                                    RETRY_PROMPT, TRIGGER_RULE)
    schema = {
        "policy_version": "ATGG-v1",
        "guard_id": G_P3_SOURCE,
        "trigger_rule": TRIGGER_RULE,
        "max_retry": MAX_RETRY,
        "outcomes": {
            "NO_TRIGGER": "commit the parsed proposal unchanged",
            "RESOLVED": "the retry is no longer a decision-path P3; commit it",
            "BLOCKED": "the retry is still P3; commit the original minus the "
                       "offending node and its SUPPORT/REFUTE edge",
            "RETRY_FAILED": "the retry did not parse or had the wrong shape; "
                            "same fallback as BLOCKED",
        },
        "action_record_fields": {
            "guard_event": "GUARD_TRIGGER | GUARD_RETRY | "
                           "GUARD_RETRY_SCHEMA_REJECTED",
            "guard_id": "G_P3_SOURCE",
            "guard_result": "RESOLVED | BLOCKED | RETRY_FAILED",
            "original_candidate_hash": "sha256 over the proposal the guard "
                                       "judged, sorted keys",
            "retry_prompt_hash": "sha256 of the correction prompt",
            "retry_response_hash": "sha256 of the retry response",
            "offending_nodes": "candidate ids the rule fired on",
            "dropped": "node:/edge: entries removed before commit",
            "max_retry": MAX_RETRY,
        },
        "retry_prompt_sha256": hashlib.sha256(
            RETRY_PROMPT.encode("utf-8")).hexdigest(),
        "retry_prompt": RETRY_PROMPT,
        "invariants": [
            "the raw LLM response is recorded in the invocation ledger "
            "before the guard runs and is never modified (§7)",
            "the guard rewrites no content and invents no source (§8.2)",
            "at most one retry per graph update (§8.2)",
            "a committed delta always carries nodes, edges and edit keys "
            "(§14 system correctness)",
        ],
        "event_vocabulary": (
            "unchanged. `EVENT_TYPES` is frozen and the hash chain is built "
            "over it, so guard actions are written to guard_actions.jsonl as a "
            "separate ledger rather than as new provenance events."),
    }
    _dump(os.path.join(out_dir, "guard_schema.json"), schema)
    return schema


def not_produced(out_dir: str, risk: Dict[str, Any]) -> List[str]:
    made = []
    files = {
        "guard_actions.jsonl": ("one JSON object per guard intervention",
                                ARM_REASON),
        "guard_case_summary.csv": ("per-case guard outcome counts",
                                   ARM_REASON),
        "outcome_results.csv": (
            "OutcomeVerifier over both arms",
            "§11 conditions outcome verification on both arms being sealed; "
            "there is no Guard arm, so there is nothing to verify against and "
            "the baseline's gold is deliberately left unread. Reading it would "
            "produce numbers no part of §10's decision uses, at the cost of "
            "case 60-69's gold-unopened status."),
        "diagnostic_transitions.csv": (
            "baseline verdict -> guard verdict",
            "there is no second arm, so no transition exists to report"),
    }
    for name, (what, why) in files.items():
        p = os.path.join(out_dir, name)
        if name.endswith(".jsonl"):
            with open(p, "w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "record": "NOT_PRODUCED", "file": name,
                    "would_have_contained": what, "reason": why,
                    "stage_verdict": VERDICT}, ensure_ascii=False) + "\n")
        elif name.endswith(".csv"):
            with open(p, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["record", "file", "would_have_contained",
                            "reason", "stage_verdict"])
                w.writerow(["NOT_PRODUCED", name, what, why, VERDICT])
        made.append(name)
    os.makedirs(os.path.join(out_dir, "guard_60_69"), exist_ok=True)
    _dump(os.path.join(out_dir, "guard_60_69", "NOT_PRODUCED.json"), {
        "record": "NOT_PRODUCED", "directory": "guard_60_69/",
        "reason": ARM_REASON, "stage_verdict": VERDICT,
        "what_was_run_instead": os.path.relpath(
            os.path.join(out_dir, "baseline_60_69"), ROOT)})
    os.makedirs(os.path.join(out_dir, "judge_raw"), exist_ok=True)
    _dump(os.path.join(out_dir, "judge_raw", "NOT_PRODUCED.json"), {
        "record": "NOT_PRODUCED", "directory": "judge_raw/",
        "reason": ("the OutcomeVerifier was not run; see outcome_results.csv "
                   "for why"),
        "stage_verdict": VERDICT})
    made += ["guard_60_69/", "judge_raw/"]
    return made


def safety_metrics(out_dir: str, risk: Dict[str, Any]) -> Dict[str, Any]:
    """§12.1's primary safety metrics, for the baseline arm alone.

    §12 frames these as a two-arm comparison. With one arm they are a
    description of the block, and the file says so rather than presenting a
    single column as if a comparison had happened.
    """
    rows = []
    for r in risk["per_case"]:
        rows.append({
            "arm": "baseline (ATGG off)", "case": r["case"],
            "p3_used_in_reasoning": r["n_p3_used"],
            "p3_unused": r["n_p3_unused"],
            "decision_path_ungrounded_evidence": r["n_decision_path_ungrounded"],
            "derived_on_decision_path": r["n_derived_used"],
            "derived_derivations": "|".join(r["derived_derivations"]),
            "n_decisions": r["n_decisions"],
            "n_anomalies": r["n_anomalies"],
            "anomaly_types": "|".join(r["anomaly_types"]),
            "guard_triggers": r["guard_triggers"],
        })
    p = os.path.join(out_dir, "safety_metrics.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return {
        "arm": "baseline",
        "n_cases": len(rows),
        "total_p3_used": sum(r["n_p3_used"] for r in risk["per_case"]),
        "total_derived_on_decision_path": sum(r["n_derived_used"]
                                              for r in risk["per_case"]),
        "comparison_available": False,
        "comparison_note": (
            "§12 specifies these as Guard-vs-Baseline deltas. Only the "
            "baseline arm ran, so the table is a description of one arm and no "
            "delta column is reported -- a single column presented under a "
            "two-arm heading is how an absent comparison looks like a null "
            "one."),
    }


def density_analysis(out_dir: str, risk: Dict[str, Any]) -> Dict[str, Any]:
    """Why zero, and how much can be concluded from ten cases."""
    import numpy as np
    from scipy.stats import beta

    pol = os.path.join(out_dir, "guard_policy_manifest.json")
    with open(pol, encoding="utf-8") as f:
        hist = json.load(f)["historical_evidence"]["per_block"]
    n_cases = sum(b["n_cases"] for b in hist)
    n_p3 = sum(b["n_p3_used_in_reasoning"] for b in hist)
    p_hat = n_p3 / n_cases

    obs_cases = risk["n_cases"]
    obs_p3 = risk["total_p3_used"]
    a, b = n_p3 + 1, n_cases - n_p3 + 1
    rng = np.random.default_rng(20260919)
    theta = rng.beta(a, b, 200000)

    return {
        "historical": {
            "block": "10-59", "n_cases": n_cases,
            "n_cases_with_p3_used": n_p3,
            "per_case_rate": p_hat,
            "per_block": {b_["block"]: {
                "cases": b_["n_cases"],
                "p3_used": b_["n_p3_used_in_reasoning"],
                "rate": b_["n_p3_used_in_reasoning"] / b_["n_cases"]}
                for b_ in hist},
            "posterior": {"family": f"Beta({a},{b})",
                          "mean": a / (a + b),
                          "ci95": [float(beta.ppf(0.025, a, b)),
                                   float(beta.ppf(0.975, a, b))]},
        },
        "observed": {
            "block": "60-69", "n_cases": obs_cases,
            "n_cases_with_p3_used": risk["n_cases_with_decision_path_p3"],
            "total_p3_nodes": risk["total_p3_used"] + risk["total_p3_unused"],
            "note": ("the block contains no P3 node at all -- not one used in "
                     "reasoning and not one left unused"),
        },
        "how_surprising": {
            "frequentist_p_of_zero_in_10": float((1 - p_hat) ** obs_cases),
            "posterior_predictive_p_of_zero_in_10": float(
                np.mean((1 - theta) ** obs_cases)),
            "reading": (
                "roughly a 6% chance under the historical rate. Low enough to "
                "say this block is quieter than the corpus it was drawn from, "
                "and not low enough to say the risk has gone. Both readings "
                "are available and this stage takes neither: what it can say "
                "is that ten cases did not exercise the guard, which is what "
                "§10 asks."),
        },
        "what_this_does_not_mean": (
            "It is not evidence that ATGG does not work. The trigger rule was "
            "shown to fire on the §9.2 controls; there was simply no P3 in "
            "this block for it to fire on. A guard that is never asked is not "
            "a guard that failed."),
    }


def leakage_check(out_dir: str, risk: Dict[str, Any]) -> Dict[str, Any]:
    from traceevo.s4b_run import check_seals
    seals = check_seals(out_dir, "baseline")
    out = {
        "baseline_seals_checked": len(seals),
        "baseline_seals_verified": sum(1 for s in seals if s["match"]),
        "baseline_seals_rejected": sum(1 for s in seals if not s["match"]),
        "gold_read": False,
        "gold_note": ("case 60-69's gold is unread. §11 conditions outcome "
                      "verification on both arms; with one arm the baseline's "
                      "outcome is not needed by any part of §10's decision, "
                      "and leaving it unread keeps the block available if the "
                      "review re-scopes it."),
        "case_70_137_read": False,
        "case_70_137_note": "not opened by any S4B module",
        "memory_injected": False,
        "role_routing_used": False,
        "role_routing_note": ("§1 closed the role-weighting branch on S4A's "
                              "negative result; nothing in S4B reads a role "
                              "profile"),
        "frozen_rules_modified": [],
        "guard_off_identical_to_pre_s4b": None,   # filled from the regression
    }
    reg = os.path.join(out_dir, "regression_summary.json")
    if os.path.exists(reg):
        with open(reg, encoding="utf-8") as f:
            r = json.load(f)
        out["guard_off_identical_to_pre_s4b"] = r["all_identical"]
        out["guard_off_regression"] = {
            "recording": r["recording"], "all_identical": r["all_identical"],
            "strict_replay_clean": r["strict_replay_clean"],
            "no_guard_log_written": r["no_guard_log_written"],
            "excluded_fields": r["excluded_fields"]}
    fr = os.path.join(out_dir, "freeze_manifest.json")
    if os.path.exists(fr):
        from traceevo.s4b_policy import verify as verify_freeze
        out["freeze"] = verify_freeze(out_dir)
    _dump(os.path.join(out_dir, "leakage_check.json"), out)
    return out


def overhead(out_dir: str) -> Dict[str, Any]:
    """§12.2's overhead, for the arm that ran.

    §12.2 says `TOKEN_UNAVAILABLE` rather than an estimate when the API does
    not report usage, and says wall-clock is an engineering number and not a
    basis for a strong claim. Both are honoured: tokens are reported as
    unavailable unless the ledger actually carries them, and runtime is
    reported per case with the caveat attached.
    """
    art = os.path.join(out_dir, "baseline_60_69", "traceevo", "runs", "atgg_off")
    rows = []
    for c in range(60, 70):
        p = os.path.join(art, f"case-{c}_invocations.jsonl")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            calls = [json.loads(ln) for ln in f if ln.strip()]
        stages = {}
        for r in calls:
            stages[r.get("stage", "")] = stages.get(r.get("stage", ""), 0) + 1
        rows.append({
            "arm": "baseline", "case": c,
            "llm_calls": len(calls),
            "guard_retry_calls": stages.get("GRAPH_UPDATE_GUARD_RETRY", 0),
            "graph_update_calls": stages.get("GRAPH_UPDATE", 0),
            "expert_calls": sum(v for k, v in stages.items()
                                if k in ("EXPERT_DECISION", "EXPERT_CONTENT")),
            "retrieval_events": "",
            "token_overhead": "TOKEN_UNAVAILABLE",
            "wall_clock_note": "see run.log; network-bound and not comparable",
        })
    p = os.path.join(out_dir, "overhead_results.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return {
        "arm": "baseline", "per_case": rows,
        "guard_retry_calls_total": sum(r["guard_retry_calls"] for r in rows),
        "token_overhead": "TOKEN_UNAVAILABLE",
        "token_note": ("§12.2: the guard makes one extra LLM call per "
                       "trigger and this arm had no triggers, so the marginal "
                       "cost of the guard is unmeasured here; the API does "
                       "not report usage per call, so no estimate is given"),
        "wall_clock_note": ("§12.2: reported as an engineering figure only; "
                            "network variance dominates it"),
    }


def git_artifacts(out_dir: str) -> None:
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    branch = subprocess.run(["git", "-C", ROOT, "rev-parse", "--abbrev-ref",
                             "HEAD"], capture_output=True,
                            text=True).stdout.strip()
    gos_head = subprocess.run(["git", "-C", os.path.join(ROOT, "gos"),
                               "rev-parse", "HEAD"], capture_output=True,
                              text=True).stdout.strip()
    from traceevo.audit.event_schema import utc_now
    with open(os.path.join(out_dir, "git_commit.txt"), "w",
              encoding="utf-8") as f:
        f.write(f"# TraceEvo S4B — code provenance\n"
                f"# generated {utc_now()}\n\n"
                f"## TraceEvo package\npath:   {ROOT}\n"
                f"branch: {branch}\nHEAD:   {head}\n\n"
                f"## Instrumented GoS working copy\n"
                f"path:   {os.path.join(ROOT, 'gos')}\n"
                f"HEAD:   {gos_head}\n\n"
                f"S4B changed GoS in exactly one place: `agents/"
                f"central_agent.py` gained a guarded assignment of "
                f"`response_dict` between parse and apply. With the guard "
                f"disabled the hook returns None and the assignment does not "
                f"happen, which regression_results.csv verifies against a "
                f"recording made before S4B existed.\n\n"
                f"## Corpus run by S4B\n"
                f"baseline arm: case 60-69, Memory OFF, ATGG OFF, sealed\n"
                f"guard arm:    not run (§10 branch 1)\n"
                f"not read:     case 70-137\n")
    gos_diff = subprocess.run(
        ["git", "-C", os.path.join(ROOT, "gos"), "diff"],
        capture_output=True, text=True).stdout
    own_diff = subprocess.run(["git", "-C", ROOT, "diff"],
                              capture_output=True, text=True).stdout
    with open(os.path.join(out_dir, "git_diff.patch"), "w",
              encoding="utf-8") as f:
        f.write(gos_diff)
        if own_diff:
            f.write("\n" + own_diff)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    d = args.out
    with open(os.path.join(d, "baseline_risk_summary_atgg_off.json"),
              encoding="utf-8") as f:
        risk = json.load(f)
    guard_schema(d)
    made = not_produced(d, risk)
    m = safety_metrics(d, risk)
    dens = density_analysis(d, risk)
    ov = overhead(d)
    lk = leakage_check(d, risk)
    git_artifacts(d)
    _dump(os.path.join(d, "stage_verdict.json"), {
        "verdict": VERDICT,
        "decided_by": "§10 branch 1 (0 of 10 cases with a decision-path P3)",
        "guard_arm_run": False,
        "safety_metrics": m,
        "density": dens,
        "overhead": {k: v for k, v in ov.items() if k != "per_case"},
        "leakage": {k: v for k, v in lk.items()
                    if k not in ("guard_off_regression",)},
        "not_produced": made,
    })
    print(json.dumps({
        "verdict": VERDICT,
        "n_cases_with_p3": risk["n_cases_with_decision_path_p3"],
        "total_p3_nodes": risk["total_p3_used"] + risk["total_p3_unused"],
        "p_zero_in_10_under_historical_rate":
            dens["how_surprising"]["frequentist_p_of_zero_in_10"],
        "guard_arm_run": False,
        "gold_read": lk["gold_read"],
        "not_produced": made,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
