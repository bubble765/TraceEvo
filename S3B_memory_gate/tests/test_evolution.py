"""S3B: the admission path, from blinding through the leakage guard.

The tests are grouped by the property they protect, because the properties are
what §13 makes load-bearing: a leak after the fact cannot be undone by a good
experiment, and a `manual_review` that becomes retrievable is a soft yes.
"""
from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from traceevo.evolution import experience_gate as gate
from traceevo.evolution.blinding import (gold_by_case_index,
                                         make_blinded_dataset,
                                         verify_blinded)
from traceevo.evolution.experience import (Experience, build_candidate,
                                           forbidden_content)
from traceevo.evolution.leakage_guard import guard, scan_prompt
from traceevo.evolution.memory_formatter import (inject_into_plan_prompt,
                                                 render)
from traceevo.evolution.memory_retriever import BM25Index, retrieve
from traceevo.evolution.memory_store import MemoryStore
from traceevo.evolution.outcome_verifier import (verdict_from_scores,
                                                 verify_trace)
import hashlib


# ------------------------------------------------------------------ blinding
def test_blinding_removes_the_gold_columns(tmp_path):
    src = tmp_path / "src.csv"
    pd.DataFrame([{"id": 1, "Case Information": "x",
                   "Final Diagnosis": "Foo", "Right Option": "B"}]).to_csv(
        src, index=False)
    dst = tmp_path / "blinded.csv"
    m = make_blinded_dataset(str(src), str(dst))
    assert m["columns_removed"] == ["Final Diagnosis", "Right Option"]
    assert verify_blinded(str(dst))["blinded"] is True
    assert "Final Diagnosis" not in pd.read_csv(dst).columns


def test_blinding_refuses_a_file_with_no_gold(tmp_path):
    """A vacuous blinding would make the isolation claim false, so it raises."""
    src = tmp_path / "src.csv"
    pd.DataFrame([{"id": 1, "Case Information": "x"}]).to_csv(src, index=False)
    with pytest.raises(ValueError):
        make_blinded_dataset(str(src), str(tmp_path / "b.csv"))


def test_verify_blinded_catches_a_gold_column(tmp_path):
    src = tmp_path / "src.csv"
    pd.DataFrame([{"id": 1, "Final Diagnosis": "Foo"}]).to_csv(src, index=False)
    check = verify_blinded(str(src))
    assert check["blinded"] is False
    assert check["gold_columns_present"] == ["Final Diagnosis"]


# ---------------------------------------------------------- outcome verifier
@pytest.mark.parametrize("scores,expected", [
    ([2, 2, 2], "verified_success"),
    ([0, 0, 0], "verified_incorrect"),
    ([2, 2, 1], "uncertain"),      # §4.2: score 1 never admits a success
    ([2, 0, 2], "uncertain"),
    ([1, 1, 1], "uncertain"),
    ([None, 2, 2], "uncertain"),   # a judge that failed is not a vote
    ([2, 2, None], "uncertain"),
])
def test_verdict_mapping(scores, expected):
    assert verdict_from_scores(scores)["outcome_verdict"] == expected


def test_verifier_output_contains_no_gold_text():
    gold = "Lightning-induced retinopathy with delayed-onset cystoid changes"
    trace = {"prediction": "Lightning injury", "report": "..."}
    fn = lambda s, u: ("1. Case 1 - x: score 2;", {})
    result = verify_trace(trace, gold, fn, 12)
    blob = json.dumps(result, ensure_ascii=False)
    assert gold not in blob
    assert result["outcome_verdict"] == "verified_success"
    assert result["reference_sha256"] == hashlib.sha256(
        gold.encode()).hexdigest()


def test_a_failing_judge_is_uncertain_and_not_retried():
    calls = []

    def boom(system_prompt, user_prompt):
        calls.append(1)
        raise RuntimeError("gateway down")

    result = verify_trace({"prediction": "p", "report": "r"}, "gold", boom, 1)
    assert result["outcome_verdict"] == "uncertain"
    # §4.2: no retry until it agrees. Exactly REPEATS attempts, no more.
    assert len(calls) == 3


# ------------------------------------------------------------- experience
def _audit(decisions=None, by_tier=None):
    return {"decisions": decisions or [],
            "by_tier": by_tier or {"P0_DIRECT_GROUNDED": 3,
                                   "P2_DERIVED": 1,
                                   "P3_UNSUPPORTED_ASSERTION": 0}}


