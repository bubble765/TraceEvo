"""S4B: the guard rule, its inertness when off, and its controls.

Three properties carry this stage and each has a failure mode that would look
like success:

* **the trigger rule** -- if it never fires the stage reports a clean zero;
  `test_the_trigger_rule_can_fire` is the control, and S3B/S3C/S3D each
  recorded a version of this failure.
* **inertness when disabled** -- §9.1's claim is that adding the code changed
  nothing, and the tempting way to satisfy it is to check that the *flag* is
  read rather than that the *behaviour* is unchanged.
* **§8.2's outcomes** -- RESOLVED, BLOCKED and RETRY_FAILED are three different
  things and the fallback between them is where a guard turns a refusal into a
  silent rewrite.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "S4B_adaptive_guard")

from traceevo.audit.cross_graph_binding import RetrievalRecord  # noqa: E402
from traceevo.evolution import grounding_guard as gg  # noqa: E402


RECORD_TEXT = "Troponin I was 0.02 ng/mL, within the reference range."
ANALYSIS_TEXT = ("The ECG shows ST elevation in leads II, III and aVF; this is "
                 "consistent with an inferior myocardial infarction.")
UNSUPPORTED = "Troponin is markedly elevated"


def _records():
    return [RetrievalRecord(record_id="rec-1", retrieval_event_id="evt-1",
                            query="troponin", step_id=1,
                            expert="Laboratory_Physician", text=RECORD_TEXT)]


def _analyses():
    return [{"event_id": "evt-a1", "analysis": ANALYSIS_TEXT}]


def _cand(label, node_type="Evidence", edge="support"):
    hyp = {"id": "n2", "node_type": "Hypothesis", "label": "Acute MI",
           "confidence": 0.8, "why": "leading hypothesis"}
    node = {"id": "n1", "node_type": node_type, "label": label}
    nodes = [node, hyp] if edge else [node]
    edges = [{"src": "n1", "dst": "n2", "relation": edge}] if edge else []
    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------- the trigger
def test_the_trigger_rule_can_fire():
    """The negative result's positive control.

    If this fails, a run of case 60-69 with no triggers says nothing.
    """
    d = gg.evaluate(_cand(UNSUPPORTED), [], [], "")
    assert d["trigger"] is True
    assert [o["candidate_id"] for o in d["offenders"]] == ["n1"]
    assert d["offenders"][0]["provenance_tier"] == "P3_UNSUPPORTED_ASSERTION"


def test_refute_triggers_like_support():
    """§8 names both relations; a guard reading only `support` would miss half
    the surface it was written for."""
    assert gg.evaluate(_cand(UNSUPPORTED, edge="refute"), [], [], "")["trigger"]


def test_a_p3_without_a_support_refute_edge_does_not_trigger():
    """§8's edge condition, isolated: tier alone is not enough."""
    d = gg.evaluate(_cand(UNSUPPORTED, edge="refines"), [], [], "")
    assert d["offenders"] == []
    assert d["trigger"] is False
    assert d["tiers"][0]["provenance_tier"] == "P3_UNSUPPORTED_ASSERTION", (
        "the node is still P3; only the edge keeps it from triggering")


def test_a_sourced_node_does_not_trigger():
    """§8's tier condition, isolated: a support edge alone is not enough."""
    assert not gg.evaluate(_cand(RECORD_TEXT), _records(), [], "")["trigger"]
    assert not gg.evaluate(_cand(ANALYSIS_TEXT), [], _analyses(), "")["trigger"]


def test_a_hypothesis_never_triggers():
    """§8's node-type condition."""
    assert not gg.evaluate(_cand("Acute coronary syndrome", "Hypothesis"),
                           [], [], "")["trigger"]


# --------------------------------------------------------------- §8.2 actions
def test_strip_removes_only_the_offending_node_and_its_edges():
    """§8.2's BLOCKED action is surgical: everything else commits.

    A guard that discarded the whole proposal would be a much larger
    intervention than the one §8 authorises, and the two arms would stop being
    comparable.
    """
    # The unsupported label must share no vocabulary with the record, or the
    # frozen classifier scores it P1 (a paraphrase of that record) and the
    # test would be measuring its own setup. This cost two attempts: the first
    # control design and then this test both used a troponin claim against a
    # troponin record.
    other_claim = "The patient has a moderate pericardial effusion"
    cand = {"nodes": [{"id": "n1", "node_type": "Evidence", "label": other_claim},
                      {"id": "n2", "node_type": "Hypothesis", "label": "MI",
                       "confidence": 0.5, "why": "w"},
                      {"id": "n3", "node_type": "Evidence",
                       "label": RECORD_TEXT}],
            "edges": [{"src": "n1", "dst": "n2", "relation": "support"},
                      {"src": "n3", "dst": "n2", "relation": "support"},
                      {"src": "n1", "dst": "n3", "relation": "refines"}]}
    d = gg.evaluate(cand, _records(), [], "")
    stripped, dropped = gg.strip_offenders(cand, d["offenders"])
    ids = [n["id"] for n in stripped["nodes"]]
    assert ids == ["n2", "n3"], "the sourced node and the hypothesis stay"
    assert {"src": "n3", "dst": "n2", "relation": "support"} in stripped["edges"]
    assert {"src": "n1", "dst": "n3", "relation": "refines"} in stripped["edges"], (
        "only SUPPORT/REFUTE edges from the offender are dropped; a refines "
        "edge is outside §8's stated surface and is left alone")
    assert "node:n1" in dropped and "edge:n1->n2" in dropped


