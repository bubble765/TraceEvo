"""Versioned data contracts shared by the independent RAG stores."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagStore(str, Enum):
    PATIENT_EVIDENCE = "patient_evidence"
    MEDICAL_KNOWLEDGE = "medical_knowledge"
    EXPERIENCE = "experience"


class QueryMode(str, Enum):
    SINGLE = "single"
    DECOMPOSE = "decompose"


class SubQuery(_StrictModel):
    query_id: str = Field(min_length=1)
    query_text: str = Field(min_length=1)
    retrieval_goal: str = Field(min_length=1)
    modality: str | None = None
    exam_type: str | None = None

    @field_validator("query_id", "query_text", "retrieval_goal")
    @classmethod
    def validate_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subquery IDs, query text, and retrieval goals cannot be blank")
        return value


class QueryPlan(_StrictModel):
    mode: QueryMode
    store: RagStore
    queries: list[SubQuery] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_mode_and_ids(self) -> "QueryPlan":
        ids = [query.query_id for query in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError("query_id values must be unique within a QueryPlan")
        if self.mode is QueryMode.SINGLE and len(self.queries) != 1:
            raise ValueError("single mode requires exactly one subquery")
        if self.mode is QueryMode.DECOMPOSE and not 2 <= len(self.queries) <= 4:
            raise ValueError("decompose mode requires two to four subqueries")
        return self


class PatientEvidenceRecord(_StrictModel):
    record_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    modality: str = Field(min_length=1)
    exam_type: str = Field(min_length=1)
    title: str | None = None
    text: str = Field(min_length=1)
    timestamp: str | None = None
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MedicalDocument(_StrictModel):
    document_id: str = Field(min_length=1)
    source_org: str = Field(min_length=1)
    source_type: Literal["guideline", "article", "book", "review"]
    title: str = Field(min_length=1)
    abstract: str | None = None
    authors: list[str] = Field(default_factory=list)
    publication_date: str | None = None
    version: str | None = None
    specialty: list[str] = Field(default_factory=list)
    source_uri: str = Field(min_length=1)
    license: str | None = None
    license_id: str | None = None
    license_uri: str | None = None
    license_notice: str | None = None
    raw_path: str = Field(min_length=1)


class ParentSection(_StrictModel):
    section_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    ordinal: int = Field(ge=0)


class KnowledgeChunk(_StrictModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    parent_section_id: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    token_count: int = Field(ge=0)
    ordinal_in_parent: int = Field(ge=0)
    source_uri: str = Field(min_length=1)
    # The default splitter uses a documented lexical estimate until the
    # configured embedding model's tokenizer is available at index time.
    tokenizer_name: str = "unicode_lexical_estimate_v1"


class DocumentParagraph(_StrictModel):
    """One source paragraph/block, addressable for neighboring-paragraph hydration."""

    paragraph_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    ordinal_in_document: int = Field(ge=0)
    heading_path: list[str] = Field(default_factory=list)
    paragraph_type: Literal["paragraph", "list", "table", "caption", "abstract", "other"] = "paragraph"
    text: str = Field(min_length=1)


class ParagraphSpan(_StrictModel):
    """Half-open character offsets into one normalized source paragraph."""

    paragraph_id: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "ParagraphSpan":
        if self.end_char <= self.start_char:
            raise ValueError("paragraph span end_char must be greater than start_char")
        return self


class DocumentChunk(_StrictModel):
    """A direct document child; no section/parent-section foreign key."""

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    paragraph_ids: list[str] = Field(min_length=1)
    paragraph_spans: list[ParagraphSpan] = Field(default_factory=list)
    heading_paths: list[list[str]] = Field(default_factory=list)
    boundary_notes: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    embedding_text: str = Field(min_length=1)
    token_count: int = Field(ge=0)
    ordinal_in_document: int = Field(ge=0)
    source_uri: str = Field(min_length=1)
    tokenizer_name: str = "unicode_lexical_estimate_v1"


class SectionBlock(_StrictModel):
    """A typed source block with offsets into its normalized parent section."""

    block_id: str = Field(min_length=1)
    block_type: Literal[
        "abstract", "paragraph", "list_item", "list", "table_row",
        "caption", "equation", "other",
    ] = "paragraph"
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    text: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_block_span(self) -> "SectionBlock":
        if self.end_char <= self.start_char:
            raise ValueError("section block end_char must be greater than start_char")
        if len(self.text) != self.end_char - self.start_char:
            raise ValueError("section block span length must match its normalized text")
        return self


class DocumentSection(_StrictModel):
    """One heading-scoped source unit; block/chunk offsets are section-local."""

    section_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    blocks: list[SectionBlock] = Field(min_length=1)
    ordinal: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_blocks(self) -> "DocumentSection":
        previous_end = 0
        for block in self.blocks:
            if block.end_char > len(self.text):
                raise ValueError(f"block {block.block_id} extends past its section text")
            if self.text[block.start_char:block.end_char] != block.text:
                raise ValueError(f"block {block.block_id} text does not match its section span")
            if block.start_char < previous_end:
                raise ValueError("section blocks must be source-ordered and non-overlapping")
            previous_end = block.end_char
        return self


class SectionSpan(_StrictModel):
    """Half-open character offsets into one normalized source section."""

    section_id: str = Field(min_length=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_span(self) -> "SectionSpan":
        if self.end_char <= self.start_char:
            raise ValueError("section span end_char must be greater than start_char")
        return self


class SectionChunk(_StrictModel):
    """A direct document child, addressable inside a single source section."""

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_span: SectionSpan
    block_ids: list[str] = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    content_type: str = Field(min_length=1)
    table_metadata: dict[str, Any] = Field(default_factory=dict)
    boundary_notes: list[str] = Field(default_factory=list)
    text: str = Field(min_length=1)
    embedding_text: str = Field(min_length=1)
    token_count: int = Field(ge=0)
    ordinal_in_document: int = Field(ge=0)
    source_uri: str = Field(min_length=1)
    tokenizer_name: str = "qwen3_embedding_0_6b_tokenizer"

    @model_validator(mode="after")
    def validate_span_matches_chunk(self) -> "SectionChunk":
        if self.section_span.section_id != self.section_id:
            raise ValueError("chunk section_id must match its source span")
        if len(self.text) != self.section_span.end_char - self.section_span.start_char:
            raise ValueError("section span length must match chunk text")
        return self


class ExperienceRecord(_StrictModel):
    experience_id: str = Field(min_length=1)
    experience_type: Literal["validated_success", "validated_failure"]
    task_feature_summary: str = Field(min_length=1)
    experience_payload: dict[str, Any] = Field(default_factory=dict)
    source_trace_ids: list[str] = Field(min_length=1)
    audit_summary_hash: str = Field(min_length=1)
    outcome_verdict: str = Field(min_length=1)
