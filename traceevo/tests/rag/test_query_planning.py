from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import QueryMode, QueryPlan, RagStore, SubQuery
from traceevo.rag.query_planner import (
    OpenAICompatibleStructuredOutputProvider,
    QueryPlanner,
    QueryPlanningError,
)
from traceevo.rag.query_service import RagQueryService


class _Provider:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_structured(self, *, system_prompt, user_prompt, output_model):
        self.calls.append((system_prompt, json.loads(user_prompt), output_model))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _subquery(query_id: str, query_text: str, goal: str, **kwargs) -> SubQuery:
    return SubQuery(query_id=query_id, query_text=query_text, retrieval_goal=goal, **kwargs)


def _plan(store: RagStore, *queries: SubQuery, mode: QueryMode | None = None) -> QueryPlan:
    return QueryPlan(
        mode=mode or (QueryMode.SINGLE if len(queries) == 1 else QueryMode.DECOMPOSE),
        store=store,
        queries=list(queries),
    )


def _result(store: RagStore, query_id: str, *hits: RetrievalHit, task_id=None, empty_reason=None):
    return SourceAwareResult(
        store=store,
        query_id=query_id,
        task_id=task_id,
        hits=list(hits),
        empty_reason=empty_reason,
    )


def test_query_plan_enforces_mode_cardinality_and_unique_query_ids():
    single = _plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q1", "adult Ottawa ankle rule radiography indications", "find criteria"),
    )
    assert single.mode is QueryMode.SINGLE

    with pytest.raises(ValueError, match="exactly one"):
        _plan(
            RagStore.MEDICAL_KNOWLEDGE,
            _subquery("q1", "one", "goal"),
            _subquery("q2", "two", "goal"),
            mode=QueryMode.SINGLE,
        )
    with pytest.raises(ValueError, match="two to four"):
        _plan(
            RagStore.MEDICAL_KNOWLEDGE,
            _subquery("q1", "one", "goal"),
            mode=QueryMode.DECOMPOSE,
        )
    with pytest.raises(ValueError, match="unique"):
        _plan(
            RagStore.MEDICAL_KNOWLEDGE,
            _subquery("q1", "one", "goal"),
            _subquery("q1", "two", "goal"),
        )
    with pytest.raises(ValueError, match="cannot be blank"):
        _subquery("q3", "   ", "goal")


def test_planner_reformulates_and_keeps_patient_task_scope_outside_plan():
    original = "What did the ankle x-ray show?"
    planned = _plan(
        RagStore.PATIENT_EVIDENCE,
        _subquery("exam-xray", "retrieve the radiograph report for the ankle injury", "find x-ray result", exam_type="xray", modality="imaging"),
    )
    provider = _Provider(planned)
    result = QueryPlanner(provider).plan(original, store=RagStore.PATIENT_EVIDENCE, task_id="case-007")

    assert result == planned
    assert provider.calls[0][1] == {
        "requested_store": "patient_evidence",
        "user_query": original,
    }
    assert "case-007" not in provider.calls[0][1].values()
    assert "task_id" not in result.model_dump()


@pytest.mark.parametrize(
    "response, message",
    [
        (_plan(RagStore.MEDICAL_KNOWLEDGE, _subquery("q1", "rewritten medical query", "goal")), "store="),
        (_plan(RagStore.PATIENT_EVIDENCE, _subquery("q1", "What did the ankle x-ray show?", "goal")), "rewrite"),
        ({"mode": "decompose", "store": "medical_knowledge", "queries": []}, "provider output"),
    ],
)
def test_planner_rejects_wrong_store_verbatim_copy_and_invalid_schema(response, message):
    planner = QueryPlanner(_Provider(response))
    with pytest.raises(QueryPlanningError, match=message):
        planner.plan("What did the ankle x-ray show?", store=RagStore.PATIENT_EVIDENCE, task_id="case-0")


def test_planner_requires_patient_task_id_and_rejects_it_for_other_stores():
    planner = QueryPlanner(_Provider(_plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q1", "ankle radiography clinical criteria", "find criteria"),
    )))
    with pytest.raises(ValueError, match="task_id is mandatory"):
        planner.plan("x-ray rules", store=RagStore.PATIENT_EVIDENCE)
    with pytest.raises(ValueError, match="only accepted for Patient Evidence"):
        planner.plan("x-ray rules", store=RagStore.MEDICAL_KNOWLEDGE, task_id="case-0")


def test_openai_compatible_provider_sends_strict_query_plan_schema():
    calls = []
    output = _plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q1", "adult Ottawa ankle radiography rule", "find imaging criteria"),
    )

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                        refusal=None,
                        parsed=None,
                        content=output.model_dump_json(),
                    ))])

    provider = OpenAICompatibleStructuredOutputProvider(_Client(), model="test-model")
    assert QueryPlanner(provider).plan("ankle x-ray rules", store=RagStore.MEDICAL_KNOWLEDGE) == output
    request = calls[0]
    assert request["model"] == "test-model"
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["$defs"]["SubQuery"]["additionalProperties"] is False
    assert set(schema["$defs"]["SubQuery"]["required"]) == set(schema["$defs"]["SubQuery"]["properties"])


