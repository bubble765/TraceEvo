# TraceEvo RAG — Patient Evidence, Medical Knowledge, and Experience

This is an independent RAG package. Importing it does not patch GoS, the frozen
Core-v1 experiment, or the legacy LLM `evidence_retriever`.

## Patient Evidence

Phase A stores only the DiagnosisArena `Diagnostic Tests` field. Gold columns
(`Final Diagnosis`, `Options`, `Right Option`) are not dereferenced by the
importer. The task key is the zero-based dataset row (`case-0`, `case-1`, ...),
which is the task-id convention used by GoS/TraceEvo artifacts.

Patient Evidence is SQLite-backed and deliberately has no vector search. Every
read requires an exact task id; an optional exam type and/or modality adds
exact SQL filters. No-filter reads are still task-scoped and capped. Re-imports
are idempotent because record IDs and SHA-256 content hashes are deterministic.

Build or refresh the local database:

```bash
python -m traceevo.rag.patient_evidence.import_diagnosisarena \
  --csv gos/datasets/DiagnosisArena/test_set.csv
```

The default database is `traceevo_runs/rag/patient_evidence.sqlite3` (ignored
runtime data). For a small smoke import, `--case-indices 0,1` limits the
zero-based rows. It does not change the indexing or matching rules.

Example use:

```python
from traceevo.rag.patient_evidence import PatientEvidenceRetriever, PatientEvidenceStore
from traceevo.rag.tools import PatientEvidenceTool

with PatientEvidenceStore("traceevo_runs/rag/patient_evidence.sqlite3") as store:
    tool = PatientEvidenceTool(PatientEvidenceRetriever(store))
    result = tool.run(task_id="case-0", query_id="q1", query="What did the CT show?")
    print(result.source_ids)
```

Each output hit carries its stable Patient Evidence record ID. Retrieval scores
are not yet produced and are never interpreted as medical confidence. Event
ledger/provenance emission is reserved for the provenance integration phase.

## Current boundary

Implemented: shared Pydantic schemas, exam/modality aliases, stable record
normalization, SQLite schema and indexes, task-scoped retrieval, an
agent-callable source-aware adapter, a drop-in callable `legacy` /
`patient_evidence` switch, and the DiagnosisArena importer.

The compatibility switch is not installed into Core-v1. Replacing the legacy
LLM evidence retriever requires a separate, versioned integration branch.

## Medical Knowledge ingestion (Phase B)

The independent `traceevo.rag.medical_knowledge` package now supports:

- PMC OA full text through NCBI BioC JSON, with article metadata from
  ESummary, including author metadata for attribution. PMC OA/license metadata
  is checked before source bytes are kept.
- WHO guideline files only through an explicit JSON manifest that names the
  source URL, local raw file, and reuse license. There is no WHO crawler or
  implicit downloader.
- A conservative Creative Commons gate. Missing/custom/unknown licenses and
  NoDerivatives are rejected. Noncommercial licenses require the explicit
  `--allow-noncommercial` flag.
- Raw, content-addressed source files plus immutable normalized artifacts.
  Historical `schema-v2` through `schema-v4` data stores have been archived outside the project. Current
  `schema-v5` stores a `MedicalDocument`, heading-scoped `DocumentSection`s,
  typed source blocks, and direct child chunks with section-local character
  spans. A section is the offset container; it is not a retrieval parent.
- Schema-v5 uses the pinned Qwen3 embedding tokenizer's fast offset mapping:
  aim for 384 content tokens, extend to the first complete sentence boundary
  through 480, then fall back to a whitespace boundary at the hard limit.
  Chunks do not overlap. A tail under 96 tokens is merged only when it remains
  within 480. BioC abstract/body text, lists, captions, and table rows remain
  typed boundaries. PMC tables are represented as labeled row groups with
  column names and captions retained as context metadata.

