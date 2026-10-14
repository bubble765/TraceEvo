from __future__ import annotations

import hashlib
import json

import pytest

from traceevo.evolution.experience import FAILURE_PATTERNS, Experience
from traceevo.evolution.memory_store import MemoryStore
from traceevo.rag.common.schemas import ExperienceRecord, RagStore, SubQuery
from traceevo.rag.experience.embedding import Qwen3ExperienceEmbedder
from traceevo.rag.experience.indexer import (
    ExperienceIndexError,
    ExperienceIndexer,
    IndexedExperience,
    fingerprint_experience,
)
from traceevo.rag.experience.prompts import EXPERIENCE_QUERY_INSTRUCTION, EXPERIENCE_RERANK_INSTRUCTION
from traceevo.rag.experience.retriever import ExperienceRetriever
from traceevo.rag.experience.seed_simulated import load_simulated_records
from traceevo.rag.experience.simulated_schema import SimulatedExperienceRecord
from traceevo.rag.experience.simulated_store import QdrantSimulatedExperienceStore
from traceevo.rag.experience.vector_store import ExperienceSearchPoint, QdrantExperienceStore
from traceevo.rag.medical_knowledge.reranker import RerankedChunk
from traceevo.rag.tools.experience_tool import ExperienceTool


def _success_payload():
    return {
        "type": "validated_success",
        "strategy": {
            "expert_roles_used": ["Radiologist"],
            "expert_sequence": ["Radiologist", "Pathologist", "Radiologist"],
            "planning_steps": [
                {"expert_role": "Radiologist", "intent": "interpret_existing_evidence"},
                {"expert_role": "Pathologist", "intent": "resolve_evidence_conflict"},
            ],
            "workflow_sequence": ["central_plan", "consult_expert", "retrieve_evidence"],
            "risk_flags": ["soft_grounded_evidence_present"],
            "retrieval_count": 2,
            "backtrack_count": 0,
            "drilldown_count": 1,
            "graph_update_count": 3,
            "analysis_count": 2,
            "plan_count": 1,
        },
        "grounding_constraints": [
            "Only use evidence that can be traced to the case input or to a retrieved record",
            "Verify evidence provenance before narrowing the hypothesis",
            "Soft-grounded evidence is a lead, not a verified fact; check its provenance before relying on it",
        ],
    }


def _failure_payload():
    pattern = FAILURE_PATTERNS["A1"]
    return {
        "type": "validated_failure",
        "failure_pattern": pattern["failure_pattern"],
        "avoid": list(pattern["avoid"]),
        "recommended_check": list(pattern["recommended_check"]),
    }


def _experience(memory_id: str, payload: dict, key: str, verdict: str) -> Experience:
    return Experience(
        memory_id=memory_id,
        source_trace_id=f"trace-{memory_id}",
        task_id=f"task-{memory_id}",
        task_retrieval_key=key,
        outcome_verdict=verdict,
        audit_summary_hash=hashlib.sha256(memory_id.encode()).hexdigest(),
        payload=payload,
        source_trace_hashes={"trace": hashlib.sha256(f"trace-{memory_id}".encode()).hexdigest()},
    )


