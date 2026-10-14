import json

import pytest

from traceevo.rag.experience.retrieval_eval import _annotation_record, compare_expansion, evaluate, retrieval_metrics, validate_labels
from traceevo.rag.experience.build_cohort_eval import ExcludingMemorySource, excluded_memories
from traceevo.rag.experience.rarecase_experiment import _sha256, _write_csv, _write_json


def test_blind_labels_require_all_ids_once_and_integer_grades():
    valid = {"labels": [{"memory_id": "a", "relevance": 2, "reason": "shared symptoms"},
                        {"memory_id": "b", "relevance": 0, "reason": "different symptoms"}]}
    assert len(validate_labels(valid, {"a", "b"})) == 2
    with pytest.raises(ValueError, match="exactly once"):
        validate_labels(valid, {"a", "b", "c"})
    with pytest.raises(ValueError, match="exactly once"):
        validate_labels({"labels": [valid["labels"][0], valid["labels"][0]]}, {"a", "b"})
    with pytest.raises(ValueError, match="integer"):
        validate_labels({"labels": [{"memory_id": "a", "relevance": True, "reason": "test"}]}, {"a"})


def test_metrics_distinguish_positive_and_null_queries():
    labels = [{"query_id": "q1", "memory_id": "a", "relevance": 2},
              {"query_id": "q1", "memory_id": "b", "relevance": 2},
              {"query_id": "q2", "memory_id": "a", "relevance": 0}]
    rankings = [{"query_id": "q1", "experience_id": memory, "rank": rank}
                for rank, memory in enumerate(["c", "a", "d", "e", "b"], 1)]
    rankings.append({"query_id": "q2", "experience_id": "a", "rank": 1})
    metrics = retrieval_metrics(["q1", "q2"], labels, rankings, minimum_relevance=2)
    assert metrics["precision_at_1"] == 0
    assert metrics["precision_at_2"] == .25
    assert metrics["precision_at_5"] == .2
    assert metrics["recall_at_2"] == .5
    assert metrics["recall_at_5"] == 1
    assert metrics["mrr_at_5"] == .5
    assert metrics["hit_at_5"] == 1
    assert metrics["all_query_hit_at_5"] == .5
    assert metrics["queries_hit_at_5"] == 1
    assert metrics["relevant_hits_at_5"] == 2
    assert metrics["unique_experiences_hit_at_5"] == 2
    assert metrics["micro_recall_at_5"] == 1
    assert metrics["queries_without_relevant_experience"] == 1
    assert metrics["null_queries_returning_experience"] == 1


def test_no_relevant_corpus_items_have_undefined_recall():
    metrics = retrieval_metrics(["q"], [], [], minimum_relevance=2)
    assert metrics["precision_at_5"] == 0
    assert metrics["recall_at_5"] is None
    assert metrics["mrr_at_5"] is None


def test_failed_annotation_retries_preserve_original_and_freeze_valid_grades(tmp_path):
    base = tmp_path / "q.json"
    failed = {"input_sha256": "i", "prompt_sha256": "p", "error": "empty response"}
    base.write_text(json.dumps(failed))
    path, record = _annotation_record(tmp_path, "q", "i", "p")
    assert path == base and record == failed
    retry, record = _annotation_record(tmp_path, "q", "i", "p", retry_failed=True)
    assert retry.name == "q.retry-001.json" and record is None
    valid = dict(failed, error="", labels=[{"relevance": 0}])
    retry.write_text(json.dumps(valid))
    assert json.loads(base.read_text()) == failed
    for retry_failed in (False, True):
        path, record = _annotation_record(tmp_path, "q", "i", "p", retry_failed=retry_failed)
        assert path == retry and record == valid
    with pytest.raises(ValueError, match="input/prompt changed"):
        _annotation_record(tmp_path, "q", "changed", "p", retry_failed=True)


def test_successful_annotation_is_never_rejudged(tmp_path):
    base = tmp_path / "q.json"
    valid = {"input_sha256": "i", "prompt_sha256": "p", "error": ""}
    base.write_text(json.dumps(valid))
    assert _annotation_record(tmp_path, "q", "i", "p", retry_failed=True) == (base, valid)


