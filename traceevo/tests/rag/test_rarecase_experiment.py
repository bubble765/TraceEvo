import json
import hashlib

import pytest

from traceevo.rag.experience.rarecase_adapter import (
    pretest_retrieval_text,
    prepare_independent_cohort,
)
from traceevo.rag.experience.rarecase_experiment import (
    _TRANSIENT_RE,
    _build_clean_success_store,
    _make_seal,
    _reconcile_unsupported_schema_probe,
    _write_json,
    check_seal,
    parse_cases,
)


def test_parse_cases_rejects_duplicates_and_out_of_range():
    assert parse_cases("0-2,4", 5) == [0, 1, 2, 4]
    with pytest.raises(ValueError, match="duplicates"):
        parse_cases("1,1", 3)
    with pytest.raises(ValueError, match="indices"):
        parse_cases("3", 3)


def test_transient_classifier_retries_network_failures_only():
    assert _TRANSIENT_RE.search("HTTP 502 proxy error")
    assert _TRANSIENT_RE.search("LLMTransientError: timeout")
    assert not _TRANSIENT_RE.search("HTTP 400 invalid_request_error")


def test_seal_detects_artifact_changes(tmp_path):
    attempt = tmp_path / "attempt-01"
    artifact_dir = attempt / "traceevo" / "runs" / "rare_000_a01"
    artifact_dir.mkdir(parents=True)
    task = "case-0"
    (artifact_dir / f"{task}_events.jsonl").write_text(
        json.dumps({"event_type": "FINAL_REPORT"}) + "\n", encoding="utf-8")
    (artifact_dir / f"{task}_bindings.json").write_text("{}", encoding="utf-8")
    (artifact_dir / f"{task}_integrity.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8")
    (artifact_dir / f"{task}_replay.json").write_text(
        json.dumps({"all_ok": True}), encoding="utf-8")
    (artifact_dir / f"{task}_summary.json").write_text("{}", encoding="utf-8")
    (artifact_dir / f"{task}_invocations.jsonl").write_text("", encoding="utf-8")

    seal = _make_seal(attempt, artifact_dir, task, 0, "dataset-hash")
    _write_json(attempt / "seal.json", seal)
    assert check_seal(attempt) == (True, "verified")

    (artifact_dir / f"{task}_bindings.json").write_text(
        '{"changed": true}', encoding="utf-8")
    ok, reason = check_seal(attempt)
    assert not ok
    assert reason == f"artifact_changed:{task}_bindings.json"


def test_reconcile_only_proven_no_response_schema_capability_probe(tmp_path):
    attempt = tmp_path / "attempt-01"
    case_log = attempt / "logs_rare_000_a01" / "Case_0" / "run.txt"
    case_log.parent.mkdir(parents=True)
    case_log.write_text(
        "Structured output mode json_schema_strict is unsupported; "
        "falling back to json_object\n"
        "LLM structured output used compatibility mode: json_object\n",
        encoding="utf-8",
    )
    ledger = [
        {"task_id": "case-0", "call_id": "call-1", "stage": "GRAPH_UPDATE",
         "parse_status": "failed", "response_hash": "", "discard_reason": "OTHER"},
        {"task_id": "case-0", "call_id": "call-2", "stage": "GRAPH_UPDATE",
         "parse_status": "accepted", "response_hash": "abc", "discard_reason": "NONE"},
    ]

    reconciled, proof = _reconcile_unsupported_schema_probe(ledger, attempt)

    assert ledger[0]["parse_status"] == "failed"  # sealed source input unchanged
    assert reconciled[0]["parse_status"] == "discarded"
    assert reconciled[0]["discard_reason"] == "UNSUPPORTED_RESPONSE_FORMAT"
    assert proof["accepted_graph_update_call_ids"] == ["call-2"]
    assert proof["raw_sealed_ledger_modified"] is False


