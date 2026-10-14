from __future__ import annotations

import pytest

from traceevo.rag.common.schemas import (
    DocumentChunk, DocumentParagraph, DocumentSection, MedicalDocument, ParagraphSpan,
    SectionBlock, SectionChunk, SectionSpan, SubQuery,
)
from traceevo.rag.medical_knowledge.context import DocumentParagraphStore, DocumentSectionStore
from traceevo.rag.medical_knowledge.embedding import MEDICAL_QUERY_INSTRUCTION, Qwen3Embedder
from traceevo.rag.medical_knowledge.indexer import index_normalized_corpus, load_normalized_corpus
from traceevo.rag.medical_knowledge.reranker import Qwen3Reranker, RerankedChunk
from traceevo.rag.medical_knowledge.retriever import MedicalKnowledgeRetriever
from traceevo.rag.medical_knowledge.splitter import RegexTokenCounter
from traceevo.rag.medical_knowledge.storage import write_document_level_normalized, write_section_normalized
from traceevo.rag.medical_knowledge.vector_store import (
    DocumentSearchPoint,
    QdrantMedicalKnowledgeStore,
    VectorSearchPoint,
    VectorStoreError,
)
from traceevo.rag.tools.medical_knowledge_tool import MedicalKnowledgeTool


class _FakeEmbeddingModel:
    def __init__(self):
        self.inputs = []

    def encode(self, inputs, **kwargs):
        self.inputs.extend(inputs)
        return [[1.0, *([0.0] * 31)] for _ in inputs]


class _FakeEmbedder:
    dimension = 3

    def __init__(self):
        self.queries = []

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return [1.0, 0.0, 0.0]


class _FakeIndexStore:
    dimension = 3

    def __init__(self):
        self.created = False
        self.documents = []
        self.document_texts = []
        self.chunks = []

    def ensure_collection(self):
        self.created = True

    def upsert_documents(self, documents, retrieval_texts, vectors):
        self.documents.extend(zip(documents, vectors, strict=True))
        self.document_texts.extend(retrieval_texts)
        return len(documents)

    def upsert_chunks(self, chunks, vectors, documents):
        self.chunks.extend((chunk, vector, documents[chunk.document_id]) for chunk, vector in zip(chunks, vectors, strict=True))
        return len(chunks)


def _document(document_id: str, title: str, abstract: str | None = None) -> MedicalDocument:
    return MedicalDocument(
        document_id=document_id,
        source_org="PMC OA",
        source_type="article",
        title=title,
        abstract=abstract,
        authors=["A. Author"],
        source_uri=f"https://pmc.ncbi.nlm.nih.gov/articles/{document_id}/",
        license="CC BY 4.0",
        license_id="CC BY 4.0",
        raw_path="raw/pmc/sample.json",
    )


def _paragraph(document_id: str, ordinal: int, text: str, heading: list[str] | None = None) -> DocumentParagraph:
    return DocumentParagraph(
        paragraph_id=f"{document_id}-p{ordinal}",
        document_id=document_id,
        ordinal_in_document=ordinal,
        heading_path=heading or ["Findings"],
        text=text,
    )


def _chunk(document: MedicalDocument, paragraph: DocumentParagraph, chunk_id: str) -> DocumentChunk:
    heading_paths = [paragraph.heading_path] if paragraph.heading_path else []
    return DocumentChunk(
        chunk_id=chunk_id,
        document_id=document.document_id,
        paragraph_ids=[paragraph.paragraph_id],
        paragraph_spans=[ParagraphSpan(
            paragraph_id=paragraph.paragraph_id,
            start_char=0,
            end_char=len(paragraph.text),
        )],
        heading_paths=heading_paths,
        text=paragraph.text,
        embedding_text=f"Document: {document.title}\nHeading: {' > '.join(paragraph.heading_path)}\n{paragraph.text}",
        token_count=8,
        ordinal_in_document=paragraph.ordinal_in_document,
        source_uri=document.source_uri,
    )


def _point(document: MedicalDocument, paragraph: DocumentParagraph, chunk_id: str, score: float) -> VectorSearchPoint:
    chunk = _chunk(document, paragraph, chunk_id)
    return VectorSearchPoint(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        paragraph_ids=chunk.paragraph_ids,
        paragraph_spans=chunk.paragraph_spans,
        ordinal_in_document=chunk.ordinal_in_document,
        text=chunk.text,
        score=score,
        payload={
            "title": document.title,
            "authors": document.authors,
            "source_uri": document.source_uri,
            "source_org": document.source_org,
            "source_type": document.source_type,
            "license": document.license,
            "license_id": document.license_id,
            "heading_paths": chunk.heading_paths,
            "tokenizer_name": chunk.tokenizer_name,
        },
    )