def test_expansion_comparison_uses_same_relevance_denominator(tmp_path):
    build = tmp_path / "isolated_build"
    evaluation = build / "relevance_evaluation_v1"
    evaluation.mkdir(parents=True)
    label = {"query_id": "q", "memory_id": "new", "relevance": 1}
    (evaluation / "relevance_labels.jsonl").write_text(json.dumps(label) + "\n")
    for suffix, memory, size in (("clean_baseline_v11", "old", 1),
                                 ("clean_independent_v11", "new", 2)):
        _write_csv(build / f"retrieval_rankings_{suffix}.csv", [
            {"query_id": "q", "experience_id": memory, "rank": 1}])
        _write_json(build / f"retrieval_summary_{suffix}.json", {
            "query_file_sha256": "query-hash", "indexed_successes": size})
    annotation = {"fully_annotated_queries": 1, "query_file_sha256": "query-hash",
                  "ranking_file_sha256": _sha256(build / "retrieval_rankings_clean_independent_v11.csv"),
                  "annotation_source": "model", "limitations": ["exploratory"]}
    _write_json(evaluation / "retrieval_metrics_model_annotated.json", annotation)
    result = compare_expansion(tmp_path)
    comparison = result["comparisons"]["partial_or_strong_relevance"]
    assert comparison["baseline"]["queries_with_relevant_experience"] == 1
    assert comparison["baseline"]["recall_at_5"] == 0
    assert comparison["expanded"]["recall_at_5"] == 1
    assert comparison["delta"]["precision_at_1"] == {"absolute": 1, "relative": None}
    annotation["query_file_sha256"] = "changed"
    _write_json(evaluation / "retrieval_metrics_model_annotated.json", annotation)
    with pytest.raises(ValueError, match="same query"):
        compare_expansion(tmp_path)


def test_leave_article_out_uses_source_ids_not_repeated_case_numbers():
    query = {"source_record_id": "123-1", "initial_presentation": "symptoms"}
    memories = [
        {"memory_id": "self", "retrieval_source_record_id": "123-1", "task_retrieval_key": "x"},
        {"memory_id": "same-article", "retrieval_source_record_id": "123-2", "task_retrieval_key": "y"},
        {"memory_id": "duplicate", "retrieval_source_record_id": "456-1", "task_retrieval_key": "symptoms"},
        {"memory_id": "other", "retrieval_source_record_id": "789-1", "task_retrieval_key": "z"},
    ]
    assert excluded_memories(query, memories) == ["duplicate", "same-article", "self"]


def test_excluded_memory_source_filters_before_retrieval():
    class Source:
        def entries(self, partition):
            return [{"memory_id": "self"}, {"memory_id": "other"}]

        def audit_links(self):
            return [{"memory_id": "self"}, {"memory_id": "other"}]

    filtered = ExcludingMemorySource(Source(), ["self"])
    assert filtered.entries("success") == [{"memory_id": "other"}]
    assert len(filtered.audit_links()) == 2


def test_blind_annotation_excludes_self_and_rejects_self_ranking(tmp_path, monkeypatch):
    from traceevo.s2_cases import load_env_file
    import utils.public_function as public

    monkeypatch.setattr("traceevo.s2_cases.load_env_file", lambda: None)
    memory_dir = tmp_path / "memories"
    for memory_id in ("self", "other"):
        _write_json(memory_dir / "success" / f"{memory_id}.json", {
            "memory_id": memory_id, "task_retrieval_key": "initial symptoms",
            "payload": {"strategy": {}, "grounding_constraints": []}})
    query_path = tmp_path / "queries.jsonl"
    query_path.write_text(json.dumps({"query_id": "q", "initial_presentation": "new symptoms",
                                      "excluded_experience_ids": ["self"]}) + "\n")
    ranking_path = tmp_path / "rankings.csv"
    _write_csv(ranking_path, [{"query_id": "q", "experience_id": "other", "rank": 1}])

    def fake_generate(**kwargs):
        prompt = json.loads(kwargs["user_prompt"])
        assert [x["memory_id"] for x in prompt["prior_experiences"]] == ["other"]
        return json.dumps({"labels": [{"memory_id": "other", "relevance": 1,
                                        "reason": "shared features"}]}), {}

    monkeypatch.setattr(public, "llm_generate_response", fake_generate)
    result = evaluate(memory_dir=memory_dir, query_path=query_path, rankings_path=ranking_path,
                      output_dir=tmp_path / "relevance")
    assert result["annotated_pairs"] == 1
    _write_csv(ranking_path, [{"query_id": "q", "experience_id": "self", "rank": 1}])
    with pytest.raises(ValueError, match="excluded for this query"):
        evaluate(memory_dir=memory_dir, query_path=query_path, rankings_path=ranking_path,
                 output_dir=tmp_path / "relevance")
