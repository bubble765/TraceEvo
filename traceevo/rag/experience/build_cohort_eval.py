"""Exploratory leave-source-article-out retrieval over the 40 build cases.

Read-only inputs are the frozen clean features and admitted success store.
Self experiences are excluded before Qdrant search, reranking, and blind labels.
This cohort supplied memories, so results are not independent heldout metrics.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .rarecase_experiment import (
    EXPERIMENT_ROOT, INDEPENDENT_OUT, INDEPENDENT_PREPARED, _json, _jsonl,
    _sha256, _write_csv, _write_json,
)
from .retrieval_eval import evaluate, retrieval_metrics

DEFAULT_OUT = EXPERIMENT_ROOT / "build_cohort_retrieval_40_v1"
MEMORY_DIR = INDEPENDENT_OUT / "clean_initial_plan_memory_store_v1"


def excluded_memories(query: dict[str, Any], memories: list[dict[str, Any]]) -> list[str]:
    source = str(query["source_record_id"])
    article = source.split("-", 1)[0]
    text = query["initial_presentation"]
    return sorted(memory["memory_id"] for memory in memories if (
        str(memory.get("retrieval_source_record_id") or "").split("-", 1)[0] == article
        or memory["task_retrieval_key"] == text
    ))


def prepare_queries(out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    from .rarecase_adapter import (
        _EARLY_TEST_RE, _INITIAL_CLINICAL_CUE_RE, _RETRIEVAL_CUTOFF_RE, pretest_retrieval_text,
    )
    memories = [_json(path) for path in sorted((MEMORY_DIR / "success").glob("*.json"))]
    if not memories:
        raise ValueError("clean admitted successes are required")
    inputs = [
        ("pilot", EXPERIMENT_ROOT / "prepared_clean_v22/retrieval_build_features_clean.jsonl"),
        ("independent", INDEPENDENT_PREPARED / "retrieval_build_features_clean.jsonl"),
    ]
    queries, skipped = [], []
    for cohort, path in inputs:
        rows = _jsonl(path)
        if len(rows) != 20:
            raise ValueError(f"{cohort} must contain exactly 20 build cases")
        for row in rows:
            query = {"query_id": f"build-{cohort}-{row['case_index']:03d}",
                     "cohort": cohort, "case_index": row["case_index"],
                     "source_record_id": row["source_record_id"]}
            if not row["retrieval_eligible"]:
                skipped.append(dict(query, reason=row["retrieval_exclusion_reason"]))
                continue
            raw = row["retrieval_text"]
            if hashlib.sha256(raw.encode()).hexdigest() != row["retrieval_text_sha256"]:
                raise ValueError("feature hash mismatch")
            text = pretest_retrieval_text(raw)["text"]
            if (len(text) < 50 or not _INITIAL_CLINICAL_CUE_RE.search(text)
                    or _EARLY_TEST_RE.search(text) or _RETRIEVAL_CUTOFF_RE.search(text)):
                skipped.append(dict(query, reason="current_cleaning_rejected_feature"))
                continue
            query["initial_presentation"] = text
            query["excluded_experience_ids"] = excluded_memories(query, memories)
            queries.append(query)
    if len({query["source_record_id"] for query in queries}) != len(queries):
        raise ValueError("build query records must be unique")
    manifest = {
        "requested_cases": 40, "valid_queries": len(queries), "skipped_cases": skipped,
        "corpus_successes": len(memories), "self_experience_exclusions": sum(
            len(query["excluded_experience_ids"]) for query in queries),
        "policy": "exclude same source article and identical presentation before retrieval and annotation",
        "input_features": [{"cohort": tag, "path": str(path), "sha256": _sha256(path)}
                           for tag, path in inputs],
        "memory_files": {path.name: _sha256(path) for path in sorted((MEMORY_DIR / "success").glob("*.json"))},
        "evaluation_type": "exploratory build-cohort leave-source-article-out; not independent heldout",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "query_manifest.json"
    query_path = out_dir / "queries.jsonl"
    if manifest_path.exists() or query_path.exists():
        if _json(manifest_path) != manifest or _jsonl(query_path) != queries:
            raise ValueError("refusing to change existing cohort evaluation inputs")
    else:
        _write_json(manifest_path, manifest)
        with query_path.open("x", encoding="utf-8") as stream:
            for query in queries:
                stream.write(json.dumps(query, ensure_ascii=False) + "\n")
    return manifest


class ExcludingMemorySource:
    def __init__(self, source: Any, excluded: list[str]):
        self.source, self.excluded = source, set(excluded)

    def entries(self, partition: str) -> list[dict[str, Any]]:
        return [entry for entry in self.source.entries(partition)
                if entry["memory_id"] not in self.excluded]

    def audit_links(self) -> list[dict[str, Any]]:
        return self.source.audit_links()


def rank_queries(out_dir: Path = DEFAULT_OUT, device: str = "mps") -> dict[str, Any]:
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.rag.medical_knowledge.reranker import Qwen3Reranker
    from .embedding import Qwen3ExperienceEmbedder
    from .indexer import ExperienceIndexer
    from .prompts import EXPERIENCE_RERANK_INSTRUCTION
    from .retriever import ExperienceRetriever
    from .vector_store import QdrantExperienceStore

    manifest = prepare_queries(out_dir)
    query_path = out_dir / "queries.jsonl"
    summaries_path = out_dir / "ranking_summary.json"
    if summaries_path.is_file():
        summary = _json(summaries_path)
        if summary["query_file_sha256"] != _sha256(query_path):
            raise ValueError("cached ranking inputs changed")
        for name, digest in summary["ranking_hashes"].items():
            if _sha256(out_dir / name) != digest:
                raise ValueError("cached ranking results changed")
        return summary
    memories = MemoryStore(str(MEMORY_DIR), enabled=True, initial_plan_partitions=("success",))
    embedder = Qwen3ExperienceEmbedder(device=device)
    store = QdrantExperienceStore(out_dir / "qdrant", collection_name="traceevo_build_cohort_40_v1",
                                  dimension=embedder.dimension)
    reranker = Qwen3Reranker(device=device, instruction=EXPERIENCE_RERANK_INSTRUCTION)
    ranked, vector_ranked = [], []
    try:
        indexed = ExperienceIndexer(embedder, store).index_memory_store(memories)
        for query in _jsonl(query_path):
            filtered = ExcludingMemorySource(memories, query["excluded_experience_ids"])
            retriever = ExperienceRetriever(
                embedder, store, filtered, reranker=reranker, retrieve_k=20,
                final_k=manifest["corpus_successes"], min_reranker_score=-1_000_000.0)
            hits = retriever.retrieve(query["initial_presentation"], query_id=query["query_id"],
                                      channel="initial_plan").hits
            if any(hit.source_id in query["excluded_experience_ids"] for hit in hits):
                raise ValueError("self experience survived retrieval filtering")
            for rank, hit in enumerate(hits, 1):
                ranked.append({"query_id": query["query_id"], "experience_id": hit.source_id,
                               "rank": rank, "reranker_score": hit.metadata["reranker_score"],
                               "vector_score": hit.metadata["vector_score"]})
            for rank, hit in enumerate(sorted(hits, key=lambda hit: hit.metadata["vector_score"],
                                              reverse=True), 1):
                vector_ranked.append({"query_id": query["query_id"], "experience_id": hit.source_id,
                                      "rank": rank, "vector_score": hit.metadata["vector_score"]})
            print(f"{query['query_id']}: {len(hits)} candidates, self excluded", flush=True)
        _write_csv(out_dir / "rankings.csv", ranked)
        _write_csv(out_dir / "vector_only_rankings.csv", vector_ranked)
        summary = {"valid_queries": manifest["valid_queries"], "indexed_successes": indexed.validated_success,
                   "query_file_sha256": _sha256(query_path),
                   "ranking_hashes": {name: _sha256(out_dir / name)
                                      for name in ("rankings.csv", "vector_only_rankings.csv")},
                   "final_k_for_metrics": 5, "reranker_threshold": "disabled; exploratory ranking only"}
        _write_json(summaries_path, summary)
        return summary
    finally:
        store.close()


def summarize(out_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    manifest = prepare_queries(out_dir)
    annotation = _json(out_dir / "relevance/retrieval_metrics_model_annotated.json")
    if annotation is None:
        raise ValueError("complete relevance annotation before summarizing")
    ranking_summary = _json(out_dir / "ranking_summary.json")
    if (annotation["query_file_sha256"] != _sha256(out_dir / "queries.jsonl")
            or annotation["ranking_file_sha256"] != _sha256(out_dir / "rankings.csv")):
        raise ValueError("annotation and current retrieval inputs disagree")
    if ranking_summary is None or any(
        _sha256(out_dir / name) != digest
        for name, digest in ranking_summary["ranking_hashes"].items()
    ):
        raise ValueError("ranking artifacts changed")
    labels = _jsonl(out_dir / "relevance/relevance_labels.jsonl")
    query_ids = sorted({label["query_id"] for label in labels})
    if len(query_ids) != annotation["fully_annotated_queries"]:
        raise ValueError("incomplete annotation summary")
    comparisons = {}
    for name, grade in (("partial_or_strong", 1), ("strong", 2)):
        conditions = {}
        for condition, filename in (("vector_only", "vector_only_rankings.csv"),
                                    ("reranked", "rankings.csv")):
            with (out_dir / filename).open(encoding="utf-8", newline="") as stream:
                rankings = list(csv.DictReader(stream))
            conditions[condition] = retrieval_metrics(query_ids, labels, rankings, minimum_relevance=grade)
        before, after = conditions["vector_only"]["recall_at_5"], conditions["reranked"]["recall_at_5"]
        conditions["recall_at_5_delta"] = {"absolute": after-before if before is not None and after is not None else None,
                                            "relative": (after-before)/before if before and after is not None else None}
        comparisons[name] = conditions
    rows = []
    with (out_dir / "rankings.csv").open(encoding="utf-8", newline="") as stream:
        rankings = list(csv.DictReader(stream))
    for query in _jsonl(out_dir / "queries.jsonl"):
        relevant = {label["memory_id"] for label in labels
                    if label["query_id"] == query["query_id"] and label["relevance"] >= 1}
        hits = [row["experience_id"] for row in rankings
                if row["query_id"] == query["query_id"] and int(row["rank"]) <= 5
                and row["experience_id"] in relevant]
        rows.append({"query_id": query["query_id"], "source_record_id": query["source_record_id"],
                     "annotated": query["query_id"] in query_ids,
                     "available_relevant_experiences": len(relevant), "top5_relevant_hits": len(hits),
                     "hit_experience_ids": "|".join(hits),
                     "recall_at_5": len(hits)/len(relevant) if relevant else "",
                     "excluded_self_ids": "|".join(query["excluded_experience_ids"])})
    rows.extend({"query_id": row["query_id"], "source_record_id": row["source_record_id"],
                 "annotated": False, "skip_reason": row["reason"]} for row in manifest["skipped_cases"])
    _write_csv(out_dir / "case_results.csv", rows)
    summary = {"requested_cases": 40, "valid_queries": manifest["valid_queries"],
               "skipped_cases": manifest["skipped_cases"], "fully_annotated_queries": len(query_ids),
               "annotation_failures": annotation["model_annotation_failures"],
               "corpus_successes": manifest["corpus_successes"], "comparisons": comparisons,
               "limitations": ["build-cohort leave-source-article-out, not independent heldout",
                               "blind LLM labels, clinical expert review pending",
                               "threshold disabled; no diagnosis improvement measurement"],
               "query_file_sha256": _sha256(out_dir / "queries.jsonl"),
               "labels_file_sha256": _sha256(out_dir / "relevance/relevance_labels.jsonl")}
    _write_json(out_dir / "metrics.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("prepare", "rank", "annotate", "summarize"), required=True)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    if args.phase == "prepare":
        result = prepare_queries(out)
    elif args.phase == "rank":
        result = rank_queries(out, args.device)
    elif args.phase == "annotate":
        prepare_queries(out)
        result = evaluate(memory_dir=MEMORY_DIR, query_path=out / "queries.jsonl",
                          rankings_path=out / "rankings.csv", output_dir=out / "relevance",
                          retry_failed=args.retry_failed)
    else:
        result = summarize(out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