Example PMC import (replace the placeholder with a real OA PMCID; check each
article's license, and use the NC opt-in only when authorized):

```bash
python -m traceevo.rag.medical_knowledge.ingest \
  --pmcid PMC123456 \
  --data-dir traceevo_runs/rag/medical_knowledge \
  --allow-noncommercial
```

Ingestion writes schema-v5 by default. `--schema-version schema-v4` keeps the
legacy paragraph/lexicon pipeline available for reproducibility.

WHO manifest shape (paths are relative to the manifest unless absolute):

```json
{
  "documents": [
    {
      "document_id": "WHO-guideline-version",
      "title": "Exact title from the source",
      "raw_path": "raw-files/guideline.pdf",
      "source_uri": "https://www.who.int/...",
      "license": "Exact reuse license shown by the source",
      "license_uri": "https://creativecommons.org/...",
      "publication_date": "YYYY-MM",
      "version": "edition/version",
      "specialty": ["specialty"]
    }
  ]
}
```

```bash
python -m traceevo.rag.medical_knowledge.ingest \
  --who-manifest path/to/who_manifest.json \
  --data-dir traceevo_runs/rag/medical_knowledge \
  --allow-noncommercial
```

The WHO adapter accepts local HTML, XML, TXT/Markdown, and PDF files. PDF
parsing uses the optional `pypdf` package. Medical Knowledge is available
through the separate local Qdrant v5 collection and source-aware retriever.
The GoS diagnosis runner now has an opt-in pilot flag (`--medical-rag`), which
passes each specialist's retrieval query to this tool in addition to the
existing patient-case evidence search. The flag is off by default. Literature
and patient findings are kept as separate history entries and provenance
records; PMC title, URL, license, and document/chunk IDs are attached to the
literature read event. The pilot uses the specialist's query directly (no
extra query-planning LLM call) and does not modify frozen evaluation artifacts.

Example single-case pilot (run from `gos/`; provide the configured gateway key
in `TRACEEVO_API_KEY` without putting it in source files):

```bash
PYTHONPATH=.:.. TRACEEVO_DATASET_CSV=datasets/DiagnosisArena/test_set_original.csv \
  python main.py --cases 79 --medical-rag --medical-rag-top-k 4 \
    --medical-rag-child-candidates-per-document 4 \
  --tag medical_rag_pilot \
  --out-csv outputs/medical_rag_pilot/results.csv \
  --html-dir outputs/medical_rag_pilot/html \
  --json-dir outputs/medical_rag_pilot/graphs \
  --log-dir logs/medical_rag_pilot
```

The original seed had two license-checked PMC articles: `PMC11879485` (Ottawa
Ankle Rules in children/adolescents; CC BY) and `PMC11100091` (systematic
review of musculoskeletal imaging-rule implementation; CC BY 4.0). Six more
CC-BY PMC OA articles have since been added:

- `PMC9502997`: adult Ottawa Ankle Rule diagnostic accuracy systematic review
  and meta-analysis.
- `PMC7204241`: bedside ultrasound diagnostic study for ankle/fifth-metatarsal
  fractures.
- `PMC9568458`: acute clinical evaluation of syndesmosis injury.
- `PMC9301067`: umbrella review of acute ankle-sprain management.
- `PMC7599579`: systematic review of conservative acute ankle-sprain treatment.
- `PMC13160383`: urgent-care adherence to ankle-sprain/Ottawa-rule guidance.

The expanded local schema-v5 corpus now contains 50 distinct PMC OA articles:
the 8 ankle/musculoskeletal seed articles above plus 42 articles across
cardiovascular, respiratory, neurologic, endocrine, renal, gastrointestinal,
infectious-disease, oncology, and rheumatology topics. All 50 passed the
default derivative-use license gate. The current normalized corpus contains
917 source sections, 5,076 typed source blocks, and 2,033 non-overlapping
retrieval chunks; the Qdrant v5 collection contains 50 document vectors and
2,033 child-chunk vectors.

The document unit is the original publication, not a disease label. A paper
may focus on one condition, a disease family or syndrome, a diagnostic method,
or a broader management question. The corpus therefore does not enforce
“one document = one disease,” and its reviews are not equivalent to an
official clinical-practice-guideline collection.

Recreate the original seed records with:

