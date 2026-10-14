"""Normalize sources into stable document-ordered paragraph records."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
import xml.etree.ElementTree as ET

from traceevo.rag.common.schemas import (
    DocumentParagraph,
    DocumentSection,
    MedicalDocument,
    ParentSection,
    SectionBlock,
)

from .licenses import normalize_license


@dataclass(frozen=True)
class MedicalIngestionBundle:
    document: MedicalDocument
    paragraphs: list[DocumentParagraph]


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _bioc_collection(payload: bytes | str | dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list):
        if not payload or not isinstance(payload[0], dict):
            raise ValueError("BioC payload has no collection")
        return payload[0]
    return payload


def _table_grid(xml_text: str) -> tuple[list[str], list[list[str]]] | None:
    """Return expanded table headers/rows, honoring ordinary rowspan/colspan."""
    if not xml_text.strip():
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    parent = {child: node for node in root.iter() for child in list(node)}
    row_nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].casefold() == "tr"]
    if not row_nodes:
        return None
    grid: dict[tuple[int, int], str] = {}
    header_rows: set[int] = set()
    for row_index, row in enumerate(row_nodes):
        ancestor = parent.get(row)
        while ancestor is not None:
            if ancestor.tag.rsplit("}", 1)[-1].casefold() == "thead":
                header_rows.add(row_index)
                break
            ancestor = parent.get(ancestor)
        cells = [
            child for child in list(row)
            if child.tag.rsplit("}", 1)[-1].casefold() in {"th", "td"}
        ]
        if not cells:
            cells = [
                child for child in row.iter()
                if child is not row and child.tag.rsplit("}", 1)[-1].casefold() in {"th", "td"}
            ]
        if cells and all(cell.tag.rsplit("}", 1)[-1].casefold() == "th" for cell in cells):
            header_rows.add(row_index)
        column = 0
        for cell in cells:
            while (row_index, column) in grid:
                column += 1
            value = _clean(" ".join(cell.itertext()))
            try:
                rowspan = max(1, min(100, int(cell.attrib.get("rowspan", "1"))))
                colspan = max(1, min(100, int(cell.attrib.get("colspan", "1"))))
            except ValueError:
                rowspan = colspan = 1
            for row_offset in range(rowspan):
                for column_offset in range(colspan):
                    grid.setdefault((row_index + row_offset, column + column_offset), value)
            column += colspan

    width = max((column for _row, column in grid), default=-1) + 1
    matrix = [[grid.get((row, column), "") for column in range(width)] for row in range(len(row_nodes))]
    if not width:
        return None
    header_indexes = sorted(header_rows)
    headers: list[str]
    if header_indexes:
        headers = []
        for column in range(width):
            values = list(dict.fromkeys(
                matrix[row][column].strip()
                for row in header_indexes
                if matrix[row][column].strip()
            ))
            headers.append(" / ".join(values) or f"Column {column + 1}")
    else:
        headers = [f"Column {column + 1}" for column in range(width)]
    data_rows = [row for index, row in enumerate(matrix) if index not in header_rows]
    data_rows = [row for row in data_rows if any(cell.strip() for cell in row)]
    return headers, data_rows


def _render_table_rows(
    passage: dict[str, Any],
    captions: dict[str, str],
) -> tuple[list[tuple[str, dict[str, Any]]], str] | None:
    infons = passage.get("infons", {}) or {}
    table_key = _clean(infons.get("id") or infons.get("file"))
    caption = captions.get(table_key, "")
    grid = _table_grid(str(infons.get("xml") or ""))
    structure_source = "table_xml" if grid else "bioc_text_fallback"
    if grid:
        headers, rows = grid
    else:
        raw_cells = [_clean(value) for value in str(passage.get("text") or "").split("\t")]
        raw_cells = [value for value in raw_cells if value]
        if not raw_cells:
            return None
        headers = [f"Column {index + 1}" for index in range(len(raw_cells))]
        rows = [raw_cells]
    result: list[tuple[str, dict[str, Any]]] = []
    for row_index, row in enumerate(rows):
        fields = [
            f"{headers[index]}: {value.strip()}"
            for index, value in enumerate(row)
            if index < len(headers) and value.strip()
        ]
        rendered = " | ".join(fields) if fields else " | ".join(value for value in row if value.strip())
        if not rendered:
            continue
        metadata = {
            "table_id": table_key or None,
            "caption": caption or None,
            "column_headers": headers,
            "row_index": row_index,
            "row_count": len(rows),
            "structure_source": structure_source,
        }
        result.append((rendered, metadata))
    return (result, structure_source) if result else None


def sections_from_bioc_payload(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    document_id: str,
) -> list[DocumentSection]:
    """Preserve heading-scoped sections and typed BioC source blocks for schema-v5."""
    collection = _bioc_collection(payload)
    documents = collection.get("documents", [])
    if not documents or not isinstance(documents[0], dict):
        raise ValueError("BioC collection contains no source document")
    passages = documents[0].get("passages", [])

    caption_by_key: dict[str, list[str]] = {}
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        infons = passage.get("infons", {}) or {}
        kind = _passage_type(passage)
        if kind not in {"table_caption", "table_title_caption"}:
            continue
        key = _clean(infons.get("id") or infons.get("file"))
        text = _clean(passage.get("text"))
        if key and text:
            caption_by_key.setdefault(key, []).append(text)
    captions = {key: " | ".join(dict.fromkeys(values)) for key, values in caption_by_key.items()}

    drafts: list[tuple[list[str], list[tuple[str, str, dict[str, Any]]]]] = []
    active_path: list[str] = []
    active_blocks: list[tuple[str, str, dict[str, Any]]] = []

    def flush() -> None:
        nonlocal active_blocks
        if active_blocks:
            drafts.append((active_path.copy() or ["Full text"], active_blocks))
            active_blocks = []

    def set_path(path: list[str]) -> None:
        nonlocal active_path
        if path != active_path:
            flush()
            active_path = path

    for passage in passages:
        if not isinstance(passage, dict):
            continue
        infons = passage.get("infons", {}) or {}
        kind = _passage_type(passage)
        text = _clean(passage.get("text"))
        section_type = _clean(infons.get("section_type")).replace("_", " ").title()

        abstract_heading = re.fullmatch(r"abstract_title_([1-9][0-9]*)", kind)
        section_heading = re.fullmatch(r"title_([1-9][0-9]*)", kind)
        if abstract_heading and text:
            set_path(["Abstract", text])
            continue
        if section_heading and text:
            level = int(section_heading.group(1))
            base = active_path
            if not base and section_type and section_type.casefold() not in {"abstract", "front"}:
                base = [section_type]
            parent = base[: max(0, level - 1)] if base else []
            set_path([*parent, text] if parent else [text])
            continue
        if kind in {"title", "article_title", "table_title_caption", "table_caption"}:
            # Article title is metadata; table captions are attached to each row below.
            if kind in {"table_title_caption", "table_caption"}:
                continue
            continue

        if kind == "abstract":
            if not active_path or active_path[0] != "Abstract":
                set_path(["Abstract"])
        elif kind in {"paragraph", "list", "list_item", "table", "table_footnote", "fig_caption", "caption", "equation"}:
            if not active_path:
                set_path(["Abstract"] if section_type.casefold() == "abstract" else [section_type or "Full text"])
            elif active_path[0] == "Abstract" and section_type and section_type.casefold() not in {"abstract", "front"}:
                set_path([section_type])

        if kind == "table":
            table = _render_table_rows(passage, captions)
            if table:
                for row_text, metadata in table[0]:
                    active_blocks.append(("table_row", row_text, metadata))
            continue
        if kind in {"table_caption", "table_title_caption"}:
            continue
        if not text:
            continue
        block_type: str | None = {
            "abstract": "abstract",
            "paragraph": "paragraph",
            "list": "list",
            "list_item": "list_item",
            "table_footnote": "caption",
            "fig_caption": "caption",
            "caption": "caption",
            "equation": "equation",
        }.get(kind)
        if block_type:
            active_blocks.append((block_type, text, {}))

    flush()
    sections: list[DocumentSection] = []
    for ordinal, (heading_path, block_drafts) in enumerate(drafts):
        heading_key = " > ".join(heading_path)
        section_id = stable_id("section", document_id, str(ordinal), heading_key)
        text_parts: list[str] = []
        blocks: list[SectionBlock] = []
        cursor = 0
        for block_ordinal, (block_type, block_text, metadata) in enumerate(block_drafts):
            if text_parts:
                text_parts.append("\n\n")
                cursor += 2
            start = cursor
            text_parts.append(block_text)
            cursor += len(block_text)
            blocks.append(
                SectionBlock(
                    block_id=stable_id("block", section_id, str(block_ordinal), block_type, block_text),
                    block_type=block_type,
                    start_char=start,
                    end_char=cursor,
                    text=block_text,
                    metadata=metadata,
                )
            )
        section_text = "".join(text_parts).strip()
        if section_text:
            sections.append(
                DocumentSection(
                    section_id=section_id,
                    document_id=document_id,
                    heading_path=heading_path,
                    text=section_text,
                    blocks=blocks,
                    ordinal=len(sections),
                )
            )
    if not sections:
        raise ValueError("BioC document contains no parseable abstract/body sections")
    return sections


def sections_from_parent_sections(
    document_id: str,
    parent_sections: list[ParentSection],
) -> list[DocumentSection]:
    """Adapt text-based guideline parsers to the section-local schema-v5 contract."""
    sections: list[DocumentSection] = []
    for ordinal, parent in enumerate(sorted(parent_sections, key=lambda item: item.ordinal)):
        if parent.document_id != document_id:
            raise ValueError(f"parent section {parent.section_id} belongs to another document")
        parts = [part.strip() for part in re.split(r"\n\s*\n", parent.text) if part.strip()]
        if not parts:
            continue
        section_id = stable_id("section", document_id, str(ordinal), " > ".join(parent.heading_path))
        text_parts: list[str] = []
        blocks: list[SectionBlock] = []
        cursor = 0
        for block_ordinal, part in enumerate(parts):
            if text_parts:
                text_parts.append("\n\n")
                cursor += 2
            start = cursor
            text_parts.append(part)
            cursor += len(part)
            blocks.append(
                SectionBlock(
                    block_id=stable_id("block", section_id, str(block_ordinal), "paragraph", part),
                    block_type="paragraph",
                    start_char=start,
                    end_char=cursor,
                    text=part,
                )
            )
        section_text = "".join(text_parts)
        sections.append(
            DocumentSection(
                section_id=section_id,
                document_id=document_id,
                heading_path=parent.heading_path,
                text=section_text,
                blocks=blocks,
                ordinal=len(sections),
            )
        )
    return sections


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _passage_type(passage: dict[str, Any]) -> str:
    return str(passage.get("infons", {}).get("type", "")).casefold()


def _section_heading_path(passage: dict[str, Any], current: list[str]) -> list[str]:
    infons = passage.get("infons", {})
    kind = _passage_type(passage)
    level_match = re.fullmatch(r"title_([1-9][0-9]*)", kind)
    if level_match or kind == "title":
        level = int(level_match.group(1)) if level_match else 1
        title = _clean(passage.get("text"))
        if not title:
            return current
        parent = current[: max(0, level - 1)]
        return [*parent, title]
    return current


def parse_bioc_json(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    pmcid: str,
    source_uri: str,
    license_uri: str | None = None,
    raw_path: str,
    metadata: dict[str, Any] | None = None,
    specialty: list[str] | None = None,
) -> tuple[MedicalDocument, list[ParentSection]]:
    """Parse NCBI's PMC OA BioC JSON representation, preserving section scope."""
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    elif isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, list):
        if not payload or not isinstance(payload[0], dict):
            raise ValueError("BioC payload has no collection")
        collection = payload[0]
    else:
        collection = payload
    documents = collection.get("documents", [])
    if not documents or not isinstance(documents[0], dict):
        raise ValueError(f"BioC response for {pmcid} contains no document (not available in PMC OA)")
    source_doc = documents[0]
    passages = source_doc.get("passages", [])
    doc_infons = source_doc.get("infons", {}) or {}
    metadata = metadata or {}

    license_candidates = [_clean(doc_infons.get("license"))]
    license_candidates.extend(
        _clean((passage.get("infons", {}) or {}).get("license")) for passage in passages
    )
    license_candidates = [candidate for candidate in license_candidates if candidate]
    if not license_candidates:
        raise ValueError(f"BioC response for {pmcid} has no license metadata")
    # The document-level field identifies the article license. Passage-level
    # notices can describe separate datasets (for example CC0 data inside a
    # CC BY article), so only attach a detailed notice from the same family.
    license_text = license_candidates[0]
    primary_license = normalize_license(license_text)
    if primary_license is None:
        for candidate in license_candidates[1:]:
            primary_license = normalize_license(candidate)
            if primary_license:
                license_text = candidate
                break
    if primary_license is None:
        raise ValueError(f"BioC response for {pmcid} has unrecognized license metadata")
    primary_family = " ".join(primary_license.identifier.split()[:2])
    detail_candidates = []
    for candidate in license_candidates[1:]:
        candidate_license = normalize_license(candidate)
        if candidate_license and " ".join(candidate_license.identifier.split()[:2]) == primary_family:
            detail_candidates.append(candidate)
    license_notice = max(detail_candidates, key=len) if detail_candidates else None
    if not license_uri:
        license_uri = next(
            (
                match.group(0).rstrip(".)")
                for candidate in ([license_text] + detail_candidates)
                for match in re.finditer(r"https?://[^\s)\]}>,;]+", candidate)
                if "creativecommons.org" in match.group(0)
            ),
            None,
        )

    title = _clean(metadata.get("title"))
    if not title:
        # BioC's `front`/TITLE passage can be a journal short-title rather than
        # the article title, so use it only when a source labels it as `title`.
        title = next((_clean(p.get("text")) for p in passages if _passage_type(p) == "title" and _clean(p.get("text"))), "")
    if not title:
        raise ValueError(f"No article title metadata for {pmcid}")

    document_id = f"PMC-{pmcid.upper().removeprefix('PMC')}"
    doi = str(metadata.get("doi") or "").strip()
    pmid = str(metadata.get("pmid") or "").strip()
    version = f"doi:{doi}" if doi else (f"pmid:{pmid}" if pmid else None)
    document = MedicalDocument(
        document_id=document_id,
        source_org="PubMed Central Open Access Subset",
        source_type="article",
        title=title,
        authors=[_clean(author) for author in metadata.get("authors", []) if _clean(author)],
        publication_date=_clean(metadata.get("publication_date")) or None,
        version=version,
        specialty=specialty or [],
        source_uri=source_uri,
        license=license_text,
        license_notice=license_notice,
        license_uri=license_uri,
        raw_path=raw_path,
    )

    # Preserve source order and heading paths as paragraph metadata. The
    # document-level schema does not persist these heading groups as parents.
    known_content_types = {
        "abstract", "paragraph", "table", "table_caption", "table_footnote",
        "list", "list_item", "fig_caption", "caption", "equation",
    }
    drafts: list[tuple[list[str], list[str]]] = []
    active_path: list[str] = []
    active_text: list[str] = []

    def flush() -> None:
        nonlocal active_text
        text = "\n\n".join(item for item in active_text if item).strip()
        if text:
            drafts.append((active_path.copy() or ["Full text"], [text]))
        active_text = []

    for passage in passages:
        if not isinstance(passage, dict):
            continue
        kind = _passage_type(passage)
        text = _clean(passage.get("text"))
        if kind.startswith("title_") or kind == "title":
            next_path = _section_heading_path(passage, active_path)
            if next_path != active_path:
                flush()
                active_path = next_path
            continue
        if kind not in known_content_types or not text:
            continue
        if kind == "abstract":
            if active_text:
                flush()
            if not active_path:
                active_path = ["Abstract"]
            elif active_path != ["Abstract"]:
                flush()
                active_path = ["Abstract"]
        elif not active_path:
            section_type = _clean((passage.get("infons", {}) or {}).get("section_type"))
            active_path = [section_type.replace("_", " ").title()] if section_type else ["Full text"]
        active_text.append(text)
    flush()

    sections: list[ParentSection] = []
    for ordinal, (path, text_parts) in enumerate(drafts):
        section_text = "\n\n".join(text_parts).strip()
        if not section_text:
            continue
        heading_key = " > ".join(path)
        sections.append(
            ParentSection(
                section_id=stable_id("sec", document_id, str(ordinal), heading_key),
                document_id=document_id,
                heading_path=path,
                text=section_text,
                ordinal=len(sections),
            )
        )
    if not sections:
        raise ValueError(f"BioC response for {pmcid} contains no parseable abstract/body passages")
    return document, sections


