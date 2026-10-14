# TraceEvo RAG Implementation Report

Updated: 2026-10-03  
Plan: `TraceEvo_RAG_Implementation_Plan_v1.md`

## Implemented package milestone: schema-v4 retrieval, query planning, and Experience vector seam

Implemented as an independent package under `traceevo/rag/`; GoS, the frozen
Core-v1 code, frozen run artifacts, and the legacy LLM `evidence_retriever`
were not changed.

- Shared Pydantic contracts for `QueryPlan`, `PatientEvidenceRecord`, medical
  document/chunk metadata, experience records, and source-aware retrieval hits.
- SQLite schema with task, task+exam-type, and task+modality indexes. There is
  no vector/embedding path for Patient Evidence.
- Exact English/Chinese aliases for common exams and modalities (including
  OCT, MRI, CT/CTA, X-ray, ultrasound, FFA, biopsy, ECG, and laboratory tests).
- Stable record IDs and SHA-256 content hashes.
- A DiagnosisArena importer that reads only `Diagnostic Tests`, maps each
  zero-based row to the existing `case-{row_index}` task-id convention, and
  excludes gold/option fields from stored records.
- A structured retriever and source-aware `PatientEvidenceTool`; ambiguous
  multi-exam queries fail closed until the planned Query Planner can split
  them.
- A drop-in callable `legacy` / `patient_evidence` switch for staged
  integration; the frozen Core-v1 path remains on legacy mode.
- Qdrant local collection and dense retrieval are now implemented for Medical
  Knowledge; Core-v1 integration remains disabled.

## Active Medical Knowledge corpus — schema-v5 (2026-10-03)

The local schema-v5 corpus now has 50 distinct PMC OA publications: the
8-document ankle/musculoskeletal seed plus 42 new articles spanning
cardiovascular, respiratory, neurologic, endocrine, renal, gastrointestinal,
infectious-disease, oncology, and rheumatology topics. All passed the
default license gate for derivative chunking. Normalization produced 917
source sections, 5,076 typed source blocks, and 2,033 non-overlapping child
chunks. Qdrant v5 contains 2,083 points (50 document-summary vectors and
2,033 child-chunk vectors).

Each document is the original PMC publication, not a forced disease-level
record: its scope may be one condition, a disease family/syndrome, a diagnostic
method, or a broader management question. The corpus is mixed biomedical
literature and is not a collection of 50 formal clinical-practice guidelines.

A schema-v5 smoke query about high-sensitivity point-of-care troponin returned
the acute-myocardial-infarction diagnostic review (`PMC12638167`) as its top
result after document routing, child-vector retrieval, global reranking, and
section-local context hydration. The 23 targeted ingestion/vector tests pass;
this validates the pipeline, not clinical answer quality.

## Phase A — Patient Evidence

Phase A remains implemented as described in `traceevo/rag/README.md`: 138
DiagnosisArena tasks and 655 `Diagnostic Tests` records were imported into an
ignored local SQLite database, re-import was idempotent, and the cross-task
smoke check returned zero leakage. Gold columns were not copied.

## Phase B — Medical Knowledge ingestion

Implemented under `traceevo/rag/medical_knowledge/`:

- PMC OA adapter: NCBI BioC JSON full text plus ESummary title/authors/date/DOI/PMID;
  it rejects malformed/non-OA responses and missing/unrecognized licenses.
- WHO manifest adapter: explicit JSON manifest and local raw files only;
  validates HTTPS WHO-hosted source URLs, accepted file types, and license.
- Conservative CC license gate: NoDerivatives and unknown/missing licenses are
  refused; NC derivatives require an explicit CLI opt-in.
- The historical schema-v2 parser/splitter emitted `ParentSection` and
  section-local `KnowledgeChunk` records. The active schema-v4 path stores
  document-ordered paragraphs and direct child chunks instead.
- Immutable raw files and per-document normalized JSON/JSONL. Token counting
  is injectable and labeled `unicode_lexical_estimate_v1`; it is not treated
  as an exact Qwen token count.
- CLI: `python -m traceevo.rag.medical_knowledge.ingest`.

An NCBI live endpoint probe confirmed the BioC service returns OA full text and
license metadata for valid PMCIDs. The current local seed corpus contains two
articles verified as reusable under the default policy:

