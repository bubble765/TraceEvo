"""Exact, alias-aware retrieval constrained to one active patient task."""
from __future__ import annotations

from traceevo.rag.common.schemas import PatientEvidenceRecord
from .normalizer import (
    canonical_exam_filter,
    extract_exam_types,
    normalize_modality,
)
from .store import PatientEvidenceStore


class PatientEvidenceRetriever:
    """Structured retrieval; query text is never used for vector similarity."""

    def __init__(self, store: PatientEvidenceStore, *, max_results: int = 20):
        if max_results < 1:
            raise ValueError("max_results must be positive")
        self.store = store
        self.max_results = min(max_results, 100)

    def retrieve(self, *, task_id: str, query: str = "",
                 exam_type: str | None = None, modality: str | None = None,
                 limit: int | None = None) -> list[PatientEvidenceRecord]:
        """Return only records for ``task_id``.

        A single unambiguous exam mention may supply the exact exam filter.
        Multi-exam questions are intentionally not guessed here; the planned
        QueryPlanner will decompose them into separate subqueries later.
        Unknown explicit filters return no records rather than broadening.
        """
        if not str(task_id or "").strip():
            raise ValueError("task_id is mandatory; cross-task search is forbidden")

        requested_exam = exam_type
        if requested_exam is None:
            mentioned = extract_exam_types(query)
            if len(mentioned) == 1:
                requested_exam = mentioned[0]
            elif len(mentioned) > 1:
                raise ValueError(
                    "query mentions multiple exams; decompose it into subqueries first")
        canonical_exam = (canonical_exam_filter(requested_exam)
                          if requested_exam is not None else None)

        canonical_modality = None
        if modality is not None:
            canonical_modality = normalize_modality(modality)
            if canonical_modality is None:
                return []

        return self.store.retrieve(
            task_id=task_id,
            exam_type=canonical_exam,
            modality=canonical_modality,
            limit=self.max_results if limit is None else min(limit, self.max_results),
        )
