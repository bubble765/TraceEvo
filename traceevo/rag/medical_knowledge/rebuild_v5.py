"""Reparse cached PMC BioC raw files into the immutable section-local schema-v5."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .embedding import QWEN3_EMBEDDING_REVISION
from .indexer import load_normalized_corpus
from .parser import sections_from_bioc_payload
from .splitter import Qwen3TokenizerCounter, SectionChunkConfig, SectionChunkSplitter
from .storage import write_section_normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="traceevo_runs/rag/medical_knowledge")
    parser.add_argument("--target-tokens", type=int, default=384)
    parser.add_argument("--max-tokens", type=int, default=480)
    parser.add_argument("--tail-merge-tokens", type=int, default=96)
    parser.add_argument("--tokenizer-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--tokenizer-revision", default=QWEN3_EMBEDDING_REVISION)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    # The v4 layer supplies validated document metadata and raw-file pointers;
    # source files are only read, never replaced.
    corpus = load_normalized_corpus(data_dir, schema_version="schema-v4")
    counter = Qwen3TokenizerCounter(args.tokenizer_model, revision=args.tokenizer_revision)
    splitter = SectionChunkSplitter(
        SectionChunkConfig(
            target_tokens=args.target_tokens,
            max_tokens=args.max_tokens,
            tail_merge_tokens=args.tail_merge_tokens,
        ),
        token_counter=counter,
    )
    for document in corpus.documents.values():
        raw_file = data_dir / document.raw_path
        if not raw_file.is_file():
            raise FileNotFoundError(f"cached BioC raw file is missing for {document.document_id}: {raw_file}")
        payload = json.loads(raw_file.read_text(encoding="utf-8"))
        sections = sections_from_bioc_payload(payload, document_id=document.document_id)
        chunks = splitter.split(document, sections)
        target = write_section_normalized(data_dir, document, sections, chunks)
        print(
            f"{document.document_id}: sections={len(sections)} chunks={len(chunks)} "
            f"tokenizer={counter.name} normalized={target}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"schema-v5 rebuild failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
