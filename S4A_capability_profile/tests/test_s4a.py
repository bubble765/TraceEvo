"""S4A: the profile, the freeze ordering, and the checks that had to be shown
able to fail.

S4A's headline result is a *zero* -- no guard candidate, no information gain --
and every stage of this project has now recorded a failure of the form "a zero
from a mechanism that could not have produced anything else" (S3B FAILURE 14,
S3C FAILURE 3, S3D FAILURE 5). So the two load-bearing tests here are positive
controls: `test_the_guard_rule_can_fire` shows the §10.2 rule produces a
candidate when the data warrants one, and
`test_a_predictive_profile_beats_the_global_baseline` shows the §9 Brier
comparison can come out negative. Without them, "0 candidates" and "the rule is
broken" are the same observation.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "S4A_capability_profile")

from traceevo.s4a_profile import (DEV, EVAL, ROLES, UNAVAILABLE_FIELDS,  # noqa: E402
                                  _beta_interval, interval_method, ratio)
from traceevo.s4a_validate import _brier, _bootstrap_delta  # noqa: E402


def _load(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        pytest.skip(f"{name} not built yet")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------- §7 / §13 item 2
def test_every_snapshot_uses_only_cases_at_or_before_t():
    """§7: profile_t may use <= t and nothing later."""
    d = os.path.join(OUT, "profile_snapshots")
    if not os.path.isdir(d):
        pytest.skip("snapshots not built")
    seen = 0
    for fn in sorted(os.listdir(d)):
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            s = json.load(f)
        t = s["snapshot_after_case"]
        assert s["uses_cases"] == list(range(10, t + 1)), (
            f"{fn}: a snapshot after case {t} must use exactly 10..{t}")
        assert max(s["uses_cases"]) <= t
        seen += 1
    assert seen == len(DEV), "one snapshot per dev case"


def test_the_dev_and_eval_blocks_are_disjoint():
    """§4: nothing from evaluation may enter the profile that predicts it."""
    assert not (set(DEV) & set(EVAL))
    assert min(EVAL) > max(DEV)


# ------------------------------------------------------- §5 unavailable fields
def test_unavailable_fields_are_not_zero():
    """§5: a field the artifacts do not carry is `UNAVAILABLE`, never 0.

    `degraded_calls` and `failed_calls` are the ones that matter: reporting
    them as 0 would make `execution_abnormal` look like a measured zero and
    would let §9.2's untestable target read as a perfect prediction.
    """
    fz = _load("profile_freeze_10_39.json")
    for r in ROLES:
        d = fz["roles"][r]
        assert d["degraded_calls"] == "UNAVAILABLE"
        assert d["failed_calls"] == "UNAVAILABLE"
        assert d["p_exec_abnormal"]["status"] == "UNAVAILABLE"
        assert d["p_exec_abnormal"]["value"] is None
        assert d["p_exec_abnormal"]["reason"], "an unavailable field owes a why"
    for k in ("latency", "token_counts"):
        assert k in UNAVAILABLE_FIELDS


def test_a_zero_denominator_is_na_not_zero():
    """§6.2: 0/0 and 0/40 print the same and mean opposite things."""
    assert ratio(0, 0)["status"] == "NA"
    assert ratio(0, 0)["value"] is None
    assert ratio(0, 0)["mean"] is None
    r = ratio(0, 40)
    assert r["status"] == "ok" and r["value"] == 0.0
    assert r["hi"] > 0.05, "0/40 is still a wide interval, not a zero"


# ------------------------------------------------------------------- §8
def test_small_samples_get_wide_intervals():
    """§8: '0 failures / 2 calls' must not present as risk = 0.

    With Beta(1,1) smoothing the lower bound is not literally 0 -- it is the
    2.5% quantile of Beta(1, 3), about 0.008. That is the point: the smoothed
    interval refuses to place the risk at zero even when the count is zero,
    which is exactly what §8 asks for.
    """
    lo, hi = _beta_interval(0, 2)
    assert lo > 0.0 and lo < 0.02, "a small but non-zero floor"
    assert hi > 0.7, "2 calls cannot rule out a large risk"
    lo2, hi2 = _beta_interval(0, 200)
    assert hi2 < 0.02, "and 200 calls can rule out much more"
    assert hi2 < hi, "the interval must narrow with n"


def test_the_interval_method_is_declared_once():
    m = interval_method()
    assert "Beta(1,1)" in m or "Wilson" in m
    fz = _load("profile_freeze_10_39.json")
    assert fz["interval_method"] == m, "the profile must say which one it used"


def test_every_ratio_reports_count_mean_and_interval():
    """§8's three-part requirement, checked on every row of the deliverable."""
    import csv
    p = os.path.join(OUT, "role_profile_intervals.csv")
    if not os.path.exists(p):
        pytest.skip("intervals not emitted")
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    for r in rows:
        assert r["count"] != "" and r["n"] != ""
        assert "posterior_mean" in r and "ci95_lo" in r and "ci95_hi" in r