def _ledger(n=6, pending=0, failed=0, uncommitted=2):
    rows = []
    for i in range(6):
        rows.append({"call_id": f"c{i}", "stage": "EXPERT_DECISION",
                     "parse_status": "pending" if i < pending else "accepted",
                     "committed_to_state": i >= uncommitted})
    for i in range(failed):
        rows.append({"call_id": f"f{i}", "stage": "EXPERT_CONTENT",
                     "parse_status": "failed", "committed_to_state": False})
    return rows


def _cand(verdict="verified_success"):
    return Experience(memory_id="m", source_trace_id="case-1", task_id="case-1",
                      task_retrieval_key="k", outcome_verdict=verdict,
                      audit_summary_hash="h",
                      payload={"type": "validated_success"})


def test_uncommitted_decision_calls_do_not_break_completeness():
    """Every expert decision is uncommitted by construction; treating that as
    an incomplete invocation would reject every trace in the corpus."""
    v = gate.evaluate(_cand(), _audit(), _ledger(uncommitted=4), [], True, True)
    assert v["eligibility"] == gate.ELIGIBLE
    assert v["ledger"]["uncommitted"] == 4


def test_a_pending_call_does_break_completeness():
    v = gate.evaluate(_cand(), _audit(), _ledger(pending=1), [], True, True)
    assert v["eligibility"] == gate.REJECT


def test_p3_on_the_decision_path_goes_to_manual_review():
    audit = _audit(decisions=[{"provenance_tier": "P3_UNSUPPORTED_ASSERTION",
                               "used_in_reasoning": True}])
    v = gate.evaluate(_cand(), audit, _ledger(), [], True, True)
    assert v["eligibility"] == gate.MANUAL_REVIEW


def test_p1_on_the_decision_path_goes_to_manual_review():
    audit = _audit(decisions=[{"provenance_tier": "P1_SOFT_GROUNDED",
                               "used_in_reasoning": True}])
    v = gate.evaluate(_cand(), audit, _ledger(), [], True, True)
    assert v["eligibility"] == gate.MANUAL_REVIEW


def test_outcome_and_audit_are_both_required():
    """The two counterexamples §7.2 is built to produce."""
    # correct result, untrustworthy process
    v = gate.evaluate(_cand("verified_success"), _audit(), _ledger(),
                      [{"type": "A1", "severity": "high",
                        "root_event_id": "e1", "detector_rule": "r"}], True, True)
    assert v["eligibility"] == gate.FAILURE_EXPERIENCE
    # clean process, wrong result
    v = gate.evaluate(_cand("verified_incorrect"), _audit(), _ledger(), [],
                      True, True)
    assert v["eligibility"] == gate.MANUAL_REVIEW


def test_forbidden_payload_content_is_rejected():
    v = gate.evaluate(_cand(), _audit(), _ledger(), [], True, True,
                      forbidden_hits=["gold:Lightning-induced retinopathy"])
    assert v["eligibility"] == gate.REJECT


def test_baselines_are_blind_in_the_expected_directions():
    audit = _audit()
    led = _ledger()
    anomalies = [{"type": "A1", "severity": "high", "root_event_id": "e1",
                  "detector_rule": "evidence_tier_p3_used"}]
    # OutcomeOnly admits a poisoned twin because the answer was right
    assert gate.baseline_outcome_only(_cand("verified_success"))[
        "eligibility"] == gate.ELIGIBLE
    # AuditOnly admits a wrong answer from a clean process
    assert gate.baseline_audit_only(_cand("verified_incorrect"), audit, led,
                                    [])["eligibility"] == gate.ELIGIBLE
    # and each rejects where the other admits
    assert gate.baseline_outcome_only(_cand("verified_incorrect"))[
        "eligibility"] == gate.REJECT
    assert gate.baseline_audit_only(_cand(), audit, led, anomalies)[
        "eligibility"] == gate.REJECT


