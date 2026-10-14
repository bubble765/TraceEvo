# TraceEvo RAG 构建计划 v1.0

## 0. 目标与边界

目标：在不修改现有 TraceEvo / GoS Core-v1 冻结实验的前提下，新建独立 RAG 模块，形成三类相互独立的数据源：Patient Evidence、Medical Knowledge、Experience。

设计原则：Patient Evidence 使用结构化文本库，不做向量检索；Medical Knowledge 与 Experience 使用独立向量库；每次检索必须经过 Query Reformulation；复杂问题由同一次 Structured Output 调用自动拆成多个子查询；Medical Knowledge 使用 Parent-Child 分块，只对 Child Chunk 建向量索引；所有检索结果保留稳定来源 ID，并接入 TraceEvo provenance。

## 1. 总体架构

```text
Central Agent / Expert Agent
          │
          │ 根据 tool description 自主选择
          ├──── PatientEvidenceTool
          ├──── MedicalKnowledgeTool
          └──── ExperienceTool
                    │
          Mandatory Query Reformulation
                    │
              QueryPlan(Pydantic)
                    │
       ┌────────────┼────────────┐
       │            │            │
Patient Evidence  Medical KB   Experience
SQLite/Text DB     Qdrant       Qdrant
field filter       HNSW         HNSW
       │            │            │
 exact/alias    Qwen3 Embed   Qwen3 Embed
 retrieval        0.6B          0.6B
       │            │            │
       │        Dense Top-N   Dense Top-N
       │            │            │
       │       Qwen3 Rerank  Qwen3 Rerank
       │           0.6B          0.6B
       └────────────┴────────────┘
                    │
             Source-aware Result
                    │
           Agent / Causal Graph
                    │
             Provenance Event
```

## 2. 初始技术配置

```yaml
rag:
  embedding:
    model: Qwen/Qwen3-Embedding-0.6B
    dimension: 1024
    edge_candidate_dimension: 512
    normalize: true
  reranker:
    model: Qwen/Qwen3-Reranker-0.6B
  medical_knowledge:
    vector_db: qdrant
    index: hnsw
    distance: cosine
    retrieve_k: 30
    rerank_k: 5
    final_k: 4
  experience:
    vector_db: qdrant
    index: hnsw
    distance: cosine
    retrieve_k: 20
    rerank_k: 5
    final_k: 2
  patient_evidence:
    store: sqlite
    vector_search: false
  chunking:
    target_tokens: 384
    max_tokens: 480
    overlap_tokens: 64
    min_tokens: 120
  query_planning:
    max_subqueries: 4
    always_reformulate: true
```

1024 维先作为质量基线；512 维作为 Qwen3 MRL 的边缘部署候选，只有在 Recall@K / nDCG 降幅可接受时再切换。

## 3. 目录结构

```text
traceevo/
├── rag/
│   ├── common/
│   │   ├── schemas.py
│   │   ├── query_planner.py
│   │   ├── query_prompt.py
│   │   └── result_schema.py
│   ├── patient_evidence/
│   │   ├── store.py
│   │   ├── normalizer.py
│   │   ├── aliases.py
│   │   └── retriever.py
│   ├── medical_knowledge/
│   │   ├── source_adapters/
│   │   ├── parser.py
│   │   ├── splitter.py
│   │   ├── embedder.py
│   │   ├── vector_store.py
│   │   ├── reranker.py
│   │   └── retriever.py
│   ├── experience/
│   │   ├── indexer.py
│   │   ├── retriever.py
│   │   └── compressor.py
│   └── tools/
│       ├── patient_evidence_tool.py
│       ├── medical_knowledge_tool.py
│       └── experience_tool.py
└── tests/rag/
```

## 4. Pydantic Schema

