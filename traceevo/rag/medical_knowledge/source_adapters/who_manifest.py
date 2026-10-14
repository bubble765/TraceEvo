"""Explicit-manifest adapter for locally held WHO guideline files.

No WHO-wide crawler or implicit downloader is provided. Every imported item
must be listed with source and reuse-license metadata by the operator.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from traceevo.rag.common.schemas import MedicalDocument, ParentSection
from traceevo.rag.medical_knowledge.licenses import LicensePolicy
from traceevo.rag.medical_knowledge.parser import stable_id
from traceevo.rag.medical_knowledge.storage import store_raw


class WHOManifestError(ValueError):
    pass


class WHOManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_org: Literal["WHO"] = "WHO"
    source_type: Literal["guideline"] = "guideline"
    title: str = Field(min_length=1)
    raw_path: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    license: str = Field(min_length=1)
    license_uri: str | None = None
    publication_date: str | None = None
    version: str | None = None
    specialty: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class WHOManifestSource:
    document: MedicalDocument
    sections: list[ParentSection]


class WHOManifestAdapter:
    def __init__(self, data_dir: str | Path, *, license_policy: LicensePolicy | None = None):
        self.data_dir = Path(data_dir)
        self.license_policy = license_policy or LicensePolicy()

    def ingest_manifest(self, manifest_path: str | Path) -> list[WHOManifestSource]:
        manifest_path = Path(manifest_path).resolve()
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WHOManifestError(f"cannot read WHO manifest {manifest_path}: {exc}") from exc
        if isinstance(payload, dict):
            entries_raw = payload.get("documents")
        else:
            entries_raw = payload
        if not isinstance(entries_raw, list):
            raise WHOManifestError("manifest must be a JSON array or an object with a documents array")
        results = []
        seen_ids: set[str] = set()
        for raw_entry in entries_raw:
            try:
                entry = WHOManifestEntry.model_validate(raw_entry)
            except Exception as exc:
                raise WHOManifestError(f"invalid WHO manifest entry: {exc}") from exc
            if entry.document_id in seen_ids:
                raise WHOManifestError(f"duplicate WHO document_id: {entry.document_id}")
            seen_ids.add(entry.document_id)
            results.append(self._ingest_entry(entry, manifest_path.parent))
        return results

    def _ingest_entry(self, entry: WHOManifestEntry, manifest_dir: Path) -> WHOManifestSource:
        parsed_uri = urlparse(entry.source_uri)
        host = (parsed_uri.hostname or "").casefold()
        if parsed_uri.scheme != "https" or not (host == "who.int" or host.endswith(".who.int")):
            raise WHOManifestError("WHO source_uri must be an HTTPS URL hosted by who.int")
        license_info = self.license_policy.require_ingestible(entry.license, entry.license_uri)
        source_path = Path(entry.raw_path)
        if not source_path.is_absolute():
            source_path = manifest_dir / source_path
        try:
            raw_bytes = source_path.read_bytes()
        except OSError as exc:
            raise WHOManifestError(f"cannot read local raw source {source_path}: {exc}") from exc
        suffix = source_path.suffix.lower()
        if suffix not in {".pdf", ".html", ".htm", ".xml", ".txt", ".md"}:
            raise WHOManifestError(f"unsupported WHO source format {suffix or '(no extension)'}")

        relative_raw = store_raw(self.data_dir, "who", raw_bytes, suffix)
        document = MedicalDocument(
            document_id=entry.document_id,
            source_org="WHO",
            source_type="guideline",
            title=entry.title,
            publication_date=entry.publication_date,
            version=entry.version,
            specialty=entry.specialty,
            source_uri=entry.source_uri,
            license=entry.license,
            license_id=license_info.identifier,
            license_uri=license_info.uri,
            raw_path=relative_raw,
        )
        sections = _parse_local_document(raw_bytes, suffix, document)
        return WHOManifestSource(document=document, sections=sections)


class _HTMLBlocks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple[str, int, str]] = []
        self.heading_tag: str | None = None
        self.heading_parts: list[str] = []
        self.block_tag: str | None = None
        self.block_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if re.fullmatch(r"h[1-6]", tag):
            self._flush_block()
            self.heading_tag = tag
            self.heading_parts = []
        elif tag in {"p", "li", "caption", "figcaption", "td", "th"} and self.block_tag is None:
            self.block_tag = tag
            self.block_parts = []
        elif tag == "br":
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.heading_tag == tag:
            text = _tidy("".join(self.heading_parts))
            if text:
                self.events.append(("heading", int(tag[1]), text))
            self.heading_tag = None
            self.heading_parts = []
        elif self.block_tag == tag:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        self._append(data)

    def _append(self, value: str) -> None:
        if self.heading_tag:
            self.heading_parts.append(value)
        elif self.block_tag:
            self.block_parts.append(value)

    def _flush_block(self) -> None:
        text = _tidy(" ".join(self.block_parts))
        if text:
            self.events.append(("block", 0, text))
        self.block_tag = None
        self.block_parts = []


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _xml_events(raw: bytes) -> list[tuple[str, int, str]]:
    root = ET.fromstring(raw)
    events: list[tuple[str, int, str]] = []

    def walk(node: ET.Element, depth: int = 0) -> None:
        tag = _local(node.tag)
        if tag in {"sec", "section"}:
            title_node = next((child for child in list(node) if _local(child.tag) in {"title", "heading"}), None)
            if title_node is not None:
                title = _tidy(" ".join(title_node.itertext()))
                if title:
                    events.append(("heading", min(depth + 1, 6), title))
            for child in list(node):
                if child is not title_node:
                    walk(child, depth + (1 if title_node is not None else 0))
            return
        if tag in {"p", "para", "list-item", "caption", "table", "fig", "abstract"}:
            text = _tidy(" ".join(node.itertext()))
            if text:
                events.append(("block", 0, text))
            return
        for child in list(node):
            walk(child, depth)

    walk(root)
    return events


def _text_events(text: str) -> list[tuple[str, int, str]]:
    events: list[tuple[str, int, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        joined = _tidy(" ".join(paragraph))
        if joined:
            events.append(("block", 0, joined))
        paragraph.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        markdown_heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        all_caps_heading = len(stripped) <= 100 and len(stripped.split()) >= 2 and stripped.upper() == stripped and re.search(r"[A-Z]", stripped)
        if markdown_heading or all_caps_heading:
            flush()
            level = len(markdown_heading.group(1)) if markdown_heading else 1
            heading = markdown_heading.group(2) if markdown_heading else stripped
            events.append(("heading", level, _tidy(heading)))
        else:
            paragraph.append(stripped)
    flush()
    return events


def _parse_local_document(raw: bytes, suffix: str, document: MedicalDocument) -> list[ParentSection]:
    if suffix in {".html", ".htm"}:
        parser = _HTMLBlocks()
        parser.feed(raw.decode("utf-8", "replace"))
        parser.close()
        parser._flush_block()
        events = parser.events
    elif suffix == ".xml":
        events = _xml_events(raw)
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise WHOManifestError("PDF parsing requires the optional pypdf package") from exc
        reader = PdfReader(BytesIO(raw))
        page_text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        events = _text_events(page_text)
    else:
        events = _text_events(raw.decode("utf-8", "replace"))
    sections = _events_to_sections(events, document.document_id)
    if not sections:
        raise WHOManifestError(f"no text sections could be extracted from {document.raw_path}")
    return sections


def _events_to_sections(events: list[tuple[str, int, str]], document_id: str) -> list[ParentSection]:
    drafts: list[tuple[list[str], str]] = []
    path: list[str] = []
    body: list[str] = []

    def flush() -> None:
        nonlocal body
        text = "\n\n".join(body).strip()
        if text:
            drafts.append((path.copy() or ["Guideline text"], text))
        body = []

    for kind, level, value in events:
        if kind == "heading":
            flush()
            path = [*path[: max(0, level - 1)], value]
        elif value:
            body.append(value)
    flush()
    result = []
    for ordinal, (heading_path, text) in enumerate(drafts):
        heading_key = " > ".join(heading_path)
        result.append(
            ParentSection(
                section_id=stable_id("sec", document_id, str(ordinal), heading_key),
                document_id=document_id,
                heading_path=heading_path,
                text=text,
                ordinal=ordinal,
            )
        )
    return result


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
