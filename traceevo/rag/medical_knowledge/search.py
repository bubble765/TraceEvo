"""Search medical knowledge with document routing, local child search, and global reranking."""
from __future__ import annotations

import argparse
import sys

from .context import DocumentParagraphStore, DocumentSectionStore
from .embedding import QWEN3_EMBEDDING_REVISION, Qwen3Embedder
from .retriever import MedicalKnowledgeRetriever
from .reranker import QWEN3_RERANKER_REVISION, Qwen3Reranker
from .splitter import Qwen3TokenizerCounter
from .vector_store import QdrantMedicalKnowledgeStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="medical knowledge information need")
    parser.add_argument("--data-dir", default="traceevo_runs/rag/medical_knowledge")
    parser.add_argument("--qdrant-dir", default="traceevo_runs/rag/qdrant_v5")
    parser.add_argument("--collection", default="traceevo_medical_knowledge_v5")
    parser.add_argument("--schema-version", choices=["schema-v4", "schema-v5"], default="schema-v5")
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--revision", default=QWEN3_EMBEDDING_REVISION)
    parser.add_argument("--reranker-model", default="Qwen/Qwen3-Reranker-0.6B")
    parser.add_argument("--reranker-revision", default=QWEN3_RERANKER_REVISION)
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--top-k", type=int, default=4)
    parser.add_argument("--document-top-k", type=int, default=5)
    parser.add_argument("--children-per-document", type=int, default=4)
    parser.add_argument("--max-context-tokens", type=int, default=1200)
    parser.add_argument("--max-anchor-tokens", type=int, default=800)
    parser.add_argument("--max-neighbor-tokens", type=int, default=200)
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    parser.add_argument("--reranker-device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    args = parser.parse_args(argv)
    embedder = Qwen3Embedder(args.model, dimension=args.dimension, device=args.device, revision=args.revision)
    if args.schema_version == "schema-v5":
        context_store = DocumentSectionStore(
            args.data_dir,
            token_counter=Qwen3TokenizerCounter(args.model, revision=args.revision),
        )
        context_store_kwargs = {"section_store": context_store}
    else:
        context_store = DocumentParagraphStore(args.data_dir, schema_version="schema-v4")
        context_store_kwargs = {"paragraph_store": context_store}
    reranker = Qwen3Reranker(args.reranker_model, revision=args.reranker_revision, device=args.reranker_device)
    with QdrantMedicalKnowledgeStore(
        args.qdrant_dir, collection_name=args.collection, dimension=args.dimension
    ) as store:
        retriever = MedicalKnowledgeRetriever(
            embedder,
            store,
            final_k=args.top_k,
            document_candidate_k=args.document_top_k,
            child_candidates_per_document=args.children_per_document,
            reranker=reranker,
            **context_store_kwargs,
            max_context_tokens=args.max_context_tokens,
            max_anchor_tokens=args.max_anchor_tokens,
            max_neighbor_tokens=args.max_neighbor_tokens,
        )
        result = retriever.retrieve(args.query, query_id="manual-query", limit=args.top_k)
        print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"medical-knowledge search failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
