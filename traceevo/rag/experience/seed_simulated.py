"""Seed a clearly isolated, non-production Experience demo collection."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from traceevo.rag.medical_knowledge.embedding import QWEN3_EMBEDDING_REVISION

from .embedding import Qwen3ExperienceEmbedder
from .simulated_schema import SimulatedExperienceRecord
from .simulated_store import QdrantSimulatedExperienceStore


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "simulated_experiences.jsonl"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qdrant-dir", default="traceevo_runs/rag/qdrant_experience_simulated_v1")
    parser.add_argument("--collection", default="traceevo_experience_simulated_v1")
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--revision", default=QWEN3_EMBEDDING_REVISION)
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", default="auto", choices=["auto", "mps", "cuda", "cpu"])
    return parser


def load_simulated_records(path: Path = FIXTURE_PATH) -> list[SimulatedExperienceRecord]:
    records = [
        SimulatedExperienceRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [record.simulation_id for record in records]
    if not records or len(ids) != len(set(ids)):
        raise ValueError("simulation fixture must contain records with unique IDs")
    if any(record.online_eligible or not record.simulation_only for record in records):
        raise ValueError("simulation fixtures may never be marked online eligible")
    return records


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = load_simulated_records()
    embedder = Qwen3ExperienceEmbedder(
        args.model,
        dimension=args.dimension,
        batch_size=args.batch_size,
        device=args.device,
        normalize=True,
        revision=args.revision,
    )
    store = QdrantSimulatedExperienceStore(
        args.qdrant_dir,
        collection_name=args.collection,
        dimension=args.dimension,
    )
    try:
        vectors = embedder.embed_documents([record.task_feature_summary for record in records])
        if len(vectors) != len(records):
            raise RuntimeError("experience embedder returned the wrong number of vectors")
        written = store.upsert(records, vectors)
        print(
            f"seeded_simulated={written} success_patterns="
            f"{sum(r.simulation_type == 'simulated_success_pattern' for r in records)} "
            f"prevention_patterns={sum(r.simulation_type == 'simulated_prevention_pattern' for r in records)} "
            f"online_eligible=0 dimension={args.dimension} qdrant={args.qdrant_dir} "
            f"collection={args.collection}"
        )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"simulated Experience seeding failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