```bash
python -m traceevo.rag.medical_knowledge.ingest \
  --pmcid PMC11879485 --pmcid PMC11100091 \
  --data-dir traceevo_runs/rag/medical_knowledge
```

Recreate the six additions with:

```bash
python -m traceevo.rag.medical_knowledge.ingest \
  --pmcid PMC9502997 --pmcid PMC7204241 --pmcid PMC9568458 \
  --pmcid PMC9301067 --pmcid PMC7599579 --pmcid PMC13160383 \
  --data-dir traceevo_runs/rag/medical_knowledge
```

To migrate already-cached PMC BioC source files without downloading them again:

```bash
python -m traceevo.rag.medical_knowledge.rebuild_v5 \
  --data-dir traceevo_runs/rag/medical_knowledge
```

This reads the existing document metadata/raw-file pointers, then writes an
independent immutable `normalized/schema-v5` layer. It does not modify
schema-v4 artifacts. Section offsets address the normalized extracted text
stored in each section record, not byte offsets into the BioC JSON/XML.

The active schema-v5 corpus contains 50 articles and remains a
mixed biomedical-literature collection rather than an issuing-body clinical
practice-guideline library. A formal guideline can be added through the
WHO-manifest path after its exact document license is verified.

## Dense retrieval (Phase C)

Install the optional local retrieval dependencies:

```bash
python -m pip install -r traceevo/rag/requirements-medical-knowledge.txt
```

Index schema-v5 into a separate Qdrant local collection with the pinned Qwen3
Embedding revision (1024 dimensions, cosine distance). Existing v2/v3/v4
collections are left untouched:

```bash
python -m traceevo.rag.medical_knowledge.index \
  --data-dir traceevo_runs/rag/medical_knowledge \
  --qdrant-dir traceevo_runs/rag/qdrant_v5 \
  --dimension 1024 --batch-size 4 --device auto
```

Run document-first retrieval, global child-chunk reranking, and section-local
sentence context hydration:

```bash
python -m traceevo.rag.medical_knowledge.search \
  "When should ankle radiographs be obtained after an acute injury?" \
  --data-dir traceevo_runs/rag/medical_knowledge \
  --qdrant-dir traceevo_runs/rag/qdrant_v5 --top-k 4 --device auto
```

The retrieval sequence is: (1) compare the query with document vectors made
from title plus abstract (or a short extractive lead if no abstract exists),
(2) retrieve child chunks independently inside each candidate document,
(3) deduplicate and merge those chunks, then use Qwen3-Reranker-0.6B to rank
the merged set globally, and (4) hydrate each selected chunk with at most its
immediately preceding and following complete sentences within that source
section, subject to token budgets. It does not retrieve sections as parents
or add neighboring chunks. Table hits carry their caption and column labels;
adjacent table rows are not treated as prose sentences.
Defaults are 5 candidate documents, up to 4 child candidates per document
(up to 20 candidates for a single query), then 4 final hits. Context is capped at 1,200 Qwen tokenizer tokens per hit, with
the anchor passage capped at 800 and each neighboring sentence capped at 200
Qwen tokenizer tokens (the total cap still wins). Search options can override
these values.

The retrieval order remains document title+abstract routing -> candidate
document-local child-vector search -> deduplication/merge -> one global
cross-encoder rerank over the merged candidate set. Schema-v5 changes source
normalization, token-accurate chunking, and context hydration only; it does
not change this candidate-generation/reranking sequence.

Embedding and reranker weights are fetched from Hugging Face into its cache,
not the repository. Both model revisions are pinned. Qdrant stores document
routing vectors and direct child-chunk vectors as separate `record_type`s in
the v5 collection. Retrieval results retain source/license metadata; vector
and cross-encoder scores are relevance signals, never clinical confidence.

The current index is still a small smoke corpus. It now includes both a
pediatric Ottawa-rules validation and an adult diagnostic-accuracy review;
results must still be matched to the patient's age and setting. Population-
aware retrieval evaluation and connection to the central agent remain pending;
Core-v1 integration remains disabled.

## Medical candidate expansion and experimental rejection

