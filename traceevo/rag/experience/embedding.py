"""Qwen3 embedding configuration for task-to-task Experience retrieval."""
from __future__ import annotations

from traceevo.rag.medical_knowledge.embedding import Qwen3Embedder, QWEN3_EMBEDDING_REVISION

from .prompts import EXPERIENCE_QUERY_INSTRUCTION


class Qwen3ExperienceEmbedder(Qwen3Embedder):
    """Use the pinned Qwen model with task-similarity, not medical-KB, prompting."""

    def __init__(
        self,
        model_name: str = "Qwen/Qwen3-Embedding-0.6B",
        *,
        dimension: int = 1024,
        batch_size: int = 8,
        device: str = "auto",
        normalize: bool = True,
        revision: str | None = QWEN3_EMBEDDING_REVISION,
    ) -> None:
        super().__init__(
            model_name,
            dimension=dimension,
            batch_size=batch_size,
            device=device,
            normalize=normalize,
            revision=revision,
            query_instruction=EXPERIENCE_QUERY_INSTRUCTION,
        )