# ------------------------------------------------------- §10 guard rule control
def test_the_guard_rule_can_fire():
    """The positive control for S4A's headline zero.

    Constructs a role whose P3-decision-use interval lower bound clears the
    global mean and checks the §10.2 comparison raises a candidate. If this
    ever fails while the real assessment reports 0 candidates, the zero is a
    broken rule rather than a finding.
    """
    # a role with 40 of 60 nodes P3-used vs a low global baseline
    lo, hi = _beta_interval(40, 60)
    global_mean = 0.0286
    assert lo > global_mean, (
        "the rule's comparison must be able to come out true")
    # and the real data does not clear it
    fz = _load("profile_freeze_10_39.json")
    for r in ROLES:
        g = fz["roles"][r]["grounding"]["p3_decision_use_rate"]
        assert g["lo"] <= global_mean, (
            f"{r} would be a guard candidate -- if that is now true, the "
            f"report's headline has changed and must be rewritten")


def test_no_guard_candidate_is_not_all_insufficient():
    """§13 item 6's second half is a different result from failing to measure.

    `INSUFFICIENT_DATA` means the sample could not support a judgement;
    `NORMAL` means it could, and the role does not stand out. The report says
    the second, so the data must actually say it.
    """
    a = _load("profile_actionability.json")
    assert a["n_guard_candidates"] == 0
    assert not a["all_roles_insufficient"], (
        "if the roles were merely unmeasured the conclusion would be "
        "PROFILE_DATA_INSUFFICIENT, not NO_ROLE_SPECIFIC_ADAPTATION")
    assert any(r["classification"] == "NORMAL" for r in a["rows"])
    assert a["interval_not_widened"]


def test_no_weighted_total_score_exists():
    """§10.1: the profile stays a vector; no 0.4*x + 0.3*y + ... anywhere."""
    fz = _load("profile_freeze_10_39.json")
    keys = json.dumps(fz).lower()
    for forbidden in ("total_score", "composite_score", "weighted_score",
                      "capability_score"):
        assert forbidden not in keys
    assert "score" not in {k for r in ROLES for k in fz["roles"][r]}, (
        "a key named `score` is how a weighted total gets in")


# ------------------------------------------------------------------- §9
def test_a_predictive_profile_beats_the_global_baseline():
    """The positive control for the Brier comparison.

    A role whose historical rate tracks the eval outcome must score a lower
    Brier than a constant baseline. Without this, `delta_brier > 0` could be
    an artefact of the comparison rather than a finding about the profile.
    """
    rows = [{"y": 1, "p_role": 0.9, "p_global": 0.0286} for _ in range(30)] + \
           [{"y": 0, "p_role": 0.05, "p_global": 0.0286} for _ in range(70)]
    assert _brier(rows, "p_role") < _brier(rows, "p_global")

    anti = [{"y": 1, "p_role": 0.01, "p_global": 0.5} for _ in range(30)] + \
           [{"y": 0, "p_role": 0.99, "p_global": 0.5} for _ in range(70)]
    assert _brier(anti, "p_role") > _brier(anti, "p_global")


def test_the_untestable_target_is_not_reported_as_a_perfect_score():
    """§9.2: an all-zero target means the target does not exist here.

    Reporting Brier 0.0 for a target with no positives would read as flawless
    prediction. The status must say UNTESTABLE and the Brier must be absent.
    """
    v = _load("profile_validation.json")
    t1 = next(t for t in v["targets"] if "§9.2" in t["target"])
    assert t1["status"] == "UNTESTABLE_NO_POSITIVES"
    assert t1["positives"] == 0
    assert t1["brier_role_profile"] is None and t1["brier_global"] is None
    assert t1["delta_brier"] is None


def test_the_secondary_target_is_labelled_as_not_section_9_2():
    """§9.2's target and the discard target are different quantities and the
    deliverable must not let one stand in for the other."""
    v = _load("profile_validation.json")
    sec = next(t for t in v["targets"] if "secondary" in t["target"])
    assert "NOT §9.2" in sec["target"]