class _FakeEmbedder:
    dimension = 3

    def __init__(self):
        self.document_texts = []
        self.queries = []

    def embed_documents(self, texts):
        self.document_texts.extend(texts)
        return [[1.0, 0.0, 0.0] if "ankle" in text.casefold() else [0.0, 1.0, 0.0] for text in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return [1.0, 0.0, 0.0]


class _FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.seen = []

    def rerank(self, query, candidates):
        self.seen = list(candidates)
        return sorted(
            [RerankedChunk(point, self.scores[point.chunk_id]) for point in candidates],
            key=lambda item: item.score,
            reverse=True,
        )


def test_experience_models_use_task_similarity_instructions_not_medical_kb_instructions():
    embedder = Qwen3ExperienceEmbedder(dimension=32)
    captured = []

    class _FakeEmbeddingModel:
        def encode(self, inputs, **_kwargs):
            captured.extend(inputs)
            return [[1.0, *([0.0] * 31)] for _ in inputs]

    embedder._model = _FakeEmbeddingModel()
    embedder.embed_query("adult task with acute ankle swelling")
    assert captured[0].startswith(f"Instruct: {EXPERIENCE_QUERY_INSTRUCTION}\nQuery: ")

    from traceevo.rag.medical_knowledge.reranker import Qwen3Reranker

    pair_inputs = []

    class _FakeCrossEncoder:
        def predict(self, inputs, **kwargs):
            pair_inputs.extend(inputs)
            assert kwargs["prompt"] == EXPERIENCE_RERANK_INSTRUCTION
            return [0.8 for _ in inputs]

    reranker = Qwen3Reranker(instruction=EXPERIENCE_RERANK_INSTRUCTION)
    reranker._model = _FakeCrossEncoder()
    record = ExperienceRecord(
        experience_id="exp-x",
        experience_type="validated_success",
        task_feature_summary="adult ankle injury task",
        experience_payload=_success_payload(),
        source_trace_ids=["trace-x"],
        audit_summary_hash="a" * 64,
        outcome_verdict="verified_success",
    )
    indexed = IndexedExperience(
        record=record,
        partition="success",
        fingerprint=fingerprint_experience(record, "success"),
    )
    point = ExperienceSearchPoint("exp-x", "validated_success", "success", indexed.fingerprint, 0.7)
    retriever_input = ExperienceRetriever._as_passage(indexed, point)
    assert reranker.rerank_pairs([("current task summary", retriever_input)]) == [0.8]
    assert "adult ankle injury task" in pair_inputs[0][1]
    assert "diagnosis" not in pair_inputs[0][1].casefold()


def test_simulated_batch_is_explicitly_non_online_and_stored_separately(tmp_path):
    records = load_simulated_records()
    assert len(records) == 32
    assert sum(record.simulation_type == "simulated_success_pattern" for record in records) == 16
    assert sum(record.simulation_type == "simulated_prevention_pattern" for record in records) == 16
    assert all(record.simulation_only and not record.online_eligible for record in records)
    assert all("synthetic" in record.simulation_basis.casefold() for record in records)

    embedder = _FakeEmbedder()
    vectors = embedder.embed_documents([record.task_feature_summary for record in records])
    store = QdrantSimulatedExperienceStore(tmp_path / "simulation-qdrant", dimension=3)
    assert store.upsert(records, vectors) == 32
    hits = store.search([1.0, 0.0, 0.0], limit=10)
    assert len(hits) == 10
    assert all(hit.record.online_eligible is False for hit in hits)
    assert store.collection_name != "traceevo_experience_v1"

    forbidden = records[0].model_copy(update={"online_eligible": True})
    with pytest.raises(ValueError, match="online-ineligible"):
        store.upsert([forbidden], [vectors[0]])


def test_simulated_record_schema_rejects_gold_and_admission_claims():
    record = load_simulated_records()[0].model_dump(mode="json")
    record["simulated_payload"]["final_diagnosis"] = "forbidden"
    with pytest.raises(ValueError, match="cannot include"):
        SimulatedExperienceRecord.model_validate(record)

    record = load_simulated_records()[0].model_dump(mode="json")
    record["simulation_only"] = False
    with pytest.raises(ValueError):
        SimulatedExperienceRecord.model_validate(record)


def _memory_store(tmp_path):
    store = MemoryStore(str(tmp_path / "admitted-memories"), enabled=True,
                        initial_plan_partitions=("success",))
    store.add(
        _experience("exp-success", _success_payload(),
                    "adult acute ankle injury with malleolar tenderness", "verified_success"),
        "eligible",
    )
    store.add(
        _experience("exp-failure", _failure_payload(),
                    "adult acute ankle injury with swelling", "verified_incorrect"),
        "failure_experience",
    )
    store.add(
        _experience("exp-review", _success_payload(),
                    "manual review task features", "uncertain"),
        "manual_review",
    )
    store.add(
        _experience("exp-reject", _success_payload(),
                    "rejected task features", "verified_incorrect"),
        "reject",
    )
    return store


def test_indexer_reads_only_admitted_partitions_and_keeps_payload_out_of_qdrant(tmp_path):
    memories = _memory_store(tmp_path)
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-experience", dimension=3)
    embedder = _FakeEmbedder()

    result = ExperienceIndexer(embedder, qdrant).index_memory_store(memories)

    assert result.indexed == 2
    assert result.validated_success == result.validated_failure == 1
    assert embedder.document_texts == [
        "adult acute ankle injury with malleolar tenderness",
        "adult acute ankle injury with swelling",
    ]
    assert qdrant.count() == 2
    raw_points, _next = qdrant._get_client().scroll(
        collection_name=qdrant.collection_name,
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert set(raw_points[0].payload) == {
        "record_type", "experience_id", "experience_type", "partition", "fingerprint"
    }
    success_only = qdrant.search(
        [1.0, 0.0, 0.0], limit=10,
        allowed_experience_ids=["exp-success", "exp-failure"],
    )
    all_admitted = qdrant.search(
        [1.0, 0.0, 0.0], limit=10,
        allowed_experience_ids=["exp-success", "exp-failure"],
        include_failures=True,
    )
    assert [hit.experience_id for hit in success_only] == ["exp-success"]
    assert {hit.experience_id for hit in all_admitted} == {"exp-success", "exp-failure"}


def test_indexer_accepts_legacy_success_strategy_shape(tmp_path):
    """Previously admitted strategies lack v2 sequence/intent fields.

    Those records remain useful as coarse collaboration examples and should
    not need to be rewritten before an index refresh.
    """
    legacy_payload = {
        "type": "validated_success",
        "strategy": {
            "expert_roles_used": ["Radiologist", "Pathologist"],
            "retrieval_count": 3,
            "backtrack_count": 1,
            "drilldown_count": 2,
            "graph_update_count": 4,
            "analysis_count": 3,
            "plan_count": 2,
        },
        "grounding_constraints": [
            "Only use evidence that can be traced to the case input or to a retrieved record",
            "Verify evidence provenance before narrowing the hypothesis",
        ],
    }
    memories = MemoryStore(str(tmp_path / "legacy-memories"), enabled=True)
    memories.add(
        _experience("exp-legacy", legacy_payload,
                    "adult ankle injury with focal tenderness", "verified_success"),
        "eligible",
    )
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-legacy", dimension=3)

    result = ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)

    assert result.indexed == result.validated_success == 1
    assert result.validated_failure == 0
    assert qdrant.search(
        [1.0, 0.0, 0.0], limit=1,
        allowed_experience_ids=["exp-legacy"],
    )[0].experience_id == "exp-legacy"


def test_experience_retriever_defaults_to_success_and_renders_only_safe_payload(tmp_path):
    memories = _memory_store(tmp_path)
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-experience", dimension=3)
    ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)
    reranker = _FakeReranker({"exp-success": 1.4, "exp-failure": 2.0})
    retriever = ExperienceRetriever(
        _FakeEmbedder(), qdrant, memories,
        reranker=reranker,
        retrieve_k=10,
        final_k=2,
        min_reranker_score=0.5,
    )

    initial = retriever.retrieve(
        "similar adult ankle injury workflow", query_id="q-initial", channel="initial_plan"
    )
    assert initial.store is RagStore.EXPERIENCE
    assert initial.source_ids == ["exp-success"]
    hit = initial.hits[0]
    assert hit.source_trace_ids == ["trace-exp-success"]
    assert "NOT patient facts" in hit.text
    assert "adult acute ankle injury with malleolar tenderness" not in hit.text
    assert "verified_success" not in json.dumps(hit.model_dump())
    assert hit.metadata["historical_task_summary_injected"] is False
    assert hit.metadata["admission_partition"] == "success"

    recovery = retriever.retrieve(
        "similar adult ankle injury workflow", query_id="q-recovery", channel="recovery"
    )
    assert set(recovery.source_ids) == {"exp-success", "exp-failure"}
    assert "Pattern to avoid" in next(hit.text for hit in recovery.hits if hit.source_id == "exp-failure")


