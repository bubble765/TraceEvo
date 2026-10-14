import json
from types import SimpleNamespace

import pytest

from agents import expert_agent as module
from agents.expert_agent import Expert_Agent
from belief.graph_update_schema import strict_json_schema_response_format
from traceevo.rag.patient_evidence.query_request import (
    PatientRetrievalRequest, filtered_evidence, request_prompt, validate_request,
)


def payload(**changes):
    return dict({"type": 1, "decision": "retrieve", "store": "patient_evidence",
                 "exam_type": "mri",
                 "query_text": "Retrieve brain MRI findings", "retrieval_goal": "Assess structural abnormality"},
                **changes)


@pytest.mark.parametrize("changes", [
    {"type": "1"}, {"type": True}, {"task_id": 0}, {"task_id": "case-1"},
    {"exam_type": "MRI"}, {"exam_type": "unknown"}, {"query_text": 3},
    {"query_text": " "}, {"store": "medical_knowledge"}, {"extra": 1},
    {"query_text": "Retrieve CT and MRI"}, {"exam_type": "ct"},
])
def test_strict_request_rejects_invalid_fields_and_scope(changes):
    with pytest.raises(ValueError):
        validate_request(payload(**changes))


def test_schema_prompt_and_filter_are_consistent():
    request = validate_request(payload())
    schema = strict_json_schema_response_format(PatientRetrievalRequest, name="request")
    assert schema["json_schema"]["strict"] is True
    assert set(schema["json_schema"]["schema"]["required"]) == set(payload())
    instruction, user = request_prompt(belief="hypothesis", history=[], frontier="h1")
    assert "active_task_id" not in json.loads(user)
    assert "task_id" not in schema["json_schema"]["schema"]["properties"]
    assert "Do not generate task_id" in instruction
    assert "mri" in json.loads(user)["supported_exam_types"]
    assert "not available patient findings" in instruction
    text = filtered_evidence(request, "MRI showed edema.\nCBC normal.", expected_task_id="case-0")
    assert "edema" in text and "CBC" not in text
    assert filtered_evidence(request, "CBC normal.", expected_task_id="case-0") == ""
    with pytest.raises(ValueError):
        filtered_evidence(request, "MRI edema.", expected_task_id="")


def test_execution_injects_caller_scope_into_sql(monkeypatch):
    from traceevo.rag.patient_evidence.retriever import PatientEvidenceRetriever

    observed = []
    original = PatientEvidenceRetriever.retrieve

    def inspect_scope(self, **kwargs):
        observed.append(kwargs["task_id"])
        results = original(self, **kwargs)
        assert all(record.task_id == kwargs["task_id"] for record in results)
        return results

    monkeypatch.setattr(PatientEvidenceRetriever, "retrieve", inspect_scope)
    request = validate_request(payload())
    assert "edema" in filtered_evidence(request, "MRI edema.", expected_task_id="case-79")
    assert observed == ["case-79"]
    assert "task_id" not in request.model_dump()


def agent(monkeypatch):
    obj = Expert_Agent("Radiologist", SimpleNamespace(belief="structural lesion"), None, 0,
                       "DiagnosisArena", "unused.log", {"tool_names": ["Evidence_Text"]},
                       {"model_path": "test", "temperature": 0, "max_tokens": 1024,
                        "return_meta": True}, "MRI showed edema.\nCBC normal.", 2)
    monkeypatch.setattr(module, "construct_expert_analyze_prompt", lambda **kw: ("role", "context"))
    monkeypatch.setattr(module, "log_to_file", lambda *a, **k: None)
    monkeypatch.setattr(module, "_txemit", lambda *a, **k: None)
    monkeypatch.setattr(module, "_txreject", lambda *a, **k: None)
    return obj


def test_expert_loop_uses_strict_request_and_prefilters_evidence(monkeypatch):
    obj = agent(monkeypatch)
    decisions = iter(["retrieve", "analyze"])
    calls, evidence_inputs = [], []

    def llm(**kw):
        calls.append(kw)
        if kw["stage"] == "EXPERT_DECISION":
            raw = {"type": 1, "decision": next(decisions)}
        elif kw.get("response_format"):
            raw = payload()
        else:
            raw = {"type": 1, "decision": "analyze", "content": "MRI supports lesion"}
        return json.dumps(raw), {"finish_reason": "stop"}

    monkeypatch.setattr(module, "llm_generate_response", llm)
    monkeypatch.setattr(module, "evidence_retriever", lambda **kw: evidence_inputs.append(kw) or "MRI showed edema.")
    assert obj.run("h1") == "MRI supports lesion"
    assert len(evidence_inputs) == 1
    assert "CBC" not in evidence_inputs[0]["evidence_texts"]
    assert calls[1]["response_format"]["json_schema"]["strict"] is True


def test_validation_feedback_retries_once_and_never_changes_scope(monkeypatch):
    obj = agent(monkeypatch)
    calls = []

    def llm(**kw):
        calls.append(kw)
        return json.dumps(payload(task_id="case-1") if len(calls) == 1 else payload()), {}

    monkeypatch.setattr(module, "llm_generate_response", llm)
    request, _, _ = obj._patient_query_call("h1")
    assert "task_id" not in request.model_dump() and len(calls) == 2
    assert "Extra inputs are not permitted" in calls[1]["user_prompt"]
    calls.clear()
    monkeypatch.setattr(module, "llm_generate_response", lambda **kw: (json.dumps(payload(task_id="case-1")), {}))
    with pytest.raises(ValueError, match="after one correction"):
        obj._patient_query_call("h1")


def test_gateway_fallback_keeps_local_strict_validation_and_caches_mode(monkeypatch):
    obj = agent(monkeypatch)
    calls = []

    def llm(**kw):
        calls.append(kw)
        if kw["response_format"]["type"] == "json_schema":
            raise module.LLMUnsupportedResponseFormatError("unsupported")
        return json.dumps(payload()), {}

    monkeypatch.setattr(module, "llm_generate_response", llm)
    obj._patient_query_call("h1")
    obj._patient_query_call("h1")
    assert [call["response_format"]["type"] for call in calls] == ["json_schema", "json_object", "json_object"]


def test_invalid_requests_never_execute_retrieval(monkeypatch):
    obj = agent(monkeypatch)

    def llm(**kw):
        raw = ({"type": 1, "decision": "retrieve"} if kw["stage"] == "EXPERT_DECISION"
               else payload(task_id="case-1"))
        return json.dumps(raw), {"finish_reason": "stop"}

    monkeypatch.setattr(module, "llm_generate_response", llm)
    monkeypatch.setattr(module, "evidence_retriever", lambda **kw: pytest.fail("invalid request reached retrieval"))
    assert "Insufficient information" in obj.run("h1")


def test_no_matching_exam_does_not_call_extraction_llm(monkeypatch):
    obj = agent(monkeypatch)
    obj.evidence_text = "CBC normal."
    decisions = iter(["retrieve", "analyze"])

    def llm(**kw):
        if kw["stage"] == "EXPERT_DECISION":
            raw = {"type": 1, "decision": next(decisions)}
        elif kw.get("response_format"):
            raw = payload()
        else:
            raw = {"type": 1, "decision": "analyze", "content": "MRI unavailable; uncertainty remains"}
        return json.dumps(raw), {"finish_reason": "stop"}

    monkeypatch.setattr(module, "llm_generate_response", llm)
    monkeypatch.setattr(module, "evidence_retriever", lambda **kw: pytest.fail("no records should skip extraction"))
    assert obj.run("h1") == "MRI unavailable; uncertainty remains"
    assert obj.retrieval_history == [("Retrieve brain MRI findings", "")]
