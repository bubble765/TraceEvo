from __future__ import annotations

import json
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from traceevo.rag.common.schemas import DocumentParagraph, DocumentSection, MedicalDocument, ParentSection, SectionBlock
from traceevo.rag.medical_knowledge.licenses import IngestionLicenseError, LicensePolicy, normalize_license
from traceevo.rag.medical_knowledge.parser import parse_bioc_document, parse_bioc_json, sections_from_bioc_payload
from traceevo.rag.medical_knowledge.source_adapters.pmc import PMCOpenAccessAdapter
from traceevo.rag.medical_knowledge.source_adapters import pmc as pmc_module
from traceevo.rag.medical_knowledge.source_adapters.who_manifest import WHOManifestAdapter
from traceevo.rag.medical_knowledge.splitter import (
    DocumentChunkConfig,
    DocumentChunkSplitter,
    MedicalEntityLexicon,
    ParentChildSplitter,
    RegexTokenCounter,
    SectionChunkConfig,
    SectionChunkSplitter,
    SplitterConfig,
    sentence_spans,
)
from traceevo.rag.medical_knowledge.storage import (
    ImmutableArtifactConflict,
    write_document_level_normalized,
    write_normalized,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_license_policy_requires_known_derivative_rights_and_nc_opt_in():
    assert normalize_license("Creative Commons Attribution License (https://creativecommons.org/licenses/by/4.0/)").identifier == "CC BY 4.0"
    assert normalize_license("Creative Commons Attribution License, which permits unrestricted reuse.").identifier == "CC BY"
    assert normalize_license("CC BY; article is CC BY 4.0 but supporting data are CC0-1.0").identifier == "CC BY"
    assert normalize_license("CC BY-NC-SA 3.0 IGO").identifier == "CC BY-NC-SA 3.0 IGO"
    with pytest.raises(IngestionLicenseError, match="missing or unrecognized"):
        LicensePolicy().require_ingestible(None)
    with pytest.raises(IngestionLicenseError, match="noncommercial"):
        LicensePolicy().require_ingestible("CC BY-NC")
    with pytest.raises(IngestionLicenseError, match="does not permit derivative"):
        LicensePolicy(allow_noncommercial=True).require_ingestible("CC BY-ND 4.0")
    assert LicensePolicy(allow_noncommercial=True).require_ingestible("CC BY-NC-SA 3.0 IGO").identifier == "CC BY-NC-SA 3.0 IGO"


def test_bioc_parser_preserves_heading_hierarchy_and_skips_front_matter():
    payload = json.loads((FIXTURES / "pmc_bioc_sample.json").read_text())
    document, sections = parse_bioc_json(
        payload,
        pmcid="PMC1234567",
        source_uri="https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/",
        raw_path="pending",
        metadata={"title": "Evidence in diagnosis", "publication_date": "2025", "doi": "10.1000/test"},
    )
    assert document.document_id == "PMC-1234567"
    assert document.title == "Evidence in diagnosis"
    assert document.license == "CC BY"
    assert document.license_notice and "creativecommons.org/licenses/by/4.0" in document.license_notice
    assert "CC0" not in document.license_notice
    assert [section.heading_path for section in sections] == [
        ["Abstract"], ["Diagnosis"], ["Diagnosis", "Imaging"], ["Management"]
    ]
    assert "Example Journal" not in " ".join(section.text for section in sections)
    assert "reference that should not" not in " ".join(section.text for section in sections)
    assert all(section.document_id == document.document_id for section in sections)

    flat_document, paragraphs = parse_bioc_document(
        payload,
        pmcid="PMC1234567",
        source_uri="https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/",
        raw_path="pending",
        metadata={"title": "Evidence in diagnosis"},
    )
    assert flat_document.abstract == paragraphs[0].text
    assert [paragraph.ordinal_in_document for paragraph in paragraphs] == list(range(len(paragraphs)))
    assert paragraphs[0].paragraph_type == "abstract"
    assert paragraphs[1].heading_path == ["Diagnosis"]
    assert "reference that should not" not in " ".join(paragraph.text for paragraph in paragraphs)


class _FakePMCAdapter(PMCOpenAccessAdapter):
    def __init__(self, data_dir: Path, bioc: bytes):
        super().__init__(data_dir)
        self.bioc = bioc

    def _get(self, url: str) -> bytes:
        if "BioC_json" in url:
            return self.bioc
        return json.dumps({
            "result": {
                "uids": ["1234567"],
                "1234567": {
                    "title": "Evidence in diagnosis",
                    "pubdate": "2025",
                    "authors": [{"name": "A. Researcher"}],
                    "articleids": [
                        {"idtype": "pmcid", "value": "PMC1234567"},
                        {"idtype": "doi", "value": "10.1000/test"},
                    ],
                },
            }
        }).encode()


def test_pmc_adapter_checks_metadata_and_writes_content_addressed_raw(tmp_path: Path):
    source = _FakePMCAdapter(tmp_path, (FIXTURES / "pmc_bioc_sample.json").read_bytes()).ingest("pmc1234567")
    assert source.document.license_id == "CC BY 4.0"
    assert source.document.license_uri == "https://creativecommons.org/licenses/by/4.0/"
    assert source.document.authors == ["A. Researcher"]
    assert source.document.raw_path.startswith("raw/pmc/")
    assert (tmp_path / source.document.raw_path).exists()
    assert source.paragraphs
    assert source.document.abstract


def test_pmc_client_retries_rate_limit_with_bounded_backoff(monkeypatch, tmp_path: Path):
    adapter = PMCOpenAccessAdapter(tmp_path, timeout_seconds=1)
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            headers = Message()
            headers["Retry-After"] = "0"
            raise pmc_module.HTTPError(request.full_url, 429, "rate limited", headers, None)
        return BytesIO(b"retried")

    monkeypatch.setattr(pmc_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(pmc_module.time, "sleep", lambda _: None)
    assert adapter._get("https://example.invalid") == b"retried"
    assert calls == 2


def test_splitter_respects_parent_boundary_budget_and_injected_counter():
    sentences = [f"Sentence {i:02d} alpha beta gamma delta." for i in range(1, 13)]
    first = ParentSection(
        section_id="sec-a", document_id="doc", heading_path=["Diagnosis"],
        text="\n\n".join(sentences), ordinal=0,
    )
    second = ParentSection(
        section_id="sec-b", document_id="doc", heading_path=["Treatment"],
        text="Separate section omega sigma.", ordinal=1,
    )
    splitter = ParentChildSplitter(SplitterConfig(target_tokens=18, max_tokens=28, overlap_tokens=6, min_tokens=0))
    chunks = splitter.split("doc", "https://example.org/source", [first, second])
    assert len(chunks) > 2
    assert all(chunk.token_count <= 28 for chunk in chunks)
    assert all(chunk.tokenizer_name == "unicode_lexical_estimate_v1" for chunk in chunks)
    assert {chunk.parent_section_id for chunk in chunks if chunk.parent_section_id == "sec-b"} == {"sec-b"}
    first_texts = [chunk.text for chunk in chunks if chunk.parent_section_id == "sec-a"]
    assert all(sentence in "\n".join(first_texts) for sentence in sentences)
    assert all("Separate section" not in chunk.text for chunk in chunks if chunk.parent_section_id == "sec-a")
    assert all("Sentence" not in chunk.text for chunk in chunks if chunk.parent_section_id == "sec-b")
    with pytest.raises(ValueError, match="belongs to"):
        splitter.split("different-doc", "https://example.org/source", [first])


def test_document_chunk_splitter_keeps_flat_document_order_without_parent_entities(tmp_path: Path):
    document = MedicalDocument(
        document_id="PMC-FLAT",
        source_org="PMC OA",
        source_type="article",
        title="Flat document test",
        source_uri="https://example.org/flat",
        license="CC BY 4.0",
        raw_path="raw/flat.json",
    )
    paragraphs = [
        DocumentParagraph(
            paragraph_id=f"p-{ordinal}", document_id=document.document_id, ordinal_in_document=ordinal,
            heading_path=heading, text=text,
        )
        for ordinal, heading, text in [
            (0, ["Abstract"], "Abstract evidence on diagnosis."),
            (1, ["Findings"], "First body paragraph with useful detail."),
            (2, ["Findings", "Imaging"], "Second body paragraph describes imaging."),
        ]
    ]
    splitter = DocumentChunkSplitter(
        DocumentChunkConfig(target_tokens=100, max_tokens=120, min_tokens=0)
    )
    chunks = splitter.split(document, paragraphs)
    assert len(chunks) == 1
    assert chunks[0].document_id == document.document_id
    assert chunks[0].paragraph_ids == ["p-0", "p-1", "p-2"]
    assert chunks[0].heading_paths == [["Abstract"], ["Findings"], ["Findings", "Imaging"]]
    assert "Document: Flat document test" in chunks[0].embedding_text
    assert "Findings > Imaging" in chunks[0].embedding_text

    target = write_document_level_normalized(tmp_path, document, paragraphs, chunks)
    assert target.parts[-2] == "schema-v4"
    assert {path.name for path in target.iterdir()} == {"document.json", "paragraphs.jsonl", "child_chunks.jsonl"}
    assert chunks[0].paragraph_spans[0].start_char == 0
    assert chunks[0].paragraph_spans[-1].end_char == len(paragraphs[-1].text)


def test_schema_v5_bioc_parser_keeps_sections_tables_rows_and_column_labels():
    payload = [{
        "documents": [{"passages": [
            {"infons": {"type": "title_1"}, "text": "Clinical findings"},
            {"infons": {"type": "paragraph", "section_type": "RESULTS"},
             "text": "Dr. Smith found swelling. Imaging confirmed the finding."},
            {"infons": {"type": "table_title_caption", "id": "T1"}, "text": "Examination results"},
            {"infons": {
                "type": "table", "id": "T1",
                "xml": '<table><thead><tr><th>Finding</th><th>Result</th></tr></thead>'
                       '<tbody><tr><td>Swelling</td><td>Present</td></tr>'
                       '<tr><td>Radiograph</td><td>No fracture</td></tr></tbody></table>',
            }, "text": "Finding\tResult\tSwelling\tPresent\tRadiograph\tNo fracture"},
        ]}]
    }]

    sections = sections_from_bioc_payload(payload, document_id="PMC-V5")
    assert len(sections) == 1
    section = sections[0]
    assert section.heading_path == ["Clinical findings"]
    assert section.text[section.blocks[0].start_char:section.blocks[0].end_char] == section.blocks[0].text
    table_rows = [block for block in section.blocks if block.block_type == "table_row"]
    assert len(table_rows) == 2
    assert table_rows[0].metadata["caption"] == "Examination results"
    assert table_rows[0].metadata["column_headers"] == ["Finding", "Result"]
    assert table_rows[0].text == "Finding: Swelling | Result: Present"
    assert table_rows[1].text == "Finding: Radiograph | Result: No fracture"


def test_section_splitter_extends_to_sentence_and_falls_back_at_word_boundary():
    document = MedicalDocument(
        document_id="PMC-V5-SPLIT", source_org="PMC OA", source_type="article", title="Section offsets",
        source_uri="https://example.org/v5", license="CC BY 4.0", raw_path="raw/v5.json",
    )
    text = "one two three. four five six seven eight."
    block = SectionBlock(
        block_id="block-v5", block_type="paragraph", start_char=0, end_char=len(text), text=text,
    )
    section = DocumentSection(
        section_id="section-v5", document_id=document.document_id, heading_path=["Results"],
        text=text, blocks=[block], ordinal=0,
    )
    chunks = SectionChunkSplitter(
        SectionChunkConfig(target_tokens=3, max_tokens=7, tail_merge_tokens=3),
        token_counter=RegexTokenCounter(),
    ).split(document, [section])
    assert chunks[0].text == "one two three."
    assert chunks[0].token_count == 4
    assert chunks[0].section_span.start_char == 0
    assert section.text[chunks[0].section_span.start_char:chunks[0].section_span.end_char] == chunks[0].text

    fallback_text = "one two three four five six seven eight nine ten eleven twelve"
    fallback_block = block.model_copy(update={
        "block_id": "block-fallback", "end_char": len(fallback_text), "text": fallback_text,
    })
    fallback_section = section.model_copy(update={"text": fallback_text, "blocks": [fallback_block]})
    fallback_chunks = SectionChunkSplitter(
        SectionChunkConfig(target_tokens=3, max_tokens=7, tail_merge_tokens=3),
        token_counter=RegexTokenCounter(),
    ).split(document, [fallback_section])
    assert fallback_chunks[0].token_count <= 7
    assert any("hard-limit fallback" in note for note in fallback_chunks[0].boundary_notes)
    assert fallback_text[fallback_chunks[0].section_span.start_char:fallback_chunks[0].section_span.end_char] == fallback_chunks[0].text


def test_sentence_span_detection_ignores_common_abbreviations_and_decimals():
    text = "Dr. Smith measured 3.5 mm. The result was stable."
    spans = sentence_spans(text)
    assert [text[start:end] for start, end in spans] == [
        "Dr. Smith measured 3.5 mm.", "The result was stable.",
    ]


def test_document_chunker_snaps_english_medical_entity_to_one_chunk():
    document = MedicalDocument(
        document_id="PMC-ENTITY-EN", source_org="PMC OA", source_type="article", title="Entity boundary",
        source_uri="https://example.org/entity", license="CC BY 4.0", raw_path="raw/entity.json",
    )
    text = "Patient shows bruising and acute ankle fracture after a fall requiring evaluation today."
    paragraph = DocumentParagraph(
        paragraph_id="p-en", document_id=document.document_id, ordinal_in_document=0, text=text,
    )
    chunks = DocumentChunkSplitter(
        DocumentChunkConfig(target_tokens=5, max_tokens=9, min_tokens=0),
        entity_lexicon=MedicalEntityLexicon(["acute ankle fracture"]),
    ).split(document, [paragraph])
    containing = [chunk for chunk in chunks if "acute ankle fracture" in chunk.text]
    assert len(containing) == 1
    assert all(not ("acute" in chunk.text and "ankle fracture" not in chunk.text) for chunk in chunks)
    span = containing[0].paragraph_spans[0]
    assert text[span.start_char:span.end_char] == containing[0].text
    assert all(chunk.token_count <= 9 for chunk in chunks)


def test_document_chunker_handles_unspaced_chinese_and_protects_entity():
    document = MedicalDocument(
        document_id="PMC-ENTITY-ZH", source_org="PMC OA", source_type="article", title="中文实体边界",
        source_uri="https://example.org/entity-zh", license="CC BY 4.0", raw_path="raw/entity-zh.json",
    )
    text = "患者出现踝关节骨折并伴明显肿胀，建议影像检查确认。"
    paragraph = DocumentParagraph(
        paragraph_id="p-zh", document_id=document.document_id, ordinal_in_document=0, text=text,
    )
    chunks = DocumentChunkSplitter(
        DocumentChunkConfig(target_tokens=7, max_tokens=11, min_tokens=0),
        entity_lexicon=MedicalEntityLexicon(["踝关节骨折"]),
    ).split(document, [paragraph])
    containing = [chunk for chunk in chunks if "踝关节骨折" in chunk.text]
    assert len(containing) == 1
    assert all(chunk.token_count <= 11 for chunk in chunks)
    source_ranges = sorted(
        (span.start_char, span.end_char)
        for chunk in chunks
        for span in chunk.paragraph_spans
    )
    assert all(left_end <= right_start for (_, left_end), (right_start, _) in zip(source_ranges, source_ranges[1:]))
    assert "".join(text[start:end] for start, end in source_ranges) == text


def test_entity_longer_than_hard_chunk_limit_stays_intact_and_is_annotated():
    document = MedicalDocument(
        document_id="PMC-LONG-ENTITY", source_org="PMC OA", source_type="article", title="Long entity",
        source_uri="https://example.org/long-entity", license="CC BY 4.0", raw_path="raw/long-entity.json",
    )
    text = "before very rare special compound condition after."
    paragraph = DocumentParagraph(
        paragraph_id="p-long-entity", document_id=document.document_id, ordinal_in_document=0, text=text,
    )
    chunks = DocumentChunkSplitter(
        DocumentChunkConfig(target_tokens=2, max_tokens=3, min_tokens=0),
        entity_lexicon=MedicalEntityLexicon(["very rare special compound condition"]),
    ).split(document, [paragraph])
    entity_chunks = [chunk for chunk in chunks if "very rare special compound condition" in chunk.text]
    assert len(entity_chunks) == 1
    assert entity_chunks[0].text == "very rare special compound condition"
    assert entity_chunks[0].token_count > 3
    assert "protected entity exceeded the chunk budget and was kept intact" in entity_chunks[0].boundary_notes


def test_who_manifest_is_explicit_license_gated_and_uses_local_raw_file(tmp_path: Path):
    raw_path = tmp_path / "who_guideline.html"
    raw_path.write_bytes((FIXTURES / "who_guideline_sample.html").read_bytes())
    manifest = tmp_path / "who_manifest.json"
    manifest.write_text(json.dumps({"documents": [{
        "document_id": "WHO-TEST-GUIDELINE",
        "title": "Clinical guideline",
        "raw_path": "who_guideline.html",
        "source_uri": "https://www.who.int/publications/i/item/example-guideline",
        "license": "CC BY-NC-SA 3.0 IGO",
        "license_uri": "https://creativecommons.org/licenses/by-nc-sa/3.0/igo/",
        "publication_date": "2025",
        "specialty": ["general medicine"],
    }]}), encoding="utf-8")
    output = tmp_path / "ingestion"
    with pytest.raises(IngestionLicenseError, match="noncommercial"):
        WHOManifestAdapter(output).ingest_manifest(manifest)
    assert not (output / "raw").exists()

    (source,) = WHOManifestAdapter(output, license_policy=LicensePolicy(allow_noncommercial=True)).ingest_manifest(manifest)
    assert source.document.source_org == "WHO"
    assert source.document.license_id == "CC BY-NC-SA 3.0 IGO"
    assert len(source.sections) == 3
    assert source.sections[-1].heading_path == ["Clinical guideline", "Diagnostic assessment", "Imaging"]
    assert (output / source.document.raw_path).exists()


def test_who_manifest_rejects_non_who_origin(tmp_path: Path):
    raw_path = tmp_path / "source.txt"
    raw_path.write_text("A guideline paragraph.", encoding="utf-8")
    manifest = tmp_path / "bad_manifest.json"
    manifest.write_text(json.dumps([{
        "document_id": "WHO-FAKE", "title": "Example", "raw_path": str(raw_path),
        "source_uri": "https://example.org/guideline", "license": "CC BY 4.0",
    }]), encoding="utf-8")
    with pytest.raises(ValueError, match="hosted by who.int"):
        WHOManifestAdapter(tmp_path / "out").ingest_manifest(manifest)


def test_normalized_writer_is_idempotent_and_refuses_conflicting_overwrite(tmp_path: Path):
    document, sections = parse_bioc_json(
        json.loads((FIXTURES / "pmc_bioc_sample.json").read_text()),
        pmcid="PMC1234567", source_uri="https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/",
        raw_path="raw/pmc/sample.json", metadata={"title": "Evidence in diagnosis"},
    )
    chunks = ParentChildSplitter(SplitterConfig(target_tokens=24, max_tokens=32, overlap_tokens=4, min_tokens=0)).split(
        document.document_id, document.source_uri, sections
    )
    output = write_normalized(tmp_path, document, sections, chunks)
    assert write_normalized(tmp_path, document, sections, chunks) == output
    changed = document.model_copy(update={"title": "A conflicting title"})
    with pytest.raises(ImmutableArtifactConflict):
        write_normalized(tmp_path, changed, sections, chunks)
    assert json.loads((output / "document.json").read_text())["title"] == "Evidence in diagnosis"
