import pytest

from traceevo.rag.medical_knowledge.retrieval_eval import compute_metrics, lexical_scores, validate_labels
from traceevo.rag.medical_knowledge.calibration import select_threshold


def test_judge_labels_cover_pool_and_need_exact_support_quotes():
    passages = [{"chunk_id": "a", "passage": "MRI can detect marrow edema."}]
    raw = {"labels": [{"chunk_id": "a", "relevance": 2, "reason": "direct answer",
                       "support_quote": "detect marrow edema"}]}
    assert validate_labels(raw, passages)[0]["relevance"] == 2
    raw["labels"][0]["support_quote"] = "detect fracture with certainty"
    with pytest.raises(ValueError, match="exact passage quote"):
        validate_labels(raw, passages)
    raw["labels"][0]["relevance"] = True
    with pytest.raises(ValueError, match="integer"):
        validate_labels(raw, passages)


def test_duplicate_or_missing_judge_ids_are_rejected():
    passages = [{"chunk_id": "a", "passage": "text"}, {"chunk_id": "b", "passage": "text"}]
    label = {"chunk_id": "a", "relevance": 0, "reason": "unrelated", "support_quote": ""}
    with pytest.raises(ValueError, match="exactly once"):
        validate_labels({"labels": [label, label]}, passages)


def fixture():
    pools = [{"query_id": q, "passages": [{"chunk_id": c} for c in "abcd"]} for q in ("q1", "q2")]
    labels = [{"query_id": q, "chunk_id": c, "relevance": 2 if q == "q1" and c in "ab" else 0}
              for q in ("q1", "q2") for c in "abcd"]
    rankings = [{"query_id": q, "method": "reranked", "chunk_id": c, "rank": rank}
                for q in ("q1", "q2") for rank, c in enumerate("cadb", 1)]
    return pools, labels, rankings


def test_precision_and_pooled_recall_have_different_denominators():
    result = compute_metrics(*fixture(), method="reranked")
    assert result["precision_at_4"] == .25
    assert result["pooled_recall_at_4"] == 1
    assert result["micro_pooled_recall_at_4"] == 1
    assert result["relevant_hits_at_4"] == 2
    assert result["queries_hit_at_4"] == 1
    assert result["null_queries"] == ["q2"]


def test_incomplete_labels_exclude_whole_query_and_background_is_not_direct():
    pools, labels, rankings = fixture()
    labels = [label for label in labels if not (label["query_id"] == "q1" and label["chunk_id"] == "a")]
    result = compute_metrics(pools, labels, rankings, method="reranked")
    assert result["incomplete_queries"] == ["q1"]
    assert result["n_queries"] == 1 and result["pooled_recall_at_4"] is None
    pools, labels, rankings = fixture()
    for label in labels:
        if label["relevance"] == 2:
            label["relevance"] = 1
    result = compute_metrics(pools, labels, rankings, method="reranked")
    assert result["precision_at_4"] == 0 and result["relevant_pairs"] == 0


def test_lexical_reference_pool_prefers_discriminative_terms():
    scores = lexical_scores("Ottawa ankle rule fracture", ["Ottawa ankle rule excludes fracture", "urine culture limitations"])
    assert scores[0] > scores[1]


def test_threshold_calibration_separates_positive_and_negative_logits():
    result = select_threshold([(-8, False), (-7, False), (-2, True), (1, True)])
    assert -7 < result["threshold"] < -2
    assert result["f1"] == 1
    with pytest.raises(ValueError):
        select_threshold([(float("nan"), True), (0, False)])
    with pytest.raises(ValueError):
        select_threshold([(1, True)])
