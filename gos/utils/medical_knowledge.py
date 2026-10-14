"""Optional Medical Knowledge RAG runtime for the GoS diagnosis pilot.

The expert's already-formed retrieval query is sent directly to the existing
document-first Medical Knowledge retriever. This pilot intentionally avoids an
additional query-planning LLM call; patient findings and general literature
remain separate retrieval records.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MedicalKnowledgeRuntime:
    tool: Any
    store: Any

    def close(self) -> None:
        self.store.close()


_MEDICAL_KNOWLEDGE_CORPORA = {
    "v5": (
        "traceevo_runs/rag/medical_knowledge",
        "traceevo_runs/rag/qdrant_v5",
        "traceevo_medical_knowledge_v5",
    ),
    # Candidate corpus for the blinded case-57 retrieval experiment. The
    # original v5 artifacts remain untouched so this can be compared and
    # rolled back without rebuilding or deleting the existing collection.
    "dle_v6": (
        "traceevo_runs/rag/medical_knowledge_dle_v6",
        "traceevo_runs/rag/qdrant_dle_v6",
        "traceevo_medical_knowledge_dle_v6",
    ),
}


def resolve_medical_knowledge_corpus_paths(
    project_root: str | Path,
    corpus_version: str = "v5",
) -> tuple[Path, Path, str]:
    """Resolve an isolated corpus/index pair without mutating either version."""
    try:
        data_relative, qdrant_relative, collection_name = _MEDICAL_KNOWLEDGE_CORPORA[
            corpus_version
        ]
    except KeyError as exc:
        supported = ", ".join(sorted(_MEDICAL_KNOWLEDGE_CORPORA))
        raise ValueError(
            f"unknown medical knowledge corpus {corpus_version!r}; supported: {supported}"
        ) from exc
    root = Path(project_root).resolve()
    return root / data_relative, root / qdrant_relative, collection_name


def build_medical_knowledge_runtime(
    *,
    project_root: str | Path | None = None,
    final_k: int = 4,
    corpus_version: str = "v5",
    child_candidates_per_document: int = 4,
    document_candidate_k: int = 5,
    min_reranker_score: float | None = None,
) -> MedicalKnowledgeRuntime:
    """Build a pinned schema-v5/Qdrant runtime for an isolated corpus version."""
    if final_k <= 0:
        raise ValueError("final_k must be positive")
    if child_candidates_per_document <= 0:
        raise ValueError("child_candidates_per_document must be positive")
    root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
    data_dir, qdrant_dir, collection_name = resolve_medical_knowledge_corpus_paths(
        root,
        corpus_version,
    )

    from traceevo.rag.medical_knowledge.context import DocumentSectionStore
    from traceevo.rag.medical_knowledge.embedding import QWEN3_EMBEDDING_REVISION, Qwen3Embedder
    from traceevo.rag.medical_knowledge.reranker import QWEN3_RERANKER_REVISION, Qwen3Reranker
    from traceevo.rag.medical_knowledge.retriever import MedicalKnowledgeRetriever
    from traceevo.rag.medical_knowledge.splitter import Qwen3TokenizerCounter
    from traceevo.rag.medical_knowledge.vector_store import QdrantMedicalKnowledgeStore
    from traceevo.rag.tools.medical_knowledge_tool import MedicalKnowledgeTool

    embedder = Qwen3Embedder(
        dimension=1024,
        batch_size=8,
        device="auto",
        revision=QWEN3_EMBEDDING_REVISION,
    )
    store = QdrantMedicalKnowledgeStore(
        qdrant_dir,
        collection_name=collection_name,
        dimension=1024,
    )
    try:
        sections = DocumentSectionStore(
            data_dir,
            schema_version="schema-v5",
            token_counter=Qwen3TokenizerCounter(revision=QWEN3_EMBEDDING_REVISION),
        )
        retriever = MedicalKnowledgeRetriever(
            embedder,
            store,
            section_store=sections,
            final_k=final_k,
            document_candidate_k=document_candidate_k,
            child_candidates_per_document=child_candidates_per_document,
            reranker=Qwen3Reranker(
                revision=QWEN3_RERANKER_REVISION,
                device="auto",
                batch_size=4,
            ),
            max_context_tokens=900,
            max_anchor_tokens=480,
            max_neighbor_tokens=200,
            min_reranker_score=min_reranker_score,
        )
    except Exception:
        store.close()
        raise
    return MedicalKnowledgeRuntime(tool=MedicalKnowledgeTool(retriever), store=store)


def render_medical_knowledge_result(result: Any) -> tuple[str, list[dict[str, Any]]]:
    """Format literature hits so they cannot be mistaken for case observations."""
    if not result.hits:
        return (
            "[GENERAL MEDICAL LITERATURE — NOT PATIENT-SPECIFIC EVIDENCE]\n"
            "No relevant passage was retrieved from the indexed PMC OA corpus; "
            "this is not evidence against any diagnosis.",
            [],
        )

    sections = [
        "[GENERAL MEDICAL LITERATURE — NOT PATIENT-SPECIFIC EVIDENCE]",
        "Use only as general clinical context. Do not report these statements as findings from this patient. "
        "Retrieval relevance is not diagnostic confidence.",
    ]
    source_refs: list[dict[str, Any]] = []
    for index, hit in enumerate(result.hits, start=1):
        metadata = hit.metadata
        title = str(metadata.get("title") or hit.document_id or "PMC OA article")
        source_uri = str(metadata.get("source_uri") or "")
        license_id = str(metadata.get("license_id") or metadata.get("license") or "")
        document_id = str(hit.document_id or "")
        heading_paths = metadata.get("heading_paths") or []
        heading = "; ".join(
            " > ".join(str(part) for part in path if part)
            for path in heading_paths
            if isinstance(path, list) and path
        )
        citation_lines = [f"[{index}] {title}"]
        if document_id:
            citation_lines.append(f"PMCID: {document_id}")
        if heading:
            citation_lines.append(f"Section: {heading}")
        if source_uri:
            citation_lines.append(f"Source: {source_uri}")
        if license_id:
            citation_lines.append(f"License: {license_id}")
        citation_lines.append(f"Passage: {hit.text}")
        sections.append("\n".join(citation_lines))
        source_refs.append({
            "source_id": hit.source_id,
            "document_id": document_id,
            "title": title,
            "source_uri": source_uri,
            "license_id": license_id,
            "section_id": metadata.get("section_id"),
            "retrieval_score": hit.retrieval_score,
            "retrieval_score_kind": metadata.get("reranker_score_kind"),
        })
    return "\n\n".join(sections), source_refs
