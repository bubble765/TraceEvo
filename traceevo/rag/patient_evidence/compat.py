"""Drop-in callable shape for staged migration from the legacy retriever.

This adapter is not installed into GoS. A caller must explicitly construct it
for one task, keeping the default path and the frozen Core-v1 experiment intact.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from .retriever import PatientEvidenceRetriever


class CompatibleEvidenceRetriever:
    """Switch between the old callable and task-scoped SQLite retrieval.

    The `__call__(query, evidence_texts)` signature matches the legacy
    ``evidence_retriever`` helper. SQLite mode requires an instance scoped to a
    concrete task; it cannot fall back to a cross-task lookup.
    """

    def __init__(self, *, mode: Literal["legacy", "patient_evidence"],
                 legacy_retriever: Callable[[str, Any], str],
                 task_id: str | None = None,
                 patient_retriever: PatientEvidenceRetriever | None = None):
        if mode not in ("legacy", "patient_evidence"):
            raise ValueError(f"unsupported evidence retriever mode: {mode}")
        self.mode = mode
        self.legacy_retriever = legacy_retriever
        self.task_id = task_id
        self.patient_retriever = patient_retriever
        self.last_source_ids: tuple[str, ...] = ()
        if mode == "patient_evidence":
            if not task_id or not task_id.strip():
                raise ValueError("patient_evidence mode requires a task_id")
            if patient_retriever is None:
                raise ValueError("patient_evidence mode requires a retriever")

    def __call__(self, query: str, evidence_texts: Any = None) -> str:
        if self.mode == "legacy":
            self.last_source_ids = ()
            return self.legacy_retriever(query, evidence_texts)

        assert self.patient_retriever is not None and self.task_id is not None
        records = self.patient_retriever.retrieve(
            task_id=self.task_id, query=query)
        self.last_source_ids = tuple(record.record_id for record in records)
        return "\n".join(record.text for record in records)