```python
from enum import Enum
from pydantic import BaseModel, Field
from typing import Literal

class RagStore(str, Enum):
    PATIENT_EVIDENCE = "patient_evidence"
    MEDICAL_KNOWLEDGE = "medical_knowledge"
    EXPERIENCE = "experience"

class QueryMode(str, Enum):
    SINGLE = "single"
    DECOMPOSE = "decompose"

class SubQuery(BaseModel):
    query_id: str
    query_text: str
    retrieval_goal: str
    modality: str | None = None
    exam_type: str | None = None

class QueryPlan(BaseModel):
    mode: QueryMode
    store: RagStore
    queries: list[SubQuery] = Field(min_length=1, max_length=4)

class PatientEvidenceRecord(BaseModel):
    record_id: str
    task_id: str
    modality: str
    exam_type: str
    title: str | None = None
    text: str
    timestamp: str | None = None
    record_hash: str

class MedicalDocument(BaseModel):
    document_id: str
    source_org: str
    source_type: Literal["guideline", "article", "book", "review"]
    title: str
    publication_date: str | None = None
    version: str | None = None
    specialty: list[str] = []
    source_uri: str
    license: str | None = None
    raw_path: str

class ParentSection(BaseModel):
    section_id: str
    document_id: str
    heading_path: list[str]
    text: str
    ordinal: int

class KnowledgeChunk(BaseModel):
    chunk_id: str
    document_id: str
    parent_section_id: str
    heading_path: list[str]
    text: str
    token_count: int
    ordinal_in_parent: int
    source_uri: str

class ExperienceRecord(BaseModel):
    experience_id: str
    experience_type: Literal["validated_success", "validated_failure"]
    task_feature_summary: str
    experience_payload: dict
    source_trace_ids: list[str]
    audit_summary_hash: str
    outcome_verdict: str
```

## 5. Patient Evidence：结构化文本检索

不使用向量库。建议 SQLite：

```sql
CREATE TABLE patient_evidence (
    record_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    modality TEXT NOT NULL,
    exam_type TEXT NOT NULL,
    title TEXT,
    text TEXT NOT NULL,
    timestamp TEXT,
    record_hash TEXT NOT NULL
);
CREATE INDEX idx_patient_task ON patient_evidence(task_id);
CREATE INDEX idx_patient_task_exam ON patient_evidence(task_id, exam_type);
CREATE INDEX idx_patient_task_modality ON patient_evidence(task_id, modality);
```

检索规则：永远先 `WHERE task_id = ?`；明确 `exam_type` 时做规范化后精确/别名匹配；否则按 `modality`；均未指定时返回当前 task 下有限记录供专家进一步判断。可选加 SQLite FTS5 作为宽泛文本检索，但不使用向量召回。

建立检查别名：OCT/optical coherence tomography/光学相干断层扫描，MRI/magnetic resonance imaging/磁共振，CT/computed tomography/计算机断层扫描，FFA/fundus fluorescein angiography/荧光素眼底血管造影等。

## 6. Medical Knowledge 数据源与规范化

优先来源：WHO Guidelines；PubMed Central Open Access Subset；许可允许复用的 NCBI Bookshelf guidelines/manuals/reference works；NICE Guidance 仅在获得适用 API/licence 后加入。

不要把 Markdown 作为 canonical storage。采用三层存储：Raw Layer 保存 XML/HTML/PDF；Normalized Layer 保存 JSONL/Parquet；Retrieval Layer 保存 ParentSection、ChildChunk 和 embedding。

规范化记录示例：

```json
{
  "document_id": "WHO-XXX",
  "section_id": "sec-12",
  "heading_path": ["Diagnosis", "Imaging", "MRI"],
  "section_text": "...",
  "source_org": "WHO",
  "source_type": "guideline",
  "publication_date": "...",
  "version": "...",
  "source_uri": "...",
  "license": "..."
}
```

PMC 优先使用 BioC JSON / JATS XML，而不是先转 Markdown 再解析。

## 7. Parent-Child Semantic Chunking

只对 Child Chunk 计算 embedding 并写入 Qdrant。ParentSection 保存完整 section，不直接建立向量索引，仅作为上下文容器。

```text
MedicalDocument
  └── ParentSection
       ├── ChildChunk-1
       ├── ChildChunk-2
       └── ChildChunk-3
```

代码关系是普通外键：`child.parent_section_id == parent.section_id`。

分块算法：先按 heading 形成 section；section 内把 paragraph/list/table/caption 作为 semantic block；按顺序 greedy packing，目标 384 token，上限 480；单个 block 超过 480 时再按 sentence 切；overlap 只取上一个 chunk 尾部完整句子/完整 block，累计约 64 token，并且绝不跨 section；末块小于 120 token 时才安全合并到前块。

伪代码：

```python
for section in sections:
    blocks = parse_semantic_blocks(section)
    chunks, current = [], []
    for block in blocks:
        if token_len(current + [block]) <= MAX_TOKENS:
            current.append(block)
        else:
            chunks.append(pack(current))
            overlap = tail_semantic_units(current, OVERLAP_TOKENS)
            current = overlap + split_if_needed(block)
    flush(current)
    if last_chunk.token_count < MIN_TOKENS:
        merge_with_previous_if_safe()
```