- PMC11879485, “Ottawa Ankle Rules: A Reliable Clinical Instrument to Detect
  Fractures in Children and Adolescents” (CC BY).
- PMC11100091, “Effectiveness of implementation strategies for increasing
  clinicians' use of five validated imaging decision rules for musculoskeletal
  injuries: a systematic review” (CC BY 4.0).

The records provide 65 parent sections and 103 child chunks in the ignored
`traceevo_runs/rag/medical_knowledge/normalized/schema-v2` directory. They are
research evidence, not practice guidelines. PMC BioC and E-utilities requests
now succeed with rate spacing; bounded `Retry-After`/backoff remains for 429s.
The WHO IRIS REST probe returned 401 and the CDC page probe returned 403; no
WHO/CDC material was downloaded. The earlier probe's `CC BY-NC` sample remains
rejected under default settings.

Additional repository checks on 2026-09-25:

- WHO IRIS DSpace REST returned 401, but its anonymous OAI-PMH `Identify`,
  `ListSets`, and `ListRecords` endpoints returned 200. `oai_dc` metadata
  includes rights and identifiers; sample rights included CC BY-NC-SA 3.0 IGO.
  This currently supports metadata discovery only, not automatic full-text
  ingestion.
- NICE NG38 pages/recommendations returned 200. NICE's reuse page says its
  open-content license is UK-only; international reuse beyond personal
  research/study requires a separate agreement, so NICE is excluded from the
  default corpus.
- The VA/DoD CPG portal, GIN library, and PAHO BIGG catalogue returned 200.
  Their linked documents have individual rights; no such document was
  ingested without verification.
- A CDC guideline URL returned 403 from this network. No conclusion about
  general CDC access or reuse policy is inferred from that single route.

During the first real import, one BioC record also exposed a subtle licensing
case: its document-level license was CC BY while a passage described separate
CC0 supporting data. The parser was corrected to keep the document-level
license authoritative and attach only same-family notices. The original
immutable pre-correction files remain in place but are superseded. At that
initial build, only `normalized/schema-v2` was eligible for indexing; the
schema-v3 and schema-v4 workflows were added without overwriting those artifacts.
Fixture tests cover this mixed-license case. No WHO source has been added
because no verified WHO raw document/manifest was supplied.

## Historical Phase C — section-first Qdrant and embedding

Implemented and exercised against the local seed corpus:

- Added `QdrantMedicalKnowledgeStore` using Qdrant local persistent mode at
  `traceevo_runs/rag/qdrant`; no server is required. The medical collection is
  isolated from Patient Evidence and Experience, uses HNSW/cosine, and refuses
  a dimension/distance mismatch rather than recreating an existing collection.
- The initial schema-v2-only loader verified document/parent/chunk IDs and
  parent foreign keys. It indexed `KnowledgeChunk` text and retained complete
  parent sections as context containers. This collection is retained as a
  historical baseline; the current indexer does not read schema-v2.
