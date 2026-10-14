"""Isolated Qdrant collection for synthetic demo experiences only.

This collection is intentionally not readable by ``ExperienceRetriever`` and
cannot be indexed through the validated MemoryStore pathway.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import uuid
from typing import Any

from .simulated_schema import SimulatedExperienceRecord


class SimulatedExperienceStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SimulatedExperienceHit:
    record: SimulatedExperienceRecord
    score: float


class QdrantSimulatedExperienceStore:
    """Local demo database; distinct path/collection from production memories."""

    def __init__(
        self,
        path: str | Path,
        *,
        collection_name: str = "traceevo_experience_simulated_v1",
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
            return
        info = client.get_collection(self.collection_name)
        vectors = info.config.params.vectors
        vector_config = vectors.get("") if isinstance(vectors, dict) else vectors
        if vector_config is None or vector_config.size != self.dimension:
            raise SimulatedExperienceStoreError(
                f"simulated collection has a different vector dimension; refusing to recreate it"
            )
        if "cosine" not in str(vector_config.distance).casefold():
            raise SimulatedExperienceStoreError("simulated collection must use cosine distance")

    def upsert(
        self,
        records: list[SimulatedExperienceRecord],
        vectors: list[list[float]],
    ) -> int:
        if len(records) != len(vectors):
            raise ValueError("simulated experience/vector count mismatch")
        if not records:
            return 0
        if any(not record.simulation_only or record.online_eligible for record in records):
            raise ValueError("only simulation-only, online-ineligible records may enter this collection")
        self.ensure_collection()
        from qdrant_client import models

        points = []
        for record, vector in zip(records, vectors, strict=True):
            self._check_vector(vector)
            payload = record.model_dump(mode="json")
            payload["record_type"] = "simulated_experience"
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"traceevo:simulated-experience:{record.simulation_id}"))
            points.append(models.PointStruct(id=point_id, vector=vector, payload=payload))
        self._get_client().upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def search(self, vector: list[float], *, limit: int = 5) -> list[SimulatedExperienceHit]:
        if limit <= 0:
            return []
        self._check_vector(vector)
        self.ensure_collection()
        from qdrant_client import models

        result = self._get_client().query_points(
            collection_name=self.collection_name,
            query=vector,
            query_filter=models.Filter(must=[models.FieldCondition(
                key="record_type", match=models.MatchValue(value="simulated_experience")
            )]),
            search_params=models.SearchParams(exact=True),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            SimulatedExperienceHit(
                record=SimulatedExperienceRecord.model_validate({
                    key: value for key, value in (point.payload or {}).items()
                    if key != "record_type"
                }),
                score=float(point.score),
            )
            for point in result.points
        ]

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
            raise ValueError(f"expected {self.dimension}-D vector, got {len(vector)}")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("simulated experience vector contains NaN or infinity")
