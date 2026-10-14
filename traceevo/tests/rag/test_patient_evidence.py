from __future__ import annotations

import csv
import json

import pytest

from traceevo.rag.common.schemas import PatientEvidenceRecord, QueryPlan, QueryMode, RagStore, SubQuery
from traceevo.rag.patient_evidence.import_diagnosisarena import import_csv, iter_csv_records
from traceevo.rag.patient_evidence.compat import CompatibleEvidenceRetriever
from traceevo.rag.patient_evidence.normalizer import (
    extract_exam_types,
    normalize_exam_type,
    normalize_modality,
)
from traceevo.rag.patient_evidence.records import make_record
from traceevo.rag.patient_evidence.retriever import PatientEvidenceRetriever
from traceevo.rag.patient_evidence.store import PatientEvidenceStore


def _record(task_id: str, exam_type: str, text: str):
    modality = {
        "mri": "imaging", "xray": "imaging", "cbc": "laboratory",
    }[exam_type]
    return make_record(task_id=task_id, modality=modality,
                       exam_type=exam_type, title=exam_type.upper(), text=text)


def test_query_plan_schema_enforces_one_to_four_subqueries():
    single = QueryPlan(
        mode=QueryMode.SINGLE, store=RagStore.PATIENT_EVIDENCE,
        queries=[SubQuery(query_id="q1", query_text="MRI", retrieval_goal="find MRI")],
    )
    assert single.mode is QueryMode.SINGLE
    with pytest.raises(ValueError):
        QueryPlan(mode="decompose", store="patient_evidence", queries=[])
    with pytest.raises(ValueError):
        QueryPlan(
            mode="decompose", store="patient_evidence",
            queries=[SubQuery(query_id=f"q{i}", query_text="x", retrieval_goal="y")
                     for i in range(5)],
        )


def test_exam_and_modality_aliases_are_deterministic():
    assert normalize_exam_type("MRI") == "mri"
    assert normalize_exam_type("magnetic resonance imaging") == "mri"
    assert normalize_exam_type("optical coherence tomography") == "oct"
    assert normalize_exam_type("光学相干断层扫描") == "oct"
    assert normalize_exam_type("CT angiography") == "cta"
    assert extract_exam_types("CT angiography and brain MRI") == ["cta", "mri"]
    assert normalize_exam_type("MRI / CT") is None
    assert normalize_modality("Radiology") == "imaging"
    assert normalize_modality("MRI and CT") == "imaging"
    assert normalize_modality("检验") == "laboratory"


def test_sqlite_retrieval_is_task_scoped_and_exact(tmp_path):
    db = tmp_path / "patient-evidence.sqlite3"
    with PatientEvidenceStore(db) as store:
        store.upsert_records([
            _record("case-0", "mri", "MRI showed edema."),
            _record("case-0", "xray", "X-ray showed no fracture."),
            _record("case-0", "cbc", "CBC within reference range."),
            _record("case-1", "mri", "Different patient MRI."),
        ])
        retriever = PatientEvidenceRetriever(store, max_results=10)

        mri = retriever.retrieve(task_id="case-0", query="What did MRI show?")
        assert [r.exam_type for r in mri] == ["mri"]
        assert all(r.task_id == "case-0" for r in mri)

        imaging = retriever.retrieve(task_id="case-0", modality="radiology")
        assert {r.exam_type for r in imaging} == {"mri", "xray"}
        assert len(retriever.retrieve(task_id="case-0")) == 3
        assert retriever.retrieve(task_id="case-0", exam_type="unknown scan") == []
        assert retriever.retrieve(task_id="case-0", modality="unmapped modality") == []
        assert retriever.retrieve(task_id="case-missing") == []
        assert store.count(task_id="case-1") == 1
        with pytest.raises(ValueError, match="decompose it into subqueries"):
            retriever.retrieve(task_id="case-0", query="Compare MRI and CT")

        with pytest.raises(ValueError, match="task_id is mandatory"):
            retriever.retrieve(task_id="")


def test_retrieval_is_finite_even_when_no_filters_are_given(tmp_path):
    with PatientEvidenceStore(tmp_path / "patient.sqlite3") as store:
        store.upsert_records([
            make_record(task_id="case-0", modality="unknown",
                        exam_type="diagnostic_tests", text=f"Finding {i}")
            for i in range(25)
        ])
        assert len(PatientEvidenceRetriever(store, max_results=7)
                   .retrieve(task_id="case-0")) == 7