def test_qwen_embedder_injects_medical_instruction_only_for_queries():
    embedder = Qwen3Embedder("fake-model", dimension=32)
    fake = _FakeEmbeddingModel()
    embedder._model = fake
    assert embedder.embed_documents(["document text"]) == [[1.0, *([0.0] * 31)]]
    assert embedder.embed_query("when is ankle x-ray indicated?") == [1.0, *([0.0] * 31)]
    assert embedder.embed_queries(["criteria one", "criteria two"]) == [
        [1.0, *([0.0] * 31)], [1.0, *([0.0] * 31)]
    ]
    assert fake.inputs[0] == "document text"
    assert fake.inputs[1].startswith(f"Instruct: {MEDICAL_QUERY_INSTRUCTION}\nQuery: ")
    assert fake.inputs[2].startswith(f"Instruct: {MEDICAL_QUERY_INSTRUCTION}\nQuery: criteria one")
    assert fake.inputs[3].endswith("Query: criteria two")


def test_schema_v3_loader_and_indexer_embed_document_routes_and_child_chunks(tmp_path):
    doc = _document("PMC-1", "Ankle imaging", "Rules for ankle radiography.")
    paragraph = _paragraph(doc.document_id, 0, "Ankle radiography is considered using clinical rules.")
    chunk = _chunk(doc, paragraph, "chunk-1")
    write_document_level_normalized(tmp_path, doc, [paragraph], [chunk])

    corpus = load_normalized_corpus(tmp_path)
    assert corpus.documents == {doc.document_id: doc}
    assert corpus.paragraphs == {paragraph.paragraph_id: paragraph}
    assert corpus.chunks == [chunk]
    with pytest.raises(ValueError, match="only schema-v4"):
        load_normalized_corpus(tmp_path, schema_version="schema-v3")

    store = _FakeIndexStore()
    result = index_normalized_corpus(tmp_path, embedder=_FakeEmbedder(), store=store, batch_size=1)
    assert result.documents == 1
    assert result.paragraphs == 1
    assert result.child_chunks == 1
    assert result.document_vectors_upserted == result.child_vectors_upserted == 1
    assert store.created
    assert "Title: Ankle imaging" in store.document_texts[0]
    assert "Abstract: Rules for ankle radiography." in store.document_texts[0]
    assert store.chunks[0][0] == chunk


class _FakeSearchStore:
    dimension = 3

    def __init__(self, document_hits, chunks_by_document):
        self.document_hits = document_hits
        self.chunks_by_document = chunks_by_document
        self.document_limits = []
        self.child_queries = []

    def search_documents(self, vector, *, limit):
        assert vector == [1.0, 0.0, 0.0]
        self.document_limits.append(limit)
        return self.document_hits[:limit]

    def search_chunks_for_document(self, vector, *, document_id, limit):
        assert vector == [1.0, 0.0, 0.0]
        self.child_queries.append((document_id, limit))
        return self.chunks_by_document.get(document_id, [])[:limit]


class _FakeReranker:
    def __init__(self, scores):
        self.scores = scores
        self.seen = []

    def rerank(self, query, candidates):
        self.seen = list(candidates)
        return sorted(
            [RerankedChunk(point=candidate, score=self.scores[candidate.chunk_id]) for candidate in candidates],
            key=lambda item: item.score,
            reverse=True,
        )


