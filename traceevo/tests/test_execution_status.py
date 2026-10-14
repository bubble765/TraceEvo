"""S2 §3.1: observable execution status.

The three tests the amendment names, each pinning one outcome of the same code
path so a future change cannot silently collapse them:

    test_budget_exhaustion_emits_degraded
    test_invalid_dispatch_emits_failed_then_reraises
    test_normal_analysis_emits_success

What matters for the non-invasiveness claim (S1 §5): the *text* upstream
returns and the *exception* upstream raises are unchanged. Only the recorded
observation differs. Each test therefore also asserts the upstream-visible
behaviour, not just the event.
"""
import json

import pytest

from traceevo import hooks
from traceevo.audit.event_schema import REASON_CODES, STATUSES

# The fixed upstream fallback string (expert_agent.py); S2 must not alter it.
UPSTREAM_FALLBACK = ("Insufficient information to complete the analysis "
                     "(max retrieval steps reached or invalid decision/response)")
PATIENT_QUERY = {"type": 1, "decision": "retrieve", "store": "patient_evidence",
                 "exam_type": "blood_test", "query_text": "troponin",
                 "retrieval_goal": "Check troponin evidence for ACS"}


def _enable(tmp_path, task_id):
    import os
    os.environ.pop("TRACEEVO_DISABLED", None)
    from traceevo import config as tx_config
    tx_config.load(force_reload=True)
    ctx = hooks.start_task(task_id, str(tmp_path / task_id))
    assert ctx is not None, "provenance must be enabled for this test"
    return ctx


def _mk_expert(tmp_path, task_id, max_retrieval_steps=2, name="Cardiologist"):
    from belief.graph import BeliefGraph
    from belief.fsm import BeliefFSM
    from agents.expert_agent import Expert_Agent

    graph = BeliefGraph()
    graph.add_node(node_type="Signal", label="chest pain")
    return Expert_Agent(
        name=name, graph=graph, fsm=BeliefFSM(), case_id=0,
        dataset="DiagnosisArena", log_path=str(tmp_path / "trace.txt"),
        cfg_domain={"tool_names": ["Evidence_Text"], "tool_descriptions": {}},
        llm_args={"model_path": "stub", "temperature": 0.0, "max_tokens": 32,
                  "return_meta": True, "session_max_steps": 3,
                  "max_retrieval_steps": max_retrieval_steps},
        evidence_text="Blood test: Troponin was elevated at 4.2 ng/mL.",
        max_retrieval_steps=max_retrieval_steps)