def test_candidate_payload_carries_no_gold_or_prediction():
    events = [{"event_type": "AGENT_CALL", "actor_id": "A",
               "tool_or_model": "Radiologist"},
              {"event_type": "EVIDENCE_READ", "actor_id": "A"},
              {"event_type": "ANALYSIS", "actor_id": "A"}]
    exp = build_candidate("case-1", {}, "key", "verified_success", events,
                          _ledger(), _audit(), [])
    gold = {"final_diagnosis": "Lightning-induced retinopathy",
            "right_option": "D"}
    assert forbidden_content(exp, gold, "Lightning injury") == []
    assert exp.payload["type"] == "validated_success"
    assert "retrieval_key" not in exp.payload


def test_failure_payload_names_the_pattern_not_the_content():
    events = [{"event_type": "EVIDENCE_READ", "actor_id": "A"}]
    exp = build_candidate("case-1", {}, "key", "verified_success", events,
                          _ledger(), _audit(), ["A1"])
    assert exp.payload["type"] == "validated_failure"
    assert exp.payload["failure_pattern"] == "UNSUPPORTED_EVIDENCE"
    # no evidence text, no case text, no diagnosis
    assert "key" not in json.dumps(exp.payload)


# ------------------------------------------------------------- memory store
def test_manual_review_is_not_retrievable(tmp_path):
    store = MemoryStore(str(tmp_path), enabled=True)
    exp = _cand()
    store.add(exp, gate.ELIGIBLE)
    store.add(exp, gate.MANUAL_REVIEW)
    store.add(exp, gate.FAILURE_EXPERIENCE)
    store.add(exp, gate.REJECT)
    assert sorted(os.listdir(tmp_path)) != []
    ids = store.retrievable_ids()
    assert len(ids) == 2                      # eligible + failure only
    assert len(store.entries("manual_review")) == 1
    assert len(store.entries("rejected")) == 1
    for e in store.retrievable():
        assert e["partition"] in ("success", "failure")


def test_store_writes_nothing_when_disabled(tmp_path):
    store = MemoryStore(str(tmp_path), enabled=False)
    r = store.add(_cand(), gate.ELIGIBLE)
    assert r["stored"] is False
    assert store.retrievable_ids() == []


def test_runtime_query_matches_what_gos_builds():
    """The plan phase must retrieve with the string the run will use.

    These were different: the plan built `case_info + "\\n" + physical`, while
    `gos/Run/DiagnosisArena.py` wraps both fields in `##Case information:` /
    `## Physical Examination:` labels. The four extra content words move BM25
    enough to retrieve different memories -- on case-45, `exp-case-37` under
    one and `exp-case-34` under the other -- so the §13 guard was scanning
    prompts the planner never received (failure_cases.md FAILURE 15).

    This pins the format against GoS's own construction. If DiagnosisArena
    changes its labels, this test fails rather than the guard silently going
    stale.
    """
    from traceevo.s3b_probe import runtime_query
    ci, pe = "A patient with X", "Examination shows Y"
    q = runtime_query(ci, pe)
    assert q == (f"##Case information: {ci}\n## Physical Examination: {pe}")
    # the labels are content tokens, not decoration: if they were stripped the
    # retrieval changes, so their presence is the property under test
    for token in ("case", "information", "physical", "examination"):
        assert token in q.lower()


def test_runtime_query_is_not_the_bare_concatenation():
    """Guards the exact regression: someone 'simplifying' back to the join."""
    from traceevo.s3b_probe import runtime_query
    ci, pe = "head", "body"
    assert runtime_query(ci, pe) != f"{ci}\n{pe}"


# ---------------------------------------------------------------- retrieval
def test_retrieval_returns_nothing_below_the_floor():
    docs = [("a", "lightning strike retinopathy macular edema"),
            ("b", "cholangiocarcinoma biliary obstruction")]
    idx = BM25Index(docs)
    assert idx.top_k("unrelated query about thyroid nodules", k=2,
                     min_score=0.5) == []


def test_retrieval_is_deterministic_and_ranked():
    docs = [("a", "lightning strike retinopathy macular edema vision"),
            ("b", "macular edema vision loss lightning")]
    idx = BM25Index(docs)
    first = idx.top_k("lightning macular edema", k=2, min_score=0.0)
    second = idx.top_k("lightning macular edema", k=2, min_score=0.0)
    assert first == second
    assert first[0]["score"] >= first[-1]["score"]


def test_retrieve_over_an_empty_store_is_empty(tmp_path):
    store = MemoryStore(str(tmp_path), enabled=True)
    assert retrieve(store, "anything") == []


