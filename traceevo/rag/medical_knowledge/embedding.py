"""Lazy Qwen3 medical embedding backend with a small injectable interface."""
from __future__ import annotations

import math
from typing import Any, Protocol


MEDICAL_QUERY_INSTRUCTION = (
    "Given a medical diagnostic information need, retrieve passages from clinical guidelines and "
    "biomedical literature that provide directly relevant diagnostic, differential-diagnostic, "
    "examination, or treatment knowledge. Prefer passages that can answer the query rather than "
    "merely mention the same disease."
)
QWEN3_EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"


class TextEmbedder(Protocol):
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class Qwen3Embedder:
    """Qwen3-Embedding via SentenceTransformers; loads weights only on first use."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-0.6B",
        *,
        dimension: int = 1024,
        batch_size: int = 8,
        device: str = "auto",
        normalize: bool = True,
        revision: str | None = QWEN3_EMBEDDING_REVISION,
        query_instruction: str = MEDICAL_QUERY_INSTRUCTION,
    ) -> None:
        if not 32 <= dimension <= 1024:
            raise ValueError("Qwen3-Embedding-0.6B supports output dimensions in [32, 1024]")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if not query_instruction.strip():
            raise ValueError("query instruction cannot be empty")
        self.model_name = model_name
        self.dimension = dimension
        self.batch_size = batch_size
        self.requested_device = device
        self.normalize = normalize
        self.revision = revision
        self.query_instruction = query_instruction
        self._model: Any | None = None
        self.device: str | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=False)

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("query text cannot be empty")
        return self._encode([text], query=True)[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, query=True)

    def _encode(self, texts: list[str], *, query: bool) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("all embedding inputs must be non-empty strings")
        model = self._load_model()
        inputs = texts
        if query:
            inputs = [f"Instruct: {self.query_instruction}\nQuery: {text}" for text in texts]
        vectors = model.encode(
            inputs,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=self.normalize,
            truncate_dim=self.dimension,
        )
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedding backend returned {len(vectors)} vectors for {len(texts)} inputs")
        result: list[list[float]] = []
        for vector in vectors:
            row = [float(value) for value in vector]
            if len(row) != self.dimension:
                raise RuntimeError(f"embedding dimension mismatch: expected {self.dimension}, got {len(row)}")
            if not all(math.isfinite(value) for value in row):
                raise RuntimeError("embedding backend returned NaN or infinite values")
            result.append(row)
        return result

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "embedding dependencies are missing; install traceevo/rag/requirements-medical-knowledge.txt"
            ) from exc

        device = self.requested_device
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        kwargs: dict[str, Any] = {
            "device": device,
            "truncate_dim": self.dimension,
        }
        if self.revision:
            kwargs["revision"] = self.revision
        # MPS can fit the 0.6B model comfortably in fp16 and is present on
        # Apple Silicon. CPU/CUDA keep the library's default dtype behavior.
        if device == "mps":
            kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
        self._model = SentenceTransformer(self.model_name, **kwargs)
        self.device = str(device)
        return self._model