## 8. Embedding 与 Reranking

Embedding 使用 `Qwen/Qwen3-Embedding-0.6B`。Medical Knowledge Query instruction：

```text
Given a medical diagnostic information need, retrieve passages from
clinical guidelines and biomedical literature that provide directly
relevant diagnostic, differential-diagnostic, examination, or treatment
knowledge. Prefer passages that can answer the query rather than merely
mention the same disease.
```

Experience Query instruction：

```text
Given a structured medical task description, retrieve historical audited
tasks with similar diagnostic context, evidence requirements, and
collaboration needs. Match task applicability, not the historical diagnosis.
```

Reranker 使用 `Qwen/Qwen3-Reranker-0.6B`。

Medical Knowledge 初始参数：Dense Top-30 → Rerank Top-5 → Final Top-4。Experience：Dense Top-20 → Rerank Top-5 → Final Top-2。最终允许空结果，不强行填满 top-k。

## 9. Vector DB：Qdrant

核心开发使用 Qdrant local/server，HNSW + cosine。Medical Knowledge 与 Experience 使用两个独立 collection。Patient Evidence 不进入 Qdrant。

边缘实验分支评估 Qdrant Edge + 512-dim embedding + INT8 scalar quantization + on-disk vectors/payload。若 Qdrant Edge beta 状态不满足稳定性要求，则保留 Qdrant server ARM64/本地部署作为可复现主版本。

不优先 Milvus Lite：其 Lite 模式当前仅支持 FLAT 索引，不利于展示边缘 ANN/HNSW/量化链路。

## 10. Query Reformulation：单次 Structured Output

每次检索都 reformulate。LLM 自行判断简单问题还是复杂问题：简单问题输出 `mode=single` 和一个改写后的查询；复杂问题输出 `mode=decompose` 和 2~4 个互不重叠的子查询。判断、改写、拆分和 metadata 提取均在同一次 LLM Structured Output 调用中完成，不需要先“改写调用一次”再“拆分调用一次”。

System Prompt：

```text
You are the retrieval query planner for a medical multi-agent reasoning system.

Your job is NOT to answer the medical question.
Your job is to transform the current information need into retrieval-ready queries.

Rules:
1. Every input must be reformulated.
2. If the request contains one atomic information need, output mode="single"
   and exactly one rewritten query.
3. If the request contains multiple independent information needs that should
   retrieve different evidence, output mode="decompose" and split it into
   2-4 non-overlapping subqueries.
4. Each query must be self-contained and retrieval-oriented.
5. For differential diagnosis, prefer discriminative queries that can support
   OR refute competing hypotheses; do not only search for confirmation.
6. Never convert an unverified hypothesis into a patient fact.
7. Never insert historical diagnosis or gold labels.
8. For Patient Evidence, preserve task_id exactly; extract modality/exam_type
   only when explicitly implied; never invent examination results.
9. For Medical Knowledge, query general medical knowledge, not patient facts.
10. For Experience, describe task characteristics and process needs, not the
    desired diagnosis.
11. Return only schema-compatible JSON.
```

简单查询也必须改写，例如“查 OCT”→一个规范化 OCT 查询，但不拆分。复杂的鉴别诊断请求可一次输出多个 subquery。

## 11. 并行 Subquery Retrieval

一次 QueryPlan 返回多个子查询后，用 `asyncio.gather()` 并发检索；向量 query embedding 尽量 batch；Qdrant 使用 batch/concurrent query；Reranker 对所有 `(query, candidate)` pair 批处理；合并结果时按 `chunk_id/document_id` 去重，并保留命中的 `query_id`。

```python
plan = await query_planner.plan(...)
results = await asyncio.gather(
    *[retrieval_service.retrieve(q) for q in plan.queries]
)
merged = merge_deduplicate(results)
```

## 12. Experience RAG

只索引 Validated Success 和允许在线使用的 Validated Failure。Manual Review / Reject 不进入在线索引。

向量检索对象是 `task_feature_summary`，不是历史诊断，也不是直接拿 strategy 文本和当前病例匹配：`current_task_summary ↔ historical_task_summary`。Rerank 继续比较任务适用性。最终注入 Planner 的才是对应的 `experience_payload`。

