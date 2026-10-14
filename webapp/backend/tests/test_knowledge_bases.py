from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from webapp.backend.app import app
from webapp.backend.services import knowledge_bases as catalog


def test_knowledge_base_catalog_shows_real_samples_before_scale_counts():
    response = TestClient(app).get("/api/knowledge-bases")
    assert response.status_code == 200, response.text
    payload = response.json()
    bases = {item["id"]: item for item in payload["knowledgeBases"]}
    assert set(bases) == {"patient_evidence", "medical_knowledge", "experience"}

    patient = bases["patient_evidence"]
    assert patient["recordCount"] == 655
    assert patient["taskCount"] == 138
    assert patient["distributions"]
    assert len(patient["samples"]) == 3
    assert patient["samples"][0]["fields"]
    assert any("肌酐" in field["value"] for field in patient["samples"][0]["fields"])
    assert "Corynebacterium" not in str(patient["samples"])
    assert "record_id" not in response.text
    assert "task_id" not in response.text

    medical = bases["medical_knowledge"]
    assert medical["recordCount"] == 50
    medical_distribution = {row["label"]: row["count"] for row in medical["distributions"]}
    assert set(medical_distribution) == {"PMC OA 文献", "原文章节", "原文内容块", "检索子块"}
    assert medical_distribution == {
        "PMC OA 文献": 50,
        "原文章节": 917,
        "原文内容块": 5076,
        "检索子块": 2033,
    }
    assert len(medical["samples"]) == 7
    assert any(
        field["label"] == "主要发现"
        for sample in medical["samples"]
        for field in sample["fields"]
    )
    assert "尿路感染诊断方法" in {sample["title"] for sample in medical["samples"]}
    assert "Effectiveness of implementation" not in str(medical["samples"])

    experience = bases["experience"]
    assert experience["recordCount"] == 32
    assert sum(row["count"] for row in experience["distributions"]) == 32
    assert [sample["title"] for sample in experience["samples"]] == [
        "Case01", "Case02", "防错规则 01", "防错规则 02",
    ]
    assert any(field["label"] == "初始症状"
               for field in experience["samples"][0]["fields"])
    assert any(field["label"] == "协作策略"
               for field in experience["samples"][0]["fields"])
    assert "workflow_profile" not in response.text
    assert '"guidance"' not in response.text

    serialized = response.text
    for removed in (
        "覆盖领域", "关键词", "simulation_only", "online_eligible", "模拟样例",
        "离线隔离", "未接入", "保存按任务隔离的检查与报告记录",
        "当前语料是 PMC Open Access 文献", "展示可供未来经验检索使用",
    ):
        assert removed not in serialized
    assert "privacyNote" in serialized


def test_patient_catalog_does_not_leak_untranslated_or_rare_records(tmp_path, monkeypatch):
    db = tmp_path / "patient_evidence.sqlite3"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE patient_evidence (record_id TEXT, task_id TEXT, modality TEXT, "
        "exam_type TEXT, title TEXT, text TEXT, timestamp TEXT, record_hash TEXT)"
    )
    connection.executemany(
        "INSERT INTO patient_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (f"r{i}", f"task-{i % 2}", "imaging", "mri", "SECRET PATIENT TITLE", "TEST BODY FROM SOURCE", None, "hash")
            for i in range(6)
        ] + [
            ("rare", "task-rare", "laboratory", "rare-custom-exam", "SECRET PATIENT TITLE", "TEST BODY FROM SOURCE", None, "hash")
        ],
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(catalog, "PATIENT_EVIDENCE_DB", db)

    payload = catalog.build_knowledge_bases_view().model_dump(mode="json")
    patient = next(item for item in payload["knowledgeBases"] if item["id"] == "patient_evidence")
    assert patient["recordCount"] == 7
    assert patient["taskCount"] == 3
    assert {row["label"] for row in patient["distributions"]} == {"MRI", "其他低频/未分类项目"}
    assert patient["samples"] == []
    assert "TEST BODY FROM SOURCE" not in str(payload)
    assert "SECRET PATIENT TITLE" not in str(payload)
    assert "task-0" not in str(payload)
    assert "rare-custom-exam" not in str(payload)