def test_experience_retriever_returns_empty_below_validated_threshold(tmp_path):
    memories = _memory_store(tmp_path)
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-experience", dimension=3)
    ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)
    retriever = ExperienceRetriever(
        _FakeEmbedder(), qdrant, memories,
        reranker=_FakeReranker({"exp-success": 0.4}),
        min_reranker_score=0.5,
    )
    result = retriever.retrieve("similar task", query_id="q-empty")
    assert result.hits == []
    assert "threshold" in result.empty_reason


def test_experience_retriever_rejects_stale_vectors_after_memory_edit(tmp_path):
    memories = _memory_store(tmp_path)
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-experience", dimension=3)
    ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)
    success_file = next((tmp_path / "admitted-memories" / "success").glob("*.json"))
    entry = json.loads(success_file.read_text(encoding="utf-8"))
    entry["task_retrieval_key"] = "edited task features after vector indexing"
    success_file.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

    retriever = ExperienceRetriever(
        _FakeEmbedder(), qdrant, memories,
        reranker=_FakeReranker({"exp-success": 1.4}),
        min_reranker_score=0.5,
    )
    result = retriever.retrieve("similar task", query_id="q-stale")
    assert result.hits == []
    assert result.empty_reason == "no current admitted experience vectors matched"


def test_experience_tool_pools_subqueries_before_one_global_reranker_batch(tmp_path):
    memories = _memory_store(tmp_path)
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-experience", dimension=3)
    ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)

    class _PairReranker:
        def __init__(self):
            self.pairs = []

        def rerank_pairs(self, pairs):
            self.pairs = list(pairs)
            scores = {
                ("presentation match", "exp-success"): 1.1,
                ("workflow failure mode", "exp-success"): 0.2,
                ("presentation match", "exp-failure"): 0.3,
                ("workflow failure mode", "exp-failure"): 1.5,
            }
            return [scores[(query, point.chunk_id)] for query, point in pairs]

        def rerank(self, query, candidates):
            raise AssertionError("multi-query experience search should use one pair batch")

    reranker = _PairReranker()
    retriever = ExperienceRetriever(
        _FakeEmbedder(), qdrant, memories,
        reranker=reranker, retrieve_k=10, final_k=2, min_reranker_score=0.5,
    )
    tool = ExperienceTool(retriever)
    result = tool.run_many(
        query_id="outer",
        channel="recovery",
        queries=[
            SubQuery(query_id="q-presentation", query_text="presentation match", retrieval_goal="match features"),
            SubQuery(query_id="q-process", query_text="workflow failure mode", retrieval_goal="match process"),
        ],
        limit=2,
    )

    assert [hit.source_id for hit in result.hits] == ["exp-failure", "exp-success"]
    assert len(reranker.pairs) == 4  # two subqueries × two unique experiences
    first = result.hits[0]
    assert first.metadata["reranker_query_id"] == "q-process"
    assert first.metadata["reranker_query_goal"] == "match process"
    assert first.metadata["matched_retrieval_goals"] == ["match features", "match process"]


def test_experience_indexer_rejects_payload_fields_outside_the_safe_allowlist(tmp_path):
    memories = MemoryStore(str(tmp_path / "unsafe-memories"), enabled=True)
    payload = _success_payload()
    payload["historical_final_diagnosis"] = "FORBIDDEN_GOLD"
    memories.add(
        _experience("exp-unsafe", payload, "acute ankle injury", "verified_success"),
        "eligible",
    )
    qdrant = QdrantExperienceStore(tmp_path / "qdrant-unsafe", dimension=3)
    with pytest.raises(ExperienceIndexError, match="unsupported fields"):
        ExperienceIndexer(_FakeEmbedder(), qdrant).index_memory_store(memories)
    assert qdrant.count() == 0
