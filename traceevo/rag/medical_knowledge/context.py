"""Paragraph-based context expansion for document-level child hits."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from traceevo.rag.common.schemas import DocumentParagraph, DocumentSection

from .splitter import Qwen3TokenizerCounter, RegexTokenCounter, TokenCounter, sentence_spans
from .vector_store import VectorSearchPoint


class DocumentContextError(ValueError):
    pass


@dataclass(frozen=True)
class HydratedContext:
    text: str
    paragraph_ids: list[str]
    anchor_paragraph_ids: list[str]
    truncated: bool
    section_ids: list[str] = field(default_factory=list)
    anchor_section_ids: list[str] = field(default_factory=list)


class DocumentParagraphStore:
    """In-memory paragraph lookup for the local v4 corpus, in source order."""

    def __init__(self, data_dir: str | Path, *, schema_version: str = "schema-v4") -> None:
        if schema_version != "schema-v4":
            raise ValueError("paragraph hydration only supports schema-v4")
        self.root = Path(data_dir) / "normalized" / schema_version
        if not self.root.is_dir():
            raise FileNotFoundError(f"normalized document corpus not found: {self.root}")
        self._by_document: dict[str, list[DocumentParagraph]] = {}
        self._by_id: dict[str, DocumentParagraph] = {}
        import json

        for folder in sorted(path for path in self.root.iterdir() if path.is_dir()):
            document_file = folder / "document.json"
            paragraph_file = folder / "paragraphs.jsonl"
            if not document_file.is_file() or not paragraph_file.is_file():
                raise DocumentContextError(f"incomplete document/paragraph bundle: {folder}")
            document = json.loads(document_file.read_text(encoding="utf-8"))
            document_id = str(document["document_id"])
            if document_id in self._by_document:
                raise DocumentContextError(f"duplicate document bundle: {document_id}")
            paragraphs = [
                DocumentParagraph.model_validate_json(line)
                for line in paragraph_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            paragraphs.sort(key=lambda paragraph: paragraph.ordinal_in_document)
            ordinals = [paragraph.ordinal_in_document for paragraph in paragraphs]
            if ordinals != list(range(len(paragraphs))):
                raise DocumentContextError(f"paragraph ordinals are not contiguous in {document_id}")
            for paragraph in paragraphs:
                if paragraph.document_id != document_id or paragraph.paragraph_id in self._by_id:
                    raise DocumentContextError(f"invalid/duplicate paragraph: {paragraph.paragraph_id}")
                self._by_id[paragraph.paragraph_id] = paragraph
            self._by_document[document_id] = paragraphs
        self._counter = RegexTokenCounter()

    def hydrate(
        self,
        hit: VectorSearchPoint,
        *,
        max_context_tokens: int = 1200,
        max_anchor_tokens: int = 800,
        max_neighbor_tokens: int = 200,
    ) -> HydratedContext:
        if max_context_tokens <= 0 or max_anchor_tokens <= 0 or max_neighbor_tokens < 0:
            raise ValueError("context token limits must be positive (neighbor limit may be zero)")
        paragraphs = self._by_document.get(hit.document_id, [])
        if not paragraphs:
            raise DocumentContextError(f"no paragraphs for document {hit.document_id}")
        by_id = {paragraph.paragraph_id: paragraph for paragraph in paragraphs}
        if not hit.paragraph_spans:
            raise DocumentContextError(f"chunk {hit.chunk_id} has no source character spans")
        anchor_ids = list(dict.fromkeys(span.paragraph_id for span in hit.paragraph_spans))
        if anchor_ids != hit.paragraph_ids or any(paragraph_id not in by_id for paragraph_id in anchor_ids):
            raise DocumentContextError(f"chunk {hit.chunk_id} refers to missing document paragraphs")
        anchors = sorted((by_id[paragraph_id] for paragraph_id in anchor_ids), key=lambda p: p.ordinal_in_document)
        anchor_positions = [paragraph.ordinal_in_document for paragraph in anchors]
        before = paragraphs[min(anchor_positions) - 1] if min(anchor_positions) > 0 else None
        after = paragraphs[max(anchor_positions) + 1] if max(anchor_positions) + 1 < len(paragraphs) else None

        spans_by_paragraph: dict[str, list[tuple[int, int]]] = {paragraph_id: [] for paragraph_id in anchor_ids}
        for span in hit.paragraph_spans:
            paragraph = by_id.get(span.paragraph_id)
            if paragraph is None or span.end_char > len(paragraph.text):
                raise DocumentContextError(f"chunk {hit.chunk_id} has an invalid source paragraph span")
            spans_by_paragraph[span.paragraph_id].append((span.start_char, span.end_char))
        # Always reconstruct only the source range covered by this child.
        # Whole-paragraph expansion here would make separate children from a
        # long paragraph repeat the same anchor text in their result contexts.
        center_fragments = []
        for paragraph in anchors:
            ranges = spans_by_paragraph[paragraph.paragraph_id]
            start_char = min(start for start, _ in ranges)
            end_char = max(end for _, end in ranges)
            center_fragments.append((paragraph.text[start_char:end_char], paragraph))
        used_anchor_ids = [paragraph.paragraph_id for paragraph in anchors]
        truncated = False

        center_text = "\n\n".join(text for text, _ in center_fragments)
        center_count = self._counter.count(center_text)
        if center_count > max_anchor_tokens or center_count > max_context_tokens:
            # Never cut the retrieved evidence itself: a hard-budget/entity
            # exception is surfaced as truncated metadata, but the exact
            # source span stays intact.
            truncated = True
        remaining = max(0, max_context_tokens - center_count)

        before_text = ""
        after_text = ""
        if before is not None and before.paragraph_id not in anchor_ids and remaining > 0:
            allowance = min(max_neighbor_tokens, remaining // 2 if after is not None else remaining)
            before_text = self._fit_neighbor(before.text, allowance, keep_tail=True)
            if before_text != before.text:
                truncated = True
            remaining -= self._counter.count(before_text)
        if after is not None and after.paragraph_id not in anchor_ids and remaining > 0:
            allowance = min(max_neighbor_tokens, remaining)
            after_text = self._fit_neighbor(after.text, allowance, keep_tail=False)
            if after_text != after.text:
                truncated = True

        ordered_fragments: list[tuple[str, DocumentParagraph]] = []
        if before_text:
            ordered_fragments.append((before_text, before))
        ordered_fragments.extend(center_fragments)
        if after_text:
            ordered_fragments.append((after_text, after))

        rendered: list[str] = []
        previous_heading: tuple[str, ...] | None = None
        for text, paragraph in ordered_fragments:
            heading = tuple(paragraph.heading_path)
            label = " > ".join(heading)
            if heading and heading != previous_heading:
                rendered.append(f"[{label}]\n{text}")
            else:
                rendered.append(text)
            previous_heading = heading or None
        context_ids = ([before.paragraph_id] if before_text and before is not None else [])
        # If several paragraphs are represented by one oversized child chunk,
        # keep all its source paragraph IDs even though its matched text is a
        # single center fragment.
        context_ids.extend(used_anchor_ids)
        if after_text and after is not None:
            context_ids.append(after.paragraph_id)
        return HydratedContext(
            text="\n\n".join(rendered),
            paragraph_ids=context_ids,
            anchor_paragraph_ids=used_anchor_ids,
            truncated=truncated,
        )

    def _fit_neighbor(self, text: str, token_budget: int, *, keep_tail: bool) -> str:
        """Keep whole edge sentences; do not cut a term inside a sentence."""
        if token_budget <= 0:
            return ""
        sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?。！？])\s+", text) if sentence.strip()]
        ordered = list(reversed(sentences)) if keep_tail else sentences
        selected: list[str] = []
        for sentence in ordered:
            candidate = ([sentence] + selected) if keep_tail else (selected + [sentence])
            if self._counter.count(" ".join(candidate)) > token_budget:
                break
            selected = candidate
        return " ".join(selected)


class DocumentSectionStore:
    """Lazy source-span hydration for schema-v5, adding whole adjacent sentences."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        schema_version: str = "schema-v5",
        token_counter: TokenCounter | None = None,
    ) -> None:
        if schema_version != "schema-v5":
            raise ValueError("section hydration only supports schema-v5")
        self.root = Path(data_dir) / "normalized" / schema_version
        if not self.root.is_dir():
            raise FileNotFoundError(f"normalized section corpus not found: {self.root}")
        self._by_document: dict[str, list[DocumentSection]] = {}
        self._by_id: dict[str, DocumentSection] = {}
        import json

        for folder in sorted(path for path in self.root.iterdir() if path.is_dir()):
            document_file = folder / "document.json"
            section_file = folder / "sections.jsonl"
            if not document_file.is_file() or not section_file.is_file():
                raise DocumentContextError(f"incomplete schema-v5 document/section bundle: {folder}")
            document = json.loads(document_file.read_text(encoding="utf-8"))
            document_id = str(document["document_id"])
            if document_id in self._by_document:
                raise DocumentContextError(f"duplicate document bundle: {document_id}")
            sections = [
                DocumentSection.model_validate_json(line)
                for line in section_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            sections.sort(key=lambda section: section.ordinal)
            if [section.ordinal for section in sections] != list(range(len(sections))):
                raise DocumentContextError(f"section ordinals are not contiguous in {document_id}")
            for section in sections:
                if section.document_id != document_id or section.section_id in self._by_id:
                    raise DocumentContextError(f"invalid/duplicate section: {section.section_id}")
                self._by_id[section.section_id] = section
            self._by_document[document_id] = sections
        self._counter = token_counter or Qwen3TokenizerCounter()

    def hydrate(
        self,
        hit: VectorSearchPoint,
        *,
        max_context_tokens: int = 1200,
        max_anchor_tokens: int = 800,
        max_neighbor_tokens: int = 200,
    ) -> HydratedContext:
        if max_context_tokens <= 0 or max_anchor_tokens <= 0 or max_neighbor_tokens < 0:
            raise ValueError("context token limits must be positive (neighbor limit may be zero)")
        span = hit.section_span
        if span is None:
            raise DocumentContextError(f"chunk {hit.chunk_id} has no schema-v5 section span")
        section = self._by_id.get(span.section_id)
        if section is None or section.document_id != hit.document_id:
            raise DocumentContextError(f"chunk {hit.chunk_id} refers to a missing source section")
        if span.end_char > len(section.text) or section.text[span.start_char:span.end_char] != hit.text:
            raise DocumentContextError(f"chunk {hit.chunk_id} has an invalid source section span")

        anchor = hit.text
        anchor_tokens = self._counter.count(anchor)
        truncated = anchor_tokens > max_anchor_tokens or anchor_tokens > max_context_tokens
        remaining = max(0, max_context_tokens - anchor_tokens)
        sentences = sentence_spans(section.text)
        before_candidates = [item for item in sentences if item[1] <= span.start_char]
        after_candidates = [item for item in sentences if item[0] >= span.end_char]
        before_text = ""
        after_text = ""
        table_metadata = hit.payload.get("table_metadata") or {}
        is_table = hit.payload.get("content_type") == "table_row"
        # A table's adjacent rows are not prose sentences; do not add a nearby
        # row as though it were ordinary linguistic context.
        if not is_table and before_candidates and remaining > 0:
            start, end = before_candidates[-1]
            candidate = section.text[start:end]
            allowance = min(max_neighbor_tokens, remaining // 2 if after_candidates else remaining)
            if self._counter.count(candidate) <= allowance:
                before_text = candidate
                remaining -= self._counter.count(candidate)
            else:
                truncated = True
        if not is_table and after_candidates and remaining > 0:
            start, end = after_candidates[0]
            candidate = section.text[start:end]
            allowance = min(max_neighbor_tokens, remaining)
            if self._counter.count(candidate) <= allowance:
                after_text = candidate
            else:
                truncated = True

        rendered: list[str] = []
        heading = " > ".join(section.heading_path)
        if heading:
            rendered.append(f"[{heading}]")
        if before_text:
            rendered.append(before_text)
        if is_table:
            if table_metadata.get("caption"):
                rendered.append(f"[Table: {table_metadata['caption']}]")
            headers = table_metadata.get("column_headers") or []
            if headers:
                rendered.append("[Columns: " + " | ".join(headers) + "]")
        rendered.append(anchor)
        if after_text:
            rendered.append(after_text)
        return HydratedContext(
            text="\n\n".join(rendered),
            paragraph_ids=[],
            anchor_paragraph_ids=[],
            truncated=truncated,
            section_ids=[section.section_id],
            anchor_section_ids=[section.section_id],
        )
