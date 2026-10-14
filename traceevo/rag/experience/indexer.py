"""Index only gate-admitted experience records into the separate vector store."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Protocol

from traceevo.evolution.experience import (
    FAILURE_PATTERNS,
    PLANNING_INTENTS,
    WORKFLOW_ACTIONS,
    grounding_constraints,
)
from traceevo.rag.common.schemas import ExperienceRecord


AdmissionPartition = Literal["success", "failure"]
_STRATEGY_FIELDS = {
    "expert_roles_used",
    "expert_sequence",
    "planning_steps",
    "workflow_sequence",
    "risk_flags",
    "retrieval_count",
    "backtrack_count",
    "drilldown_count",
    "graph_update_count",
    "analysis_count",
    "plan_count",
}
_ALLOWED_GROUNDING_TEXT = {
    item
    for p1 in (0, 1)
    for p2 in (0, 1)
    for p3 in (0, 1)
    for item in grounding_constraints({"p1": p1, "p2": p2, "p3": p3})
}
_STRATEGY_COUNTERS = {
    "retrieval_count", "backtrack_count", "drilldown_count",
    "graph_update_count", "analysis_count", "plan_count",
}
_RISK_FLAGS = {
    "soft_grounded_evidence_present",
    "recoverable_execution_warning",
}
_WORKFLOW_ACTIONS = set(WORKFLOW_ACTIONS.values()) | {"additional_steps"}


@dataclass(frozen=True)
class IndexedExperience:
    record: ExperienceRecord
    partition: AdmissionPartition
    fingerprint: str


@dataclass(frozen=True)
class ExperienceIndexResult:
    indexed: int
    validated_success: int
    validated_failure: int


class AdmittedMemorySource(Protocol):
    def entries(self, partition: str) -> list[dict[str, Any]]: ...

    def audit_links(self) -> list[dict[str, Any]]: ...


class ExperienceEmbedder(Protocol):
    dimension: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class ExperienceVectorWriter(Protocol):
    dimension: int

    def upsert(self, records: list[IndexedExperience], vectors: list[list[float]]) -> int: ...


class ExperienceIndexError(ValueError):
    """An admitted-memory artifact violates the online Experience contract."""


def fingerprint_experience(record: ExperienceRecord, partition: str) -> str:
    body = {
        "partition": partition,
        "record": record.model_dump(mode="json"),
    }
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_injectable_payload(payload: Any, experience_type: str) -> dict[str, Any]:
    """Allow only the fixed process-guidance shapes produced by evolution."""
    if not isinstance(payload, dict) or payload.get("type") != experience_type:
        raise ExperienceIndexError("experience type and injectable payload type do not match")

    if experience_type == "validated_success":
        if set(payload) != {"type", "strategy", "grounding_constraints"}:
            raise ExperienceIndexError("validated-success payload contains unsupported fields")
        strategy = payload.get("strategy")
        constraints = payload.get("grounding_constraints")
        if not isinstance(strategy, dict) or set(strategy) - _STRATEGY_FIELDS:
            raise ExperienceIndexError("validated-success strategy contains unsupported fields")
        for field in ("expert_roles_used", "expert_sequence"):
            roles = strategy.get(field, [])
            if not isinstance(roles, list) or any(
                not isinstance(role, str) or not role.strip() for role in roles
            ):
                raise ExperienceIndexError(f"{field} must contain non-empty role names")
        steps = strategy.get("planning_steps", [])
        if not isinstance(steps, list):
            raise ExperienceIndexError("planning_steps must be a list")
        for step in steps:
            if not isinstance(step, dict) or set(step) != {"expert_role", "intent"}:
                raise ExperienceIndexError("planning_steps contain an unsupported shape")
            if not isinstance(step["expert_role"], str) or not step["expert_role"].strip():
                raise ExperienceIndexError("planning step expert_role must be non-empty")
            if step["intent"] not in PLANNING_INTENTS:
                raise ExperienceIndexError("planning step intent is not allowlisted")
        workflow = strategy.get("workflow_sequence", [])
        if not isinstance(workflow, list) or any(item not in _WORKFLOW_ACTIONS for item in workflow):
            raise ExperienceIndexError("workflow_sequence contains an unsupported action")
        risk_flags = strategy.get("risk_flags", [])
        if not isinstance(risk_flags, list) or any(flag not in _RISK_FLAGS for flag in risk_flags):
            raise ExperienceIndexError("risk_flags contains an unsupported value")
        for key, value in strategy.items():
            if key in {"expert_roles_used", "expert_sequence", "planning_steps",
                       "workflow_sequence", "risk_flags"}:
                continue
            if key not in _STRATEGY_COUNTERS or not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ExperienceIndexError(f"strategy field {key!r} must be a non-negative integer")
        if not isinstance(constraints, list) or any(item not in _ALLOWED_GROUNDING_TEXT for item in constraints):
            raise ExperienceIndexError("grounding constraints must come from the fixed allowlist")
        return payload

    if experience_type == "validated_failure":
        if set(payload) != {"type", "failure_pattern", "avoid", "recommended_check"}:
            raise ExperienceIndexError("validated-failure payload contains unsupported fields")
        pattern = payload.get("failure_pattern")
        patterns = {value["failure_pattern"]: value for value in FAILURE_PATTERNS.values()}
        expected = patterns.get(pattern)
        if expected is None:
            # build_candidate has one fixed fallback for unknown detector IDs.
            expected = {
                "failure_pattern": "ANOMALOUS_EXECUTION",
                "avoid": [],
                "recommended_check": [
                    "Review the localized anomaly before reusing anything from this trace"
                ],
            }
            if pattern != expected["failure_pattern"]:
                raise ExperienceIndexError(f"unrecognized validated-failure pattern: {pattern!r}")
        if payload.get("avoid") != expected["avoid"] or payload.get("recommended_check") != expected["recommended_check"]:
            raise ExperienceIndexError("failure guidance does not match the fixed detector pattern")
        return payload

    raise ExperienceIndexError(f"unsupported experience type: {experience_type!r}")


def admitted_experiences(
    source: AdmittedMemorySource,
    *,
    partitions: tuple[AdmissionPartition, ...] = ("success", "failure"),
) -> list[IndexedExperience]:
    """Read only selected success/failure partitions; review/reject are unreachable."""
    if not partitions or any(partition not in {"success", "failure"} for partition in partitions):
        raise ExperienceIndexError("only success/failure admission partitions may be read online")
    indexed: list[IndexedExperience] = []
    seen: set[str] = set()
    audit_by_id: dict[str, dict[str, Any]] = {}
    for link in source.audit_links():
        memory_id = str(link.get("memory_id") or "")
        if not memory_id:
            continue
        previous_link = audit_by_id.get(memory_id)
        if previous_link is not None and any(
            previous_link.get(key) != link.get(key)
            for key in ("partition", "source_trace_id", "audit_summary_hash")
        ):
            raise ExperienceIndexError(f"conflicting admission audit links for {memory_id}")
        audit_by_id[memory_id] = link
    for partition in partitions:
        for entry in source.entries(partition):
            if entry.get("partition") != partition:
                raise ExperienceIndexError("memory entry partition marker does not match its store directory")
            expected_type = "validated_success" if partition == "success" else "validated_failure"
            payload = _validate_injectable_payload(entry.get("payload"), expected_type)
            experience_id = str(entry.get("memory_id") or "").strip()
            source_trace_id = str(entry.get("source_trace_id") or "").strip()
            if not experience_id or not source_trace_id:
                raise ExperienceIndexError("admitted experiences require stable experience and source trace IDs")
            if experience_id in seen:
                raise ExperienceIndexError(f"duplicate experience_id in admitted partitions: {experience_id}")
            seen.add(experience_id)
            summary = entry.get("task_retrieval_key")
            if not isinstance(summary, str) or not summary.strip():
                raise ExperienceIndexError("admitted experience is missing its pre-task feature summary")
            audit_hash = str(entry.get("audit_summary_hash") or "").strip()
            if not audit_hash:
                raise ExperienceIndexError("admitted experience is missing its audit summary hash")
            audit_link = audit_by_id.get(experience_id)
            if audit_link is None:
                raise ExperienceIndexError(f"experience {experience_id} has no admission audit link")
            if (
                audit_link.get("partition") != partition
                or audit_link.get("source_trace_id") != source_trace_id
                or audit_link.get("audit_summary_hash") != audit_hash
            ):
                raise ExperienceIndexError(f"experience {experience_id} does not match its admission audit link")
            record = ExperienceRecord.model_validate({
                "experience_id": experience_id,
                "experience_type": expected_type,
                "task_feature_summary": summary,
                "experience_payload": payload,
                "source_trace_ids": [source_trace_id],
                "audit_summary_hash": audit_hash,
                "outcome_verdict": str(entry.get("outcome_verdict") or "").strip(),
            })
            indexed.append(IndexedExperience(
                record=record,
                partition=partition,
                fingerprint=fingerprint_experience(record, partition),
            ))
    return indexed


class ExperienceIndexer:
    """Batch embed eligible memory summaries and upsert into Experience Qdrant."""

    def __init__(self, embedder: ExperienceEmbedder, store: ExperienceVectorWriter) -> None:
        if embedder.dimension != store.dimension:
            raise ValueError("experience embedder and vector store dimensions do not match")
        self.embedder = embedder
        self.store = store

    def index_memory_store(self, source: AdmittedMemorySource) -> ExperienceIndexResult:
        records = admitted_experiences(source)
        if not records:
            return ExperienceIndexResult(indexed=0, validated_success=0, validated_failure=0)
        vectors = self.embedder.embed_documents([
            item.record.task_feature_summary for item in records
        ])
        if len(vectors) != len(records):
            raise RuntimeError("experience embedder returned the wrong number of vectors")
        written = self.store.upsert(records, vectors)
        if written != len(records):
            raise RuntimeError(f"experience store acknowledged {written} of {len(records)} vectors")
        return ExperienceIndexResult(
            indexed=written,
            validated_success=sum(item.partition == "success" for item in records),
            validated_failure=sum(item.partition == "failure" for item in records),
        )
