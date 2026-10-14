"""Validate schema-v4 bundles and index document summaries plus offset-aware chunks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from traceevo.rag.common.schemas import (
    DocumentChunk,
    DocumentParagraph,
    DocumentSection,
    MedicalDocument,
    SectionChunk,
)

from .embedding import TextEmbedder
from .splitter import RegexTokenCounter
from .vector_store import QdrantMedicalKnowledgeStore


class NormalizedCorpusError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedCorpus:
    documents: dict[str, MedicalDocument]
    paragraphs: dict[str, DocumentParagraph]
    chunks: list[DocumentChunk | SectionChunk]
    sections: dict[str, DocumentSection]


def load_normalized_corpus(data_dir: str | Path, *, schema_version: str = "schema-v4") -> NormalizedCorpus:
    if schema_version == "schema-v5":
        return load_section_corpus(data_dir, schema_version=schema_version)
    if schema_version != "schema-v4":
        raise ValueError("only schema-v4 and schema-v5 (document -> source spans/offset-aware chunks) are eligible for indexing")
    root = Path(data_dir) / "normalized" / schema_version
    if not root.is_dir():
        raise FileNotFoundError(f"normalized corpus not found: {root}")

    documents: dict[str, MedicalDocument] = {}
    paragraphs: dict[str, DocumentParagraph] = {}
    chunks: list[DocumentChunk] = []
    chunk_ids: set[str] = set()
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        document_file = folder / "document.json"
        paragraph_file = folder / "paragraphs.jsonl"
        chunk_file = folder / "child_chunks.jsonl"
        if not (document_file.is_file() and paragraph_file.is_file() and chunk_file.is_file()):
            raise NormalizedCorpusError(f"incomplete schema-v4 bundle: {folder}")
        try:
            document = MedicalDocument.model_validate_json(document_file.read_text(encoding="utf-8"))
            doc_paragraphs = [
                DocumentParagraph.model_validate_json(line)
                for line in paragraph_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            doc_chunks = [
                DocumentChunk.model_validate_json(line)
                for line in chunk_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as exc:
            raise NormalizedCorpusError(f"invalid schema-v4 bundle {folder}: {exc}") from exc
        if document.document_id in documents:
            raise NormalizedCorpusError(f"duplicate document_id {document.document_id}")
        documents[document.document_id] = document
        for paragraph in doc_paragraphs:
            if paragraph.document_id != document.document_id or paragraph.paragraph_id in paragraphs:
                raise NormalizedCorpusError(f"invalid or duplicate paragraph {paragraph.paragraph_id}")
            paragraphs[paragraph.paragraph_id] = paragraph
        ranges_by_paragraph: dict[str, list[tuple[int, int, str]]] = {
            paragraph.paragraph_id: [] for paragraph in doc_paragraphs
        }
        for chunk in doc_chunks:
            if chunk.document_id != document.document_id:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} points to the wrong document")
            if not chunk.paragraph_ids or any(pid not in paragraphs for pid in chunk.paragraph_ids):
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} has a missing paragraph reference")
            if any(paragraphs[pid].document_id != chunk.document_id for pid in chunk.paragraph_ids):
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} references a paragraph from another document")
            span_paragraph_ids = list(dict.fromkeys(span.paragraph_id for span in chunk.paragraph_spans))
            if not chunk.paragraph_spans or span_paragraph_ids != chunk.paragraph_ids:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} has missing or inconsistent source spans")
            for span in chunk.paragraph_spans:
                paragraph = paragraphs.get(span.paragraph_id)
                if paragraph is None or span.end_char > len(paragraph.text):
                    raise NormalizedCorpusError(f"chunk {chunk.chunk_id} has an invalid span into {span.paragraph_id}")
                ranges_by_paragraph[span.paragraph_id].append((span.start_char, span.end_char, chunk.chunk_id))
            reconstructed = "\n\n".join(
                paragraphs[span.paragraph_id].text[span.start_char:span.end_char]
                for span in chunk.paragraph_spans
            )
            if reconstructed != chunk.text:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} text differs from its paragraph spans")
            if chunk.chunk_id in chunk_ids:
                raise NormalizedCorpusError(f"duplicate chunk_id {chunk.chunk_id}")
            chunk_ids.add(chunk.chunk_id)
            chunks.append(chunk)
        for paragraph_id, ranges in ranges_by_paragraph.items():
            ranges.sort()
            for previous, current in zip(ranges, ranges[1:]):
                if previous[1] > current[0]:
                    raise NormalizedCorpusError(
                        f"chunks {previous[2]} and {current[2]} overlap in paragraph {paragraph_id}"
                    )
    return NormalizedCorpus(documents=documents, paragraphs=paragraphs, chunks=chunks, sections={})


def load_section_corpus(data_dir: str | Path, *, schema_version: str = "schema-v5") -> NormalizedCorpus:
    if schema_version != "schema-v5":
        raise ValueError("section-local indexing only supports schema-v5")
    root = Path(data_dir) / "normalized" / schema_version
    if not root.is_dir():
        raise FileNotFoundError(f"normalized corpus not found: {root}")
    documents: dict[str, MedicalDocument] = {}
    sections: dict[str, DocumentSection] = {}
    chunks: list[SectionChunk] = []
    chunk_ids: set[str] = set()
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        document_file = folder / "document.json"
        section_file = folder / "sections.jsonl"
        chunk_file = folder / "child_chunks.jsonl"
        if not (document_file.is_file() and section_file.is_file() and chunk_file.is_file()):
            raise NormalizedCorpusError(f"incomplete schema-v5 bundle: {folder}")
        try:
            document = MedicalDocument.model_validate_json(document_file.read_text(encoding="utf-8"))
            doc_sections = [
                DocumentSection.model_validate_json(line)
                for line in section_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            doc_chunks = [
                SectionChunk.model_validate_json(line)
                for line in chunk_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as exc:
            raise NormalizedCorpusError(f"invalid schema-v5 bundle {folder}: {exc}") from exc
        if document.document_id in documents:
            raise NormalizedCorpusError(f"duplicate document_id {document.document_id}")
        documents[document.document_id] = document
        if [section.ordinal for section in doc_sections] != list(range(len(doc_sections))):
            raise NormalizedCorpusError(f"section ordinals are not contiguous in {document.document_id}")
        for section in doc_sections:
            if section.document_id != document.document_id or section.section_id in sections:
                raise NormalizedCorpusError(f"invalid or duplicate section {section.section_id}")
            sections[section.section_id] = section
        ranges_by_section: dict[str, list[tuple[int, int, str]]] = {
            section.section_id: [] for section in doc_sections
        }
        for chunk in doc_chunks:
            section = sections.get(chunk.section_id)
            if chunk.document_id != document.document_id or section is None:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} points to the wrong or missing section")
            span = chunk.section_span
            if span.section_id != section.section_id or span.end_char > len(section.text):
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} has an invalid section span")
            if section.text[span.start_char:span.end_char] != chunk.text:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} text differs from its section span")
            block_by_id = {block.block_id: block for block in section.blocks}
            if not chunk.block_ids or any(block_id not in block_by_id for block_id in chunk.block_ids):
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} has a missing source block reference")
            overlapping_blocks = [
                block for block in section.blocks
                if block.start_char < span.end_char and block.end_char > span.start_char
            ]
            if chunk.block_ids != [block.block_id for block in overlapping_blocks]:
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} block IDs do not match its span")
            if any(block.block_type != chunk.content_type for block in overlapping_blocks):
                raise NormalizedCorpusError(f"chunk {chunk.chunk_id} crosses source content types")
            ranges_by_section[section.section_id].append((span.start_char, span.end_char, chunk.chunk_id))
            if chunk.chunk_id in chunk_ids:
                raise NormalizedCorpusError(f"duplicate chunk_id {chunk.chunk_id}")
            chunk_ids.add(chunk.chunk_id)
            chunks.append(chunk)
        for section_id, ranges in ranges_by_section.items():
            ranges.sort()
            for previous, current in zip(ranges, ranges[1:]):
                if previous[1] > current[0]:
                    raise NormalizedCorpusError(
                        f"chunks {previous[2]} and {current[2]} overlap in section {section_id}"
                    )
    return NormalizedCorpus(documents=documents, paragraphs={}, chunks=chunks, sections=sections)


def document_retrieval_text(
    document: MedicalDocument,
    source_units: list[DocumentParagraph] | list[DocumentSection],
) -> str:
    """Build an extractive document-level routing text from title and abstract.

    If the source has no abstract, use a short extractive lead from its first
    paragraphs. This text routes to documents; returned evidence remains source
    paragraphs/chunks.
    """
    parts = [f"Title: {document.title}"]
    if document.abstract and document.abstract.strip():
        parts.append(f"Abstract: {document.abstract.strip()}")
    else:
        counter = RegexTokenCounter()
        excerpt: list[str] = []
        count = 0
        ordered = sorted(source_units, key=lambda unit: getattr(unit, "ordinal_in_document", getattr(unit, "ordinal", 0)))
        for paragraph in ordered:
            if len(excerpt) >= 3 or count >= 240:
                break
            text = paragraph.text
            excerpt.append(text)
            count += counter.count(text)
        if excerpt:
            parts.append("Extractive lead: " + " ".join(excerpt))
    return "\n".join(parts)


@dataclass(frozen=True)
class IndexingSummary:
    documents: int
    paragraphs: int
    child_chunks: int
    document_vectors_upserted: int
    child_vectors_upserted: int
    dimension: int
    sections: int = 0


def index_normalized_corpus(
    data_dir: str | Path,
    *,
    embedder: TextEmbedder,
    store: QdrantMedicalKnowledgeStore,
    batch_size: int = 32,
    schema_version: str = "schema-v4",
) -> IndexingSummary:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if embedder.dimension != store.dimension:
        raise ValueError(f"embedder dimension {embedder.dimension} != Qdrant dimension {store.dimension}")
    corpus = load_normalized_corpus(data_dir, schema_version=schema_version)
    if schema_version == "schema-v5" and hasattr(embedder, "model_name") and hasattr(embedder, "revision"):
        expected_tokenizer = (
            f"{getattr(embedder, 'model_name')}@{getattr(embedder, 'revision') or 'default'}"
        )
        mismatches = sorted({chunk.tokenizer_name for chunk in corpus.chunks if chunk.tokenizer_name != expected_tokenizer})
        if mismatches:
            raise ValueError(
                "schema-v5 chunking tokenizer does not match the embedding model revision: "
                f"chunks={mismatches}, embedder={expected_tokenizer}"
            )
    store.ensure_collection()
    docs = list(corpus.documents.values())
    source_units_by_doc: dict[str, list[DocumentParagraph | DocumentSection]] = {
        doc_id: [] for doc_id in corpus.documents
    }
    source_units = corpus.sections.values() if schema_version == "schema-v5" else corpus.paragraphs.values()
    for source_unit in source_units:
        source_units_by_doc[source_unit.document_id].append(source_unit)
    document_texts = [document_retrieval_text(doc, source_units_by_doc[doc.document_id]) for doc in docs]
    document_vectors = embedder.embed_documents(document_texts)
    document_vectors_upserted = store.upsert_documents(docs, document_texts, document_vectors)

    chunks_upserted = 0
    for start in range(0, len(corpus.chunks), batch_size):
        batch = corpus.chunks[start : start + batch_size]
        vectors = embedder.embed_documents([chunk.embedding_text for chunk in batch])
        chunks_upserted += store.upsert_chunks(batch, vectors, corpus.documents)
    return IndexingSummary(
        documents=len(corpus.documents),
        paragraphs=len(corpus.paragraphs),
        child_chunks=len(corpus.chunks),
        document_vectors_upserted=document_vectors_upserted,
        child_vectors_upserted=chunks_upserted,
        dimension=store.dimension,
        sections=len(corpus.sections),
    )
