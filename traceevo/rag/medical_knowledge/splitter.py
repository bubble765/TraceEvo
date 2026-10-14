"""Document-level child chunking plus the retained schema-v2 section splitter."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Protocol

from traceevo.rag.common.schemas import (
    DocumentChunk,
    DocumentParagraph,
    DocumentSection,
    KnowledgeChunk,
    MedicalDocument,
    ParagraphSpan,
    ParentSection,
    SectionBlock,
    SectionChunk,
    SectionSpan,
)

from .embedding import QWEN3_EMBEDDING_REVISION
from .parser import stable_id


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class RegexTokenCounter:
    """Tokenizer seam for v1; counts lexical units, not Qwen tokenizer IDs."""

    name = "unicode_lexical_estimate_v1"
    _pattern = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*|[\u3400-\u9fff]|[^\s\w]", re.UNICODE)

    def count(self, text: str) -> int:
        return len(self._pattern.findall(text))

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        return [(match.start(), match.end()) for match in self._pattern.finditer(text)]


class Qwen3TokenizerCounter:
    """Exact fast-tokenizer counter matching the pinned embedding tokenizer."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-0.6B",
        *,
        revision: str | None = QWEN3_EMBEDDING_REVISION,
    ) -> None:
        self.model_name = model_name
        self.revision = revision
        self.name = f"{model_name}@{revision or 'default'}"
        self._tokenizer = None

    def _load_tokenizer(self):
        if self._tokenizer is not None:
            return self._tokenizer
        try:
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "exact medical chunking requires transformers; install "
                "traceevo/rag/requirements-medical-knowledge.txt"
            ) from exc
        kwargs = {"use_fast": True}
        if self.revision:
            kwargs["revision"] = self.revision
        tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
        if not tokenizer.is_fast:
            raise RuntimeError("Qwen3 medical chunking requires a fast tokenizer with character offsets")
        self._tokenizer = tokenizer
        return tokenizer

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        if not text:
            return []
        encoded = self._load_tokenizer()(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        return [
            (int(start), int(end))
            for start, end in encoded["offset_mapping"]
            if int(end) > int(start)
        ]

    def count(self, text: str) -> int:
        return len(self.token_offsets(text))


@dataclass(frozen=True)
class SplitterConfig:
    target_tokens: int = 384
    max_tokens: int = 480
    overlap_tokens: int = 64
    min_tokens: int = 120

    def __post_init__(self) -> None:
        if self.target_tokens <= 0 or self.max_tokens <= 0:
            raise ValueError("target_tokens and max_tokens must be positive")
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if self.overlap_tokens < 0 or self.min_tokens < 0:
            raise ValueError("overlap_tokens and min_tokens cannot be negative")
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("overlap_tokens must be smaller than max_tokens")


@dataclass(frozen=True)
class DocumentChunkConfig:
    """Schema-v4 chunk limits; deliberately has no overlap parameter."""

    target_tokens: int = 384
    max_tokens: int = 480
    min_tokens: int = 120

    def __post_init__(self) -> None:
        if self.target_tokens <= 0 or self.max_tokens <= 0:
            raise ValueError("target_tokens and max_tokens must be positive")
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if self.min_tokens < 0:
            raise ValueError("min_tokens cannot be negative")


@dataclass(frozen=True)
class ProtectedTextSpan:
    start_char: int
    end_char: int
    label: str


DEFAULT_MEDICAL_ENTITY_TERMS = (
    "Ottawa Ankle Rules", "OARs", "lateral malleolus", "medial malleolus",
    "fifth metatarsal", "navicular bone", "ankle fracture", "ankle fractures",
    "ankle sprain", "Salter-Harris", "踝关节骨折", "踝关节扭伤", "软组织挫伤",
    "外踝", "内踝", "第五跖骨", "舟骨", "距骨",
)


class MedicalEntityLexicon:
    """Small built-in medical lexicon, extensible from a one-term-per-line file."""

    def __init__(self, terms: Iterable[str] | None = None) -> None:
        source_terms = DEFAULT_MEDICAL_ENTITY_TERMS if terms is None else terms
        normalized = {term.strip() for term in source_terms if term.strip()}
        self.terms = tuple(sorted(normalized, key=lambda term: (-len(term), term.casefold())))
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for term in self.terms:
            escaped = re.escape(term)
            if re.search(r"[A-Za-z0-9]", term):
                pattern = re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
            else:
                pattern = re.compile(escaped)
            self._patterns.append((term, pattern))

    @classmethod
    def from_file(cls, path: str | Path) -> "MedicalEntityLexicon":
        terms = [
            line.strip()
            for line in Path(path).read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        return cls([*DEFAULT_MEDICAL_ENTITY_TERMS, *terms])

    def find_spans(self, text: str) -> list[ProtectedTextSpan]:
        matches: list[ProtectedTextSpan] = []
        for label, pattern in self._patterns:
            matches.extend(
                ProtectedTextSpan(match.start(), match.end(), label)
                for match in pattern.finditer(text)
            )
        matches.sort(key=lambda span: (span.start_char, span.end_char))
        # Merge overlapping lexicon matches so no internal boundary can split
        # a nested form (for example, a specific bone term inside a diagnosis).
        merged: list[ProtectedTextSpan] = []
        for span in matches:
            if merged and span.start_char < merged[-1].end_char:
                previous = merged[-1]
                merged[-1] = ProtectedTextSpan(
                    previous.start_char,
                    max(previous.end_char, span.end_char),
                    f"{previous.label}|{span.label}",
                )
            else:
                merged.append(span)
        return merged


class ParentChildSplitter:
    def __init__(self, config: SplitterConfig | None = None, token_counter: TokenCounter | None = None):
        self.config = config or SplitterConfig()
        self.token_counter = token_counter or RegexTokenCounter()

    def split(self, document_id: str, source_uri: str, sections: list[ParentSection]) -> list[KnowledgeChunk]:
        result: list[KnowledgeChunk] = []
        for section in sections:
            if section.document_id != document_id:
                raise ValueError(f"parent {section.section_id} belongs to {section.document_id}, not {document_id}")
            texts = self._split_section(section.text)
            for ordinal, text in enumerate(texts):
                result.append(
                    KnowledgeChunk(
                        chunk_id=stable_id("chunk", document_id, section.section_id, str(ordinal), text),
                        document_id=document_id,
                        parent_section_id=section.section_id,
                        heading_path=section.heading_path,
                        text=text,
                        token_count=self.token_counter.count(text),
                        ordinal_in_parent=ordinal,
                        source_uri=source_uri,
                        tokenizer_name=self.token_counter.name,
                    )
                )
        return result

    def _split_section(self, text: str) -> list[str]:
        blocks = self._semantic_blocks(text)
        if not blocks:
            return []
        units: list[str] = []
        for block in blocks:
            if self.token_counter.count(block) <= self.config.max_tokens:
                units.append(block)
            else:
                units.extend(self._split_oversized_block(block))

        packed: list[list[str]] = []
        current: list[str] = []
        for unit in units:
            candidate = [*current, unit]
            candidate_count = self.token_counter.count("\n\n".join(candidate))
            if current and candidate_count > self.config.target_tokens:
                packed.append(current)
                carry = self._overlap_tail(current)
                current = [*carry, unit]
                # The new unit is already bounded; if the overlap leaves no
                # room, prefer a clean boundary over violating max_tokens.
                while len(current) > 1 and self.token_counter.count("\n\n".join(current)) > self.config.max_tokens:
                    current.pop(0)
            else:
                current = candidate
        if current:
            packed.append(current)

        # Merge a short final chunk only when both parent boundary and max
        # token budget remain safe. Overlap duplicates are removed for merge.
        if len(packed) > 1 and self.token_counter.count("\n\n".join(packed[-1])) < self.config.min_tokens:
            unique_tail = self._without_prefix_overlap(packed[-2], packed[-1])
            merged = [*packed[-2], *unique_tail]
            if merged and self.token_counter.count("\n\n".join(merged)) <= self.config.max_tokens:
                packed[-2:] = [merged]

        return ["\n\n".join(part).strip() for part in packed if "\n\n".join(part).strip()]

    @staticmethod
    def _semantic_blocks(text: str) -> list[str]:
        # Parsers emit paragraphs/list/table/caption blocks with blank lines.
        # Keep each block intact unless it exceeds max_tokens.
        return [re.sub(r"\s+", " ", block).strip() for block in re.split(r"\n\s*\n", text) if block.strip()]

    def _split_oversized_block(self, block: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?。！？])\s+", block.strip())
        sentences = [item.strip() for item in sentences if item.strip()]
        # A long sentence is the last-resort split unit. Prefer whitespace
        # boundaries; for unspaced CJK text split at character boundaries.
        expanded: list[str] = []
        for sentence in sentences:
            if self.token_counter.count(sentence) <= self.config.max_tokens:
                expanded.append(sentence)
                continue
            words = re.findall(r"\S+\s*", sentence)
            if not words:
                words = list(sentence)
            current = ""
            for word in words:
                candidate = current + word
                if current and self.token_counter.count(candidate) > self.config.max_tokens:
                    expanded.append(current.strip())
                    current = word
                else:
                    current = candidate
            if current.strip():
                expanded.append(current.strip())
        return expanded

    def _overlap_tail(self, units: list[str]) -> list[str]:
        selected: list[str] = []
        for unit in reversed(units):
            count = self.token_counter.count(unit)
            if not selected and count > self.config.overlap_tokens:
                break
            if self.token_counter.count("\n\n".join([unit, *selected])) > self.config.overlap_tokens:
                break
            selected.insert(0, unit)
        return selected

    @staticmethod
    def _without_prefix_overlap(first: list[str], second: list[str]) -> list[str]:
        max_size = min(len(first), len(second))
        for size in range(max_size, 0, -1):
            if first[-size:] == second[:size]:
                return second[size:]
        return second


@dataclass(frozen=True)
class _DocumentUnit:
    text: str
    paragraph_id: str
    heading_path: tuple[str, ...]
    start_char: int
    end_char: int
    boundary_notes: tuple[str, ...] = ()


class DocumentChunkSplitter:
    """Pack document paragraphs without overlap and snap cuts around protected terms."""

    def __init__(
        self,
        config: DocumentChunkConfig | None = None,
        token_counter: TokenCounter | None = None,
        entity_lexicon: MedicalEntityLexicon | None = None,
    ):
        self.config = config or DocumentChunkConfig()
        self.token_counter = token_counter or RegexTokenCounter()
        self.entity_lexicon = entity_lexicon or MedicalEntityLexicon()

    def split(self, document: MedicalDocument, paragraphs: list[DocumentParagraph]) -> list[DocumentChunk]:
        if any(paragraph.document_id != document.document_id for paragraph in paragraphs):
            raise ValueError("all paragraphs must belong to the supplied document")
        ordered = sorted(paragraphs, key=lambda paragraph: paragraph.ordinal_in_document)
        if len({p.paragraph_id for p in ordered}) != len(ordered):
            raise ValueError("paragraph IDs must be unique within a document")

        units: list[_DocumentUnit] = []
        for paragraph in ordered:
            units.extend(self._split_paragraph(paragraph))
        packed = self._pack(units)
        chunks: list[DocumentChunk] = []
        for ordinal, group in enumerate(packed):
            text = "\n\n".join(unit.text for unit in group).strip()
            paragraph_ids = list(dict.fromkeys(unit.paragraph_id for unit in group))
            paragraph_spans = [
                ParagraphSpan(
                    paragraph_id=unit.paragraph_id,
                    start_char=unit.start_char,
                    end_char=unit.end_char,
                )
                for unit in group
            ]
            heading_paths = [list(path) for path in dict.fromkeys(unit.heading_path for unit in group) if path]
            boundary_notes = list(dict.fromkeys(note for unit in group for note in unit.boundary_notes))
            embedding_parts: list[str] = [f"Document: {document.title}"]
            active_heading: tuple[str, ...] | None = None
            for unit in group:
                if unit.heading_path and unit.heading_path != active_heading:
                    embedding_parts.append("Heading: " + " > ".join(unit.heading_path))
                    active_heading = unit.heading_path
                embedding_parts.append(unit.text)
            embedding_text = "\n".join(embedding_parts)
            chunks.append(
                DocumentChunk(
                    chunk_id=stable_id(
                        "chunk", document.document_id, str(ordinal), "|".join(paragraph_ids),
                        ";".join(f"{span.paragraph_id}:{span.start_char}-{span.end_char}" for span in paragraph_spans),
                        text,
                    ),
                    document_id=document.document_id,
                    paragraph_ids=paragraph_ids,
                    paragraph_spans=paragraph_spans,
                    heading_paths=heading_paths,
                    boundary_notes=boundary_notes,
                    text=text,
                    embedding_text=embedding_text,
                    token_count=self.token_counter.count(text),
                    ordinal_in_document=ordinal,
                    source_uri=document.source_uri,
                    tokenizer_name=self.token_counter.name,
                )
            )
        return chunks

    def _split_paragraph(self, paragraph: DocumentParagraph) -> list[_DocumentUnit]:
        text = paragraph.text
        heading_path = tuple(paragraph.heading_path)
        if self.token_counter.count(text) <= self.config.max_tokens:
            return [self._make_unit(text, paragraph.paragraph_id, heading_path, 0, len(text))]

        protected = self.entity_lexicon.find_spans(text)
        units: list[_DocumentUnit] = []
        cursor = 0
        while cursor < len(text):
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text):
                break
            if self.token_counter.count(text[cursor:]) <= self.config.max_tokens:
                units.append(self._make_unit(text, paragraph.paragraph_id, heading_path, cursor, len(text)))
                break

            target_end = self._farthest_within_budget(text, cursor, self.config.target_tokens)
            hard_end = self._farthest_within_budget(text, cursor, self.config.max_tokens)
            cut, snapped, oversized_entity = self._choose_boundary(
                text, cursor, target_end, hard_end, protected
            )
            if cut <= cursor:
                # Only possible when a protected entity itself exceeds the
                # hard chunk budget. Preserve it whole as a documented
                # exceptional oversize unit rather than cutting its name.
                enclosing = next(
                    (span for span in protected if span.start_char <= cursor < span.end_char),
                    None,
                )
                if enclosing is not None:
                    cut = enclosing.end_char
                    oversized_entity = True
                else:
                    cut = max(hard_end, cursor + 1)
            notes: list[str] = []
            if snapped:
                notes.append("cut snapped to a protected-entity boundary")
            if oversized_entity:
                notes.append("protected entity exceeded the chunk budget and was kept intact")
            units.append(
                self._make_unit(text, paragraph.paragraph_id, heading_path, cursor, cut, tuple(notes))
            )
            cursor = cut
        return units

    def _choose_boundary(
        self,
        text: str,
        cursor: int,
        target_end: int,
        hard_end: int,
        protected: list[ProtectedTextSpan],
    ) -> tuple[int, bool, bool]:
        target_tokens = self.token_counter.count(text[cursor:target_end])
        priorities: dict[int, int] = {}
        # Arbitrary character boundaries are the final fallback (especially
        # for unspaced Chinese); explicit linguistic boundaries score better.
        for position in range(cursor + 1, hard_end + 1):
            priorities[position] = 4
        for match in re.finditer(r"[.!?。！？；;]+(?:\s+|$)", text[cursor:hard_end]):
            priorities[cursor + match.end()] = 0
        for match in re.finditer(r"\s+", text[cursor:hard_end]):
            priorities[cursor + match.start()] = min(priorities.get(cursor + match.start(), 9), 1)
            priorities[cursor + match.end()] = min(priorities.get(cursor + match.end(), 9), 1)

        # Jieba contributes Chinese word boundaries when installed. The
        # lexicon remains authoritative, so a token boundary inside a medical
        # entity is filtered out below.
        try:
            import jieba  # type: ignore[import-not-found]
        except ImportError:
            jieba = None
        if jieba is not None:
            for _, _, end in jieba.tokenize(text[cursor:hard_end], mode="default"):
                position = cursor + end
                priorities[position] = min(priorities.get(position, 9), 1)

        for span in protected:
            if cursor < span.start_char <= hard_end:
                priorities[span.start_char] = min(priorities.get(span.start_char, 9), 1)
            if cursor < span.end_char <= hard_end:
                priorities[span.end_char] = min(priorities.get(span.end_char, 9), 1)

        def is_protected_interior(position: int) -> bool:
            return any(span.start_char < position < span.end_char for span in protected)

        candidates = [
            position
            for position in priorities
            if cursor < position <= hard_end and not is_protected_interior(position)
        ]
        if not candidates:
            return cursor, False, False

        def score(position: int) -> tuple[int, int, int]:
            used = self.token_counter.count(text[cursor:position])
            return (abs(used - target_tokens), priorities[position], position)

        cut = min(candidates, key=score)
        snapped = any(
            span.start_char < target_end < span.end_char and cut in {span.start_char, span.end_char}
            for span in protected
        )
        oversized_entity = any(
            span.start_char <= cursor < span.end_char
            and self.token_counter.count(text[cursor:span.end_char]) > self.config.max_tokens
            and cut == span.end_char
            for span in protected
        )
        return cut, snapped, oversized_entity

    def _farthest_within_budget(self, text: str, start: int, budget: int) -> int:
        low, high, best = start + 1, len(text), start
        while low <= high:
            middle = (low + high) // 2
            if self.token_counter.count(text[start:middle]) <= budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    @staticmethod
    def _make_unit(
        text: str,
        paragraph_id: str,
        heading_path: tuple[str, ...],
        start: int,
        end: int,
        notes: tuple[str, ...] = (),
    ) -> _DocumentUnit:
        raw = text[start:end]
        left = len(raw) - len(raw.lstrip())
        trimmed = raw.strip()
        actual_start = start + left
        actual_end = actual_start + len(trimmed)
        return _DocumentUnit(
            text=trimmed,
            paragraph_id=paragraph_id,
            heading_path=heading_path,
            start_char=actual_start,
            end_char=actual_end,
            boundary_notes=notes,
        )

    def _pack(self, units: list[_DocumentUnit]) -> list[list[_DocumentUnit]]:
        packed: list[list[_DocumentUnit]] = []
        current: list[_DocumentUnit] = []
        for unit in units:
            candidate = [*current, unit]
            if current and self.token_counter.count("\n\n".join(u.text for u in candidate)) > self.config.target_tokens:
                packed.append(current)
                current = [unit]
            else:
                current = candidate
        if current:
            packed.append(current)

        if len(packed) > 1 and self.token_counter.count("\n\n".join(u.text for u in packed[-1])) < self.config.min_tokens:
            merged = packed[-2] + packed[-1]
            if self.token_counter.count("\n\n".join(u.text for u in merged)) <= self.config.max_tokens:
                packed[-2:] = [merged]
        return packed