def test_document_first_retrieval_merges_children_reranks_and_hydrates_neighbor_paragraphs(tmp_path):
    doc_a = _document("PMC-A", "Ankle imaging", "Abstract about radiography.")
    doc_b = _document("PMC-B", "Musculoskeletal assessment", "Abstract about examination.")
    a_paragraphs = [
        _paragraph(doc_a.document_id, 0, "Previous paragraph gives the injury context.", ["Assessment"]),
        _paragraph(doc_a.document_id, 1, "Ankle radiography passage gives the rule.", ["Assessment", "Imaging"]),
        _paragraph(doc_a.document_id, 2, "Following paragraph describes the next step.", ["Assessment", "Imaging"]),
    ]
    b_paragraph = _paragraph(doc_b.document_id, 0, "A different candidate passage.")
    write_document_level_normalized(
        tmp_path,
        doc_a,
        a_paragraphs,
        [_chunk(doc_a, a_paragraphs[1], "chunk-a")],
    )
    write_document_level_normalized(
        tmp_path,
        doc_b,
        [b_paragraph],
        [_chunk(doc_b, b_paragraph, "chunk-b")],
    )

    point_a = _point(doc_a, a_paragraphs[1], "chunk-a", 0.71)
    point_b = _point(doc_b, b_paragraph, "chunk-b", 0.92)
    store = _FakeSearchStore(
        [
            DocumentSearchPoint(doc_a.document_id, 0.96, {"title": doc_a.title}),
            DocumentSearchPoint(doc_b.document_id, 0.82, {"title": doc_b.title}),
        ],
        {doc_a.document_id: [point_a, point_a], doc_b.document_id: [point_b]},
    )
    reranker = _FakeReranker({"chunk-a": 2.4, "chunk-b": -0.1})
    retriever = MedicalKnowledgeRetriever(
        _FakeEmbedder(),
        store,
        final_k=2,
        document_candidate_k=2,
        child_candidates_per_document=7,
        reranker=reranker,
        paragraph_store=DocumentParagraphStore(tmp_path),
    )

    result = MedicalKnowledgeTool(retriever).run(query_id="q-1", query="ankle radiography")
    assert [hit.source_id for hit in result.hits] == ["chunk-a", "chunk-b"]
    assert [point.chunk_id for point in reranker.seen] == ["chunk-a", "chunk-b"]
    assert store.document_limits == [2]
    assert store.child_queries == [(doc_a.document_id, 7), (doc_b.document_id, 7)]
    first = result.hits[0]
    assert first.document_id == doc_a.document_id
    assert first.retrieval_score == 2.4
    assert first.metadata["document_routing_score"] == 0.96
    assert first.metadata["child_vector_score"] == 0.71
    assert first.metadata["license_id"] == "CC BY 4.0"
    assert first.metadata["retrieval_score_is_clinical_confidence"] is False
    assert first.metadata["anchor_paragraph_ids"] == ["PMC-A-p1"]
    assert first.metadata["context_paragraph_ids"] == ["PMC-A-p0", "PMC-A-p1", "PMC-A-p2"]
    assert "Previous paragraph" in first.text
    assert "Ankle radiography passage" in first.text
    assert "Following paragraph" in first.text
    assert first.text.count("[Assessment]") == 1
    assert first.text.count("[Assessment > Imaging]") == 1

    retriever.min_reranker_score = 2.4
    accepted = retriever.retrieve("ankle radiography", query_id="q-filtered")
    assert [hit.source_id for hit in accepted.hits] == ["chunk-a"]
    retriever.min_reranker_score = 3.0
    rejected = retriever.retrieve("ankle radiography", query_id="q-rejected")
    assert rejected.hits == []
    assert "below calibrated" in rejected.empty_reason


