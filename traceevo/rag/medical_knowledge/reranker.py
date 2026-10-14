"""Qwen3 medical passage reranking via SentenceTransformers CrossEncoder."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from .vector_store import VectorSearchPoint


MEDICAL_RERANK_INSTRUCTION = (
    "Given a medical diagnostic information need, judge whether the passage directly supports, "
    "refutes, or contextualizes that need. Prefer clinically discriminative evidence over passages "
    "that only mention the same condition."
)
QWEN3_RERANKER_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"


class PassageReranker(Protocol):
    def rerank(self, query: str, candidates: list[VectorSearchPoint]) -> list["RerankedChunk"]: ...


@dataclass(frozen=True)
class RerankedChunk:
    point: VectorSearchPoint
    score: float


class Qwen3Reranker:
    """Lazy Qwen3-Reranker-0.6B cross-encoder; scores query/passage text pairs."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Reranker-0.6B",
        *,
        revision: str | None = QWEN3_RERANKER_REVISION,
        device: str = "auto",
        batch_size: int = 4,
        max_length: int = 2048,
        instruction: str = MEDICAL_RERANK_INSTRUCTION,
    ) -> None:
        if batch_size <= 0 or max_length <= 0:
            raise ValueError("batch_size and max_length must be positive")
        if not instruction.strip():
            raise ValueError("reranker instruction cannot be empty")
        self.model_name = model_name
        self.revision = revision
        self.requested_device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.instruction = instruction
        self._model: Any | None = None
        self.device: str | None = None

    def rerank(self, query: str, candidates: list[VectorSearchPoint]) -> list[RerankedChunk]:
        if not candidates:
            return []
        if not query.strip():
            raise ValueError("query cannot be empty")
        scores = self.rerank_pairs([(query, candidate) for candidate in candidates])
        ranked = [
            RerankedChunk(candidate, score)
            for candidate, score in zip(candidates, scores, strict=True)
        ]
        return sorted(ranked, key=lambda item: item.score, reverse=True)

    def rerank_pairs(self, pairs: list[tuple[str, VectorSearchPoint]]) -> list[float]:
        """Score query/passage pairs in one cross-encoder batch call."""
        if not pairs:
            return []
        if any(not query.strip() for query, _candidate in pairs):
            raise ValueError("query cannot be empty")
        model = self._load_model()
        model_inputs = []
        for query, candidate in pairs:
            payload = candidate.payload
            title = str(payload.get("title", ""))
            headings = payload.get("heading_paths", [])
            heading_text = " | ".join(" > ".join(path) for path in headings if path)
            table_metadata = payload.get("table_metadata") or {}
            table_context = ""
            if table_metadata:
                caption = str(table_metadata.get("caption") or "")
                headers = table_metadata.get("column_headers") or []
                details = []
                if caption:
                    details.append(f"Table caption: {caption}")
                if headers:
                    details.append("Table columns: " + " | ".join(str(header) for header in headers))
                table_context = "\n".join(details)
            table_suffix = f"\n{table_context}" if table_context else ""
            passage = (
                f"Document: {title}\nHeading context: {heading_text}"
                f"{table_suffix}\nPassage: {candidate.text}"
            )
            model_inputs.append((query, passage))
        scores = model.predict(
            model_inputs,
            prompt=self.instruction,
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        if len(scores) != len(pairs):
            raise RuntimeError(f"reranker returned {len(scores)} scores for {len(pairs)} query/passage pairs")
        normalized_scores = []
        for raw_score in scores:
            if isinstance(raw_score, (list, tuple)):
                if len(raw_score) == 1:
                    raw_score = raw_score[0]
                else:
                    raw_score = raw_score[-1]
            score = float(raw_score)
            if not math.isfinite(score):
                raise RuntimeError("reranker returned NaN or infinite score")
            normalized_scores.append(score)
        return normalized_scores

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "reranker dependencies are missing; install traceevo/rag/requirements-medical-knowledge.txt"
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
            "max_length": self.max_length,
        }
        if self.revision:
            kwargs["revision"] = self.revision
        if device == "mps":
            kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
        self._model = CrossEncoder(self.model_name, **kwargs)
        self.device = str(device)
        return self._model
