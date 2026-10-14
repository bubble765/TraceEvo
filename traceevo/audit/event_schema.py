"""ProvenanceEvent schema, canonical serialisation and hashing (amendment §3.1).

Canonical form: JSON with sorted keys, no insignificant whitespace, UTF-8 kept
as-is (ensure_ascii=False). The hash covers every field except `event_hash`
itself, so any mutation of a logged event invalidates its hash.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

GENESIS_HASH = "0" * 64

EVENT_TYPES = (
    # New retrievals emit one completed TOOL_CALL with query and output.
    # EVIDENCE_READ remains accepted so older sealed traces still validate.
    "PLAN", "AGENT_CALL", "TOOL_CALL", "EVIDENCE_READ", "ANALYSIS",
    "GRAPH_UPDATE", "BACKTRACK", "DRILL_DOWN", "FINAL_REPORT",
)

SOURCE_CLASSES = (
    "INPUT_OBSERVATION",     # from the initial chief complaint / physical exam
    "RETRIEVED_RECORD",      # actually returned by the evidence retriever
    "AGENT_GENERATED",       # produced by an agent with no real retrieval source
    "SYSTEM_GENERATED",      # framework-internal control object
    "UNKNOWN",
)

# Optional, non-authoritative semantic annotation (amendment §1 D2).
SEMANTIC_KINDS = (
    "clinical_evidence", "domain_knowledge", "agent_inference",
    "absence_claim", "recommendation", "hypothesis", "conclusion",
)

# Execution outcome (S2 amendment §3.1). `degraded` marks a path that completed
# but on a fallback -- upstream returns its fixed text unchanged; only the
# observation is added.
STATUSES = ("success", "degraded", "failed")

# Why an event did not simply succeed. NONE means no reason to report.
REASON_CODES = (
    "NONE",
    "BUDGET_EXHAUSTED",                  # retrieval budget ran out before an analysis
    "INSUFFICIENT_INFORMATION_FALLBACK",  # upstream's fixed fallback text was returned
    "INVALID_EXPERT_DISPATCH",           # requested agent does not exist / not permitted
    "TOOL_ERROR",
    "LLM_ERROR",
    "PARSER_ERROR",                      # model output unusable (unparseable / inconsistent)
    "TRACE_INTEGRITY_ERROR",
)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for every hash in this package."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class ProvenanceEvent:
    """One recorded operation in the GoS execution."""

    event_id: str
    task_id: str
    step_id: int
    timestamp: str
    event_type: str
    actor_id: str
    actor_role: str
    tool_or_model: str = ""
    input_refs: List[str] = field(default_factory=list)
    output_refs: List[str] = field(default_factory=list)
    causal_node_refs: List[str] = field(default_factory=list)
    parent_event_ids: List[str] = field(default_factory=list)
    source_class: str = "UNKNOWN"
    # S2 §1 D1: `model_id` is what the config names (reliable). `model_version`
    # stays None unless the provider actually reports one, and
    # `version_observable` records which of the two the reader is looking at --
    # S1 wrote the model name into `model_version`, which overstated it.
    model_id: str = ""
    model_version: Optional[str] = None
    version_observable: bool = False
    status: str = "success"
    reason_code: str = "NONE"
    prev_hash: str = GENESIS_HASH
    event_hash: str = ""

    # Extra, non-hashed-indexed context (still covered by the hash).
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type {self.event_type!r}; "
                             f"allowed: {EVENT_TYPES}")
        if self.source_class not in SOURCE_CLASSES:
            raise ValueError(f"unknown source_class {self.source_class!r}; "
                             f"allowed: {SOURCE_CLASSES}")
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {self.status!r}")
        if self.reason_code not in REASON_CODES:
            raise ValueError(f"reason_code must be one of {REASON_CODES}, "
                             f"got {self.reason_code!r}")

    def hashable(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("event_hash", None)
        return data

    def compute_hash(self) -> str:
        return sha256_hex(canonical_json(self.hashable()))

    def seal(self) -> "ProvenanceEvent":
        """Compute event_hash from the current contents."""
        self.event_hash = self.compute_hash()
        return self

    def verify(self) -> bool:
        return bool(self.event_hash) and self.event_hash == self.compute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProvenanceEvent":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