def test_multi_query_tool_merges_candidate_pools_before_one_pair_rerank_batch(tmp_path):
    docs = {
        key: _document(key, f"Medical document {key}", "Ankle injury assessment.")
        for key in ("DOC-A", "DOC-B", "DOC-C")
    }
    paragraphs = {
        "chunk-a": _paragraph("DOC-A", 0, "Passage about ankle imaging criteria."),
        "chunk-shared": _paragraph("DOC-B", 0, "Passage about fracture imaging."),
        "chunk-c": _paragraph("DOC-C", 0, "Passage about treatment after injury."),
    }
    points = {}
    for chunk_id, paragraph in paragraphs.items():
        doc = docs[paragraph.document_id]
        write_document_level_normalized(
            tmp_path,
            doc,
            [paragraph],
            [_chunk(doc, paragraph, chunk_id)],
        )
        points[chunk_id] = _point(doc, paragraph, chunk_id, 0.7)

    class _BatchEmbedder:
        dimension = 3

        def embed_documents(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

        def embed_query(self, text):
            return self.embed_queries([text])[0]

        def embed_queries(self, texts):
            return [[1.0, 0.0, 0.0] if text == "criteria" else [0.0, 1.0, 0.0] for text in texts]

    class _QueryStore:
        dimension = 3

        def search_documents(self, vector, *, limit):
            if vector[0] == 1.0:
                ids = ["DOC-A", "DOC-B"]
            else:
                ids = ["DOC-B", "DOC-C"]
            return [DocumentSearchPoint(document_id, 0.9, {"title": document_id}) for document_id in ids[:limit]]

        def search_chunks_for_document(self, vector, *, document_id, limit):
            if document_id == "DOC-A":
                ids = ["chunk-a"]
            elif document_id == "DOC-B":
                ids = ["chunk-shared"]
            else:
                ids = ["chunk-c"]
            return [points[chunk_id] for chunk_id in ids[:limit]]

    class _PairReranker:
        def __init__(self):
            self.pairs = []

        def rerank_pairs(self, pairs):
            self.pairs = list(pairs)
            scores = {
                ("criteria", "chunk-a"): 1.2,
                ("fracture imaging", "chunk-a"): 0.0,
                ("criteria", "chunk-shared"): 0.3,
                ("fracture imaging", "chunk-shared"): 1.5,
                ("criteria", "chunk-c"): 0.2,
                ("fracture imaging", "chunk-c"): 0.9,
            }
            return [scores[(query, point.chunk_id)] for query, point in pairs]

        def rerank(self, query, candidates):
            raise AssertionError("multi-query path should use one pair-reranker batch")

    reranker = _PairReranker()
    retriever = MedicalKnowledgeRetriever(
        _BatchEmbedder(),
        _QueryStore(),
        paragraph_store=DocumentParagraphStore(tmp_path),
        final_k=3,
        document_candidate_k=2,
        child_candidates_per_document=4,
        reranker=reranker,
    )
    tool = MedicalKnowledgeTool(retriever)
    result = tool.run_many(
        query_id="outer-query",
        queries=[
            SubQuery(query_id="q-criteria", query_text="criteria", retrieval_goal="find criteria"),
            SubQuery(query_id="q-imaging", query_text="fracture imaging", retrieval_goal="find imaging"),
        ],
        limit=2,
    )

    assert result.query_id == "outer-query"
    assert [hit.source_id for hit in result.hits] == ["chunk-shared", "chunk-a"]
    assert len(reranker.pairs) == 2 * 3  # two subqueries × three unique chunks
    shared = result.hits[0]
    assert shared.retrieval_score == 1.5
    assert shared.metadata["reranker_query_id"] == "q-imaging"
    assert shared.metadata["reranker_query_goal"] == "find imaging"
    assert shared.metadata["matched_query_ids"] == ["q-criteria", "q-imaging"]
    assert shared.metadata["matched_retrieval_goals"] == ["find criteria", "find imaging"]
    assert shared.metadata["reranker_scope"] == "all_subqueries_x_merged_candidate_chunks"


def test_qwen3_reranker_sends_merged_query_passage_pairs_and_orders_scores():
    doc = _document("PMC-1", "Ankle imaging")
    paragraph = _paragraph(doc.document_id, 0, "Radiography passage.", ["Diagnosis", "Imaging"])
    first = _point(doc, paragraph, "chunk-1", 0.4)
    second = _point(doc, paragraph, "chunk-2", 0.3)

    class FakeCrossEncoder:
        def predict(self, inputs, **kwargs):
            assert inputs[0][0] == "ankle radiography query"
            assert "Heading context: Diagnosis > Imaging" in inputs[0][1]
            assert kwargs["prompt"]
            return [[-0.2], [1.4]]

    reranker = Qwen3Reranker()
    reranker._model = FakeCrossEncoder()
    ranked = reranker.rerank("ankle radiography query", [first, second])
    assert [item.point.chunk_id for item in ranked] == ["chunk-2", "chunk-1"]
    assert [item.score for item in ranked] == [1.4, -0.2]


def test_context_hydration_reconstructs_exact_hit_span_for_an_oversized_paragraph(tmp_path):
    doc = _document("PMC-LONG", "Long source")
    before = _paragraph(doc.document_id, 0, "Earlier context is old. More recent context is useful.")
    long_text = " ".join(f"clinicalterm{i}" for i in range(900))
    anchor = _paragraph(doc.document_id, 1, long_text)
    after = _paragraph(doc.document_id, 2, "A short following paragraph.")
    chunk = DocumentChunk(
        chunk_id="long-span-chunk",
        document_id=doc.document_id,
        paragraph_ids=[anchor.paragraph_id],
        paragraph_spans=[ParagraphSpan(
            paragraph_id=anchor.paragraph_id,
            start_char=long_text.index("clinicalterm410"),
            end_char=long_text.index("clinicalterm412") + len("clinicalterm412"),
        )],
        heading_paths=[["Findings"]],
        text="clinicalterm410 clinicalterm411 clinicalterm412",
        embedding_text="Long source passage",
        token_count=3,
        ordinal_in_document=1,
        source_uri=doc.source_uri,
    )
    write_document_level_normalized(tmp_path, doc, [before, anchor, after], [chunk])
    hit = _point(doc, anchor, chunk.chunk_id, 0.8)
    hit = VectorSearchPoint(
        chunk_id=hit.chunk_id,
        document_id=hit.document_id,
        paragraph_ids=chunk.paragraph_ids,
        paragraph_spans=chunk.paragraph_spans,
        ordinal_in_document=hit.ordinal_in_document,
        text=chunk.text,
        score=hit.score,
        payload=hit.payload,
    )
    hydrated = DocumentParagraphStore(tmp_path).hydrate(
        hit,
        max_context_tokens=40,
        max_anchor_tokens=10,
        max_neighbor_tokens=6,
    )
    assert "clinicalterm410 clinicalterm411 clinicalterm412" in hydrated.text
    assert "clinicalterm0" not in hydrated.text
    assert "clinicalterm409" not in hydrated.text
    assert "clinicalterm413" not in hydrated.text
    assert "Earlier context is old" not in hydrated.text
    assert "More recent context is useful" in hydrated.text
    assert "following paragraph" in hydrated.text
    assert hydrated.anchor_paragraph_ids == [anchor.paragraph_id]
    assert hydrated.paragraph_ids == [before.paragraph_id, anchor.paragraph_id, after.paragraph_id]
    assert hydrated.truncated


def test_qdrant_local_collection_round_trip_keeps_document_and_chunk_records_separate(tmp_path):
    pytest.importorskip("qdrant_client")
    doc = _document("PMC-1", "Ankle imaging", "Radiography abstract.")
    paragraph = _paragraph(doc.document_id, 0, "Ankle radiography passage.")
    chunk = _chunk(doc, paragraph, "chunk-1")
    other_doc = _document("PMC-2", "Another article", "An unrelated abstract.")
    other_paragraph = _paragraph(other_doc.document_id, 0, "Different passage.")
    other_chunk = _chunk(other_doc, other_paragraph, "chunk-2")
    path = tmp_path / "qdrant-v3"
    with QdrantMedicalKnowledgeStore(path, collection_name="medical_test_v3", dimension=3) as store:
        assert store.upsert_documents(
            [doc, other_doc],
            ["Title: Ankle imaging\nAbstract: Radiography abstract.", "Title: Another article\nAbstract: Unrelated."],
            [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]],
        ) == 2
        assert store.upsert_chunks(
            [chunk, other_chunk],
            [[1.0, 0.0, 0.0], [0.9, 0.1, 0.0]],
            {doc.document_id: doc, other_doc.document_id: other_doc},
        ) == 2
        document_hits = store.search_documents([1.0, 0.0, 0.0], limit=2)
        chunk_hits = store.search_chunks_for_document([1.0, 0.0, 0.0], document_id=doc.document_id, limit=2)
        assert len(document_hits) == 2
        assert len(chunk_hits) == 1
        assert document_hits[0].document_id == doc.document_id
        assert chunk_hits[0].chunk_id == chunk.chunk_id
        assert chunk_hits[0].paragraph_ids == [paragraph.paragraph_id]
        assert chunk_hits[0].paragraph_spans == chunk.paragraph_spans
        assert chunk_hits[0].payload["license_id"] == "CC BY 4.0"
        assert "parent_section_id" not in chunk_hits[0].payload
        assert store.count(record_type="document") == 2
        assert store.count(record_type="chunk") == 2
        assert store.count() == 4
    with QdrantMedicalKnowledgeStore(path, collection_name="medical_test_v3", dimension=4) as wrong_size:
        with pytest.raises(VectorStoreError, match="refusing to recreate"):
            wrong_size.ensure_collection()
    with QdrantMedicalKnowledgeStore(path, collection_name="medical_test_v3", dimension=3) as store:
        assert store.count(record_type="chunk") == 2


