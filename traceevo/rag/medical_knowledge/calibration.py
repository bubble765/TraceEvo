"""Select a logit rejection threshold on calibration queries only."""
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from traceevo.rag.experience.rarecase_experiment import _json, _jsonl, _sha256, _write_json
from .retrieval_eval import OUT, CALIBRATION_QUERIES, QUERIES, compute_metrics


def select_threshold(points):
    if not points or any(not math.isfinite(score) for score, _ in points):
        raise ValueError("finite calibration scores required")
    positives = sum(relevant for _, relevant in points)
    if not positives or positives == len(points):
        raise ValueError("calibration needs positive and negative passage labels")
    values = sorted({score for score, _ in points})
    thresholds = [values[0]-1] + [(a+b)/2 for a, b in zip(values, values[1:])] + [values[-1]+1]
    candidates = []
    for threshold in thresholds:
        accepted = [relevant for score, relevant in points if score >= threshold]
        tp = sum(accepted)
        precision = tp/len(accepted) if accepted else 0
        recall = tp/positives
        f1 = 2*precision*recall/(precision+recall) if precision+recall else 0
        candidates.append({"threshold": threshold, "precision": precision, "recall": recall,
                           "f1": f1, "accepted": len(accepted)})
    return max(candidates, key=lambda x: (x["f1"], x["precision"], x["recall"], -x["threshold"]))


def run(out: Path):
    manifest = _json(out / "input_manifest.json")
    annotation = _json(out / "annotation_summary.json")
    if not annotation or annotation["failures"]:
        raise ValueError("complete all calibration/test annotations first")
    if _sha256(out / "labels.jsonl") != annotation["labels_sha256"]:
        raise ValueError("annotation labels changed")
    for name, digest in _json(out / "ranking_summary.json")["output_hashes"].items():
        if _sha256(out / name) != digest:
            raise ValueError("ranking inputs changed")
    pools, labels = _jsonl(out / "pools.jsonl"), _jsonl(out / "labels.jsonl")
    by_pair = {(x["query_id"], x["chunk_id"]): x["relevance"] == 2 for x in labels}
    with (out / "rankings.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["method"] == "reranked" and int(row["rank"]) <= 4]
    calibration_ids = {q for q, _ in CALIBRATION_QUERIES}
    test_ids = {q for q, _ in QUERIES}
    if calibration_ids & test_ids or {r["query_id"] for r in rows} != calibration_ids | test_ids:
        raise ValueError("query split mismatch")
    if any((row["query_id"], row["chunk_id"]) not in by_pair for row in rows):
        raise ValueError("top4 labels incomplete")
    points = [(float(row["reranker_score"]), by_pair[(row["query_id"], row["chunk_id"])])
              for row in rows if row["query_id"] in calibration_ids]
    choice = select_threshold(points)
    test_pools = [pool for pool in pools if pool["query_id"] in test_ids]
    before_rows = []
    with (OUT / "rankings.csv").open(encoding="utf-8", newline="") as stream:
        before_rows = [r for r in csv.DictReader(stream) if r["method"] == "reranked" and int(r["rank"]) <= 4]
    expanded = [r for r in rows if r["query_id"] in test_ids]
    accepted = [r for r in expanded if float(r["reranker_score"]) >= choice["threshold"]]
    result = {}
    for name, ranked in (("baseline_5x4", before_rows), ("expanded_8x8", expanded), ("expanded_with_rejection", accepted)):
        metrics = compute_metrics(test_pools, labels, ranked, method="reranked")
        relevant = sum(by_pair[(r["query_id"], r["chunk_id"])] for r in ranked)
        metrics["returned_passages"] = len(ranked)
        metrics["returned_precision"] = relevant/len(ranked) if ranked else None
        metrics["queries_returning_any"] = len({r["query_id"] for r in ranked})
        metrics["null_queries_rejected"] = sum(not any(r["query_id"] == q for r in ranked) for q in metrics["null_queries"])
        result[name] = metrics
    artifact = {"threshold": choice["threshold"], "score_kind": "cross_encoder_logit",
                "model": "Qwen/Qwen3-Reranker-0.6B", "documents": 8, "children_per_document": 8,
                "calibration_queries": sorted(calibration_ids), "test_queries_used_for_selection": False,
                "calibration_fit": choice, "label_source": "LLM, clinical review pending",
                "labels_sha256": _sha256(out / "labels.jsonl"), "rankings_sha256": _sha256(out / "rankings.csv"),
                "source_hashes": manifest["source_hashes"], "test_metrics": result}
    _write_json(out / "calibration.json", artifact)
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import json
    print(json.dumps(run(args.out), ensure_ascii=False, indent=2))
