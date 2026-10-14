"""Source-aware Medical Knowledge dense retrieval adapter; separate from patient facts."""
from __future__ import annotations

from traceevo.rag.common.result_schema import SourceAwareResult
from traceevo.rag.common.schemas import SubQuery
from traceevo.rag.medical_knowledge.retriever import MedicalKnowledgeRetriever


class MedicalKnowledgeTool:
    name = "medical_knowledge"
    description = (
        "Retrieve general medical knowledge from license-checked PMC OA documents. "
        "Returns source and license metadata. Results are literature support, not current-patient facts, "
        "diagnoses, or clinical confidence scores."
    )

    def __init__(self, retriever: MedicalKnowledgeRetriever):
        self.retriever = retriever

    def run(self, *, query_id: str, query: str, limit: int | None = None) -> SourceAwareResult:
        return self.retriever.retrieve(query, query_id=query_id, limit=limit)

    def run_many(
        self,
        *,
        query_id: str,
        queries: list[SubQuery],
        limit: int | None = None,
    ) -> SourceAwareResult:
        """Retrieve and globally rerank the merged candidate pools for a plan."""
        result = self.retriever.retrieve_many(
            [(query.query_id, query.query_text) for query in queries],
            query_id=query_id,
            limit=limit,
        )
        goals = {query.query_id: query.retrieval_goal for query in queries}
        enriched_hits = []
        for hit in result.hits:
            metadata = dict(hit.metadata)
            matched_ids = list(metadata.get("matched_query_ids", []))
            metadata["matched_retrieval_goals"] = [
                goals[subquery_id] for subquery_id in matched_ids if subquery_id in goals
            ]
            best_query_id = metadata.get("reranker_query_id")
            if best_query_id in goals:
                metadata["reranker_query_goal"] = goals[best_query_id]
            enriched_hits.append(hit.model_copy(update={"metadata": metadata}))
        return result.model_copy(update={"hits": enriched_hits})