- Connected `Qwen/Qwen3-Embedding-0.6B` using SentenceTransformers, pinned to
  HF revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` (model card license:
  Apache-2.0). Model output is normalized 1024-d cosine vectors. The local
  machine used MPS; model weights are in the HF cache, not the repository.
- Medical query embeddings include the plan's retrieval instruction; document
  chunk embeddings do not. Embedding dimensions and finite values are checked.
- The old index contains two documents, 65 parent sections, and 103 child
  chunks. It remains under `traceevo_runs/rag/qdrant` and the
  `traceevo_medical_knowledge` collection and is not modified by later schemas.

The old query was a smoke check, not a retrieval-quality benchmark. The corpus
is tiny and includes pediatric-specific evidence, so it must not be assumed to
apply to adults. Medical Knowledge retrieval is not wired into the Central
Agent or frozen Core-v1.

## Historical retrieval design — schema-v3

Schema-v3 removed parent-section entities and used document-first retrieval,
but retained overlapping child chunks and paragraph-only context hydration.
Its immutable normalized files and Qdrant collection are retained unchanged.
The v3 smoke corpus has 2 documents, 139 paragraphs, and 63 child chunks; a
live Ottawa-radiography query completed document routing, document-local
retrieval, global reranking, and previous/next paragraph hydration. This was
only a pipeline smoke check, not a quality benchmark.

## Historical retrieval design — schema-v4

The normalized shape remains `MedicalDocument -> DocumentParagraphs /
DocumentChunks`, with no `ParentSection` entity or parent foreign key. Chunks
now carry half-open character spans into source paragraphs. They do not
overlap; headings remain provenance metadata. The indexer reads only
`normalized/schema-v4`; schema-v2/v3 files and collections remain unchanged.

The retrieval chain remains document-first: route against title+abstract
document vectors, search child chunks only inside each candidate document,
deduplicate/merge those candidates, and rerank the merged cross-document set
once. After ranking, hydration reconstructs the selected source spans and
adds at most one preceding and one following source paragraph under a context
budget. This keeps retrieval overlap out of the index and adds context only
to final hits.

Oversized paragraphs split at sentence/word boundaries when possible. The
splitter uses English whitespace boundaries, Jieba word boundaries when
available, and a built-in/custom medical term lexicon. When the target cut
falls inside a protected term, it snaps to a safe boundary within the hard
chunk budget. A term longer than that hard limit is preserved intact and
annotated in `boundary_notes`. A custom lexicon is accepted at ingestion via
`--entity-lexicon`, one term per line.

Defaults are five candidate documents, up to 4 child candidates per document,
and four final hits. Context is capped at 1,200 estimated tokens per hit
(anchor up to 800; each neighbor up to 200). Chunk defaults are a 384-token
target, 480 maximum, and 120 minimum for merging a short final chunk; counts
are lexical estimates. New index/search defaults use
`traceevo_runs/rag/qdrant_v4` and `traceevo_medical_knowledge_v4`, separate
from both earlier collections. Local Qdrant performs exact search at current
corpus scale. Scores are relevance signals, not clinical confidence.

Schema-v4 currently contains 8 PMC OA articles/reviews, 578 paragraphs, and
181 direct child chunks. Six additions passed the ingestion license gate:
adult Ottawa-rule diagnostic accuracy, bedside ultrasound for ankle fractures,
syndesmosis clinical examination, two acute sprain evidence reviews, and
urgent-care guideline adherence. The no-overlap spans pass structural
validation and are indexed under `traceevo_runs/rag/qdrant_v4` /
`traceevo_medical_knowledge_v4`. A live adult Ottawa-radiography query
retrieved the adult diagnostic-accuracy meta-analysis and completed document
routing, document-local child retrieval, global Qwen3 reranking, and span-based
context hydration. This is a pipeline smoke check, not a retrieval-quality
benchmark.

## Phase E — mandatory structured query reformulation (partial)

Added `QueryPlanner`, an injectable structured-output provider protocol, an
OpenAI-compatible Chat Completions adapter, and `RagQueryService`. The planner
validates store scope, `single`/`decompose` cardinality (1 or 2–4), unique query
IDs, and rejects verbatim copies. Patient Evidence `task_id` remains entirely
outside the model prompt and is passed unchanged by the service to every
subquery; returned result and hit task IDs are checked before merging.

The Medical Knowledge path batch-embeds queries where supported, merges and
deduplicates the per-query document-local child candidate pools, and sends the
full subquery × merged-candidate matrix through one Qwen3 reranker batch. Each
chunk is ranked by its best subquery score, with query-level scores and source
matches retained in metadata. Patient Evidence subquery calls and local Qdrant
lookups remain synchronous/sequential; parallel store calls are not yet
implemented. No provider/model is configured by this package, and the
planner/service are not yet connected to the Central Agent or Core-v1. Hosts
must inject a provider approved for the data classification being processed;
the library does not create clients or load credentials.

## Phase F — admission-gated Experience vectors (implemented, not deployed)

Added a separate Experience Qdrant collection, indexer, task-similarity Qwen3
embedding/reranking instructions, retriever, source-aware tool, and an indexing
CLI. It reads only the existing `success/` and `failure/` MemoryStore
partitions and cross-checks partition, payload type, source trace ID, and audit
summary hash against the admission audit links. Manual Review and Reject are
never read. Injectable payloads are restricted to the fixed process-guidance
shapes built by `traceevo.evolution`; only that payload is formatted into the
result, never the historical task summary or outcome verdict.

The experience extractor now emits `s3b-deterministic-v2` strategy payloads:
ordered expert calls, allowlisted central planning-intent codes, an abstract
workflow outline, aggregate action counts, and provenance/execution caveats.
Retrieval features remain limited to pre-run chief complaint plus physical
examination. No historical complaint, findings, hypothesis, diagnosis, free-
form PLAN text, or patient identity is copied into the injectable strategy.
The indexer remains backward-compatible with admitted v1 success payloads that
have the older specialist-list/counter form but no v2 sequence fields.

Admission no longer discards a correct strategy solely because P1
soft-grounded evidence or a recoverable medium anomaly was present: those
become generic injection cautions. The hard contract remains: verified-success
outcome, complete ledger, valid integrity and semantic completion, and no P3
assertion used in reasoning. High/critical localized anomalies remain failure
prevention memories; unlocalized high/critical anomalies, broken/incomplete
traces, uncertain or incorrect outcomes, and forbidden-content hits cannot
enter the success partition. This lowers avoidable loss without converting
unverified or unsafe traces into success memories.

The Qdrant payload holds only experience ID/type/partition/fingerprint; summary
text and guidance remain in the admitted MemoryStore. Retrieval rechecks live
admission IDs and fingerprints so revoked or edited vectors fail closed. The
default `initial_plan` channel searches validated success only; `recovery` must
be explicitly selected to include validated failures. A reranker score
threshold is required at construction, but is not yet calibrated. No real
memory source was indexed and the tool is not wired into Core-v1 or the Central
Agent.

For demonstration, the synthetic fixture now has 32 records: eight variations
of aggregate workflow dimensions from admitted S3D training examples (the
manual-review sample was excluded), eight further workflow examples, four
illustrations of existing A1/A2/A3/A5 prevention rules, and twelve additional
synthetic retrieval/context/admission guard examples. No task summary,
diagnosis, outcome, case ID, or patient text was copied. They were embedded
with the pinned Qwen3 model and upserted to the distinct local
`traceevo_experience_simulated_v1` collection under
`traceevo_runs/rag/qdrant_experience_simulated_v1`. Each record is marked
`simulation_only=true` / `online_eligible=false`; a Qwen embedding + Qdrant
round-trip returned five records with both flags intact. The current collection
was re-seeded to 32 points after this expansion. This tests storage
mechanics only, not retrieval relevance, and the production `ExperienceRetriever`
cannot read the simulated collection.

The web app exposes `/knowledge-bases` plus `GET /api/knowledge-bases` as a
catalog of all three RAG stores. It now puts concise Chinese summaries first:
Patient Evidence summaries omit task/record IDs, Medical Knowledge summaries
are based on source paragraphs, and Experience shows collaboration strategies
as case-style cards rather than JSON. Counts and content distributions follow the samples; the
page no longer renders coverage/keyword lists or status/boundary explanations.
The underlying Experience fixture remains isolated and ineligible for online
retrieval; only its display was simplified.

## Local build and checks

The ignored local database was built at
`traceevo_runs/rag/patient_evidence.sqlite3` from
`gos/datasets/DiagnosisArena/test_set.csv`:

- 138 tasks imported; 655 evidence records stored.
- A second import remained at 655 records (stable/idempotent IDs).
- Cross-task smoke check across all 138 task IDs returned zero records whose
  `task_id` differed from the requested task.
- MRI, modality, source-ID, empty-task, and mixed-exam rejection smoke checks
  passed; a temporary CSV check confirmed gold labels/options were not copied.
- Latest full test run: `pytest -q traceevo/tests` — **234 passed**. This
  includes the experience-admission changes, old-payload indexing compatibility,
  isolated Qdrant round-trip, stale-vector refusal, success-vs-recovery channel
  behavior, multi-query reranking, and the existing RAG/audit test suites.
- `python -m compileall -q traceevo gos/prompts/prompt_generation.py` and
  `git diff --check` passed. The test run emitted only local-Qdrant and Jieba
  dependency warnings; no test failures.

## Remaining work

Remaining: parallel store calls, persisted query/result traces, host model
configuration/Central Agent wiring, a real approved Experience memory source,
calibrated Experience threshold/gold-leakage evaluation, provenance-event
emission, retrieval evaluation sets, and edge profiles. The chunk token
counter remains approximate. No retrieval-quality or diagnostic-accuracy gain
is claimed.
