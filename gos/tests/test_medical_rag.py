from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import RagStore
from utils.medical_knowledge import (
    render_medical_knowledge_result,
    resolve_medical_knowledge_corpus_paths,
)


def test_medical_rag_child_candidate_budget_defaults_to_four_and_is_overridable(monkeypatch):
    from main import parse_args

    monkeypatch.setattr(sys, "argv", ["main.py"])
    defaults = parse_args()
    assert defaults.medical_rag_child_candidates_per_document == 4
    assert defaults.medical_rag_document_candidates == 5
    assert defaults.medical_rag_top_k == 4

    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--medical-rag-child-candidates-per-document", "8", "--medical-rag-top-k", "5"],
    )
    overrides = parse_args()
    assert overrides.medical_rag_child_candidates_per_document == 8
    assert overrides.medical_rag_top_k == 5


def test_medical_corpus_versions_resolve_to_isolated_paths(tmp_path):
    v5_data, v5_qdrant, v5_collection = resolve_medical_knowledge_corpus_paths(tmp_path)
    dle_data, dle_qdrant, dle_collection = resolve_medical_knowledge_corpus_paths(
        tmp_path,
        "dle_v6",
    )

    assert v5_data == Path(tmp_path) / "traceevo_runs/rag/medical_knowledge"
    assert v5_qdrant == Path(tmp_path) / "traceevo_runs/rag/qdrant_v5"
    assert v5_collection == "traceevo_medical_knowledge_v5"
    assert dle_data == Path(tmp_path) / "traceevo_runs/rag/medical_knowledge_dle_v6"
    assert dle_qdrant == Path(tmp_path) / "traceevo_runs/rag/qdrant_dle_v6"
    assert dle_collection == "traceevo_medical_knowledge_dle_v6"
    assert v5_data != dle_data and v5_qdrant != dle_qdrant

    with pytest.raises(ValueError, match="unknown medical knowledge corpus"):
        resolve_medical_knowledge_corpus_paths(tmp_path, "missing")


def _literature_result(query: str = "urine AFB testing") -> SourceAwareResult:
    return SourceAwareResult(
        store=RagStore.MEDICAL_KNOWLEDGE,
        query_id="pilot-query",
        hits=[RetrievalHit(
            query_id="pilot-query",
            store=RagStore.MEDICAL_KNOWLEDGE,
            source_id="chunk-v5-tb-diagnostics",
            document_id="PMC-12846603",
            text="Urine specimens may be used in the microbiological work-up for tuberculosis.",
            retrieval_score=4.2,
            metadata={
                "title": "Tuberculosis: Clinical Laboratory Diagnostic Techniques",
                "source_uri": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12846603/",
                "license_id": "CC BY",
                "section_id": "section-diagnostics",
                "heading_paths": [["Laboratory diagnosis"]],
                "reranker_score_kind": "cross_encoder_logit",
            },
        )],
    )


def test_literature_formatter_keeps_patient_fact_boundary_and_citation():
    text, refs = render_medical_knowledge_result(_literature_result())
    assert "NOT PATIENT-SPECIFIC EVIDENCE" in text
    assert "Do not report these statements as findings from this patient" in text
    assert "PMC-12846603" in text
    assert "License: CC BY" in text
    assert refs[0]["source_uri"].endswith("PMC12846603/")
    assert refs[0]["retrieval_score_kind"] == "cross_encoder_logit"
    no_hit = SourceAwareResult(
        store=RagStore.MEDICAL_KNOWLEDGE,
        query_id="empty",
        empty_reason="no child chunks found",
    )
    miss, miss_refs = render_medical_knowledge_result(no_hit)
    assert "not evidence against any diagnosis" in miss
    assert miss_refs == []


def test_expert_receives_patient_and_literature_results_as_separate_history(monkeypatch, tmp_path):
    import agents.expert_agent as expert_module

    prompt_histories = []

    def make_prompt(**kwargs):
        prompt_histories.append(list(kwargs["history"]))
        return "system", "user"

    responses = iter([
        {"type": 1, "decision": "retrieve"},
        {"type": 1, "decision": "retrieve", "store": "patient_evidence",
         "exam_type": "urinalysis",
         "query_text": "urine AFB testing", "retrieval_goal": "Assess urinary infection"},
        {"type": 1, "decision": "analyze"},
        {"type": 1, "decision": "analyze", "content": "The retrieved case and literature were considered separately."},
    ])

    def fake_llm(**kwargs):
        return json.dumps(next(responses)), {"prompt_tokens": 1, "completion_tokens": 1}

    class FakeTool:
        calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return _literature_result(kwargs["query"])

    medical_tool = FakeTool()
    monkeypatch.setattr(expert_module, "construct_expert_analyze_prompt", make_prompt)
    monkeypatch.setattr(expert_module, "llm_generate_response", fake_llm)
    monkeypatch.setattr(expert_module, "evidence_retriever",
                        lambda query, evidence_texts: "Urinalysis: white cells and bacteria.")
    monkeypatch.setattr(expert_module, "log_to_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(expert_module, "_txemit", lambda *args, **kwargs: None)
    monkeypatch.setattr(expert_module, "_txparse", lambda raw, emitted=None: json.loads(raw))
    monkeypatch.setattr(expert_module, "_txreject", lambda *args, **kwargs: None)

    agent = expert_module.Expert_Agent(
        name="Laboratory_Physician",
        graph=SimpleNamespace(belief="current belief"),
        fsm=None,
        case_id=79,
        dataset="DiagnosisArena",
        log_path=str(tmp_path / "pilot.log"),
        cfg_domain={"tool_names": ["Evidence_Text"], "tool_descriptions": {}},
        llm_args={"model_path": "stub", "temperature": 0, "max_tokens": 64,
                  "return_meta": True},
        evidence_text="Urinalysis: white cells and bacteria.",
        max_retrieval_steps=2,
        medical_knowledge_tool=medical_tool,
        medical_knowledge_top_k=1,
    )

    analysis = agent.run({"node_id": "hyp-1", "label": "Genitourinary infection", "level": 1})

    assert analysis == "The retrieved case and literature were considered separately."
    assert len(medical_tool.calls) == 1
    assert medical_tool.calls[0]["query"] == "urine AFB testing"
    history_seen_by_final_decision = prompt_histories[2]
    assert len(history_seen_by_final_decision) == 2
    patient_query, patient_text = history_seen_by_final_decision[0]
    literature_query, literature_text = history_seen_by_final_decision[1]
    assert patient_query == "urine AFB testing"
    assert "Urinalysis" in patient_text
    assert "General medical literature" in literature_query
    assert "NOT PATIENT-SPECIFIC EVIDENCE" in literature_text
    assert "Tuberculosis: Clinical Laboratory Diagnostic Techniques" in literature_text