def test_resolved_commits_the_retry_delta():
    orig = _cand(UNSUPPORTED)
    d = gg.evaluate(orig, [], [], "")
    retry = _cand(RECORD_TEXT)
    rd = gg.evaluate(retry, _records(), [], "")
    committed, result, dropped = gg.apply_decision(orig, d, retry, rd)
    assert result == gg.RESOLVED and dropped == []
    assert committed is retry


def test_a_retry_that_is_still_p3_is_blocked_not_accepted():
    orig = _cand(UNSUPPORTED)
    d = gg.evaluate(orig, [], [], "")
    retry = _cand("Troponin is grossly elevated")
    rd = gg.evaluate(retry, [], [], "")
    assert rd["trigger"], "this retry is still unsourced"
    committed, result, dropped = gg.apply_decision(orig, d, retry, rd)
    assert result == gg.BLOCKED
    assert not any(n["id"] == "n1" for n in committed["nodes"])
    assert dropped


def test_an_unparseable_retry_falls_back_to_the_stripped_original():
    """§8.2's RETRY_FAILED: still no unsourced node, and the task continues."""
    orig = _cand(UNSUPPORTED)
    d = gg.evaluate(orig, [], [], "")
    committed, result, dropped = gg.apply_decision(orig, d, None, None)
    assert result == gg.RETRY_FAILED
    assert not any(n["id"] == "n1" for n in committed["nodes"]), (
        "the fallback must not commit the node the guard refused")
    assert dropped


def test_the_guard_never_rewrites_content():
    """§8.2: no turning an assertion into a fact, no inventing a source.

    Everything the guard commits is either the model's own retry or a subset of
    the original proposal. Nothing is synthesised.
    """
    orig = _cand(UNSUPPORTED)
    d = gg.evaluate(orig, [], [], "")
    for retry, rd in ((None, None),
                      (_cand("Troponin is grossly elevated"),
                       gg.evaluate(_cand("Troponin is grossly elevated"),
                                   [], [], ""))):
        committed, _r, _d = gg.apply_decision(orig, d, retry, rd)
        original_labels = {n["label"] for n in orig["nodes"]}
        for n in committed["nodes"]:
            assert n["label"] in original_labels or retry is not None, (
                "a committed node that was in neither the original nor the "
                "retry would be the guard writing content")


def test_max_retry_is_one():
    """§8.2 caps retries; an uncapped guard is a verification loop."""
    assert gg.MAX_RETRY == 1
    assert "max_retry" in gg.action_record(
        gg.evaluate(_cand(UNSUPPORTED), [], [], ""), gg.BLOCKED)


def test_the_action_record_carries_the_required_hashes():
    """§8.1's GUARD_TRIGGER / GUARD_RETRY provenance."""
    d = gg.evaluate(_cand(UNSUPPORTED), [], [], "")
    rec = gg.action_record(d, gg.RESOLVED, retry_prompt=gg.RETRY_PROMPT,
                           retry_response="{}", retry_decision=d)
    for k in ("original_candidate_hash", "retry_prompt_hash",
              "retry_response_hash", "guard_result", "guard_id"):
        assert k in rec and rec[k] != "", f"§8.1 requires {k}"


# ------------------------------------------------------------ inertness §9.1
def test_the_hook_returns_none_and_writes_nothing_when_disabled():
    """§9.1's claim, at the level the pipeline sees.

    `None` means the caller keeps the object it parsed. It is not "an equal
    dict" -- an equal-but-different object would still be a change to what the
    run committed.
    """
    from traceevo import config as tx_config
    from traceevo import hooks
    os.environ["TRACEEVO_ENABLED"] = "1"
    os.environ["TRACEEVO_GUARD"] = "0"
    tx_config.load(force_reload=True)
    assert tx_config.guard_enabled() is False
    cand = _cand(UNSUPPORTED)
    assert hooks.on_graph_candidate(cand, "", "", {}) is None, (
        "a disabled guard must not hand back a replacement proposal")


