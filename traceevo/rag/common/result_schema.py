"""Source-aware retrieval output contract for future agent tools."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .schemas import RagStore


class RetrievalHit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str = Field(min_length=1)
    store: RagStore
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    task_id: str | None = None
    document_id: str | None = None
    parent_section_id: str | None = None
    source_trace_ids: list[str] = Field(default_factory=list)
    # Retrieval relevance only. This is not a clinical confidence score.
    retrieval_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceAwareResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: RagStore
    query_id: str = Field(min_length=1)
    task_id: str | None = None
    hits: list[RetrievalHit] = Field(default_factory=list)
    empty_reason: str | None = None

    @property
    def source_ids(self) -> list[str]:
        return [hit.source_id for hit in self.hits]