The 2026-10-13 pilot compared the original 5-document/4-child configuration
with 8-document/8-child candidates, keeping final K=4. Expansion alone did not
improve the fixed 8-query test, so production defaults remain 5/4. Both limits
are configurable through `--medical-rag-document-candidates` and
`--medical-rag-child-candidates-per-document`.

An independent 6-query calibration split selected the Qwen3 reranker logit
cutoff 0.7109375. This threshold applies to the experimental 8/8 setup and is
not a probability or a default for every corpus/model. To reproduce that
configuration, use document candidates 8, child candidates 8, and
`--medical-rag-min-reranker-score 0.7109375`. Rejection is disabled by default
pending broader clinical review. Below-threshold passages are removed before
final top-K selection; all rejected yields an empty result.

Calibration artifacts are under `traceevo_runs/rag/medical_retrieval_expansion_v2/`.
The final result has fewer than K passages when filters reject candidates;
report actual returned-content precision separately from fixed-K precision.

## Expert patient-evidence structured requests

The active GoS DiagnosisArena expert retrieval content call now returns a
`PatientRetrievalRequest`: `type`, `decision`, `store`, `exam_type`,
`query_text`, and `retrieval_goal`. Every field is required. The contract uses
Pydantic `strict=True`, rejects extra fields, and uses the shared exam-type
vocabulary as an enum. `task_id` is absent from the model output schema and
the query-planning prompt data. The execution layer injects the active `case-N`
scope directly into the importer and SQL filter. Model-generated `task_id`
is rejected as an extra field. Each request names one exam; separate
exams require separate retrieval steps.

The expert prefers JSON Schema strict output. Unsupported gateway formats
fall back to JSON-object/prompt-only modes with strict local validation still
mandatory. JSON cleanup remains available, but scalar types are not loosely
coerced. Invalid requests receive one error-informed regeneration attempt;
no retrieval executes before validation succeeds.

Current-case `Diagnostic Tests` are converted to the existing typed records
in an isolated in-memory SQLite store, filtered by `task_id` and `exam_type`,
then supplied to the legacy evidence extraction LLM. Empty filters skip that
LLM call. Source fragmentation follows the existing importer: a source
fragment containing several exams can retain their shared text. The filter
guarantees record-level type/task matching, not perfect sentence-level exam
segmentation. The original source text and sealed historical runs are not
rewritten.

The optional PMC tool still uses the same query as a separate general-knowledge
lookup; medical-source selection and structured medical query output were not
changed in this implementation. The generic QueryPlanner service below
remains available but is not an additional model call in this expert path.

## Mandatory query reformulation (Phase E — partial)

`QueryPlanner` requires every request to pass through a structured-output
provider before retrieval. It emits `single` for one atomic need or `decompose`
for 2–4 independent needs. The validated plan must target the requested store,
use unique query IDs, and not copy the original query verbatim. For Patient
Evidence, `task_id` is required by the planner/service API but is deliberately
kept out of the model prompt; `RagQueryService` passes it unchanged to every
task-scoped tool call and fails closed if returned hits escape that scope.

The package does not create an LLM client or load credentials. The host
application injects its approved OpenAI-compatible client and model; queries
are sent to that configured endpoint, so use an endpoint authorized for the
data being processed (or a local/private model where required):

```python
from openai import OpenAI

from traceevo.rag.common.schemas import RagStore
from traceevo.rag.query_planner import (
    OpenAICompatibleStructuredOutputProvider,
    QueryPlanner,
)

provider = OpenAICompatibleStructuredOutputProvider(
    OpenAI(),  # configured by the host application
    model="your-approved-structured-output-model",
)
planner = QueryPlanner(provider)
plan = planner.plan(
    "When should ankle radiographs be obtained after an acute injury?",
    store=RagStore.MEDICAL_KNOWLEDGE,
)
```

