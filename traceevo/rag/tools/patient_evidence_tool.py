"""Source-aware adapter for Patient Evidence; not wired into frozen GoS."""
from __future__ import annotations

from traceevo.rag.common.result_schema import RetrievalHit, SourceAwareResult
from traceevo.rag.common.schemas import RagStore
from traceevo.rag.patient_evidence.retriever import PatientEvidenceRetriever


class PatientEvidenceTool:
    name = "patient_evidence"
    description = (
        "Retrieve only the active task's existing examination records. "
        "Never returns evidence from another task and never invents results."
    )

    def __init__(self, retriever: PatientEvidenceRetriever):
        self.retriever = retriever

    def run(self, *, task_id: str, query_id: str, query: str,
            exam_type: str | None = None, modality: str | None = None,
            limit: int | None = None) -> SourceAwareResult:
        records = self.retriever.retrieve(
            task_id=task_id, query=query, exam_type=exam_type,
            modality=modality, limit=limit)
        hits = [RetrievalHit(
            query_id=query_id,
            store=RagStore.PATIENT_EVIDENCE,
            source_id=record.record_id,
            task_id=record.task_id,
            text=record.text,
            metadata={
                "modality": record.modality,
                "exam_type": record.exam_type,
                "title": record.title,
                "record_hash": record.record_hash,
            },
        ) for record in records]
        return SourceAwareResult(
            store=RagStore.PATIENT_EVIDENCE,
            query_id=query_id,
            task_id=task_id,
            hits=hits,
            empty_reason=None if hits else "no_patient_evidence_match",
        )