def test_the_disabled_hook_does_not_even_classify(monkeypatch):
    """Off means off: no classification work happens either.

    Weaker than behaviour, but it catches a guard that is inert only because
    its verdict happened to be "no trigger" on the data at hand.
    """
    from traceevo import config as tx_config
    from traceevo import hooks
    os.environ["TRACEEVO_ENABLED"] = "1"
    os.environ["TRACEEVO_GUARD"] = "0"
    tx_config.load(force_reload=True)
    called = []
    monkeypatch.setattr(gg, "evaluate",
                        lambda *a, **k: called.append(1) or {"trigger": False})
    hooks.on_graph_candidate(_cand(UNSUPPORTED), "", "", {})
    assert called == [], "a disabled guard must not run the rule at all"


def test_normalisation_guarantees_the_keys_the_caller_indexes():
    """§14's system-correctness clause: the guard must not introduce a crash.

    `central_agent` reads `response_dict["nodes"]` and `["edges"]` directly, so
    a retry that omits one would raise there.
    """
    from traceevo.hooks import _txguard_schema_ok, _txnormalise_guard
    assert _txnormalise_guard({"nodes": []})["edges"] == []
    assert set(_txnormalise_guard({})) >= {"nodes", "edges", "edit"}
    assert _txguard_schema_ok({"nodes": [{"id": "a", "node_type": "Evidence",
                                          "label": "x"}], "edges": []})
    assert not _txguard_schema_ok({"nodes": [{"id": "a"}], "edges": []}), (
        "a retry missing node_type or label must be treated as RETRY_FAILED")


def test_the_frozen_classifier_is_imported_not_reimplemented():
    """The guard must not carry its own opinion of what P3 means."""
    src = open(os.path.join(ROOT, "traceevo", "evolution",
                            "grounding_guard.py"), encoding="utf-8").read()
    assert "from traceevo.audit.evidence_auditor import classify_tier" in src
    assert "P3_UNSUPPORTED_ASSERTION" in src
    # and it must not define a threshold of its own
    for forbidden in ("DIRECT_MIN_COVERAGE", "SOFT_MIN_COVERAGE",
                      "soft_min_char_similarity =", "def classify_tier"):
        assert forbidden not in src


# ------------------------------------------------------------------ artifacts
def test_the_policy_manifest_lists_the_inactive_guards():
    """The decisions *not* to act are what make the manifest reviewable."""
    p = os.path.join(OUT, "guard_policy_manifest.json")
    if not os.path.exists(p):
        pytest.skip("policy manifest not built")
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    assert [g["guard_id"] for g in m["active_guards"]] == [gg.G_P3_SOURCE]
    inactive = {g["guard_id"]: g for g in m["inactive_guards"]}
    assert set(inactive) == {gg.G_P1_SOFT, gg.G_ROLE_RISK}
    assert "S4A" in inactive[gg.G_ROLE_RISK]["reason"]
    assert "case-51" in inactive[gg.G_P1_SOFT]["reason"]
    assert m["active_guards"][0]["max_retry"] == 1


def test_the_policy_manifest_hashes_its_sources():
    p = os.path.join(OUT, "guard_policy_manifest.json")
    if not os.path.exists(p):
        pytest.skip("policy manifest not built")
    with open(p, encoding="utf-8") as f:
        m = json.load(f)
    assert m["source_artifact_hashes"]
    for blk in m["source_artifact_hashes"]:
        assert blk["combined_sha256"] and blk["n_files_hashed"] > 0


def test_the_positive_controls_all_passed():
    p = os.path.join(OUT, "positive_control_results.csv")
    if not os.path.exists(p):
        pytest.skip("controls not run")
    import csv
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(r["passed"] == "True" for r in rows), (
        "§9.2's controls gate the case 60 run")
    assert any(r["expected_trigger"] == "True" for r in rows)
    assert any(r["expected_trigger"] == "False" for r in rows)


def test_the_guard_off_regression_is_identical():
    p = os.path.join(OUT, "regression_summary.json")
    if not os.path.exists(p):
        pytest.skip("regression not run")
    with open(p, encoding="utf-8") as f:
        r = json.load(f)
    assert r["all_identical"], r["checks"]
    assert r["strict_replay_clean"], "an unconsumed recording proves nothing"
    assert r["no_guard_log_written"], (
        "a disabled guard that still writes its action log is not inert")


def test_the_baseline_arm_ran_with_the_guard_off():
    """§10's Phase 1 is a No-Guard run; a baseline that actually ran the guard
    would make the two arms the same experiment twice."""
    p = os.path.join(OUT, "baseline_risk_summary_atgg_off.json")
    if not os.path.exists(p):
        pytest.skip("baseline survey not run")
    with open(p, encoding="utf-8") as f:
        s = json.load(f)
    assert s["guard_triggers"] == 0, (
        "the baseline arm must have written no guard actions")
    assert s["gold_read"] is False