def test_reconciliation_does_not_hide_real_failed_model_calls(tmp_path):
    attempt = tmp_path / "attempt-01"
    case_log = attempt / "logs_rare_000_a01" / "Case_0" / "run.txt"
    case_log.parent.mkdir(parents=True)
    case_log.write_text(
        "Structured output mode json_schema_strict is unsupported; "
        "falling back to json_object\n"
        "LLM structured output used compatibility mode: json_object\n",
        encoding="utf-8",
    )
    ledger = [{
        "task_id": "case-0", "call_id": "call-1", "stage": "GRAPH_UPDATE",
        "parse_status": "failed", "response_hash": "nonempty",
        "discard_reason": "PARSER_ERROR",
    }]
    reconciled, proof = _reconcile_unsupported_schema_probe(ledger, attempt)
    assert proof is None
    assert reconciled == ledger


def test_independent_cohort_excludes_all_prior_records_articles_and_diseases(tmp_path):
    source = tmp_path / "rdc.jsonl"
    rows = []
    for index in range(8):
        rows.append({
            "_id": f"{3000 + index}-1",
            "case_report": (
                    f"A patient presented with progressive weakness and developmental delay. "
                    f"The family reported symptom onset during early childhood, with difficulty "
                    f"walking and frequent falls. There is no known family history of these symptoms. "
                    f"The patient remains active at home and attends school with support from family."
            ),
            "test_results": "Laboratory and imaging examinations were performed with results described here.",
            "diagnosis": f"Rare Diagnosis {index}",
            "Orpha_id": str(7000 + index),
            "Orpha_name": f"Rare Condition {index}",
        })
    contaminated = dict(rows[-1], _id="3998-1", Orpha_id="7998",
                        diagnosis="Leaked Example Syndrome",
                        Orpha_name="Leaked Example Syndrome",
                        test_results="The final diagnosis is Leaked Example Syndrome, confirmed by molecular testing.")
    rows.append(contaminated)
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    prior = tmp_path / "prepared"
    prior.mkdir()
    (prior / "selection_manifest.json").write_text(json.dumps({
        "selected_build_ids": ["3000-1"],
        "selected_query_ids": ["3001-1"],
    }), encoding="utf-8")
    (prior.parent / "prepared_clean_v1").mkdir()
    (prior.parent / "prepared_clean_v1" / "clean_retrieval_manifest.json").write_text(
        json.dumps({
            "build_source_ids": ["3002-1"],
            "clean_query_source_ids": ["3999-1"],
        }), encoding="utf-8")

    out = tmp_path / "new_cohort" / "prepared"
    manifest = prepare_independent_cohort(
        source, out, previous_prepared_dirs=[prior], build_n=2, query_n=2,
        seed=7,
    )

    build_ids = set(manifest["selected_build_ids"])
    query_ids = set(manifest["selected_query_ids"])
    assert len(build_ids) == 2
    assert len(query_ids) == 2
    assert not (build_ids | query_ids) & {"3000-1", "3001-1", "3002-1"}
    assert "3998-1" not in build_ids | query_ids
    assert not build_ids & query_ids
    features = [json.loads(line) for line in
                (out / "retrieval_build_features_clean.jsonl").read_text().splitlines()]
    queries = [json.loads(line) for line in
               (out / "retrieval_queries_clean_blinded.jsonl").read_text().splitlines()]
    assert all(row["retrieval_eligible"] and row["retrieval_text"] for row in features)
    assert all("diagnosis" not in row and "test_results" not in row for row in queries)
    assert all(row["source_record_id"] not in {"3000-1", "3001-1", "3002-1"}
               for row in queries)


