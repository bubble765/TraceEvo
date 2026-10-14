"""Query-planned orchestration over the existing source-aware RAG tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import QueryPlan, RagStore
from traceevo.rag.query_planner import QueryPlanner


@dataclass(frozen=True)
class PlannedRetrieval:
    """The plan and merged results are returned together for traceability."""

    query_plan: QueryPlan
    result: SourceAwareResult


class UnsupportedRagStoreError(ValueError):
    pass


class RagQueryService:
    """Require query reformulation before dispatching to a retrieval tool.

    Patient Evidence subqueries are dispatched sequentially. The concrete
    Medical Knowledge and Experience tools accept a complete plan and pool /
    rerank candidates together; their local Qdrant calls remain synchronous.
    """

    def __init__(
        self,
        planner: QueryPlanner,
        *,
        patient_evidence_tool: Any | None = None,
        medical_knowledge_tool: Any | None = None,
        experience_tool: Any | None = None,
    ) -> None:
        self.planner = planner
        self.patient_evidence_tool = patient_evidence_tool
        self.medical_knowledge_tool = medical_knowledge_tool
        self.experience_tool = experience_tool

    def retrieve(
        self,
        *,
        store: RagStore,
        query_id: str,
        query: str,
        task_id: str | None = None,
        limit: int | None = None,
        experience_channel: str = "initial_plan",
    ) -> PlannedRetrieval:
        if not query_id.strip():
            raise ValueError("query_id cannot be empty")
        try:
            store = RagStore(store)
        except ValueError as exc:
            raise UnsupportedRagStoreError(f"unsupported RAG store: {store!r}") from exc
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if experience_channel not in {"initial_plan", "recovery"}:
            raise ValueError("experience_channel must be 'initial_plan' or 'recovery'")
        if store is not RagStore.EXPERIENCE and experience_channel != "initial_plan":
            raise ValueError("experience_channel only applies to Experience retrieval")
        plan = self.planner.plan(query, store=store, task_id=task_id)
        parts: list[SourceAwareResult] = []

        if store is RagStore.PATIENT_EVIDENCE:
            if self.patient_evidence_tool is None:
                raise UnsupportedRagStoreError("Patient Evidence tool is not configured")
            # Keep the caller-supplied task ID outside the generated plan and
            # pass it unchanged to every task-scoped retrieval call.
            assert task_id is not None
            for subquery in plan.queries:
                part = self.patient_evidence_tool.run(
                    task_id=task_id,
                    query_id=subquery.query_id,
                    query=subquery.query_text,
                    exam_type=subquery.exam_type,
                    modality=subquery.modality,
                    limit=limit,
                )
                self._validate_patient_scope(part, task_id)
                parts.append(self._attach_goal(part, subquery.query_id, subquery.retrieval_goal))
        elif store is RagStore.MEDICAL_KNOWLEDGE:
            if self.medical_knowledge_tool is None:
                raise UnsupportedRagStoreError("Medical Knowledge tool is not configured")
            run_many = getattr(self.medical_knowledge_tool, "run_many", None)
            if callable(run_many):
                # The concrete MedicalKnowledgeTool combines all dense candidate
                # pools before one cross-query reranker batch.
                parts.append(run_many(query_id=query_id, queries=plan.queries, limit=limit))
            else:
                # Compatibility seam for simple adapters and test doubles.
                for subquery in plan.queries:
                    part = self.medical_knowledge_tool.run(
                        query_id=subquery.query_id,
                        query=subquery.query_text,
                        limit=limit,
                    )
                    parts.append(self._attach_goal(part, subquery.query_id, subquery.retrieval_goal))
        elif store is RagStore.EXPERIENCE:
            if self.experience_tool is None:
                raise UnsupportedRagStoreError("Experience tool is not configured")
            run_many = getattr(self.experience_tool, "run_many", None)
            if callable(run_many):
                parts.append(run_many(
                    query_id=query_id,
                    queries=plan.queries,
                    limit=limit,
                    channel=experience_channel,
                ))
            else:
                for subquery in plan.queries:
                    part = self.experience_tool.run(
                        query_id=subquery.query_id,
                        query=subquery.query_text,
                        limit=limit,
                        channel=experience_channel,
                    )
                    parts.append(self._attach_goal(part, subquery.query_id, subquery.retrieval_goal))
        else:
            raise UnsupportedRagStoreError(
                f"unsupported RAG store: {store.value}"
            )

        result = self._merge_results(
            store=store,
            query_id=query_id,
            task_id=task_id if store is RagStore.PATIENT_EVIDENCE else None,
            parts=parts,
        )
        if store in {RagStore.MEDICAL_KNOWLEDGE, RagStore.EXPERIENCE}:
            # Both native vector tools globally rerank their merged candidate
            # pools; a compatibility adapter may return independent lists.
            hits = sorted(
                result.hits,
                key=lambda hit: hit.retrieval_score if hit.retrieval_score is not None else float("-inf"),
                reverse=True,
            )
            if limit is not None:
                hits = hits[:limit]
            result = result.model_copy(update={"hits": hits})
        elif limit is not None:
            # Patient Evidence has no ranking score, so preserve planner order.
            result = result.model_copy(update={"hits": result.hits[:limit]})
        return PlannedRetrieval(query_plan=plan, result=result)

    @staticmethod
    def _validate_patient_scope(result: SourceAwareResult, task_id: str) -> None:
        """Fail closed if a Patient Evidence adapter violates task isolation."""
        if result.task_id != task_id:
            raise ValueError(
                "Patient Evidence tool returned a result outside the requested task scope"
            )
        if any(hit.task_id != task_id for hit in result.hits):
            raise ValueError(
                "Patient Evidence tool returned a hit outside the requested task scope"
            )

    @staticmethod
    def _attach_goal(
        result: SourceAwareResult,
        query_id: str,
        retrieval_goal: str,
    ) -> SourceAwareResult:
        hits = []
        for hit in result.hits:
            metadata = dict(hit.metadata)
            metadata["retrieval_goal"] = retrieval_goal
            hits.append(hit.model_copy(update={"query_id": query_id, "metadata": metadata}))
        return result.model_copy(update={"hits": hits})

    @staticmethod
    def _merge_results(
        *,
        store: RagStore,
        query_id: str,
        task_id: str | None,
        parts: list[SourceAwareResult],
    ) -> SourceAwareResult:
        by_source: dict[str, RetrievalHit] = {}
        for part in parts:
            if part.store is not store:
                raise ValueError("retrieval tool returned a result for the wrong store")
            for hit in part.hits:
                previous = by_source.get(hit.source_id)
                if previous is None:
                    metadata = dict(hit.metadata)
                    matched_ids = list(dict.fromkeys(
                        metadata.get("matched_query_ids", [hit.query_id])
                    ))
                    metadata.update({
                        "matched_query_ids": matched_ids,
                        "matched_retrieval_goals": list(dict.fromkeys(
                            metadata.get("matched_retrieval_goals", [])
                            + ([metadata["retrieval_goal"]] if metadata.get("retrieval_goal") else [])
                        )),
                    })
                    by_source[hit.source_id] = hit.model_copy(update={"metadata": metadata})
                    continue

                matched_ids = list(previous.metadata.get("matched_query_ids", [previous.query_id]))
                incoming_ids = hit.metadata.get("matched_query_ids", [hit.query_id])
                for matched_id in incoming_ids:
                    if matched_id not in matched_ids:
                        matched_ids.append(matched_id)
                goals = list(previous.metadata.get("matched_retrieval_goals", []))
                incoming_goals = hit.metadata.get("matched_retrieval_goals", [])
                goal = hit.metadata.get("retrieval_goal")
                for candidate_goal in [*incoming_goals, *([goal] if goal else [])]:
                    if candidate_goal and candidate_goal not in goals:
                        goals.append(candidate_goal)
                # Keep the strongest score when present; for exact Patient
                # Evidence duplicates, retain the first identical record.
                selected = hit if (
                    hit.retrieval_score is not None
                    and (previous.retrieval_score is None or hit.retrieval_score > previous.retrieval_score)
                ) else previous
                metadata = dict(selected.metadata)
                metadata["matched_query_ids"] = matched_ids
                metadata["matched_retrieval_goals"] = goals
                by_source[hit.source_id] = selected.model_copy(update={"metadata": metadata})

        hits = list(by_source.values())
        empty_reason = None
        if not hits:
            reasons = list(dict.fromkeys(part.empty_reason for part in parts if part.empty_reason))
            empty_reason = "; ".join(reasons) if reasons else "all planned subqueries returned no hits"
        return SourceAwareResult(
            store=store,
            query_id=query_id,
            task_id=task_id,
            hits=hits,
            empty_reason=empty_reason,
        )