def test_schema_v5_retrieval_hydrates_sentences_and_keeps_global_rerank_order(tmp_path):
    docs = {
        key: _document(key, f"Medical article {key}", f"Abstract for {key}.")
        for key in ("DOC-A", "DOC-B")
    }
    points = {}
    for key, phrase in (("DOC-A", "Anchor passage about ankle radiographs."),
                        ("DOC-B", "Anchor passage about ankle fractures.")):
        doc = docs[key]
        text = f"Previous full sentence for {key}. {phrase} Following full sentence for {key}."
        start = text.index(phrase)
        end = start + len(phrase)
        section = DocumentSection(
            section_id=f"{key}-section", document_id=key, heading_path=["Results", "Imaging"],
            text=text,
            blocks=[SectionBlock(
                block_id=f"{key}-block", block_type="paragraph", start_char=0,
                end_char=len(text), text=text,
            )],
            ordinal=0,
        )
        chunk = SectionChunk(
            chunk_id=f"{key}-chunk", document_id=key, section_id=section.section_id,
            section_span=SectionSpan(section_id=section.section_id, start_char=start, end_char=end),
            block_ids=[section.blocks[0].block_id], heading_path=section.heading_path,
            content_type="paragraph", text=phrase, embedding_text=f"{doc.title} {phrase}",
            token_count=8, ordinal_in_document=0, source_uri=doc.source_uri,
        )
        write_section_normalized(tmp_path, doc, [section], [chunk])
        points[key] = VectorSearchPoint(
            chunk_id=chunk.chunk_id,
            document_id=key,
            paragraph_ids=[],
            paragraph_spans=[],
            ordinal_in_document=0,
            text=chunk.text,
            score=0.7,
            payload={
                "title": doc.title,
                "heading_paths": [section.heading_path],
                "content_type": "paragraph",
                "source_uri": doc.source_uri,
            },
            section_span=chunk.section_span,
        )

    class _V5SearchStore:
        dimension = 3

        def __init__(self):
            self.document_calls = []
            self.child_calls = []

        def search_documents(self, vector, *, limit):
            self.document_calls.append(limit)
            return [DocumentSearchPoint("DOC-A", 0.9, {}), DocumentSearchPoint("DOC-B", 0.8, {})]

        def search_chunks_for_document(self, vector, *, document_id, limit):
            self.child_calls.append((document_id, limit))
            return [points[document_id]]

    class _V5Reranker:
        def __init__(self):
            self.seen = []

        def rerank(self, query, candidates):
            self.seen = list(candidates)
            scores = {"DOC-A-chunk": 0.2, "DOC-B-chunk": 1.3}
            return sorted(
                [RerankedChunk(point, scores[point.chunk_id]) for point in candidates],
                key=lambda item: item.score,
                reverse=True,
            )

    store = _V5SearchStore()
    reranker = _V5Reranker()
    result = MedicalKnowledgeRetriever(
        _FakeEmbedder(),
        store,
        section_store=DocumentSectionStore(tmp_path, token_counter=RegexTokenCounter()),
        reranker=reranker,
        document_candidate_k=2,
        child_candidates_per_document=5,
        final_k=2,
    ).retrieve("ankle injury imaging", query_id="q-v5")
    assert store.document_calls == [2]
    assert store.child_calls == [("DOC-A", 5), ("DOC-B", 5)]
    assert [point.chunk_id for point in reranker.seen] == ["DOC-A-chunk", "DOC-B-chunk"]
    assert [hit.source_id for hit in result.hits] == ["DOC-B-chunk", "DOC-A-chunk"]
    assert "Previous full sentence for DOC-B." in result.hits[0].text
    assert "Following full sentence for DOC-B." in result.hits[0].text
    assert result.hits[0].metadata["section_span"]["section_id"] == "DOC-B-section"
    assert result.hits[0].metadata["reranker_scope"] == "all_subqueries_x_merged_candidate_chunks"

    corpus = load_normalized_corpus(tmp_path, schema_version="schema-v5")
    assert len(corpus.documents) == 2
    assert len(corpus.sections) == 2
    assert len(corpus.chunks) == 2
    index_store = _FakeIndexStore()
    summary = index_normalized_corpus(
        tmp_path,
        embedder=_FakeEmbedder(),
        store=index_store,
        batch_size=2,
        schema_version="schema-v5",
    )
    assert summary.sections == 2
    assert summary.paragraphs == 0
    assert summary.child_chunks == 2
    assert all("Title:" in retrieval_text and "Abstract:" in retrieval_text
               for retrieval_text in index_store.document_texts)