@dataclass(frozen=True)
class SectionChunkConfig:
    """Schema-v5 target/hard limits and short-tail merge threshold."""

    target_tokens: int = 384
    max_tokens: int = 480
    tail_merge_tokens: int = 96

    def __post_init__(self) -> None:
        if self.target_tokens <= 0 or self.max_tokens <= 0:
            raise ValueError("target_tokens and max_tokens must be positive")
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens cannot exceed max_tokens")
        if not 0 <= self.tail_merge_tokens <= self.max_tokens - self.target_tokens:
            raise ValueError("tail_merge_tokens must be no greater than max_tokens - target_tokens")


@dataclass(frozen=True)
class _SectionChunkDraft:
    start_char: int
    end_char: int
    block_ids: tuple[str, ...]
    boundary_notes: tuple[str, ...] = ()


_SENTENCE_END = re.compile(r"[.!?]+(?:[\"'’”)}\]]*)?(?=\s|$)")
_ABBREVIATIONS = {
    "approx", "dept", "dr", "eq", "fig", "inc", "jr", "mr", "mrs", "ms",
    "no", "prof", "ref", "refs", "rev", "sr", "st", "vs", "vol", "pp",
    "p", "al", "e.g", "i.e", "u.s", "u.k", "u.s.a",
}


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """Return complete English sentence spans; omit an unterminated final fragment."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for end in _sentence_end_positions(text):
        start = cursor
        while start < end and text[start].isspace():
            start += 1
        if end > start:
            spans.append((start, end))
        cursor = end
    return spans


def _sentence_end_positions(text: str) -> list[int]:
    ends: list[int] = []
    for match in _SENTENCE_END.finditer(text):
        punctuation_index = next((i for i in range(match.start(), match.end()) if text[i] in ".!?"), match.start())
        if text[punctuation_index] == ".":
            previous = text[:punctuation_index]
            following = text[match.end():]
            if punctuation_index > 0 and punctuation_index + 1 < len(text):
                if text[punctuation_index - 1].isdigit() and text[punctuation_index + 1].isdigit():
                    continue
            token_match = re.search(r"([A-Za-z]+(?:\.[A-Za-z]+)*(?:\.)?)$", previous.rstrip())
            token = token_match.group(1).casefold().rstrip(".") if token_match else ""
            if token in _ABBREVIATIONS or re.fullmatch(r"(?:[a-z]\.){1,5}", (token_match.group(1).casefold() if token_match else "")):
                continue
            token_prefix = previous.rstrip()[:token_match.start()] if token_match else previous
            if len(token) == 1 and (not token_prefix or token_prefix[-1].isspace()) and following.lstrip()[:1].isupper():
                continue
        ends.append(match.end())
    return ends


class SectionChunkSplitter:
    """Sentence-aware, no-overlap chunker with section-local character offsets."""

    def __init__(
        self,
        config: SectionChunkConfig | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self.config = config or SectionChunkConfig()
        self.token_counter = token_counter or Qwen3TokenizerCounter()

    def split(self, document: MedicalDocument, sections: list[DocumentSection]) -> list[SectionChunk]:
        if any(section.document_id != document.document_id for section in sections):
            raise ValueError("all sections must belong to the supplied document")
        ordered = sorted(sections, key=lambda section: section.ordinal)
        if len({section.section_id for section in ordered}) != len(ordered):
            raise ValueError("section IDs must be unique within a document")

        chunks: list[SectionChunk] = []
        for section in ordered:
            for group in self._block_groups(section.blocks):
                drafts = self._split_group(section.text, group)
                drafts = self._merge_short_tail(section.text, drafts)
                for draft in drafts:
                    start, end = self._trim_span(section.text, draft.start_char, draft.end_char)
                    if end <= start:
                        continue
                    text = section.text[start:end]
                    block_ids = list(dict.fromkeys(draft.block_ids))
                    table_metadata = self._table_metadata(group)
                    content_type = group[0].block_type
                    heading = " > ".join(section.heading_path)
                    embedding_parts = [f"Document: {document.title}"]
                    if heading:
                        embedding_parts.append(f"Heading: {heading}")
                    if content_type == "table_row":
                        if table_metadata.get("caption"):
                            embedding_parts.append(f"Table: {table_metadata['caption']}")
                        headers = table_metadata.get("column_headers") or []
                        if headers:
                            embedding_parts.append("Column headers: " + " | ".join(headers))
                    embedding_parts.append(text)
                    ordinal = len(chunks)
                    chunks.append(
                        SectionChunk(
                            chunk_id=stable_id(
                                "chunk-v5", document.document_id, section.section_id,
                                str(start), str(end), text,
                            ),
                            document_id=document.document_id,
                            section_id=section.section_id,
                            section_span=SectionSpan(section_id=section.section_id, start_char=start, end_char=end),
                            block_ids=block_ids,
                            heading_path=section.heading_path,
                            content_type=content_type,
                            table_metadata=table_metadata,
                            boundary_notes=list(draft.boundary_notes),
                            text=text,
                            embedding_text="\n".join(embedding_parts),
                            token_count=self.token_counter.count(text),
                            ordinal_in_document=ordinal,
                            source_uri=document.source_uri,
                            tokenizer_name=self.token_counter.name,
                        )
                    )
        return chunks

    @staticmethod
    def _block_groups(blocks: list[SectionBlock]) -> list[list[SectionBlock]]:
        groups: list[list[SectionBlock]] = []
        current: list[SectionBlock] = []
        current_key: tuple[str, str | None] | None = None
        for block in blocks:
            table_id = str(block.metadata.get("table_id") or block.block_id) if block.block_type == "table_row" else None
            key = (block.block_type, table_id)
            if current and key != current_key:
                groups.append(current)
                current = []
            current.append(block)
            current_key = key
        if current:
            groups.append(current)
        return groups

    def _split_group(self, section_text: str, blocks: list[SectionBlock]) -> list[_SectionChunkDraft]:
        if blocks[0].block_type == "table_row":
            return self._split_table_rows(section_text, blocks)
        return self._split_narrative(section_text, blocks[0].start_char, blocks[-1].end_char, blocks)

    def _split_narrative(
        self,
        source_text: str,
        start_char: int,
        end_char: int,
        blocks: list[SectionBlock],
    ) -> list[_SectionChunkDraft]:
        drafts: list[_SectionChunkDraft] = []
        cursor = start_char
        while cursor < end_char:
            while cursor < end_char and source_text[cursor].isspace():
                cursor += 1
            if cursor >= end_char:
                break
            remainder = source_text[cursor:end_char]
            offsets = self._token_offsets(remainder)
            if len(offsets) <= self.config.max_tokens:
                drafts.append(self._draft_for_span(cursor, end_char, blocks))
                break

            target_char = offsets[self.config.target_tokens - 1][1]
            hard_char = offsets[self.config.max_tokens - 1][1]
            if target_char <= 0:
                target_char = len(remainder)
            sentence_end = next(
                (
                    boundary for boundary in _sentence_end_positions(remainder)
                    if boundary >= target_char
                    and self.token_counter.count(remainder[:boundary]) <= self.config.max_tokens
                ),
                None,
            )
            notes: list[str] = []
            if sentence_end is not None:
                cut = cursor + sentence_end
            else:
                cut_in_remainder = self._last_safe_word_boundary(remainder, hard_char)
                if cut_in_remainder is None:
                    cut_in_remainder = hard_char
                    notes.append("hard-limit fallback had no whitespace boundary")
                else:
                    notes.append("hard-limit fallback at a word boundary")
                if cut_in_remainder <= 0:
                    cut_in_remainder = hard_char
                    notes.append("hard-limit fallback used tokenizer offset")
                cut = cursor + cut_in_remainder
            if cut <= cursor:
                cut = cursor + hard_char
                notes.append("hard-limit fallback used tokenizer offset")
            draft = self._draft_for_span(cursor, cut, blocks, tuple(dict.fromkeys(notes)))
            drafts.append(draft)
            cursor = cut
        return drafts

    def _split_table_rows(self, source_text: str, blocks: list[SectionBlock]) -> list[_SectionChunkDraft]:
        drafts: list[_SectionChunkDraft] = []
        row_index = 0
        while row_index < len(blocks):
            row = blocks[row_index]
            remaining_text = source_text[row.start_char:blocks[-1].end_char]
            if self.token_counter.count(remaining_text) <= self.config.max_tokens:
                drafts.append(self._draft_for_span(row.start_char, blocks[-1].end_char, blocks[row_index:]))
                break

            best_end: int | None = None
            target_end: int | None = None
            for end_index in range(row_index + 1, len(blocks) + 1):
                start = blocks[row_index].start_char
                end = blocks[end_index - 1].end_char
                count = self.token_counter.count(source_text[start:end])
                if count <= self.config.max_tokens:
                    best_end = end_index
                    if count >= self.config.target_tokens:
                        target_end = end_index
                        break
                else:
                    break
            chosen_end = target_end or best_end
            if chosen_end is not None and chosen_end > row_index:
                drafts.append(
                    self._draft_for_span(
                        blocks[row_index].start_char,
                        blocks[chosen_end - 1].end_char,
                        blocks[row_index:chosen_end],
                    )
                )
                row_index = chosen_end
                continue

            # One exceptionally long table row: retain its row metadata, but
            # allow the ordinary 384/480 sentence/word fallback within it.
            row_drafts = self._split_narrative(
                source_text, row.start_char, row.end_char, [row]
            )
            drafts.extend(
                _SectionChunkDraft(
                    item.start_char,
                    item.end_char,
                    item.block_ids,
                    tuple(dict.fromkeys([*item.boundary_notes, "oversized table row split"])),
                )
                for item in row_drafts
            )
            row_index += 1
        return drafts

    def _merge_short_tail(
        self,
        source_text: str,
        drafts: list[_SectionChunkDraft],
    ) -> list[_SectionChunkDraft]:
        if len(drafts) < 2:
            return drafts
        tail = drafts[-1]
        tail_text = source_text[tail.start_char:tail.end_char]
        if self.token_counter.count(tail_text) >= self.config.tail_merge_tokens:
            return drafts
        previous = drafts[-2]
        combined_text = source_text[previous.start_char:tail.end_char]
        if self.token_counter.count(combined_text) > self.config.max_tokens:
            return drafts
        merged = _SectionChunkDraft(
            start_char=previous.start_char,
            end_char=tail.end_char,
            block_ids=tuple(dict.fromkeys([*previous.block_ids, *tail.block_ids])),
            boundary_notes=tuple(dict.fromkeys([*previous.boundary_notes, *tail.boundary_notes, "short tail merged"])),
        )
        return [*drafts[:-2], merged]

    def _token_offsets(self, text: str) -> list[tuple[int, int]]:
        method = getattr(self.token_counter, "token_offsets", None)
        if callable(method):
            return list(method(text))
        # Compatibility for small injected test counters; production ingestion
        # uses Qwen3TokenizerCounter and its exact fast-tokenizer offsets.
        return [(match.start(), match.end()) for match in RegexTokenCounter._pattern.finditer(text)]

    def _last_safe_word_boundary(self, text: str, hard_char: int) -> int | None:
        boundaries = [match.start() for match in re.finditer(r"\s+", text) if match.start() <= hard_char]
        for boundary in reversed(boundaries):
            if boundary > 0 and self.token_counter.count(text[:boundary].rstrip()) <= self.config.max_tokens:
                return boundary
        return None

    @staticmethod
    def _draft_for_span(
        start: int,
        end: int,
        blocks: list[SectionBlock],
        notes: tuple[str, ...] = (),
    ) -> _SectionChunkDraft:
        covered = tuple(
            block.block_id for block in blocks
            if block.start_char < end and block.end_char > start
        )
        return _SectionChunkDraft(start, end, covered, notes)

    @staticmethod
    def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        return start, end

    @staticmethod
    def _table_metadata(blocks: list[SectionBlock]) -> dict[str, Any]:
        if not blocks or blocks[0].block_type != "table_row":
            return {}
        return dict(blocks[0].metadata)