def test_openai_compatible_provider_surfaces_refusal_and_empty_payload():
    class _Client:
        def __init__(self, message):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: SimpleNamespace(
                choices=[SimpleNamespace(message=message)]
            )))

    refused = OpenAICompatibleStructuredOutputProvider(
        _Client(SimpleNamespace(refusal="refused", content=None)), model="model"
    )
    with pytest.raises(QueryPlanningError, match="refused"):
        refused.generate_structured(system_prompt="s", user_prompt="u", output_model=QueryPlan)

    empty = OpenAICompatibleStructuredOutputProvider(
        _Client(SimpleNamespace(refusal=None, content=None)), model="model"
    )
    with pytest.raises(QueryPlanningError, match="no JSON content"):
        empty.generate_structured(system_prompt="s", user_prompt="u", output_model=QueryPlan)


class _PatientTool:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[kwargs["query_id"]]


class _MedicalTool:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[kwargs["query_id"]]


class _BatchMedicalTool(_MedicalTool):
    def run_many(self, *, query_id, queries, limit=None):
        self.calls.append({"query_id": query_id, "queries": queries, "limit": limit})
        return self.responses[query_id]


class _ExperienceTool(_MedicalTool):
    def run(self, *, query_id, query, limit=None, channel="initial_plan"):
        self.calls.append({
            "query_id": query_id, "query": query, "limit": limit, "channel": channel,
        })
        return self.responses[query_id]


def test_query_service_keeps_task_id_merges_duplicates_and_caps_final_results():
    shared = RetrievalHit(
        query_id="placeholder", store=RagStore.PATIENT_EVIDENCE,
        source_id="record-1", task_id="case-9", text="X-ray found no fracture.",
    )
    only_second = RetrievalHit(
        query_id="placeholder", store=RagStore.PATIENT_EVIDENCE,
        source_id="record-2", task_id="case-9", text="MRI showed edema.",
    )
    plan = _plan(
        RagStore.PATIENT_EVIDENCE,
        _subquery("q-xray", "retrieve ankle x-ray findings", "find radiograph result", exam_type="xray"),
        _subquery("q-imaging", "retrieve ankle imaging findings", "find imaging records", modality="imaging"),
    )
    tool = _PatientTool({
        "q-xray": _result(RagStore.PATIENT_EVIDENCE, "q-xray", shared, task_id="case-9"),
        "q-imaging": _result(RagStore.PATIENT_EVIDENCE, "q-imaging", shared, only_second, task_id="case-9"),
    })
    service = RagQueryService(QueryPlanner(_Provider(plan)), patient_evidence_tool=tool)

    planned = service.retrieve(
        store=RagStore.PATIENT_EVIDENCE,
        query_id="outer-query",
        query="What imaging was performed and what did it show?",
        task_id="case-9",
        limit=1,
    )

    assert [call["task_id"] for call in tool.calls] == ["case-9", "case-9"]
    assert [call["query_id"] for call in tool.calls] == ["q-xray", "q-imaging"]
    assert planned.result.query_id == "outer-query"
    assert planned.result.task_id == "case-9"
    assert planned.result.source_ids == ["record-1"]
    assert planned.result.hits[0].metadata["matched_query_ids"] == ["q-xray", "q-imaging"]
    assert planned.result.hits[0].metadata["matched_retrieval_goals"] == [
        "find radiograph result", "find imaging records"
    ]


def test_query_service_fails_closed_on_patient_scope_violation():
    plan = _plan(
        RagStore.PATIENT_EVIDENCE,
        _subquery("q1", "retrieve this task's MRI report", "find MRI result"),
    )
    leaked = RetrievalHit(
        query_id="q1", store=RagStore.PATIENT_EVIDENCE,
        source_id="record-leak", task_id="case-other", text="Other task evidence.",
    )
    tool = _PatientTool({
        "q1": _result(RagStore.PATIENT_EVIDENCE, "q1", leaked, task_id="case-expected"),
    })
    service = RagQueryService(QueryPlanner(_Provider(plan)), patient_evidence_tool=tool)
    with pytest.raises(ValueError, match="outside the requested task scope"):
        service.retrieve(
            store=RagStore.PATIENT_EVIDENCE,
            query_id="outer",
            query="MRI findings",
            task_id="case-expected",
        )