def paragraphs_from_sections(
    document_id: str,
    sections: list[ParentSection],
) -> list[DocumentParagraph]:
    """Flatten legacy parser sections into document-ordered paragraph records.

    Heading paths are retained as paragraph metadata only, not as parent
    retrieval units in the document-level schemas.
    """
    paragraphs: list[DocumentParagraph] = []
    for section in sections:
        for block in re.split(r"\n\s*\n", section.text):
            text = block.strip()
            if not text:
                continue
            ordinal = len(paragraphs)
            paragraph_type = "abstract" if section.heading_path == ["Abstract"] else "paragraph"
            paragraphs.append(
                DocumentParagraph(
                    paragraph_id=stable_id("para", document_id, str(ordinal), text),
                    document_id=document_id,
                    ordinal_in_document=ordinal,
                    heading_path=section.heading_path,
                    paragraph_type=paragraph_type,
                    text=text,
                )
            )
    return paragraphs


def parse_bioc_document(
    payload: bytes | str | dict[str, Any] | list[Any],
    *,
    pmcid: str,
    source_uri: str,
    license_uri: str | None = None,
    raw_path: str,
    metadata: dict[str, Any] | None = None,
    specialty: list[str] | None = None,
) -> tuple[MedicalDocument, list[DocumentParagraph]]:
    """Parse PMC OA into document metadata plus flat ordered paragraphs."""
    document, sections = parse_bioc_json(
        payload,
        pmcid=pmcid,
        source_uri=source_uri,
        license_uri=license_uri,
        raw_path=raw_path,
        metadata=metadata,
        specialty=specialty,
    )
    paragraphs = paragraphs_from_sections(document.document_id, sections)
    abstract = "\n\n".join(p.text for p in paragraphs if p.paragraph_type == "abstract") or None
    document = document.model_copy(update={"abstract": abstract})
    return document, paragraphs