# ---------------------------------------------------------------- formatter
def test_formatter_returns_nothing_for_no_experience():
    """An empty block with a header would tell the planner memory was
    consulted, which is a different experiment from memory off."""
    assert render([]) == ""
    assert inject_into_plan_prompt("PLAN PROMPT", []) == "PLAN PROMPT"


def test_formatter_never_renders_the_retrieval_key():
    e = {"memory_id": "m", "task_retrieval_key":
         "Patient X with a unique secret complaint",
         "payload": {"type": "validated_success",
                     "strategy": {"expert_roles_used": ["Radiologist"],
                                  "retrieval_count": 3},
                     "grounding_constraints": ["Check provenance"]}}
    text = render([e])
    assert "unique secret complaint" not in text
    assert "Radiologist" in text
    assert "[Trusted Prior Experience]" in text
    assert "MUST NOT be treated as evidence" in text


def test_failure_block_is_labelled_separately():
    e = {"payload": {"type": "validated_failure",
                     "failure_pattern": "UNSUPPORTED_EVIDENCE",
                     "avoid": ["Do not X"], "recommended_check": ["Check Y"]}}
    text = render([e])
    assert "[Failure Prevention]" in text
    assert "Pattern to avoid: UNSUPPORTED_EVIDENCE" in text


# ------------------------------------------------------------ leakage guard
def _gold():
    return {"final_diagnosis": "Lightning-induced retinopathy with delayed "
                               "cystoid macular abnormalities",
            "right_option": "D", "case_id": "439"}


def test_guard_catches_the_exact_reference():
    r = scan_prompt("Some prompt mentioning Lightning-induced retinopathy with "
                    "delayed cystoid macular abnormalities here", _gold())
    assert r["n_leaks"] >= 1
    assert any(l["kind"] == "exact_final_diagnosis" for l in r["leaks"])


def test_guard_catches_a_paraphrase_by_ngram_overlap():
    """The realistic leak is a near-copy, not the exact string."""
    r = scan_prompt("Patient shows lightning induced retinopathy with delayed "
                    "cystoid macular changes", _gold())
    assert r["n_leaks"] >= 1


def test_guard_catches_a_historical_prediction():
    r = scan_prompt("prior case concluded Lightning injury of the retina",
                    _gold(), source_predictions=["Lightning injury of the retina"])
    assert any(l["kind"] == "exact_prediction" for l in r["leaks"])


@pytest.mark.parametrize("prompt,expect_leak", [
    ("Prior case: the answer is D.", True),
    ("Guidance: correct option: D", True),
    ("the team chose D after review", True),
    ("select option D when in doubt", True),
    ("The patient has a large dilated pupil.", False),
    ("dilated disc, dark field, delayed response", False),
    ("the correct answer is B", False),   # a cue, but not for this option
])
def test_guard_checks_the_right_option(prompt, expect_leak):
    """§13 lists `Right Option` as mandatory.

    It was dead code until a spike-in control showed three of four checks
    firing and this one silent: the option is a single letter, and the check
    was `len(v) >= 3 and v in prompt`, which no option letter can satisfy.
    These cases pin both directions -- it must fire on an option presented as
    an answer, and must not fire on the same letter inside ordinary words.
    """
    r = scan_prompt(prompt, _gold())
    assert (r["n_leaks"] > 0) is expect_leak, r["leaks"]
    if expect_leak:
        assert any(l["kind"] == "exact_right_option" for l in r["leaks"])


def test_guard_passes_a_clean_guidance_block():
    block = ("[Trusted Prior Experience]\n"
             "1. Consultation pattern: Radiologist, Pathologist were consulted.\n"
             "2. Check: Verify evidence provenance before narrowing the "
             "hypothesis.")
    r = scan_prompt(block, _gold())
    assert r["n_leaks"] == 0


def test_guard_aggregates_to_the_s13_verdict():
    good = {"prompt": "1. Check: verify provenance.", "gold": _gold(),
            "target_case_index": 40}
    bad = {"prompt": "the answer is " + _gold()["final_diagnosis"],
           "gold": _gold(), "target_case_index": 41}
    assert guard([good])["gold_leakage_count"] == 0
    assert guard([good])["verdict"] == "GO"
    stopped = guard([good, bad])
    assert stopped["gold_leakage_count"] >= 1
    assert stopped["passed"] is False
    assert "STOP" in stopped["verdict"]
