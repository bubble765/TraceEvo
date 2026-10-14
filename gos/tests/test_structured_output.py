import json

import pytest

from agents import central_agent as central_agent_module
from agents.central_agent import Central_Agent
from belief.graph_update_schema import (
    InitialEvidenceExtraction,
    validate_initial_evidence_extraction,
)
from utils.public_function import (
    LLMUnsupportedResponseFormatError,
    classify_api_error,
    parse_json_response,
)


def test_parser_cleans_fences_prelude_trailing_commas_and_single_quotes():
    raw = """Model response:
```json
{'symptom_node': 'ankle pain', 'isolated_evidence': [],}
```
Done.
"""

    assert parse_json_response(raw) == {
        "symptom_node": "ankle pain", "isolated_evidence": []
    }


def test_parser_closes_truncated_container_and_returns_first_complete_object():
    assert parse_json_response('prefix {"items": [1, 2]') == {"items": [1, 2]}
    assert parse_json_response('prefix {"outer": {"x": 1}} trailing {"y": 2}') == {
        "outer": {"x": 1}
    }


def test_parser_rejects_a_cut_inside_a_string_for_model_retry():
    with pytest.raises(json.JSONDecodeError):
        parse_json_response('prefix {"label": "unfinished')


def test_gateway_capability_error_is_typed_for_response_format_fallback():
    error = classify_api_error(
        400,
        {"type": "BadRequestError",
         "message": "response_format json_schema is not supported"},
    )
    assert isinstance(error, LLMUnsupportedResponseFormatError)


def test_gateway_response_format_unavailable_error_is_typed_for_fallback():
    error = classify_api_error(
        400,
        {"type": "invalid_request_error",
         "message": "Upstream request failed: This response_format type is unavailable now"},
    )
    assert isinstance(error, LLMUnsupportedResponseFormatError)


def test_strict_schema_call_falls_back_only_when_gateway_rejects_that_mode(monkeypatch):
    agent = object.__new__(Central_Agent)
    agent.llm_args = {
        "model_path": "test-model", "temperature": 0.0,
        "max_tokens": 1024, "return_meta": True,
    }
    agent.log_path = "test.log"
    calls = []
    discarded = []

    def fake_llm(**kwargs):
        calls.append(kwargs)
        if kwargs["response_format"]["type"] == "json_schema":
            raise LLMUnsupportedResponseFormatError(
                "gateway does not support json_schema response_format"
            )
        return "{}", {"finish_reason": "stop"}

    monkeypatch.setattr(central_agent_module, "llm_generate_response", fake_llm)
    monkeypatch.setattr(central_agent_module, "log_to_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(central_agent_module, "_txreject", discarded.append)

    response, _meta = agent._structured_llm_call(
        user_prompt="Return JSON.", system_prompt="Only JSON.",
        stage="GRAPH_UPDATE", output_model=InitialEvidenceExtraction,
    )

    assert response == "{}"
    assert calls[0]["response_format"]["json_schema"]["strict"] is True
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert all(call["stage"] == "GRAPH_UPDATE" for call in calls)
    assert discarded == ["UNSUPPORTED_RESPONSE_FORMAT"]


def test_response_format_fallback_mode_is_cached_for_later_calls(monkeypatch):
    agent = object.__new__(Central_Agent)
    agent.llm_args = {
        "model_path": "test-model", "temperature": 0.0,
        "max_tokens": 1024, "return_meta": True,
    }
    agent.log_path = "test.log"
    calls = []

    def fake_llm(**kwargs):
        calls.append(kwargs)
        if kwargs["response_format"] is not None:
            raise LLMUnsupportedResponseFormatError(
                "gateway reports response_format type unavailable"
            )
        return "{}", {"finish_reason": "stop"}

    monkeypatch.setattr(central_agent_module, "llm_generate_response", fake_llm)
    monkeypatch.setattr(central_agent_module, "log_to_file", lambda *args, **kwargs: None)

    for _ in range(2):
        response, _meta = agent._structured_llm_call(
            user_prompt="Return JSON.", system_prompt="Only JSON.",
            stage="GRAPH_UPDATE", output_model=InitialEvidenceExtraction,
        )
        assert response == "{}"

    assert [call["response_format"] for call in calls] == [
        {"type": "json_schema",
         "json_schema": calls[0]["response_format"]["json_schema"]},
        {"type": "json_object"},
        None,
        None,
    ]


def test_schema_failure_is_returned_to_model_once_then_validated(monkeypatch):
    agent = object.__new__(Central_Agent)
    agent.llm_args = {
        "model_path": "test-model", "temperature": 0.0,
        "max_tokens": 1024, "return_meta": True,
    }
    agent.log_path = "test.log"
    responses = iter([
        '{"symptom_node":"ankle pain","isolated_evidence":[],"unexpected":1}',
        '{"symptom_node":"ankle pain","isolated_evidence":[]}',
    ])
    calls = []
    rejected = []

    def fake_llm(**kwargs):
        calls.append(kwargs)
        return next(responses), {"finish_reason": "stop"}

    monkeypatch.setattr(central_agent_module, "llm_generate_response", fake_llm)
    monkeypatch.setattr(
        central_agent_module, "_txparse",
        lambda raw, emitted=None: parse_json_response(raw),
    )
    monkeypatch.setattr(central_agent_module, "_txreject", rejected.append)
    monkeypatch.setattr(
        central_agent_module, "_txemit",
        lambda name, prompt, *args, **kwargs: prompt if name == "on_recovery_prompt" else None,
    )
    monkeypatch.setattr(central_agent_module, "log_to_file", lambda *args, **kwargs: None)

    result, raw, meta = agent._validated_model_call(
        system_prompt="Return one JSON object.",
        user_prompt="Extract the symptom.",
        stage="TEST_GRAPH_UPDATE",
        output_model=InitialEvidenceExtraction,
        validator=validate_initial_evidence_extraction,
    )

    assert result.symptom_node == "ankle pain"
    assert json.loads(raw)["isolated_evidence"] == []
    assert meta["finish_reason"] == "stop"
    assert len(calls) == 2
    assert all(call["response_format"]["json_schema"]["strict"] is True
               for call in calls)
    assert "Validation error" in calls[1]["user_prompt"]
    assert "Previous response" in calls[1]["user_prompt"]
    assert rejected == ["INVALID_SCHEMA"]