class _StubLLM:
    """Replays a fixed list of responses; every call returns the same shape as
    the real `llm_generate_response` (text, meta)."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, user_prompt="", model_path="", temperature=0.0,
                 max_tokens=0, system_prompt="", return_meta=True, **kw):
        self.calls.append(user_prompt)
        if not self.responses:
            raise AssertionError(
                f"stub LLM exhausted after {len(self.calls)} calls")
        body = self.responses.pop(0)
        if not isinstance(body, str):
            body = json.dumps(body)
        return body, {"prompt_tokens": 1, "completion_tokens": 1}


def _stub_prompts(monkeypatch):
    """Prompt templates are read from cwd-relative files and are irrelevant to
    status bookkeeping; return empty strings so the test does not depend on
    them (and never touches the network)."""
    import agents.expert_agent as ea
    monkeypatch.setattr(ea, "construct_expert_analyze_prompt",
                        lambda **kw: ("", ""))


def _stub_retriever(monkeypatch):
    import agents.expert_agent as ea
    seen = []

    def fake(query, evidence_texts):
        seen.append(query)
        # The Expert_Agent owns the instrumentation boundary: it records the
        # completed TOOL_CALL after this retriever returns.
        return "Troponin was elevated at 4.2 ng/mL."

    monkeypatch.setattr(ea, "evidence_retriever", fake)
    return seen


def _analysis_events(ctx):
    return [e for e in ctx.events if e["event_type"] == "ANALYSIS"]


# --------------------------------------------------------------- test 1
def test_budget_exhaustion_emits_degraded(tmp_path, monkeypatch):
    """The expert never chooses `analyze`; the budget runs out and upstream
    returns its fixed fallback text. S2 records degraded/BUDGET_EXHAUSTED."""
    import agents.expert_agent as ea

    ctx = _enable(tmp_path, "case-budget")
    expert = _mk_expert(tmp_path, "case-budget", max_retrieval_steps=2)
    _stub_prompts(monkeypatch)
    _stub_retriever(monkeypatch)

    # Every iteration: decide "retrieve", then produce a non-empty query.
    retrieve = {"type": 1, "decision": "retrieve", "content": "troponin"}
    stub = _StubLLM([retrieve, PATIENT_QUERY] * 2)
    monkeypatch.setattr(ea, "llm_generate_response", stub)

    hooks.on_step(1)
    hooks.on_agent_call("Primary_Physician", "Cardiologist")
    analysis = expert.run({"node_id": "n1", "label": "ACS", "level": 1})

    # Upstream-visible behaviour is untouched.
    assert analysis == UPSTREAM_FALLBACK

    evs = _analysis_events(ctx)
    assert len(evs) == 1, f"expected one ANALYSIS, got {len(evs)}"
    ev = evs[0]
    assert ev["status"] == "degraded"
    assert ev["reason_code"] == "BUDGET_EXHAUSTED"
    assert ev["payload"]["exit_cause"] == "budget"
    assert ev["payload"]["analysis"] == UPSTREAM_FALLBACK
    assert ev["status"] in STATUSES and ev["reason_code"] in REASON_CODES


# --------------------------------------------------------------- test 2
def test_invalid_dispatch_emits_failed_then_reraises(tmp_path):
    """The planner names an agent that does not exist (the A3 shape). The hook
    records a failed AGENT_CALL and the original exception still propagates."""
    from belief.graph import BeliefGraph
    from belief.fsm import BeliefFSM
    from agents.central_agent import Central_Agent
    from agents.expert_agent import Expert_Agent

    ctx = _enable(tmp_path, "case-dispatch")

    graph = BeliefGraph()
    graph.add_node(node_type="Signal", label="fever")
    expert = Expert_Agent(
        name="Laboratory_Physician", graph=graph, fsm=BeliefFSM(), case_id=0,
        dataset="DiagnosisArena", log_path=str(tmp_path / "t.txt"),
        cfg_domain={"tool_names": ["Evidence_Text"]},
        llm_args={"model_path": "stub", "temperature": 0.0, "max_tokens": 32,
                  "return_meta": True, "session_max_steps": 3,
                  "max_retrieval_steps": 1},
        evidence_text="", max_retrieval_steps=1)

    central = Central_Agent(
        name="Primary_Physician", graph=graph, fsm=BeliefFSM(),
        dataset="DiagnosisArena", log_path=str(tmp_path / "t.txt"), case_id=0,
        case_symptom="fever",
        llm_args={"model_path": "stub", "temperature": 0.0, "max_tokens": 32,
                  "return_meta": True, "session_max_steps": 3,
                  "max_retrieval_steps": 1},
        cfg_domain={}, experts=[expert], evidence_text="")

    hooks.on_step(1)
    hooks.on_plan("Primary_Physician", "[]")
    central.frontier = {"node_id": "n1", "label": "sepsis", "level": 1}

    with pytest.raises(ValueError):
        central.act([{"expert_name": "Laboratory Physician"}])  # space, not "_"

    calls = [e for e in ctx.events if e["event_type"] == "AGENT_CALL"]
    assert len(calls) == 1
    ev = calls[0]
    assert ev["status"] == "failed"
    assert ev["reason_code"] == "INVALID_EXPERT_DISPATCH"
    assert ev["payload"]["expert"] == "Laboratory Physician"
    assert ev["payload"]["available"] == ["Laboratory_Physician"]
    # The failure is attributed to the planner, and it did not fabricate a
    # successful call to the expert that does exist.
    assert ev["parent_event_ids"] == [
        e["event_id"] for e in ctx.events if e["event_type"] == "PLAN"]


# --------------------------------------------------------------- test 3
def test_normal_analysis_emits_success(tmp_path, monkeypatch):
    """The ordinary path: retrieve once, then analyse. Nothing is marked."""
    import agents.expert_agent as ea

    ctx = _enable(tmp_path, "case-normal")
    expert = _mk_expert(tmp_path, "case-normal", max_retrieval_steps=3)
    _stub_prompts(monkeypatch)
    retriever_calls = _stub_retriever(monkeypatch)

    stub = _StubLLM([
        {"type": 1, "decision": "retrieve", "content": "troponin"},
        PATIENT_QUERY,
        {"type": 1, "decision": "analyze",
         "content": "Elevated troponin supports an acute coronary syndrome."},
        {"type": 1, "decision": "analyze",
         "content": "Elevated troponin supports an acute coronary syndrome."},
    ])
    monkeypatch.setattr(ea, "llm_generate_response", stub)

    hooks.on_step(1)
    agent_call = hooks.on_agent_call("Primary_Physician", "Cardiologist")
    analysis = expert.run({"node_id": "n1", "label": "ACS", "level": 1})

    assert analysis == "Elevated troponin supports an acute coronary syndrome."
    assert retriever_calls == ["troponin"], "one real retrieval happened"

    evs = _analysis_events(ctx)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["status"] == "success"
    assert ev["reason_code"] == "NONE"
    assert ev["payload"]["exit_cause"] == ""
    assert ev["payload"]["analysis"] == analysis, "recorded text == returned text"

    # The successful analysis is a child of this dispatch and of the single
    # completed retrieval TOOL_CALL (which contains the record it read).
    retrievals = [e for e in ctx.events if e["event_type"] == "TOOL_CALL"
                  and "retrieval_output" in e["payload"]]
    assert len(retrievals) == 1
    retrieval = retrievals[0]
    assert retrieval["payload"]["retrieval_output"] == (
        "Troponin was elevated at 4.2 ng/mL.")
    assert set(ev["parent_event_ids"]) == {agent_call, retrieval["event_id"]}
    assert ev["input_refs"] == retrieval["output_refs"]

    # S2 §1 D1: model identity is reported as a name with observability stated,
    # never as a version the provider never gave us.
    assert ev["model_id"], "the backbone must be named"
    assert ev["model_version"] is None
    assert ev["version_observable"] is False


def test_status_and_reason_code_are_validated():
    """A typo must not become a new category."""
    from traceevo.audit.event_schema import ProvenanceEvent
    common = dict(event_id="e1", task_id="t", step_id=0, timestamp="now",
                  event_type="ANALYSIS", actor_id="a", actor_role="expert")
    with pytest.raises(ValueError):
        ProvenanceEvent(status="ok", **common)
    with pytest.raises(ValueError):
        ProvenanceEvent(reason_code="BUDGET", **common)
