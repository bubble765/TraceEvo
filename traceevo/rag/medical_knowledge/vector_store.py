"""Versioned Qdrant collection for document routing and filtered child search."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid
from typing import Any

from traceevo.rag.common.schemas import DocumentChunk, MedicalDocument, ParagraphSpan, SectionChunk, SectionSpan


class VectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentSearchPoint:
    document_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchPoint:
    chunk_id: str
    document_id: str
    paragraph_ids: list[str]
    paragraph_spans: list[ParagraphSpan]
    ordinal_in_document: int
    text: str
    score: float
    payload: dict[str, Any]
    section_span: SectionSpan | None = None


class QdrantMedicalKnowledgeStore:
    """Store document and child vectors as separate record types in one collection."""

    def __init__(
        self,
        path: str | Path,
        *,
        collection_name: str = "traceevo_medical_knowledge_v4",
        dimension: int = 1024,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.path = Path(path)
        self.collection_name = collection_name
        self.dimension = dimension
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RuntimeError(
                    "Qdrant client is missing; install traceevo/rag/requirements-medical-knowledge.txt"
                ) from exc
            self.path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self.path))
        return self._client

    def ensure_collection(self) -> None:
        client = self._get_client()
        from qdrant_client import models

        if not client.collection_exists(self.collection_name):
            client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=self.dimension, distance=models.Distance.COSINE),
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100),
            )
        else:
            info = client.get_collection(self.collection_name)
            vectors = info.config.params.vectors
            vector_config = vectors.get("") if isinstance(vectors, dict) else vectors
            if vector_config is None or vector_config.size != self.dimension:
                size = getattr(vector_config, "size", None)
                raise VectorStoreError(
                    f"collection {self.collection_name!r} has vector size {size}; configured size is {self.dimension}; "
                    "refusing to recreate or overwrite the collection"
                )
            if "cosine" not in str(vector_config.distance).casefold():
                raise VectorStoreError(
                    f"collection {self.collection_name!r} uses {vector_config.distance}, expected cosine; "
                    "refusing to recreate or overwrite the collection"
                )
        for field in ("record_type", "document_id"):
            client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def upsert_documents(
        self,
        documents: list[MedicalDocument],
        retrieval_texts: list[str],
        vectors: list[list[float]],
    ) -> int:
        if not (len(documents) == len(retrieval_texts) == len(vectors)):
            raise ValueError("document/retrieval-text/vector count mismatch")
        if not documents:
            return 0
        self.ensure_collection()
        from qdrant_client import models

        points = []
        for document, retrieval_text, vector in zip(documents, retrieval_texts, vectors, strict=True):
            self._check_vector(vector)
            payload = {
                "record_type": "document",
                "document_id": document.document_id,
                "title": document.title,
                "abstract": document.abstract,
                "retrieval_text": retrieval_text,
                "authors": document.authors,
                "source_uri": document.source_uri,
                "source_org": document.source_org,
                "source_type": document.source_type,
                "publication_date": document.publication_date,
                "version": document.version,
                "specialty": document.specialty,
                "license": document.license,
                "license_id": document.license_id,
                "license_uri": document.license_uri,
                "license_notice": document.license_notice,
            }
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"traceevo:medical:document:{document.document_id}"))
            points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))
        self._get_client().upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def upsert_chunks(
        self,
        chunks: list[DocumentChunk | SectionChunk],
        vectors: list[list[float]],
        documents: dict[str, MedicalDocument],
    ) -> int:
        if len(chunks) != len(vectors):
            raise ValueError("chunk/vector count mismatch")
        if not chunks:
            return 0
        self.ensure_collection()
        from qdrant_client import models

        points = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._check_vector(vector)
            if chunk.document_id not in documents:
                raise ValueError(f"no MedicalDocument for chunk {chunk.chunk_id}")
            document = documents[chunk.document_id]
            payload: dict[str, Any] = {
                "record_type": "chunk",
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "heading_paths": (
                    chunk.heading_paths if isinstance(chunk, DocumentChunk)
                    else ([chunk.heading_path] if chunk.heading_path else [])
                ),
                "boundary_notes": chunk.boundary_notes,
                "text": chunk.text,
                "embedding_text": chunk.embedding_text,
                "token_count": chunk.token_count,
                "tokenizer_name": chunk.tokenizer_name,
                "ordinal_in_document": chunk.ordinal_in_document,
                "source_uri": chunk.source_uri,
                "source_org": document.source_org,
                "source_type": document.source_type,
                "title": document.title,
                "authors": document.authors,
                "publication_date": document.publication_date,
                "version": document.version,
                "specialty": document.specialty,
                "license": document.license,
                "license_id": document.license_id,
                "license_uri": document.license_uri,
                "license_notice": document.license_notice,
            }
            if isinstance(chunk, DocumentChunk):
                payload.update({
                    "paragraph_ids": chunk.paragraph_ids,
                    "paragraph_spans": [span.model_dump(mode="json") for span in chunk.paragraph_spans],
                    "source_offset_kind": "paragraph",
                })
            else:
                payload.update({
                    "section_id": chunk.section_id,
                    "section_span": chunk.section_span.model_dump(mode="json"),
                    "block_ids": chunk.block_ids,
                    "content_type": chunk.content_type,
                    "table_metadata": chunk.table_metadata,
                    "source_offset_kind": "section",
                })
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"traceevo:medical:chunk:{chunk.chunk_id}"))
            points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))
        self._get_client().upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def search_documents(self, vector: list[float], *, limit: int = 10) -> list[DocumentSearchPoint]:
        if limit <= 0:
            return []
        self._check_vector(vector)
        self.ensure_collection()
        from qdrant_client import models

        result = self._get_client().query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=[models.FieldCondition(
                key="record_type", match=models.MatchValue(value="document")
            )]),
            search_params=models.SearchParams(exact=True),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            DocumentSearchPoint(
                document_id=str((point.payload or {}).get("document_id", "")),
                score=float(point.score),
                payload=point.payload or {},
            )
            for point in result.points
        ]

    def search_chunks_for_document(
        self,
        vector: list[float],
        *,
        document_id: str,
        limit: int = 30,
    ) -> list[VectorSearchPoint]:
        if limit <= 0:
            return []
        self._check_vector(vector)
        self.ensure_collection()
        from qdrant_client import models

        result = self._get_client().query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=[
                models.FieldCondition(key="record_type", match=models.MatchValue(value="chunk")),
                models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id)),
            ]),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            VectorSearchPoint(
                chunk_id=str((point.payload or {}).get("chunk_id", "")),
                document_id=str((point.payload or {}).get("document_id", "")),
                paragraph_ids=list((point.payload or {}).get("paragraph_ids", [])),
                paragraph_spans=[
                    ParagraphSpan.model_validate(span)
                    for span in (point.payload or {}).get("paragraph_spans", [])
                ],
                ordinal_in_document=int((point.payload or {}).get("ordinal_in_document", 0)),
                text=str((point.payload or {}).get("text", "")),
                score=float(point.score),
                payload=point.payload or {},
                section_span=(
                    SectionSpan.model_validate((point.payload or {}).get("section_span"))
                    if (point.payload or {}).get("section_span")
                    else None
                ),
            )
            for point in result.points
        ]

    def count(self, *, record_type: str | None = None) -> int:
        if self._client is None and not self.path.exists():
            return 0
        self.ensure_collection()
        query_filter = None
        if record_type:
            from qdrant_client import models
            query_filter = models.Filter(must=[models.FieldCondition(
                key="record_type", match=models.MatchValue(value=record_type)
            )])
        return int(self._get_client().count(self.collection_name, exact=True, count_filter=query_filter).count)

    def _check_vector(self, vector: list[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError(f"vector dimension {len(vector)} != configured dimension {self.dimension}")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "QdrantMedicalKnowledgeStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