def test_pretest_cutoff_drops_lab_sentence_and_disease_specific_treatment():
    labs = (
        "A patient presented with a rapidly enlarging painful mass on her back. "
        "The mass was tender on palpation. Routine blood samples taken on admission "
        "showed a white blood cell count of 11,400/µL and hemoglobin of 12.5."
    )
    treatment = (
        "She experienced progressive weakness for several years. "
        "She has been taking riluzole since age 15."
    )
    assert pretest_retrieval_text(labs)["text"] == (
        "A patient presented with a rapidly enlarging painful mass on her back. "
        "The mass was tender on palpation."
    )
    assert pretest_retrieval_text(treatment)["text"] == (
        "She experienced progressive weakness for several years."
    )


def test_pretest_cutoff_drops_functional_and_pathology_results():
    report = (
        "The patient had progressive shoulder weakness and difficulty walking. "
        "Examination showed a steppage gait. Vital capacity decreased from 74% "
        "sitting to 46% supine. Direct immunofluorescence showed IgG deposition."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "The patient had progressive shoulder weakness and difficulty walking. "
        "Examination showed a steppage gait."
    )


def test_pretest_cutoff_drops_treatment_and_prior_diagnostic_assessment():
    report = (
        "A teenager presented with a slowly enlarging chest wall mass. "
        "Initially, the mass was thought to be a subcutaneous abscess. "
        "Current medications included an antibiotic."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "A teenager presented with a slowly enlarging chest wall mass."
    )


def test_pretest_cutoff_drops_post_admission_immunoassay_sentence():
    report = (
        "A child was admitted with fever, headache, vomiting, and a rash. "
        "Upon admission, a fluorescence immunoassay showed elevated IgG antibodies."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "A child was admitted with fever, headache, vomiting, and a rash."
    )


def test_pretest_cutoff_drops_routine_labs_and_empirical_therapy():
    report = (
        "A patient presented with fever and diarrhea. "
        "Routine blood and urine tests were within normal ranges. "
        "Empirical antibiotic therapy was initiated."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "A patient presented with fever and diarrhea."
    )


def test_pretest_cutoff_drops_csf_and_tumor_marker_results():
    report = (
        "The patient developed progressive leg weakness and numbness. "
        "Cerebrospinal fluid examination was negative for malignant cells."
    )
    tumor = (
        "A man reported painful urination and blood in the urine. "
        "The serum prostate-specific antigen level was within the normal range."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "The patient developed progressive leg weakness and numbness."
    )
    assert pretest_retrieval_text(tumor)["text"] == (
        "A man reported painful urination and blood in the urine."
    )


def test_pretest_cutoff_ends_before_medication_and_autoantibody_results():
    report = (
        "A teenager had progressive hair loss over ten years. "
        "Five years ago, he began treatment with levothyroxine after testing "
        "positive for thyroid peroxidase autoantibodies (314."
    )
    assert pretest_retrieval_text(report)["text"] == (
        "A teenager had progressive hair loss over ten years."
    )


def test_pretest_cutoff_drops_genetic_and_visual_test_results():
    genetics = (
        "An infant was admitted due to limb trembling. "
        "Genetic analysis revealed a compound heterozygous mutation."
    )
    vision = (
        "A child presented with decreased vision in the left eye. "
        "Visual exam through Snellen's chart indicated impaired visual fields."
    )
    assert pretest_retrieval_text(genetics)["text"] == (
        "An infant was admitted due to limb trembling."
    )
    assert pretest_retrieval_text(vision)["text"] == (
        "A child presented with decreased vision in the left eye."
    )


def test_pretest_cutoff_uses_full_text_boundaries_for_decimals_and_abbreviations():
    report = (
        "The patient presented with progressive weakness. "
        "A measured value was 12.5 while MRI showed an abnormality."
    )
    abbreviation = (
        "The patient presented with progressive weakness. "
        "She was seen by Dr. Smith before MRI was performed."
    )
    expected = "The patient presented with progressive weakness."
    assert pretest_retrieval_text(report)["text"] == expected
    assert pretest_retrieval_text(abbreviation)["text"] == expected