def test_diagnosisarena_import_uses_only_diagnostic_tests_and_is_idempotent(tmp_path):
    csv_path = tmp_path / "input.csv"
    json_tests = json.dumps({
        "Imaging Studies": [
            {"Test Type": "Chest X-ray", "Image Title": "X-ray imaging",
             "Image Description": "No focal opacity."},
            {"Test Type": "CT Scan", "Image Title": "CT scan",
             "Image Description": "Small pleural effusion."},
        ],
    })
    plain_tests = (
        "- Imaging Studies: 1. Brain MRI showed a small lesion. "
        "2. CT angiography showed no stenosis. - Laboratory Tests: CBC was normal."
    )
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "Case Information", "Physical Examination", "Diagnostic Tests",
            "Final Diagnosis", "Options", "Right Option",
        ])
        writer.writeheader()
        writer.writerow({
            "id": 26, "Diagnostic Tests": json_tests,
            "Final Diagnosis": "GOLD_SECRET", "Options": "OPTION_SECRET",
            "Right Option": "B",
        })
        writer.writerow({
            "id": 688, "Diagnostic Tests": plain_tests,
            "Final Diagnosis": "OTHER_GOLD_SECRET", "Options": "OTHER_OPTIONS",
            "Right Option": "A",
        })

    imported = list(iter_csv_records(csv_path))
    assert [case for case, _records in imported] == [0, 1]
    assert {r.task_id for _case, records in imported for r in records} == {
        "case-0", "case-1",
    }
    case0_types = {r.exam_type for r in imported[0][1]}
    assert {"xray", "ct"} <= case0_types
    case1_types = {r.exam_type for r in imported[1][1]}
    assert {"mri", "cta", "cbc"} <= case1_types
    serialized = json.dumps(
        [r.model_dump() for _case, records in imported for r in records],
        ensure_ascii=False,
    )
    assert "GOLD_SECRET" not in serialized
    assert "OPTION_SECRET" not in serialized

    db = tmp_path / "import.sqlite3"
    first = import_csv(csv_path, db)
    second = import_csv(csv_path, db)
    assert first["records_written"] == second["records_written"]
    with PatientEvidenceStore(db) as store:
        assert store.count() == first["records_written"]
        assert {r.task_id for r in store.retrieve(task_id="case-0")} == {"case-0"}


def test_record_ids_and_hashes_are_stable_and_content_sensitive():
    first = _record("case-9", "mri", "MRI result A")
    same = _record("case-9", "mri", "MRI result A")
    changed = _record("case-9", "mri", "MRI result B")
    assert first.record_id == same.record_id
    assert first.record_hash == same.record_hash
    assert first.record_id != changed.record_id
    assert len(first.record_hash) == 64
    with pytest.raises(ValueError):
        PatientEvidenceRecord(
            record_id="bad", task_id="case-9", modality="imaging",
            exam_type="mri", text="data", record_hash="not-a-hash",
        )


def test_legacy_compatible_switch_defaults_are_explicit(tmp_path):
    calls = []

    def legacy(query, evidence):
        calls.append((query, evidence))
        return "legacy result"

    old_path = CompatibleEvidenceRetriever(mode="legacy", legacy_retriever=legacy)
    assert old_path("MRI?", ["source text"]) == "legacy result"
    assert calls == [("MRI?", ["source text"])]
    assert old_path.last_source_ids == ()

    with PatientEvidenceStore(tmp_path / "switch.sqlite3") as store:
        record = _record("case-0", "mri", "MRI report text")
        store.upsert_records([record, _record("case-1", "mri", "other patient")])
        patient_path = CompatibleEvidenceRetriever(
            mode="patient_evidence", legacy_retriever=legacy,
            task_id="case-0", patient_retriever=PatientEvidenceRetriever(store),
        )
        assert patient_path("MRI?") == "MRI report text"
        assert patient_path.last_source_ids == (record.record_id,)

    with pytest.raises(ValueError, match="requires a task_id"):
        CompatibleEvidenceRetriever(mode="patient_evidence", legacy_retriever=legacy)
