"""Immutable raw and normalized JSONL layers for Medical Knowledge ingestion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from traceevo.rag.common.schemas import (
    DocumentChunk,
    DocumentParagraph,
    DocumentSection,
    KnowledgeChunk,
    MedicalDocument,
    ParentSection,
    SectionChunk,
)


class ImmutableArtifactConflict(RuntimeError):
    """An artifact path already contains different bytes; existing data is kept."""


def _safe_component(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")[:64] or "document"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _immutable_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise ImmutableArtifactConflict(f"refusing to overwrite different existing artifact: {path}")


def store_raw(root: str | Path, source: str, payload: bytes, suffix: str) -> str:
    root = Path(root)
    digest = hashlib.sha256(payload).hexdigest()
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    relative = Path("raw") / source / f"{digest}{suffix}"
    _immutable_write(root / relative, payload)
    return relative.as_posix()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _jsonl_bytes(values: Iterable[object]) -> bytes:
    rows = [json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in values]
    return (("\n".join(rows) + "\n") if rows else "").encode("utf-8")


def write_normalized(
    root: str | Path,
    document: MedicalDocument,
    sections: list[ParentSection],
    chunks: list[KnowledgeChunk],
) -> Path:
    root = Path(root)
    # v2 isolates the corrected source-license/attribution metadata schema
    # from any immutable v1 artifacts created before those fields existed.
    folder = Path("normalized") / "schema-v2" / _safe_component(document.document_id)
    target = root / folder
    artifacts = {
        target / "document.json": _json_bytes(document.model_dump(mode="json")),
        target / "parent_sections.jsonl": _jsonl_bytes(s.model_dump(mode="json") for s in sections),
        target / "child_chunks.jsonl": _jsonl_bytes(c.model_dump(mode="json") for c in chunks),
    }
    # Preflight all immutable targets so a conflict cannot leave a partially
    # refreshed normalized document directory.
    for path, content in artifacts.items():
        if path.exists() and path.read_bytes() != content:
            raise ImmutableArtifactConflict(f"refusing to overwrite different existing artifact: {path}")
    for path, content in artifacts.items():
        _immutable_write(path, content)
    return target


def write_document_level_normalized(
    root: str | Path,
    document: MedicalDocument,
    paragraphs: list[DocumentParagraph],
    chunks: list[DocumentChunk],
    *,
    schema_version: str = "schema-v4",
) -> Path:
    """Write document -> paragraph records/chunks without a section layer.

    Current output is schema-v4 because the no-overlap, offset-aware chunk
    representation is not byte-compatible with the immutable schema-v3 corpus.
    """
    if schema_version != "schema-v4":
        raise ValueError("document-level writer only writes schema-v4")
    paragraph_ids = [paragraph.paragraph_id for paragraph in paragraphs]
    if len(paragraph_ids) != len(set(paragraph_ids)):
        raise ValueError("paragraph IDs must be unique within a document")
    if any(paragraph.document_id != document.document_id for paragraph in paragraphs):
        raise ValueError("all paragraphs must belong to the supplied document")
    ordinals = [paragraph.ordinal_in_document for paragraph in paragraphs]
    if ordinals != list(range(len(paragraphs))):
        raise ValueError("paragraph ordinals must be contiguous and document-ordered")
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("chunk IDs must be unique within a document")
    if any(chunk.document_id != document.document_id for chunk in chunks):
        raise ValueError("all chunks must belong to the supplied document")
    paragraph_id_set = set(paragraph_ids)
    paragraphs_by_id = {paragraph.paragraph_id: paragraph for paragraph in paragraphs}
    ranges_by_paragraph: dict[str, list[tuple[int, int, str]]] = {paragraph_id: [] for paragraph_id in paragraph_ids}
    if any(not chunk.paragraph_ids or any(pid not in paragraph_id_set for pid in chunk.paragraph_ids) for chunk in chunks):
        raise ValueError("each chunk must reference paragraphs from this document")
    for chunk in chunks:
        span_paragraph_ids = list(dict.fromkeys(span.paragraph_id for span in chunk.paragraph_spans))
        if span_paragraph_ids != chunk.paragraph_ids:
            raise ValueError(f"chunk {chunk.chunk_id} paragraph IDs do not match its source spans")
        for span in chunk.paragraph_spans:
            paragraph = paragraphs_by_id[span.paragraph_id]
            if span.end_char > len(paragraph.text):
                raise ValueError(f"chunk {chunk.chunk_id} has a source span beyond paragraph {span.paragraph_id}")
            ranges_by_paragraph[span.paragraph_id].append((span.start_char, span.end_char, chunk.chunk_id))
        reconstructed = "\n\n".join(
            paragraphs_by_id[span.paragraph_id].text[span.start_char:span.end_char]
            for span in chunk.paragraph_spans
        )
        if reconstructed != chunk.text:
            raise ValueError(f"chunk {chunk.chunk_id} text does not match its source paragraph spans")
    for paragraph_id, ranges in ranges_by_paragraph.items():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if previous[1] > current[0]:
                raise ValueError(
                    f"schema-v4 chunks {previous[2]} and {current[2]} overlap in paragraph {paragraph_id}"
                )
    root = Path(root)
    folder = Path("normalized") / schema_version / _safe_component(document.document_id)
    target = root / folder
    artifacts = {
        target / "document.json": _json_bytes(document.model_dump(mode="json")),
        target / "paragraphs.jsonl": _jsonl_bytes(p.model_dump(mode="json") for p in paragraphs),
        target / "child_chunks.jsonl": _jsonl_bytes(c.model_dump(mode="json") for c in chunks),
    }
    for path, content in artifacts.items():
        if path.exists() and path.read_bytes() != content:
            raise ImmutableArtifactConflict(f"refusing to overwrite different existing artifact: {path}")
    for path, content in artifacts.items():
        _immutable_write(path, content)
    return target


def write_section_normalized(
    root: str | Path,
    document: MedicalDocument,
    sections: list[DocumentSection],
    chunks: list[SectionChunk],
    *,
    schema_version: str = "schema-v5",
) -> Path:
    """Write immutable section-local source spans and typed child chunks."""
    if schema_version != "schema-v5":
        raise ValueError("section-level writer only writes schema-v5")
    section_ids = [section.section_id for section in sections]
    if len(section_ids) != len(set(section_ids)):
        raise ValueError("section IDs must be unique within a document")
    if any(section.document_id != document.document_id for section in sections):
        raise ValueError("all sections must belong to the supplied document")
    if [section.ordinal for section in sections] != list(range(len(sections))):
        raise ValueError("section ordinals must be contiguous and document-ordered")
    sections_by_id = {section.section_id: section for section in sections}
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("chunk IDs must be unique within a document")
    if any(chunk.document_id != document.document_id for chunk in chunks):
        raise ValueError("all chunks must belong to the supplied document")
    ranges_by_section: dict[str, list[tuple[int, int, str]]] = {
        section_id: [] for section_id in section_ids
    }
    for chunk in chunks:
        section = sections_by_id.get(chunk.section_id)
        if section is None or chunk.section_span.section_id != chunk.section_id:
            raise ValueError(f"chunk {chunk.chunk_id} points to a missing or mismatched section")
        span = chunk.section_span
        if span.end_char > len(section.text):
            raise ValueError(f"chunk {chunk.chunk_id} extends past section {chunk.section_id}")
        if section.text[span.start_char:span.end_char] != chunk.text:
            raise ValueError(f"chunk {chunk.chunk_id} text does not match its section span")
        block_ids = {block.block_id for block in section.blocks}
        if not chunk.block_ids or any(block_id not in block_ids for block_id in chunk.block_ids):
            raise ValueError(f"chunk {chunk.chunk_id} references a missing source block")
        overlapping_blocks = [
            block for block in section.blocks
            if block.start_char < span.end_char and block.end_char > span.start_char
        ]
        if chunk.block_ids != [block.block_id for block in overlapping_blocks]:
            raise ValueError(f"chunk {chunk.chunk_id} block IDs do not match its section span")
        if any(block.block_type != chunk.content_type for block in overlapping_blocks):
            raise ValueError(f"chunk {chunk.chunk_id} crosses source content types")
        ranges_by_section[chunk.section_id].append((span.start_char, span.end_char, chunk.chunk_id))
    for section_id, ranges in ranges_by_section.items():
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if previous[1] > current[0]:
                raise ValueError(
                    f"schema-v5 chunks {previous[2]} and {current[2]} overlap in section {section_id}"
                )

    root = Path(root)
    folder = Path("normalized") / schema_version / _safe_component(document.document_id)
    target = root / folder
    artifacts = {
        target / "document.json": _json_bytes(document.model_dump(mode="json")),
        target / "sections.jsonl": _jsonl_bytes(section.model_dump(mode="json") for section in sections),
        target / "child_chunks.jsonl": _jsonl_bytes(chunk.model_dump(mode="json") for chunk in chunks),
    }
    for path, content in artifacts.items():
        if path.exists() and path.read_bytes() != content:
            raise ImmutableArtifactConflict(f"refusing to overwrite different existing artifact: {path}")
    for path, content in artifacts.items():
        _immutable_write(path, content)
    return target