`RagQueryService` can then dispatch that plan to injected
`PatientEvidenceTool` / `MedicalKnowledgeTool` instances. For Medical
Knowledge, query vectors are batch-embedded when the backend supports it;
document-local child candidate pools are merged and deduplicated by chunk ID,
then the built-in Qwen3 cross-encoder scores the full
subquery × merged-candidate matrix in one batch. Each chunk is ranked by its
best subquery score, and metadata records which subqueries retrieved it and
which query produced its best reranker score. Patient Evidence remains
task-scoped and its subquery calls are sequential. The local Qdrant document
and child searches also remain synchronous/sequential. `limit`, when set,
caps the final merged result; candidate-pool size remains controlled by the
configured document and per-document child-candidate limits.

This is a tested, injectable query-planning implementation, not a configured
model deployment or a Central Agent integration. Schema validation, provider
adapter behavior, task-scope isolation, duplicate-source merging, empty
results, result limits, and multi-query candidate pooling/reranking are covered
by unit tests. Parallel store calls, persisted query/result traces, and the
real host model configuration remain follow-up work.

## Experience vector retrieval (Phase F — implementation, not deployed)

The existing `traceevo.evolution` admission/memory pipeline remains the source
of truth. The new RAG indexer reads only its `success/` and `failure/`
partitions; `manual_review/` and `rejected/` are never opened. It validates the
partition marker against the payload type, and accepts only the fixed process-
guidance payload shapes produced by the existing experience builder. It does
not create or admit experiences itself.

The current experience extractor is `s3b-deterministic-v2`. A success memory
keeps the ordered specialist-call sequence, allowlisted central-planning
intents, and a compact workflow outline. Its retrieval key is built only from
the new case's pre-run chief complaint and physical examination, so matching
is about similar initial presentations—not a later diagnosis or outcome. The
injectable payload never contains the historical complaint, findings,
hypotheses, diagnosis, free-form PLAN response, or patient identity; it carries
only role names, intent/action codes, aggregate counts, and fixed provenance
warnings. Older admitted success payloads that contain only the deduplicated
expert list and aggregate counters remain indexable; missing v2 fields are
treated as unavailable rather than reconstructed.

The admission gate is simplified without removing its hard safety checks. A
success still requires a verified-correct outcome, complete invocation ledger,
valid trace integrity and semantic completion, and no unresolved P3 assertion
on the reasoning path. P1 soft-grounded evidence no longer blocks a useful
verified workflow; it adds a provenance-check caveat. Recoverable medium
execution anomalies (currently A0/A3) may also accompany a verified success as
a generic warning. High/critical anomalies (A1/A2/A4/A5), incomplete or
unverifiable traces, P3 use, uncertain/incorrect outcomes, and forbidden
patient/gold content remain excluded from the success partition. Localized
high/critical anomalies can still create prevention memories, while hard
integrity/completeness failures are rejected.

Index an existing, audited MemoryStore into a separate collection (never the
Medical Knowledge collection). This CLI reads the source files without
initializing/mutating the source store:

```bash
python -m traceevo.rag.experience.index \
  --memory-store path/to/admitted_memory_store \
  --qdrant-dir traceevo_runs/rag/qdrant_experience_v1 \
  --dimension 1024 --device auto
```

Only the pre-task feature summary is embedded. Qdrant stores the vector and
minimal ID/type/partition/fingerprint metadata—not the summary or injectable
payload. At retrieval time, candidates must still exist in the live admitted
memory partitions and their fingerprint must match; this prevents stale or
revoked vectors from being used. The historical task summary is used for
matching/reranking but never returned to the Planner. The only returned text is
the existing formatter's process-guidance block, which explicitly says it is
not patient evidence. Source trace IDs and the audit summary hash remain in
metadata for provenance.

For subquery plans, Experience retrieval batch-embeds the queries when
supported, unions and deduplicates each query's Qdrant candidates, and sends
the complete query × candidate matrix through one reranker batch. It ranks each
experience by its best query-pair score and retains the matching query IDs and
goals. Qdrant calls remain local and synchronous.

The initial-plan channel searches validated successes only. A caller may
explicitly request the `recovery` channel to include validated failures;
manual-review and rejected records remain excluded in both. Reranking compares
task applicability, not historical diagnoses or outcomes. A reranker score
threshold is mandatory at construction and must be selected using an
Experience retrieval validation set—there is no arbitrary “always return the
nearest memory” fallback.

