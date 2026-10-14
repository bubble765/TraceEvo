"""CLI for reproducible PMC OA and explicit WHO-manifest ingestion."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from traceevo.rag.medical_knowledge.licenses import LicensePolicy
from traceevo.rag.medical_knowledge.embedding import QWEN3_EMBEDDING_REVISION
from traceevo.rag.medical_knowledge.parser import paragraphs_from_sections, sections_from_parent_sections
from traceevo.rag.medical_knowledge.source_adapters import PMCOpenAccessAdapter, WHOManifestAdapter
from traceevo.rag.medical_knowledge.splitter import (
    DocumentChunkConfig,
    DocumentChunkSplitter,
    MedicalEntityLexicon,
    Qwen3TokenizerCounter,
    SectionChunkConfig,
    SectionChunkSplitter,
)
from traceevo.rag.medical_knowledge.storage import write_document_level_normalized, write_section_normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmcid", action="append", default=[], help="PMC OA identifier; repeat for multiple articles")
    parser.add_argument("--who-manifest", help="explicit local JSON manifest of WHO raw files and licenses")
    parser.add_argument("--data-dir", default="traceevo_runs/rag/medical_knowledge")
    parser.add_argument("--email", help="optional email sent with NCBI E-utilities requests")
    parser.add_argument("--allow-noncommercial", action="store_true", help="explicitly permit CC-NC source derivatives")
    parser.add_argument("--target-tokens", type=int, default=384)
    parser.add_argument("--max-tokens", type=int, default=480)
    parser.add_argument("--tail-merge-tokens", type=int, default=96)
    parser.add_argument("--schema-version", choices=["schema-v4", "schema-v5"], default="schema-v5")
    parser.add_argument("--tokenizer-model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--tokenizer-revision", default=QWEN3_EMBEDDING_REVISION)
    parser.add_argument("--min-tokens", type=int, default=120, help="schema-v4 compatibility only")
    parser.add_argument(
        "--entity-lexicon",
        help="optional UTF-8 file containing one medical entity/term per line",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pmcid and not args.who_manifest:
        build_parser().error("provide at least one --pmcid or --who-manifest")
    data_dir = Path(args.data_dir)
    policy = LicensePolicy(allow_noncommercial=args.allow_noncommercial)
    sources = []
    if args.pmcid:
        adapter = PMCOpenAccessAdapter(data_dir, license_policy=policy, email=args.email)
        for pmcid in args.pmcid:
            source = adapter.ingest(pmcid)
            sources.append((source.document, source.paragraphs, source.sections))
    if args.who_manifest:
        adapter = WHOManifestAdapter(data_dir, license_policy=policy)
        sources.extend(
            (
                source.document,
                paragraphs_from_sections(source.document.document_id, source.sections),
                sections_from_parent_sections(source.document.document_id, source.sections),
            )
            for source in adapter.ingest_manifest(args.who_manifest)
        )

    for document, paragraphs, sections in sources:
        if args.schema_version == "schema-v5":
            splitter = SectionChunkSplitter(
                SectionChunkConfig(
                    target_tokens=args.target_tokens,
                    max_tokens=args.max_tokens,
                    tail_merge_tokens=args.tail_merge_tokens,
                ),
                token_counter=Qwen3TokenizerCounter(args.tokenizer_model, revision=args.tokenizer_revision),
            )
            chunks = splitter.split(document, sections)
            out = write_section_normalized(data_dir, document, sections, chunks)
            print(
                f"{document.document_id}: sections={len(sections)} chunks={len(chunks)} "
                f"tokenizer={args.tokenizer_model}@{args.tokenizer_revision} "
                f"license={document.license_id} schema=schema-v5 normalized={out}"
            )
        else:
            splitter = DocumentChunkSplitter(
                DocumentChunkConfig(
                    target_tokens=args.target_tokens,
                    max_tokens=args.max_tokens,
                    min_tokens=args.min_tokens,
                ),
                entity_lexicon=(
                    MedicalEntityLexicon.from_file(args.entity_lexicon)
                    if args.entity_lexicon
                    else MedicalEntityLexicon()
                ),
            )
            chunks = splitter.split(document, paragraphs)
            out = write_document_level_normalized(data_dir, document, paragraphs, chunks)
            print(
                f"{document.document_id}: paragraphs={len(paragraphs)} chunks={len(chunks)} "
                f"license={document.license_id} schema=schema-v4 normalized={out}"
            )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"medical-knowledge ingestion failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
