"""Read-only catalog of real content samples and aggregate store counts."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from .. import config
from ..schemas import KnowledgeBasesView


RAG_ROOT = Path(config.ROOT) / "traceevo_runs" / "rag"
PATIENT_EVIDENCE_DB = RAG_ROOT / "patient_evidence.sqlite3"
MEDICAL_KNOWLEDGE_ROOT_V5 = RAG_ROOT / "medical_knowledge" / "normalized" / "schema-v5"
MEDICAL_KNOWLEDGE_ROOT_V4 = RAG_ROOT / "medical_knowledge" / "normalized" / "schema-v4"
EXPERIENCE_FIXTURE = (
    Path(config.ROOT) / "traceevo" / "rag" / "experience" / "fixtures"
    / "simulated_experiences.jsonl"
)


_MODALITY_LABELS = {
    "imaging": "影像检查",
    "cardiac": "心脏相关检查",
    "pathology": "病理检查",
    "laboratory": "实验室检查",
    "procedure": "操作/介入记录",
    "unknown": "未细分检查记录",
}
_EXAM_LABELS = {
    "diagnostic_tests": "综合检查记录（未细分）",
    "ct": "CT",
    "biopsy": "活检/病理",
    "mri": "MRI",
    "ecg": "心电图",
    "echocardiogram": "超声心动图",
    "xray": "X 光",
    "blood_test": "血液检查",
    "ultrasound": "超声",
    "angiography": "血管造影",
    "pet_ct": "PET-CT",
    "endoscopy": "内镜",
    "cta": "CT 血管成像",
    "oct": "光学相干断层扫描",
    "blood_culture": "血培养",
    "fluoroscopy": "透视",
    "cbc": "全血细胞计数",
    "urinalysis": "尿液检查",
    "mammography": "乳腺摄影",
    "ffa": "荧光素眼底血管造影",
}

_PATIENT_SAMPLE_PRESENTATIONS = [
    {
        "match": "Corynebacterium jeikeium",
        "sample": {
            "title": "检查记录 01",
            "category": "检验 · 血培养 · PET-CT",
            "fields": [
                {"label": "肾功能", "value": "肌酐 354 μmol/L，较基线 124 μmol/L 升高。"},
                {"label": "炎症与尿液", "value": "C反应蛋白、降钙素原升高；尿蛋白 2.2 g/日。"},
                {"label": "血培养", "value": "检出杰氏棒状杆菌。"},
                {"label": "影像", "value": "PET-CT示辅助装置导线入口、囊袋及流出管多处代谢增高。"},
            ],
        },
    },
    {
        "match": "Leukocytosis (15×10",
        "sample": {
            "title": "检查记录 02",
            "category": "血液与生化检查",
            "fields": [
                {"label": "血常规", "value": "白细胞 15×10⁹/L；血红蛋白 11 g/dL；血小板正常。"},
                {"label": "肝胆指标", "value": "AST 66 U/L、GGT 241 U/L、ALP 404 U/L，均高于参考上限。"},
                {"label": "炎症与蛋白", "value": "C反应蛋白 14 mg/L；白蛋白 19 g/L。"},
                {"label": "筛查", "value": "乙肝、丙肝及 HIV 血清学结果阴性。"},
            ],
        },
    },
    {
        "match": "Hypocomplementemic state noted",
        "sample": {
            "title": "检查记录 03",
            "category": "检验 · 影像 · 皮肤病理",
            "fields": [
                {"label": "补体", "value": "C3 65 mg/dL、C4 8 mg/dL、C1q <29 mg/L。"},
                {"label": "免疫检测", "value": "检出 IgG-κ 型单克隆球蛋白。"},
                {"label": "影像", "value": "未见内脏或骨组织受累。"},
                {"label": "皮肤活检", "value": "提示肉芽肿性毛囊炎及瘢痕样纤维化。"},
            ],
        },
    },
]

_MEDICAL_SAMPLE_PRESENTATIONS = {
    "PMC-12638167": {
        "title": "急性心梗床旁肌钙蛋白检测",
        "category": "诊断方法综述",
        "fields": [
            {"label": "研究问题", "value": "综述高敏心肌肌钙蛋白床旁检测在急性心肌梗死诊断中的分析与临床表现。"},
            {"label": "主要内容", "value": "比较多种已获监管许可的检测设备，并讨论其性能差异与适用场景。"},
        ],
    },
    "PMC-10537306": {
        "title": "脓毒症与脓毒性休克管理",
        "category": "临床综述",
        "fields": [
            {"label": "研究范围", "value": "回顾近二十年脓毒症识别、复苏和治疗策略的演变。"},
            {"label": "主要内容", "value": "讨论抗菌药物、液体复苏、升压药等管理措施及其证据变化。"},
        ],
    },
    "PMC-12652086": {
        "title": "尿路感染诊断方法",
        "category": "诊断综述",
        "fields": [
            {"label": "研究范围", "value": "从常规检测到新兴技术，梳理尿路感染的病原识别方法。"},
            {"label": "比较维度", "value": "讨论不同方法的敏感度、特异度、成本及临床应用限制。"},
        ],
    },
    "PMC-12790941": {
        "title": "肝硬化无创诊断",
        "category": "诊断方法综述",
        "fields": [
            {"label": "研究问题", "value": "评述血清指标、弹性成像等无创方法在肝硬化诊断与监测中的作用。"},
            {"label": "注意事项", "value": "无创方法可减少侵入性检查，但肥胖及资源可及性会影响应用。"},
        ],
    },
    "PMC-11100091": {
        "title": "肌骨影像规则实施研究",
        "category": "系统综述",
        "fields": [
            {"label": "研究范围", "value": "纳入22项研究，覆盖6个国家的急诊环境。"},
            {"label": "主要发现", "value": "提醒可能提高渥太华踝关节规则的使用率，也可能增加踝部X光检查；组合干预结果不一。"},
        ],
    },
    "PMC-11879485": {
        "title": "儿童与青少年踝部骨折评估",
        "category": "横断面诊断研究",
        "fields": [
            {"label": "研究对象", "value": "144名16岁及以下、因踝扭伤接受踝/足部X光检查的患者。"},
            {"label": "评估方法", "value": "以X光结果为参照，评估渥太华踝关节规则的诊断效能。"},
        ],
    },
    "PMC-13160383": {
        "title": "急诊照护中的踝扭伤管理",
        "category": "指南依从性研究",
        "fields": [
            {"label": "研究问题", "value": "评估医师对踝关节规则及相关处置建议的遵循情况。"},
            {"label": "研究重点", "value": "分析指南执行差异及影响规范落实的因素。"},
        ],
    },
}

_EXPERIENCE_SAMPLE_PRESENTATIONS = {
    "sim-success-01": {
        "title": "Case01",
        "category": "协作策略",
        "fields": [
            {"label": "初始症状", "value": "急性肌肉骨骼损伤，局部压痛并肿胀；已有影像报告待核对。"},
            {"label": "专家顺序", "value": "影像科 → 病理科。"},
            {"label": "协作策略", "value": "先核对体检与现有报告；按需请专家复核；每条结论绑定具体来源。"},
            {"label": "流程概况", "value": "检索4次 · 深挖0次 · 回溯0次 · 规划1次。"},
        ],
    },
    "sim-success-02": {
        "title": "Case02",
        "category": "协作策略",
        "fields": [
            {"label": "初始症状", "value": "多种症状并存，已有多项证据，需寻找最有区分度的信息。"},
            {"label": "专家顺序", "value": "检验科 → 病理科 → 影像科。"},
            {"label": "协作策略", "value": "拆分独立信息需求；逐项比较证据与假设；已有记录能回答时不重复检索。"},
            {"label": "流程概况", "value": "检索13次 · 深挖1次 · 回溯0次 · 规划3次。"},
        ],
    },
    "sim-prevent-01": {
        "title": "防错规则 01",
        "category": "证据核验",
        "fields": [
            {"label": "需避免", "value": "不能把缺少来源支持的智能体断言当作医学证据。"},
            {"label": "操作检查", "value": "用证据支持或反驳假设前，先核对其来源绑定。"},
        ],
    },
    "sim-prevent-02": {
        "title": "防错规则 02",
        "category": "证据核验",
        "fields": [
            {"label": "需避免", "value": "不能用不包含该主张的检索记录作为证据。"},
            {"label": "操作检查", "value": "回读原始记录，确认其中确实写明该主张。"},
        ],
    },
}

def _grouped_counts(
    connection: sqlite3.Connection,
    column: str,
    labels: dict[str, str],
    *,
    minimum_cell: int = 5,
) -> list[dict[str, Any]]:
    # `column` is selected from this module's fixed allowlist, never user input.
    rows = connection.execute(
        f"SELECT {column}, COUNT(*) AS n FROM patient_evidence GROUP BY {column}"
    ).fetchall()
    grouped: dict[str, int] = {}
    suppressed = 0
    for raw_value, raw_count in rows:
        value, count = str(raw_value or ""), int(raw_count)
        label = labels.get(value)
        if count < minimum_cell or label is None:
            suppressed += count
            continue
        grouped[label] = grouped.get(label, 0) + count
    out = [{"label": label, "count": count} for label, count in grouped.items()]
    if suppressed:
        out.append({"label": "其他低频/未分类项目", "count": suppressed})
    return sorted(out, key=lambda item: (-item["count"], item["label"]))


def _patient_stats() -> dict[str, Any]:
    if not PATIENT_EVIDENCE_DB.is_file():
        return {"recordCount": 0, "taskCount": 0, "distributions": [], "samples": []}
    uri = PATIENT_EVIDENCE_DB.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
    except sqlite3.Error:
        return {"recordCount": 0, "taskCount": 0, "distributions": [], "samples": []}
    try:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute(
            "SELECT COUNT(*) AS records, COUNT(DISTINCT task_id) AS tasks "
            "FROM patient_evidence"
        ).fetchone()
        modality = _grouped_counts(connection, "modality", _MODALITY_LABELS)
        exams = _grouped_counts(connection, "exam_type", _EXAM_LABELS)
        rows = connection.execute(
            "SELECT MIN(exam_type), text FROM patient_evidence "
            "WHERE length(trim(text)) > 0 GROUP BY text "
            "ORDER BY length(text) DESC LIMIT 3"
        ).fetchall()
    except sqlite3.Error:
        return {"recordCount": 0, "taskCount": 0, "distributions": [], "samples": []}
    finally:
        connection.close()
    samples = []
    for _exam_type, text in rows:
        presentation = next(
            (item["sample"] for item in _PATIENT_SAMPLE_PRESENTATIONS
             if item["match"] in str(text)),
            None,
        )
        if presentation is not None:
            samples.append(presentation)
    return {
        "recordCount": int(row[0] or 0),
        "taskCount": int(row[1] or 0),
        "distributions": exams or modality,
        "samples": samples,
    }


def _line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def _medical_stats() -> dict[str, Any]:
    documents: dict[str, str] = {}
    samples: list[dict[str, Any]] = []
    paragraphs = sections = source_blocks = chunks = 0
    schema_version = "schema-v5" if MEDICAL_KNOWLEDGE_ROOT_V5.is_dir() else "schema-v4"
    medical_root = MEDICAL_KNOWLEDGE_ROOT_V5 if schema_version == "schema-v5" else MEDICAL_KNOWLEDGE_ROOT_V4
    if medical_root.is_dir():
        for folder in sorted(path for path in medical_root.iterdir() if path.is_dir()):
            document_path = folder / "document.json"
            source_path = folder / ("sections.jsonl" if schema_version == "schema-v5" else "paragraphs.jsonl")
            chunk_path = folder / "child_chunks.jsonl"
            if not (document_path.is_file() and source_path.is_file() and chunk_path.is_file()):
                continue
            try:
                metadata = json.loads(document_path.read_text(encoding="utf-8"))
                document_id = str(metadata.get("document_id") or "")
                title = str(metadata.get("title") or "")
            except (OSError, json.JSONDecodeError):
                continue
            if not document_id:
                continue
            documents[document_id] = title
            if schema_version == "schema-v5":
                try:
                    section_rows = [
                        json.loads(line)
                        for line in source_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                except (OSError, json.JSONDecodeError):
                    section_rows = []
                sections += len(section_rows)
                source_blocks += sum(len(item.get("blocks") or []) for item in section_rows)
            else:
                paragraphs += _line_count(source_path)
            chunks += _line_count(chunk_path)
            presentation = _MEDICAL_SAMPLE_PRESENTATIONS.get(document_id)
            if presentation is not None:
                try:
                    source_rows = [
                        json.loads(line)
                        for line in source_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                except (OSError, json.JSONDecodeError):
                    source_rows = []
                if schema_version == "schema-v5":
                    candidates = [
                        block
                        for section in source_rows
                        for block in section.get("blocks", [])
                        if str(block.get("text") or "").strip()
                    ]
                    abstracts = [
                        block for section in source_rows
                        if section.get("heading_path", [None])[0] == "Abstract"
                        for block in section.get("blocks", [])
                        if str(block.get("text") or "").strip()
                    ]
                else:
                    candidates = [item for item in source_rows if str(item.get("text") or "").strip()]
                    abstracts = [
                        item for item in source_rows
                        if item.get("paragraph_type") == "abstract" and str(item.get("text") or "").strip()
                    ]
                candidates = abstracts or candidates
                if candidates:
                    samples.append(presentation)
    return {
        "recordCount": len(documents),
        "paragraphs": paragraphs,
        "sections": sections,
        "sourceBlocks": source_blocks,
        "chunks": chunks,
        "samples": samples,
        "schemaVersion": schema_version,
    }


def _experience_stats() -> dict[str, Any]:
    counts = {"success": 0, "prevention": 0}
    samples: list[dict[str, Any]] = []
    try:
        from traceevo.rag.experience.seed_simulated import load_simulated_records
        records = load_simulated_records(EXPERIENCE_FIXTURE)
        for record in records:
            category = (
                "success" if record.simulation_type == "simulated_success_pattern"
                else "prevention"
            )
            counts[category] += 1
            presentation = _EXPERIENCE_SAMPLE_PRESENTATIONS.get(record.simulation_id)
            if presentation is not None:
                samples.append(presentation)
    except (OSError, ValueError):
        records = []
    return {
        "recordCount": len(records),
        "success": counts["success"],
        "prevention": counts["prevention"],
        "samples": samples,
    }


def build_knowledge_bases_view() -> KnowledgeBasesView:
    patient = _patient_stats()
    medical = _medical_stats()
    experience = _experience_stats()

    return KnowledgeBasesView.model_validate({
        "privacyNote": "以下为数据库内容的中文简要整理；患者证据样例不显示任务编号和记录编号。",
        "knowledgeBases": [
            {
                "id": "patient_evidence",
                "name": "Patient Evidence · 患者证据库",
                "recordCount": patient["recordCount"],
                "recordCountLabel": "条检查记录",
                "taskCount": patient["taskCount"],
                "distributions": patient["distributions"],
                "samples": patient["samples"],
            },
            {
                "id": "medical_knowledge",
                "name": "Medical Knowledge · 医学知识库",
                "recordCount": medical["recordCount"],
                "recordCountLabel": "篇文献",
                "taskCount": None,
                "distributions": [
                    {"label": "PMC OA 文献", "count": medical["recordCount"]},
                    *(
                        [
                            {"label": "原文章节", "count": medical["sections"]},
                            {"label": "原文内容块", "count": medical["sourceBlocks"]},
                        ]
                        if medical["schemaVersion"] == "schema-v5"
                        else [{"label": "原文段落", "count": medical["paragraphs"]}]
                    ),
                    {"label": "检索子块", "count": medical["chunks"]},
                ],
                "samples": medical["samples"],
            },
            {
                "id": "experience",
                "name": "Experience · 经验库",
                "recordCount": experience["recordCount"],
                "recordCountLabel": "条经验记录",
                "taskCount": None,
                "distributions": [
                    {"label": "协作策略", "count": experience["success"]},
                    {"label": "防错规则", "count": experience["prevention"]},
                ],
                "samples": experience["samples"],
            },
        ],
    })
