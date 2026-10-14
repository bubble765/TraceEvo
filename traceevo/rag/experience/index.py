"""Index audited success/failure memory partitions into Experience Qdrant."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from traceevo.rag.medical_knowledge.embedding import QWEN3_EMBEDDING_REVISION

from .embedding import Qwen3ExperienceEmbedder
from .indexer import ExperienceIndexer
from .vector_store import QdrantExperienceStore


class _ReadOnlyMemorySource:
    """Read exactly the two admitted partitions without initializing a store."""

    def __init__(self, root: Path):
        self.root = root

    def entries(self, partition: str) -> list[dict]:
        if partition not in {"success", "failure"}:
            raise ValueError("only admitted partitions are readable by the indexer")
        directory = self.root / partition
        if not directory.is_dir():
            return []
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]

    def audit_links(self) -> list[dict]:
        path = self.root / "memory_audit_links.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-store", required=True,
                        help="existing audited MemoryStore root; never points at a run artifact file")
    parser.add_argument("--qdrant-dir", default="traceevo_runs/rag/qdrant_experience_v1")
    parser.add_argument("--collection", default="traceevo_experience_v1")
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--revision", default=QWEN3_EMBEDDING_REVISION,
                        help="pinned Hugging Face commit")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    memory_root = Path(args.memory_store)
    if not memory_root.is_dir():
        raise ValueError(f"memory store directory does not exist: {memory_root}")
    if not (memory_root / "memory_audit_links.jsonl").is_file():
        raise ValueError("memory store has no admission audit-link file; refusing to index it")
    if not any((memory_root / partition).is_dir() for partition in ("success", "failure")):
        raise ValueError("memory store has no success/failure admission partitions")

    # Read source memory files without constructing MemoryStore (whose normal
    # constructor ensures partition folders exist). Indexing is read-only on
    # the source; only the separate Qdrant directory is written.
    memory_store = _ReadOnlyMemorySource(memory_root)
    embedder = Qwen3ExperienceEmbedder(
        args.model,
        dimension=args.dimension,
        batch_size=args.batch_size,
        device=args.device,
        normalize=True,
        revision=args.revision,
    )
    vector_store = QdrantExperienceStore(
        args.qdrant_dir,
        collection_name=args.collection,
        dimension=args.dimension,
    )
    try:
        summary = ExperienceIndexer(embedder, vector_store).index_memory_store(memory_store)
        print(
            f"indexed={summary.indexed} validated_success={summary.validated_success} "
            f"validated_failure={summary.validated_failure} dimension={args.dimension} "
            f"qdrant={args.qdrant_dir} collection={args.collection}"
        )
    finally:
        vector_store.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"experience indexing failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