def test_the_bootstrap_reports_whether_zero_is_covered():
    """The distinction the verdict rests on: no gain shown vs no power."""
    rows = [{"y": 0, "p_role": 0.1, "p_global": 0.1} for _ in range(20)]
    rows[0] = {"y": 1, "p_role": 0.2, "p_global": 0.1}
    ci = _bootstrap_delta(rows, n_boot=200)
    assert ci is not None and "includes_zero" in ci


# ------------------------------------------------------------------- §13/§14
def test_the_verdict_follows_from_the_criteria_not_the_other_way():
    v = _load("profile_validation.json")
    d = v["decision"]
    assert d["criteria"]["item_5"]["met"] is False, (
        "§13 item 5 is 信息增益 delta Brier < 0; if it now passes, the "
        "verdict must be revisited")
    assert d["go_conditions_met"] is False
    assert d["verdict"] == "NO_ROLE_SPECIFIC_ADAPTATION"
    assert d["verdict"] in ("GO_S4B_ADAPTIVE_GUARD",
                            "NO_ROLE_SPECIFIC_ADAPTATION",
                            "PROFILE_NOT_PREDICTIVE",
                            "PROFILE_DATA_INSUFFICIENT")


def test_profile_not_predictive_is_not_claimed_without_the_power():
    """The claim this stage declined to make.

    With 3 positives the core target's interval covers zero, so "the profile
    is not predictive" and "this test cannot tell" are the same data. The
    decision records that it did not take the stronger reading.
    """
    v = _load("profile_validation.json")
    core = next(t for t in v["targets"] if "§9.3" in t["target"])
    if core["delta_brier_ci95"]["includes_zero"]:
        assert "PROFILE_NOT_PREDICTIVE" in v["decision"]["not_claimed"]


# ------------------------------------------------------------------- §11/§12
def test_failure_memory_is_still_unreachable():
    h = _load("memory_hold.json")
    assert h["memory_injection"] == "OFF"
    assert h["failure_initial_plan_ids"] == []
    assert h["cross_case_review_queue"]["consumed_by_s4a"] is False
    assert h["cross_case_review_queue"]["review_labels"] == "all UNREVIEWED"


def test_the_leakage_check_ran_over_every_snapshot():
    """§13 item 2, and the check must not be vacuous."""
    lk = _load("leakage_check.json")
    assert lk["snapshots_checked"] == len(DEV), "one check per snapshot"
    assert lk["passed"] and not lk["violations"]
    assert lk["dev_eval_disjoint"]


# ---------------------------------------------------------------- §5 attribution
def test_unattributed_nodes_are_not_assigned_to_a_role():
    """§5 forbids heuristic assignment; the 43 NO_LINK nodes stay unattributed.

    If they were spread across roles the profile's denominators would all be
    slightly wrong and nothing in the output would show it.
    """
    fz = _load("profile_freeze_10_39.json")
    a = fz["attribution"]
    assert a["no_link_nodes"] > 0, "real data has unattributed nodes"
    total = sum(fz["roles"][r]["attributed_nodes"] for r in ROLES)
    assert total == a["attributable_nodes"]
    assert total + a["no_link_nodes"] == a["n_decisions"], (
        "every decision is either attributed or counted as NO_LINK; none may "
        "vanish")


def test_the_profile_rebuilds_identically():
    """§13 item 1: the profile is deterministically reconstructible.

    Re-observes one dev case and re-summarises it, then compares against the
    stored snapshot. The profile has no model, no sampling and no clock in its
    path -- this checks that no such dependency has crept in, which is what
    makes "rebuild it and look" a valid instruction to a reviewer.
    """
    from traceevo.s4a_profile import observe, summarise
    a = observe(10)
    b = observe(10)
    assert a == b, "observation must be a pure function of the artifacts"
    sa, sb = summarise([a]), summarise([b])
    for r in ROLES:
        for k in ("n_invocations", "attributed_nodes", "discarded_calls",
                  "evidence_attributed_p0", "p3_used_in_reasoning"):
            assert sa["roles"][r][k] == sb["roles"][r][k]
    stored = _load("profile_snapshots/profile_after_case_10.json")
    for r in ROLES:
        assert stored["roles"][r]["n_invocations"] == \
            sa["roles"][r]["n_invocations"]
        assert stored["roles"][r]["attributed_nodes"] == \
            sa["roles"][r]["attributed_nodes"]
