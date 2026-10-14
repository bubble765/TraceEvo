"""Build/update the document-first medical-knowledge vector collection."""
from __future__ import annotations

import argparse
import sys

from .embedding import QWEN3_EMBEDDING_REVISION, Qwen3Embedder
from .indexer import index_normalized_corpus
from .vector_store import QdrantMedicalKnowledgeStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="traceevo_runs/rag/medical_knowledge")
    parser.add_argument("--qdrant-dir", default="traceevo_runs/rag/qdrant_v5")
    parser.add_argument("--collection", default="traceevo_medical_knowledge_v5")
    parser.add_argument("--schema-version", choices=["schema-v4", "schema-v5"], default="schema-v5")
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--revision", default=QWEN3_EMBEDDING_REVISION, help="pinned Hugging Face commit")
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    embedder = Qwen3Embedder(
        args.model,
        dimension=args.dimension,
        batch_size=args.batch_size,
        device=args.device,
        normalize=True,
        revision=args.revision,
    )
    with QdrantMedicalKnowledgeStore(
        args.qdrant_dir, collection_name=args.collection, dimension=args.dimension
    ) as store:
        summary = index_normalized_corpus(
            args.data_dir,
            embedder=embedder,
            store=store,
            batch_size=args.batch_size,
            schema_version=args.schema_version,
        )
        print(
            f"indexed documents={summary.documents} sections={summary.sections} paragraphs={summary.paragraphs} "
            f"children={summary.child_chunks} document_vectors={summary.document_vectors_upserted} "
            f"child_vectors={summary.child_vectors_upserted} "
            f"dimension={summary.dimension} qdrant={args.qdrant_dir} collection={args.collection}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"medical-knowledge indexing failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
