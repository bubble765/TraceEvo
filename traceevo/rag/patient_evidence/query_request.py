"""Strict expert-owned patient retrieval requests; caller-owned task scope."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

from .aliases import EXAM_ALIASES
from .normalizer import extract_exam_types

ExamType = Enum("ExamType", {name.upper(): name for name in EXAM_ALIASES}, type=str)
Text = Annotated[str, StringConstraints(min_length=1, max_length=1200)]


class PatientRetrievalRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    type: Literal[1]
    decision: Literal["retrieve"]
    store: Literal["patient_evidence"]
    exam_type: ExamType
    query_text: Text
    retrieval_goal: Text

    @field_validator("type", mode="before")
    @classmethod
    def integer_type(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("type must be the integer 1")
        return value

    @field_validator("query_text", "retrieval_goal")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query text and goal must not be blank")
        return value


def validate_request(raw: Any) -> PatientRetrievalRequest:
    # JSON validation preserves strict scalar types while allowing JSON enum
    # strings. No loose coercion of numbers/booleans into string fields.
    request = PatientRetrievalRequest.model_validate_json(json.dumps(raw, ensure_ascii=False))
    mentioned = extract_exam_types(request.query_text)
    if mentioned and mentioned != [request.exam_type.value]:
        raise ValueError("query_text must name only the requested exam_type; split multiple exams into separate requests")
    return request


def request_prompt(*, belief: Any, history: Any, frontier: Any) -> tuple[str, str]:
    instruction = """
Generate one structured patient-evidence retrieval request. Return only JSON.
Fields: type (integer 1), decision (retrieve), store (patient_evidence),
exam_type, query_text, retrieval_goal. No content field or extra keys.
All retrievals are scoped to the current patient by the execution layer.
Do not generate task_id or any patient identifier; the caller injects the scope.
Choose exactly one canonical exam_type from the supplied global type vocabulary.
The vocabulary describes supported categories, not available patient findings.
Reformulate the needed evidence into a self-contained retrieval-oriented query.
Preserve the current information need; do not turn hypotheses into facts or
invent findings. Use retrieval_goal to explain what the evidence would test.
If several exams are needed, prioritize one missing exam now; request other
exams in later steps. query_text and exam_type must refer to the same exam.
Use the existing belief, frontier and retrieval history; avoid duplicate requests.
"""
    user = json.dumps({"supported_exam_types": list(EXAM_ALIASES),
                       "current_belief": belief, "frontier": frontier,
                       "retrieval_history": history}, ensure_ascii=False, default=str)
    return instruction, user


def filtered_evidence(request: PatientRetrievalRequest, evidence_text: Any,
                      *, expected_task_id: str) -> str:
    from .import_diagnosisarena import records_from_diagnostic_tests
    from .retriever import PatientEvidenceRetriever
    from .store import PatientEvidenceStore

    if not isinstance(expected_task_id, str) or not re.fullmatch(r"case-\d+", expected_task_id):
        raise ValueError("a valid caller-owned task_id is required for retrieval")
    validate_request(request.model_dump(mode="json"))
    # Isolated per-call store prevents repeated case-N IDs in different run
    # cohorts from colliding. The source text is the current case input only.
    with PatientEvidenceStore(":memory:") as store:
        store.upsert_records(records_from_diagnostic_tests(expected_task_id, evidence_text))
        records = PatientEvidenceRetriever(store, max_results=100).retrieve(
            task_id=expected_task_id, exam_type=request.exam_type.value, limit=100)
    return "\n".join(record.text for record in records)
