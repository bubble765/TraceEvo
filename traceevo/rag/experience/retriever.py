"""Admission-filtered dense retrieval over prior-task features, not diagnoses."""
from __future__ import annotations

import math
from typing import Any, Literal, Protocol

from traceevo.evolution.memory_formatter import render as render_experience_payload
from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import RagStore
from traceevo.rag.medical_knowledge.reranker import PassageReranker, Qwen3Reranker
from traceevo.rag.medical_knowledge.vector_store import VectorSearchPoint

from .indexer import ExperienceEmbedder, IndexedExperience, admitted_experiences
from .prompts import EXPERIENCE_RERANK_INSTRUCTION
from .vector_store import ExperienceSearchPoint

ExperienceChannel = Literal["initial_plan", "recovery"]


class AdmittedExperienceSource(Protocol):
    def entries(self, partition: str) -> list[dict[str, Any]]: ...


class ExperienceVectorReader(Protocol):
    dimension: int

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        allowed_experience_ids: list[str],
        include_failures: bool = False,
    ) -> list[ExperienceSearchPoint]: ...


class ExperienceRetriever:
    """Search only currently admitted memories and inject only safe payloads.

    `min_reranker_score` must be selected from a retrieval validation set; no
    arbitrary nearest neighbor is returned when the score is below it.
    """

    def __init__(
        self,
        embedder: ExperienceEmbedder,
        vector_store: ExperienceVectorReader,
        admission_source: AdmittedExperienceSource,
        *,
        reranker: PassageReranker | None = None,
        retrieve_k: int = 20,
        final_k: int = 2,
        min_reranker_score: float,
    ) -> None:
        if embedder.dimension != vector_store.dimension:
            raise ValueError("experience embedder and vector store dimensions do not match")
        if min(retrieve_k, final_k) <= 0:
            raise ValueError("experience retrieval limits must be positive")
        if not math.isfinite(min_reranker_score):
            raise ValueError("min_reranker_score must be finite")
        self.embedder = embedder
        self.vector_store = vector_store
        self.admission_source = admission_source
        self.reranker = reranker or Qwen3Reranker(instruction=EXPERIENCE_RERANK_INSTRUCTION)
        self.retrieve_k = retrieve_k
        self.final_k = final_k
        self.min_reranker_score = min_reranker_score

    def retrieve(
        self,
        query: str,
        *,
        query_id: str,
        limit: int | None = None,
        channel: ExperienceChannel = "initial_plan",
    ) -> SourceAwareResult:
        return self.retrieve_many(
            [(query_id, query)],
            query_id=query_id,
            limit=limit,
            channel=channel,
        )

    def retrieve_many(
        self,
        queries: list[tuple[str, str]],
        *,
        query_id: str,
        limit: int | None = None,
        channel: ExperienceChannel = "initial_plan",
    ) -> SourceAwareResult:
        if not query_id.strip():
            raise ValueError("query_id cannot be empty")
        if not queries or len(queries) > 4:
            raise ValueError("Experience retrieval requires one to four subqueries")
        query_ids = [item[0] for item in queries]
        query_texts = [item[1] for item in queries]
        if any(not isinstance(value, str) or not value.strip() for value in query_ids + query_texts):
            raise ValueError("Experience subquery IDs and text cannot be empty")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("Experience subquery IDs must be unique")
        if channel not in {"initial_plan", "recovery"}:
            raise ValueError("experience channel must be 'initial_plan' or 'recovery'")
        final_limit = self.final_k if limit is None else min(limit, self.final_k)
        if final_limit <= 0:
            raise ValueError("limit must be positive")

        active = admitted_experiences(
            self.admission_source,
            partitions=("success",) if channel == "initial_plan" else ("success", "failure"),
        )
        if not active:
            return self._empty(query_id, "no experiences admitted for this retrieval channel")
        by_id = {item.record.experience_id: item for item in active}

        batch_embed = getattr(self.embedder, "embed_queries", None)
        query_vectors = batch_embed(query_texts) if callable(batch_embed) else [
            self.embedder.embed_query(text) for text in query_texts
        ]
        if len(query_vectors) != len(queries):
            raise RuntimeError("experience embedder returned the wrong number of query vectors")
        for vector in query_vectors:
            if len(vector) != self.vector_store.dimension or any(
                not math.isfinite(float(value)) for value in vector
            ):
                raise RuntimeError("experience embedder returned an invalid query vector")

        # Qdrant searches stay local and synchronous; all per-query candidate
        # pools are combined before the cross-encoder pass.
        candidates_by_id: dict[str, tuple[IndexedExperience, ExperienceSearchPoint]] = {}
        vector_scores: dict[str, dict[str, float]] = {subquery_id: {} for subquery_id in query_ids}
        for (subquery_id, _text), vector in zip(queries, query_vectors, strict=True):
            vector_hits = self.vector_store.search(
                vector,
                limit=self.retrieve_k,
                allowed_experience_ids=list(by_id),
                include_failures=(channel == "recovery"),
            )
            for hit in vector_hits:
                current = by_id.get(hit.experience_id)
                if current is None:
                    continue
                # A Qdrant point can be stale after memory revocation/edit.
                if hit.partition != current.partition or hit.fingerprint != current.fingerprint:
                    continue
                if hit.experience_type != current.record.experience_type:
                    continue
                candidates_by_id.setdefault(hit.experience_id, (current, hit))
                vector_scores[subquery_id][hit.experience_id] = hit.score
        if not candidates_by_id:
            return self._empty(query_id, "no current admitted experience vectors matched")

        candidate_items = list(candidates_by_id.values())
        passages = [self._as_passage(record, point) for record, point in candidate_items]
        pair_scores = self._score_query_candidate_pairs(queries, passages)
        best_by_id: dict[str, tuple[str, float]] = {}
        for subquery_id, _text in queries:
            for passage in passages:
                score = pair_scores[(subquery_id, passage.chunk_id)]
                previous = best_by_id.get(passage.chunk_id)
                if previous is None or score > previous[1]:
                    best_by_id[passage.chunk_id] = (subquery_id, score)
        ranked_ids = sorted(
            best_by_id,
            key=lambda experience_id: best_by_id[experience_id][1],
            reverse=True,
        )
        record_by_id = {item.record.experience_id: item for item, _point in candidate_items}
        point_by_id = {item.record.experience_id: point for item, point in candidate_items}
        output: list[RetrievalHit] = []
        for experience_id in ranked_ids:
            best_query_id, best_score = best_by_id[experience_id]
            if best_score < self.min_reranker_score:
                continue
            admitted = record_by_id.get(experience_id)
            vector_hit = point_by_id.get(experience_id)
            if admitted is None or vector_hit is None:
                continue
            payload = admitted.record.experience_payload
            safe_text = render_experience_payload([{"payload": payload}])
            if not safe_text:
                continue
            output.append(RetrievalHit(
                query_id=query_id,
                store=RagStore.EXPERIENCE,
                source_id=experience_id,
                source_trace_ids=admitted.record.source_trace_ids,
                text=safe_text,
                retrieval_score=float(best_score),
                metadata={
                    "experience_type": admitted.record.experience_type,
                    "admission_partition": admitted.partition,
                    "source_trace_ids": admitted.record.source_trace_ids,
                    "audit_summary_hash": admitted.record.audit_summary_hash,
                    "matched_query_ids": [
                        subquery_id for subquery_id in query_ids
                        if experience_id in vector_scores[subquery_id]
                    ],
                    "reranker_query_id": best_query_id,
                    "vector_scores_by_query": {
                        subquery_id: scores[experience_id]
                        for subquery_id, scores in vector_scores.items()
                        if experience_id in scores
                    },
                    "reranker_scores_by_query": {
                        subquery_id: pair_scores[(subquery_id, experience_id)]
                        for subquery_id in query_ids
                    },
                    "vector_score": vector_hit.score,
                    "reranker_score": float(best_score),
                    "reranker_score_kind": "cross_encoder_logit",
                    "reranker_scope": "all_subqueries_x_merged_experiences",
                    "retrieval_score_is_clinical_confidence": False,
                    "historical_task_summary_injected": False,
                },
            ))
            if len(output) >= final_limit:
                break

        return SourceAwareResult(
            store=RagStore.EXPERIENCE,
            query_id=query_id,
            hits=output,
            empty_reason=(None if output else "no experience exceeded the calibrated reranker threshold"),
        )

    def _score_query_candidate_pairs(
        self,
        queries: list[tuple[str, str]],
        candidates: list[VectorSearchPoint],
    ) -> dict[tuple[str, str], float]:
        batch_method = getattr(self.reranker, "rerank_pairs", None)
        if callable(batch_method):
            pairs = [(text, candidate) for _subquery_id, text in queries for candidate in candidates]
            scores = batch_method(pairs)
            if len(scores) != len(pairs):
                raise RuntimeError("experience reranker returned the wrong number of pair scores")
            if any(not math.isfinite(float(score)) for score in scores):
                raise RuntimeError("experience reranker returned NaN or infinite score")
            keys = [
                (subquery_id, candidate.chunk_id)
                for subquery_id, _text in queries
                for candidate in candidates
            ]
            return dict(zip(keys, (float(score) for score in scores), strict=True))

        scores_by_pair: dict[tuple[str, str], float] = {}
        for subquery_id, text in queries:
            ranked = self.reranker.rerank(text, candidates)
            if len(ranked) != len(candidates):
                raise RuntimeError("experience reranker returned the wrong number of candidates")
            if any(not math.isfinite(float(item.score)) for item in ranked):
                raise RuntimeError("experience reranker returned NaN or infinite score")
            scores_by_pair.update({
                (subquery_id, item.point.chunk_id): float(item.score) for item in ranked
            })
        return scores_by_pair

    @staticmethod
    def _as_passage(record: IndexedExperience, point: ExperienceSearchPoint) -> VectorSearchPoint:
        return VectorSearchPoint(
            chunk_id=record.record.experience_id,
            document_id=record.record.experience_id,
            paragraph_ids=[],
            paragraph_spans=[],
            ordinal_in_document=0,
            text=record.record.task_feature_summary,
            score=point.score,
            payload={"title": "Prior diagnostic task", "heading_paths": []},
        )

    @staticmethod
    def _empty(query_id: str, reason: str) -> SourceAwareResult:
        return SourceAwareResult(
            store=RagStore.EXPERIENCE,
            query_id=query_id,
            hits=[],
            empty_reason=reason,
        )