def test_query_service_merges_medical_hits_by_reranker_score_and_reports_empty():
    plan = _plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q1", "Ottawa ankle criteria", "find rule"),
        _subquery("q2", "ankle imaging after injury", "find imaging indications"),
    )
    low = RetrievalHit(
        query_id="q1", store=RagStore.MEDICAL_KNOWLEDGE, source_id="chunk-low",
        document_id="doc", text="Low score", retrieval_score=0.2,
    )
    high = RetrievalHit(
        query_id="q2", store=RagStore.MEDICAL_KNOWLEDGE, source_id="chunk-high",
        document_id="doc", text="High score", retrieval_score=1.1,
    )
    duplicate = RetrievalHit(
        query_id="q2", store=RagStore.MEDICAL_KNOWLEDGE, source_id="chunk-low",
        document_id="doc", text="Low score", retrieval_score=0.8,
    )
    tool = _MedicalTool({
        "q1": _result(RagStore.MEDICAL_KNOWLEDGE, "q1", low),
        "q2": _result(RagStore.MEDICAL_KNOWLEDGE, "q2", high, duplicate),
    })
    service = RagQueryService(QueryPlanner(_Provider(plan)), medical_knowledge_tool=tool)
    planned = service.retrieve(
        store=RagStore.MEDICAL_KNOWLEDGE,
        query_id="outer",
        query="When are ankle radiographs indicated?",
        limit=2,
    )
    assert planned.result.source_ids == ["chunk-high", "chunk-low"]
    assert planned.result.hits[1].retrieval_score == 0.8
    assert planned.result.hits[1].metadata["matched_query_ids"] == ["q1", "q2"]

    empty_plan = _plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q-empty", "ankle radiography indications", "find criteria"),
    )
    empty_tool = _MedicalTool({
        "q-empty": _result(RagStore.MEDICAL_KNOWLEDGE, "q-empty", empty_reason="no child chunks found"),
    })
    empty_service = RagQueryService(QueryPlanner(_Provider(empty_plan)), medical_knowledge_tool=empty_tool)
    empty = empty_service.retrieve(
        store=RagStore.MEDICAL_KNOWLEDGE,
        query_id="outer-empty",
        query="ankle radiography rule",
    ).result
    assert empty.hits == []
    assert empty.empty_reason == "no child chunks found"


def test_query_service_uses_batch_medical_tool_for_planned_subqueries():
    plan = _plan(
        RagStore.MEDICAL_KNOWLEDGE,
        _subquery("q1", "criteria for ankle imaging", "find criteria"),
        _subquery("q2", "fracture imaging indications", "find indications"),
    )
    hit = RetrievalHit(
        query_id="outer", store=RagStore.MEDICAL_KNOWLEDGE,
        source_id="chunk-global", document_id="doc", text="Merged-pool result",
        retrieval_score=1.3,
        metadata={"matched_query_ids": ["q2"], "matched_retrieval_goals": ["find indications"]},
    )
    tool = _BatchMedicalTool({"outer": _result(RagStore.MEDICAL_KNOWLEDGE, "outer", hit)})
    service = RagQueryService(QueryPlanner(_Provider(plan)), medical_knowledge_tool=tool)

    result = service.retrieve(
        store=RagStore.MEDICAL_KNOWLEDGE,
        query_id="outer",
        query="When should ankle radiographs be obtained?",
        limit=3,
    )

    assert len(tool.calls) == 1
    assert tool.calls[0]["query_id"] == "outer"
    assert [query.query_id for query in tool.calls[0]["queries"]] == ["q1", "q2"]
    assert tool.calls[0]["limit"] == 3
    assert result.result.source_ids == ["chunk-global"]
    assert result.result.hits[0].metadata["matched_query_ids"] == ["q2"]


def test_query_service_routes_experience_to_the_explicit_channel():
    plan = _plan(
        RagStore.EXPERIENCE,
        _subquery("q-memory", "similar diagnostic task features and workflow", "find applicable process guidance"),
    )
    hit = RetrievalHit(
        query_id="q-memory", store=RagStore.EXPERIENCE,
        source_id="exp-1", text="[Trusted Prior Experience]", retrieval_score=1.2,
        source_trace_ids=["trace-1"],
    )
    tool = _ExperienceTool({
        "q-memory": _result(RagStore.EXPERIENCE, "q-memory", hit),
    })
    service = RagQueryService(QueryPlanner(_Provider(plan)), experience_tool=tool)

    initial = service.retrieve(
        store=RagStore.EXPERIENCE,
        query_id="outer",
        query="Find a similar prior task workflow",
        limit=2,
    )
    recovery = service.retrieve(
        store=RagStore.EXPERIENCE,
        query_id="outer-recovery",
        query="Find a similar prior task workflow",
        experience_channel="recovery",
    )

    assert [call["channel"] for call in tool.calls] == ["initial_plan", "recovery"]
    assert initial.result.source_ids == recovery.result.source_ids == ["exp-1"]
    assert initial.result.hits[0].source_trace_ids == ["trace-1"]