HDBSCAN 不作为 v1 在线检索前置步骤。经验数量足够后，可用于离线发现重复 strategy、候选 Top-M 内去重和生成 prototype，但原始 experience 永远保留。

## 13. Provenance 集成

Patient Evidence 返回 `record_id`；Medical Knowledge 返回 `knowledge_chunk_id + document_id`；Experience 返回 `experience_id + source_trace_ids`。所有 ID 写入 retrieval event 的 input/output refs。

只有 Patient Evidence record 可以直接作为患者事实的 `source_refs`。Medical Knowledge 和 Experience 必须使用独立 source_type，不能伪装成当前患者事实。Reranker score 只表示检索相关性，不等价于医学可信度或疾病置信度。

## 14. 测试与评估

必须覆盖：QueryPlan single/decompose；最多 4 个子查询；Patient Evidence task_id 硬过滤；exam alias；父子外键；不跨 section overlap；chunk token 边界；embedding dimension；Qdrant round-trip；rerank order；空结果；Experience gold leakage=0；retrieval event source refs。

Medical Knowledge 建 100~300 条 query-relevant passage 标注集，报告 Recall@5/10/30、nDCG@5/10、MRR、rerank gain、p50/p95 latency。Experience 建任务相似性标注，报告 Recall@K、Top-K applicability、wrong-task retrieval rate。Patient Evidence 报 exact retrieval accuracy、wrong-task=0、wrong-patient=0、exam alias recall。

Edge 对比 `1024-fp32 / 512-fp32 / 512-int8`，报告 Recall@10、rerank 后 nDCG@5、index size、RAM、p50/p95 latency、CPU utilization。

## 15. 执行顺序

Phase A：Patient Evidence Store。将 DiagnosisArena Diagnostic Tests 转成稳定 RetrievalRecord；SQLite schema；task_id/modality/exam_type 索引；别名规范化；替换原 LLM evidence_retriever，保留兼容开关。

Phase B：Medical Knowledge Ingestion。先接 WHO + PMC OA；建立 source adapter；解析 XML/HTML/PDF；生成 MedicalDocument / ParentSection / ChildChunk；实现 semantic-first splitter；本地保存 JSONL/Parquet。

Phase C：Dense Retrieval。接 Qwen3-Embedding-0.6B；建立 Qdrant collection；child embedding；HNSW cosine；先做 1024-dim baseline。

Phase D：Reranking。接 Qwen3-Reranker-0.6B；Medical 30→5→4；Experience 20→5→2；批量 rerank；允许空结果。

Phase E：Query Planner。实现 Pydantic QueryPlan；一次 Structured Output 完成 single/decompose + rewrite；强制每次 reformulation；subquery 并发；保存 query/result trace。

Phase F：Experience Vector RAG。将 task retrieval key 升级为结构化 task feature summary；Qdrant 独立 collection；success/failure 分区不变；保留 gold leakage guard。

Phase G：Provenance Integration。每次检索产生 TOOL_CALL / EVIDENCE_READ 或对应 retrieval event；output_refs 保存 record/chunk/experience ID；因果图 Evidence 的 source_refs 只绑定 Patient Evidence record。

Phase H：Edge Profile。Qdrant Edge 实验分支；512-dim MRL；INT8 scalar quantization；ARM64/x86_64 测试；输出质量/内存/时延 trade-off。

## 16. 完成标准

RAG v1 可写入简历前至少满足：Patient Evidence wrong-task retrieval=0；Medical Knowledge 有可复现的标注检索集；Dense retrieval 与 rerank 指标可复现；QueryPlan schema success≥99%；所有检索均有 source ID；Experience gold leakage=0；至少一组 1024 vs 512 edge profile；有 p50/p95 latency 和 index/RAM 数据。

交付文件：`configs/rag.yaml`、构建脚本、检索评估脚本、edge profile 脚本、单元测试、`RAG_IMPLEMENTATION_REPORT.md`、`RAG_EVAL_RESULTS.csv`、`RAG_EDGE_PROFILE.csv`。

## 17. 禁止事项

不得把 Medical Knowledge 直接写成患者事实；不得把 Experience 中历史病例 diagnosis/prediction/evidence 注入当前病例；不得使用 held-out case 80–137 调 RAG 参数；不得因无结果而强行返回最相似文档；不得把 reranker score 当作医学置信度；不得在没有实验前声称 RAG 提升诊断准确率；不得覆盖现有 TraceEvo Core-v1 frozen artifacts。