For UI/demo development, a 32-record synthetic batch is provided in
`traceevo/rag/experience/fixtures/simulated_experiences.jsonl`: eight workflow
profiles vary aggregate process counts/roles from already-run S3D admitted-
success examples (training cases only; the manual-review example is excluded),
eight further workflow examples cover source scope, uncertainty, query
planning, evidence independence, and reporting; four prevention examples
illustrate existing A1/A2/A3/A5 guard rules; and twelve more synthetic
prevention examples cover task isolation, missing-versus-negative findings,
context/score misuse, and admission discipline. None is a replay, patient case,
or validated experience. Seed it into a separate collection:

```bash
python -m traceevo.rag.experience.seed_simulated \
  --qdrant-dir traceevo_runs/rag/qdrant_experience_simulated_v1 \
  --dimension 1024 --device auto
```

Every row carries `simulation_only=true` and `online_eligible=false`. The
collection name/path is distinct from the admitted Experience collection; the
schema has no diagnosis/outcome/source-case fields and rejects those keys in
the payload. `ExperienceRetriever` cannot read this collection. Do not copy
simulated rows into the success/failure partitions or use them as
retrieval-quality evidence.

The default local simulated collection has now been seeded with 32 records at
`traceevo_runs/rag/qdrant_experience_simulated_v1`, collection
`traceevo_experience_simulated_v1`. An embedding + Qdrant round-trip smoke
query returned simulated IDs with `online_eligible=false`; this checks storage
and retrieval mechanics only, not experience relevance or clinical quality. The
web system's `/knowledge-bases` page now shows concise Chinese summaries in
structured fields first, with aggregate corpus counts below. The display omits
patient task/record IDs; the admission flags and collection isolation remain
unchanged in storage and retrieval code.

This phase is implemented and unit/Qdrant-round-trip tested, but no local
production Experience collection has been built from a real admitted-memory
source. The synthetic demo collection is separate. The retriever is not wired
into Core-v1, and the threshold is not yet calibrated. Treat it as an
integration seam until those steps and gold-leakage evaluation are done.

### Other guideline repositories checked

Network/access and rights checks on 2026-09-25 found:

- [WHO IRIS OAI-PMH](https://iris.who.int/oai/request?verb=Identify): anonymous
  `Identify`, `ListSets`, and `ListRecords` requests work even though the
  DSpace REST API returned 401. `oai_dc` records expose `dc:rights` and
  identifiers; this is a useful discovery/rights-metadata path, not yet a
  bulk full-text importer. Rights are per item (the sample metadata included
  CC BY-NC-SA 3.0 IGO), so check the license and the actual bitstream before
  importing.
- [NICE NG38](https://www.nice.org.uk/guidance/ng38) and its recommendations
  are reachable. NICE's [reuse terms](https://www.nice.org.uk/reusing-our-content/nice-uk-open-content-licence)
  describe a UK-only open-content licence; international reuse beyond personal
  research/study needs a separate agreement. NICE is therefore not in the
  default corpus for this project.
- [VA/DoD Clinical Practice Guidelines](https://www.healthquality.va.gov/guidelines/)
  pages and PDF links are reachable, but document-level reuse and third-party
  rights were not verified. Treat it as a candidate catalogue until each PDF
  passes the license gate.
- The [GIN International Guidelines Library](https://guidelines.ebmportal.com/)
  and [PAHO BIGG](https://sites.bvsalud.org/bigg/en/biblio/) are reachable as
  discovery catalogues; the linked guideline's issuing organization retains
  its own rights, so catalog access does not imply full-text reuse permission.
- The probed CDC guideline URL returned 403 in this environment. This says
  nothing about CDC's general public-domain policy; it only means that route
  could not be validated from this runtime.

Accordingly, PMC OA is the current seed source; WHO OAI-PMH is the strongest
next discovery integration, while the existing WHO local-manifest adapter can
be used after obtaining and checking a specific document. No scraped catalogue
metadata is treated as clinical evidence.