def test_clean_store_rechecks_old_features_without_modifying_source(tmp_path):
    source_store = tmp_path / "source_store"
    (source_store / "success").mkdir(parents=True)
    presentation = "A patient presented with progressive leg weakness and difficulty walking."
    feature_key = presentation + " Routine blood and urine tests were normal."
    source_entry = {"memory_id": "exp-0", "task_id": "case-0",
                    "source_trace_id": "case-0", "partition": "success",
                    "task_retrieval_key": feature_key}
    entry_path = source_store / "success" / "exp-0.json"
    entry_path.write_text(json.dumps(source_entry), encoding="utf-8")
    (source_store / "memory_audit_links.jsonl").write_text(
        json.dumps({"memory_id": "exp-0", "partition": "success",
                    "source_trace_id": "case-0"}) + "\n", encoding="utf-8")
    feature_path = tmp_path / "features.jsonl"
    feature_path.write_text(json.dumps({
        "case_index": 0, "source_record_id": "3000-1",
        "retrieval_eligible": True, "retrieval_text": feature_key,
        "retrieval_text_sha256": hashlib.sha256(feature_key.encode()).hexdigest(),
    }) + "\n", encoding="utf-8")

    destination = tmp_path / "derived_store"
    manifest = _build_clean_success_store([(source_store, feature_path)], destination)

    derived = json.loads((destination / "success" / "exp-0.json").read_text())
    assert derived["task_retrieval_key"] == presentation
    assert derived["retrieval_source_record_id"] == "3000-1"
    assert json.loads(entry_path.read_text()) == source_entry
    assert manifest["included_successes"] == 1
    assert manifest["included"][0]["feature_recleaned"] is True


def test_outcome_resume_keeps_uncertain_verdict_and_checks_transcript(tmp_path, monkeypatch):
    from traceevo.rag.experience import rarecase_experiment as experiment
    from traceevo.evolution import outcome_verifier
    from traceevo import s2_cases

    out = tmp_path / "run"
    attempt = out / "attempt-01"
    artifacts = attempt / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "case-0_events.jsonl").write_text(json.dumps({
        "event_type": "FINAL_REPORT", "payload": {"prediction": "Predicted condition", "report": "Report"},
    }) + "\n", encoding="utf-8")
    prepared = tmp_path / "prepared"
    (prepared / "label_vault").mkdir(parents=True)
    (prepared / "label_vault" / "build_gold.jsonl").write_text(json.dumps({
        "case_index": 0, "source_record_id": "source-1", "diagnosis": "Reference condition",
    }) + "\n", encoding="utf-8")
    seal = {"task_id": "case-0", "artifact_dir": "artifacts", "sealed_artifact_hash": "seal-hash"}
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment, "_load_sealed_runs", lambda *_args: [(0, attempt, seal)])
    monkeypatch.setattr(s2_cases, "load_env_file", lambda: {})
    monkeypatch.setattr(outcome_verifier, "make_judge_fn", lambda: None)
    calls = []

    def judge(trace, reference, judge_fn, case_index, raw_dir):
        calls.append(case_index)
        raw_path = __import__("pathlib").Path(raw_dir) / "case-0.raw.json"
        raw_path.write_text(json.dumps({"scores": [2, 1, 2]}), encoding="utf-8")
        return {"outcome_verdict": "uncertain", "judge_scores": [2, 1, 2],
                "judge_unanimous": False, "verdict_reason": "mixed scores",
                "reference_sha256": hashlib.sha256(reference.encode()).hexdigest()}

    monkeypatch.setattr(outcome_verifier, "verify_trace", judge)
    assert experiment.verify_outcomes(out, prepared)["verdict_counts"] == {"uncertain": 1}
    assert experiment.verify_outcomes(out, prepared)["verdict_counts"] == {"uncertain": 1}
    assert calls == [0]
    (out / "outcome_judge_raw" / "case-0.raw.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="transcript changed"):
        experiment.verify_outcomes(out, prepared)
