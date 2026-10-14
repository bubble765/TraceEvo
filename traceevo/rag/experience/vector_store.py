"""Separate local Qdrant collection for admission-gated Experience vectors."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import uuid
from typing import Any

from .indexer import IndexedExperience


class ExperienceVectorStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExperienceSearchPoint:
    experience_id: str
    experience_type: str
    partition: str
    fingerprint: str
    score: float


class QdrantExperienceStore:
    """Qdrant index stores only IDs/type/fingerprint beside the vectors.

    The task summary and injectable payload are resolved from the live gated
    MemoryStore at retrieval time, so stale or revoked memories fail closed.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        collection_name: str = "traceevo_experience_v1",
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
                vectors_config=models.VectorParams(
                    size=self.dimension,
                    distance=models.Distance.COSINE,
                ),
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100),
            )
        else:
            info = client.get_collection(self.collection_name)
            vectors = info.config.params.vectors
            vector_config = vectors.get("") if isinstance(vectors, dict) else vectors
            if vector_config is None or vector_config.size != self.dimension:
                size = getattr(vector_config, "size", None)
                raise ExperienceVectorStoreError(
                    f"collection {self.collection_name!r} has vector size {size}; configured size is "
                    f"{self.dimension}; refusing to recreate or overwrite the collection"
                )
            if "cosine" not in str(vector_config.distance).casefold():
                raise ExperienceVectorStoreError(
                    f"collection {self.collection_name!r} does not use cosine distance; refusing to reuse it"
                )
        for field in ("record_type", "experience_id", "experience_type", "partition"):
            client.create_payload_index(
                collection_name=self.collection_name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    def upsert(self, records: list[IndexedExperience], vectors: list[list[float]]) -> int:
        if len(records) != len(vectors):
            raise ValueError("experience/vector count mismatch")
        if not records:
            return 0
        self.ensure_collection()
        from qdrant_client import models

        points = []
        for item, vector in zip(records, vectors, strict=True):
            self._check_vector(vector)
            record = item.record
            payload = {
                "record_type": "experience",
                "experience_id": record.experience_id,
                "experience_type": record.experience_type,
                "partition": item.partition,
                "fingerprint": item.fingerprint,
            }
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"traceevo:experience:{record.experience_id}"))
            points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))
        self._get_client().upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        allowed_experience_ids: list[str],
        include_failures: bool = False,
    ) -> list[ExperienceSearchPoint]:
        if limit <= 0 or not allowed_experience_ids:
            return []
        self._check_vector(vector)
        self.ensure_collection()
        from qdrant_client import models

        experience_types = ["validated_success", "validated_failure"] if include_failures else ["validated_success"]
        result = self._get_client().query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=[
                models.FieldCondition(key="record_type", match=models.MatchValue(value="experience")),
                models.FieldCondition(key="experience_id", match=models.MatchAny(any=allowed_experience_ids)),
                models.FieldCondition(key="experience_type", match=models.MatchAny(any=experience_types)),
            ]),
            search_params=models.SearchParams(exact=True),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        out: list[ExperienceSearchPoint] = []
        for point in result.points:
            payload = point.payload or {}
            out.append(ExperienceSearchPoint(
                experience_id=str(payload.get("experience_id", "")),
                experience_type=str(payload.get("experience_type", "")),
                partition=str(payload.get("partition", "")),
                fingerprint=str(payload.get("fingerprint", "")),
                score=float(point.score),
            ))
        return out

    def count(self) -> int:
        if self._client is None and not self.path.exists():
            return 0
        self.ensure_collection()
        result = self._get_client().count(collection_name=self.collection_name, exact=True)
        return int(result.count)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _check_vector(self, vector: list[float]) -> None:
        if len(vector) != self.dimension:
            raise ValueError(f"expected {self.dimension}-D experience vector; got {len(vector)}")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("experience vector contains NaN or infinite values")
