"""Document-first retrieval with document-local child search and global reranking."""
from __future__ import annotations

import math
from typing import Protocol

from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import RagStore

from .context import DocumentParagraphStore, DocumentSectionStore
from .embedding import TextEmbedder
from .reranker import PassageReranker, Qwen3Reranker
from .vector_store import QdrantMedicalKnowledgeStore, VectorSearchPoint


class MedicalKnowledgeStore(Protocol):
    dimension: int

    def search_documents(self, vector: list[float], *, limit: int): ...

    def search_chunks_for_document(self, vector: list[float], *, document_id: str, limit: int): ...


class MedicalKnowledgeRetriever:
    """Retrieve document candidates, then rerank their combined child candidates.

    The document stage routes using title+abstract vectors. Child retrieval is
    scoped to each routed document; only after that are all candidates merged
    and passed through one cross-encoder reranking call.
    """

    def __init__(
        self,
        embedder: TextEmbedder,
        store: QdrantMedicalKnowledgeStore | MedicalKnowledgeStore,
        *,
        paragraph_store: DocumentParagraphStore | None = None,
        section_store: DocumentSectionStore | None = None,
        final_k: int = 4,
        document_candidate_k: int = 5,
        child_candidates_per_document: int = 4,
        reranker: PassageReranker | None = None,
        max_context_tokens: int = 1200,
        max_anchor_tokens: int = 800,
        max_neighbor_tokens: int = 200,
        min_reranker_score: float | None = None,
    ) -> None:
        if min(final_k, document_candidate_k, child_candidates_per_document) <= 0:
            raise ValueError("retrieval limits must be positive")
        if embedder.dimension != store.dimension:
            raise ValueError("embedder and Qdrant dimensions do not match")
        if min(max_context_tokens, max_anchor_tokens) <= 0 or max_neighbor_tokens < 0:
            raise ValueError("context token limits must be positive (neighbor limit may be zero)")
        if paragraph_store is None and section_store is None:
            raise ValueError("provide a paragraph store (schema-v4) or section store (schema-v5)")
        self.embedder = embedder
        self.store = store
        self.final_k = final_k
        self.document_candidate_k = document_candidate_k
        self.child_candidates_per_document = child_candidates_per_document
        self.reranker = reranker or Qwen3Reranker()
        self.context_store = section_store or paragraph_store
        self.paragraph_store = paragraph_store
        self.section_store = section_store
        self.max_context_tokens = max_context_tokens
        self.max_anchor_tokens = max_anchor_tokens
        self.max_neighbor_tokens = max_neighbor_tokens
        if min_reranker_score is not None and not math.isfinite(min_reranker_score):
            raise ValueError("rejection threshold must be finite")
        self.min_reranker_score = min_reranker_score

    def retrieve(self, query: str, *, query_id: str, limit: int | None = None) -> SourceAwareResult:
        return self.retrieve_many([(query_id, query)], query_id=query_id, limit=limit)

    def retrieve_many(
        self,
        queries: list[tuple[str, str]],
        *,
        query_id: str,
        limit: int | None = None,
    ) -> SourceAwareResult:
        """Merge query-specific candidate pools, then rerank all query/chunk pairs.

        Candidate chunks are deduplicated by source chunk ID before reranking.
        A production reranker scores the complete subquery × candidate matrix
        in one batch; compatibility rerankers without ``rerank_pairs`` fall
        back to one call per subquery.
        """
        if not query_id.strip():
            raise ValueError("query_id cannot be empty")
        if not queries or len(queries) > 4:
            raise ValueError("retrieve_many requires one to four subqueries")
        query_ids = [item[0] for item in queries]
        query_texts = [item[1] for item in queries]
        if any(not isinstance(value, str) or not value.strip() for value in query_ids):
            raise ValueError("subquery IDs cannot be empty")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("subquery IDs must be unique")
        if any(not isinstance(value, str) or not value.strip() for value in query_texts):
            raise ValueError("subquery text cannot be empty")
        final_limit = self.final_k if limit is None else limit
        if final_limit <= 0:
            raise ValueError("limit must be positive")

        batch_embed = getattr(self.embedder, "embed_queries", None)
        query_vectors = batch_embed(query_texts) if callable(batch_embed) else [
            self.embedder.embed_query(query) for query in query_texts
        ]
        if len(query_vectors) != len(queries):
            raise RuntimeError("embedding backend returned the wrong number of query vectors")
        for vector in query_vectors:
            if len(vector) != self.store.dimension or any(not math.isfinite(float(value)) for value in vector):
                raise RuntimeError("embedding backend returned an invalid query vector")

        candidates_by_id: dict[str, VectorSearchPoint] = {}
        routing_scores: dict[str, dict[str, float]] = {subquery_id: {} for subquery_id in query_ids}
        child_scores: dict[str, dict[str, float]] = {subquery_id: {} for subquery_id in query_ids}
        for (subquery_id, _query), query_vector in zip(queries, query_vectors, strict=True):
            documents = self.store.search_documents(query_vector, limit=self.document_candidate_k)
            routing_scores[subquery_id] = {hit.document_id: hit.score for hit in documents}
            for document in documents:
                child_hits = self.store.search_chunks_for_document(
                    query_vector,
                    document_id=document.document_id,
                    limit=self.child_candidates_per_document,
                )
                for chunk in child_hits:
                    if chunk.document_id != document.document_id:
                        raise RuntimeError(
                            "document-local child retrieval returned a chunk from another document"
                        )
                    existing = candidates_by_id.get(chunk.chunk_id)
                    if existing is not None and existing.document_id != chunk.document_id:
                        raise RuntimeError("one chunk ID was returned for multiple documents")
                    candidates_by_id.setdefault(chunk.chunk_id, chunk)
                    if chunk.chunk_id not in child_scores[subquery_id]:
                        child_scores[subquery_id][chunk.chunk_id] = chunk.score

        candidates = list(candidates_by_id.values())
        if not candidates:
            return SourceAwareResult(
                store=RagStore.MEDICAL_KNOWLEDGE,
                query_id=query_id,
                hits=[],
                empty_reason="no child chunks found in candidate documents",
            )

        pair_scores = self._score_query_candidate_pairs(queries, candidates)
        best_by_chunk: dict[str, tuple[str, float]] = {}
        for subquery_id, _query in queries:
            for chunk in candidates:
                score = pair_scores[(subquery_id, chunk.chunk_id)]
                previous = best_by_chunk.get(chunk.chunk_id)
                if previous is None or score > previous[1]:
                    best_by_chunk[chunk.chunk_id] = (subquery_id, score)

        ranked_chunks = sorted(
            [chunk for chunk in candidates if self.min_reranker_score is None
             or best_by_chunk[chunk.chunk_id][1] >= self.min_reranker_score],
            key=lambda chunk: best_by_chunk[chunk.chunk_id][1],
            reverse=True,
        )[:final_limit]
        result_hits: list[RetrievalHit] = []
        for chunk in ranked_chunks:
            best_query_id, best_score = best_by_chunk[chunk.chunk_id]
            context = self.context_store.hydrate(
                chunk,
                max_context_tokens=self.max_context_tokens,
                max_anchor_tokens=self.max_anchor_tokens,
                max_neighbor_tokens=self.max_neighbor_tokens,
            )
            payload = chunk.payload
            hit_routing_scores = {
                subquery_id: scores[chunk.document_id]
                for subquery_id, scores in routing_scores.items()
                if chunk.document_id in scores
            }
            hit_child_scores = {
                subquery_id: scores[chunk.chunk_id]
                for subquery_id, scores in child_scores.items()
                if chunk.chunk_id in scores
            }
            hit_reranker_scores = {
                subquery_id: pair_scores[(subquery_id, chunk.chunk_id)]
                for subquery_id, _query in queries
            }
            matched_query_ids = [
                subquery_id for subquery_id in query_ids
                if chunk.chunk_id in child_scores[subquery_id]
            ]
            result_hits.append(
                RetrievalHit(
                    query_id=query_id,
                    store=RagStore.MEDICAL_KNOWLEDGE,
                    source_id=chunk.chunk_id,
                    text=context.text,
                    document_id=chunk.document_id,
                    retrieval_score=best_score,
                    metadata={
                        "chunk_text": chunk.text,
                        "paragraph_spans": [span.model_dump(mode="json") for span in chunk.paragraph_spans],
                        "section_span": chunk.section_span.model_dump(mode="json") if chunk.section_span else None,
                        "section_id": chunk.section_span.section_id if chunk.section_span else None,
                        "content_type": payload.get("content_type"),
                        "table_metadata": payload.get("table_metadata", {}),
                        "boundary_notes": payload.get("boundary_notes", []),
                        "heading_paths": payload.get("heading_paths", []),
                        "title": payload.get("title"),
                        "authors": payload.get("authors", []),
                        "source_uri": payload.get("source_uri"),
                        "source_org": payload.get("source_org"),
                        "source_type": payload.get("source_type"),
                        "publication_date": payload.get("publication_date"),
                        "version": payload.get("version"),
                        "specialty": payload.get("specialty", []),
                        "license": payload.get("license"),
                        "license_id": payload.get("license_id"),
                        "license_uri": payload.get("license_uri"),
                        "license_notice": payload.get("license_notice"),
                        "tokenizer_name": payload.get("tokenizer_name"),
                        "matched_query_ids": matched_query_ids,
                        "reranker_query_id": best_query_id,
                        "document_routing_score": hit_routing_scores.get(best_query_id),
                        "child_vector_score": hit_child_scores.get(best_query_id),
                        "document_routing_scores_by_query": hit_routing_scores,
                        "child_vector_scores_by_query": hit_child_scores,
                        "reranker_scores_by_query": hit_reranker_scores,
                        "reranker_score": best_score,
                        "reranker_score_kind": "cross_encoder_logit",
                        "reranker_scope": "all_subqueries_x_merged_candidate_chunks",
                        "context_paragraph_ids": context.paragraph_ids,
                        "anchor_paragraph_ids": context.anchor_paragraph_ids,
                        "context_section_ids": context.section_ids,
                        "anchor_section_ids": context.anchor_section_ids,
                        "context_truncated": context.truncated,
                        "retrieval_score_is_clinical_confidence": False,
                    },
                )
            )
        return SourceAwareResult(
            store=RagStore.MEDICAL_KNOWLEDGE,
            query_id=query_id,
            hits=result_hits,
            empty_reason="all candidates below calibrated reranker threshold" if not result_hits else None,
        )

    def _score_query_candidate_pairs(
        self,
        queries: list[tuple[str, str]],
        candidates: list[VectorSearchPoint],
    ) -> dict[tuple[str, str], float]:
        batch_method = getattr(self.reranker, "rerank_pairs", None)
        if callable(batch_method):
            pairs = [(query, candidate) for _subquery_id, query in queries for candidate in candidates]
            scores = batch_method(pairs)
            if len(scores) != len(pairs):
                raise RuntimeError("reranker returned the wrong number of query/passage scores")
            if any(not math.isfinite(float(score)) for score in scores):
                raise RuntimeError("reranker returned NaN or infinite score")
            pair_keys = [
                (subquery_id, candidate.chunk_id)
                for subquery_id, _query in queries
                for candidate in candidates
            ]
            return dict(zip(pair_keys, (float(score) for score in scores), strict=True))

        pair_scores: dict[tuple[str, str], float] = {}
        for subquery_id, query in queries:
            ranked = self.reranker.rerank(query, candidates)
            if len(ranked) != len(candidates):
                raise RuntimeError("reranker returned the wrong number of candidate scores")
            if any(not math.isfinite(float(item.score)) for item in ranked):
                raise RuntimeError("reranker returned NaN or infinite score")
            pair_scores.update({(subquery_id, item.point.chunk_id): item.score for item in ranked})
        return pair_scores
