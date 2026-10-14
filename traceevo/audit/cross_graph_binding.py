"""Cross-graph binding and deterministic source classification (amendment §3.3, §1 D2).

Binds, for every GoS causal node:

    provenance_event_id  <->  GoS node_id  <->  retrieval record_id / input span

and assigns a `source_class` **deterministically** -- no LLM judgement is
involved, so the resulting grounding metric is repeatable.

Classification rules (documented, and re-runnable via `--sensitivity`)
--------------------------------------------------------------------
Let `norm(x)` be lowercase with whitespace collapsed.

* `SYSTEM_GENERATED` -- framework-internal node (the root Signal node).
* `INPUT_OBSERVATION` -- Evidence node created by `Central_Agent.ingest()`.
  Its content is derived from the chief complaint and physical examination,
  which are the only inputs given to the model.
* `RETRIEVED_RECORD` -- Evidence node created later whose label is grounded in
  the output on a completed retrieval `TOOL_CALL` event (or a legacy
  `EVIDENCE_READ` event):
    - tier `verbatim`: one normalized string contains the other, and the
      contained string is at least `min_verbatim_chars` long;
    - tier `paraphrase`: content-token coverage of the label against a record
      is >= `paraphrase_min_token_coverage` (default 0.6) with at least
      `min_content_tokens` tokens.
* `AGENT_GENERATED` -- everything else created by an agent.
* `UNKNOWN` -- should not occur; kept so silent failures are visible.

The tier and the matched record id are stored on every binding, so any
classification can be audited back to the exact string comparison.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .event_schema import canonical_json, sha256_hex

_STOPWORDS = set(
    "a an the of and or to in on for with was were is are be been being his her "
    "their patient at as by from that this these those he she it they no not did "
    "has had have who whom which".split())

# S2 §4.1 P2. The S1 patterns were too narrow: they missed most of the natural
# absence/limitation claims the model actually writes ("was not obtained",
# "have not been performed", "remain unavailable"), which would have pushed
# legitimate derived nodes into P3 and destroyed the clean-trace FPR. Each
# pattern below was added because at least one Dev-Small node needed it.
_ABSENCE_RE = re.compile(
    r"(\b(absent|unavailable|non-?diagnostic|denied)\b"
    r"|\bnot\s+(?:been\s+)?(available|obtained|performed|identified|retrieved|"
    r"confirmed|reported|found|detected|observed|demonstrated|documented|"
    r"yet|supporting|pursued)\b"
    r"|\b(was|were|are|is)\s+(normal|negative|unremarkable)\b"
    r"|\bno\s+\w{0,20}\s?(identified|found|noted|reported|present|seen|detected|evidence)\b"
    r"|\bno evidence\b"
    r"|\bdoes not (show|reveal|demonstrate|identify)\b"
    r"|\bremain(?:s|ed)?\s+(unknown|unavailable|unclear|negative)\b"
    r"|\bcannot be\b"
    r"|\bwithout\s+(definite|evidence|sign|clear)\b"
    r"|\bexclude[sd]?\b)", re.I)

# "suggest/indicate/consistent with/favour" is an *interpretation*, not a
# recommendation. S1 lumped them together; they are now separate kinds because
# the CSV reports `semantic_kind` and a wrong label there is a wrong claim.
_INFERENCE_RE = re.compile(
    r"\b(suggest(?:s|ed|ing)?|indicat(?:es|ed|ive)|consistent with|"
    r"in favou?r of|favou?rs?|reflects?|represents?|most likely|"
    r"likely (?:due to|represents|indicates)|compatible with|"
    r"concludes?|independently (?:supports?|concludes?)|exclude[sd]?)\b", re.I)
_RECOMMEND_RE = re.compile(
    r"\b(recommend(?:s|ed|ation)?|should (?:be|undergo)|advise[sd]?|"
    r"consider(?:ed)? (?:a|the)?\s?(?:dedicated|further|additional)|"
    r"warrants?|indicated for|further (?:imaging|testing|evaluation|workup|"
    r"investigation)|if clinically indicated|expedite[sd]?)\b", re.I)
_KNOWLEDGE_RE = re.compile(
    r"\b(classically|typically|characteristically|is associated with|"
    r"are associated with|is known to|usually indicates|hallmark|"
    r"pathognomonic|suggests? (?:a )?(?:diagnosis|entity)|"
    r"is a (?:targeted|first-line|standard|common|rare)\b|"
    r"primarily used (?:for|to)|is (?:expressed|seen|found) in|"
    r"rather than|first-line (?:therapy|treatment|agent)|standard of care)\b",
    re.I)


def norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def content_tokens(text: Any) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]+", norm(text))
            if w not in _STOPWORDS and len(w) > 2]


def token_coverage(label: str, record: str) -> float:
    """Fraction of the label's content tokens present (with multiplicity) in the record."""
    lt = content_tokens(label)
    if not lt:
        return 0.0
    pool: Dict[str, int] = {}
    for w in content_tokens(record):
        pool[w] = pool.get(w, 0) + 1
    hit = 0
    for w in lt:
        if pool.get(w, 0) > 0:
            pool[w] -= 1
            hit += 1
    return hit / len(lt)


# ------------------------------------------------------------------- records
def record_hash(task_id: str, query: str, text: str, expert: str) -> str:
    """Integrity hash of one retrieval record (S2 amendment §3.3).

    Covers the fields that determine what the record *is*. Mutating the stored
    text while keeping the record_id makes this hash disagree with the value
    recorded on the binding, which is what A2 `record_tampering` exploits.
    """
    return sha256_hex(canonical_json({
        "task_id": task_id, "query": query, "text": text, "expert": expert,
    }))


@dataclass
class RetrievalRecord:
    record_id: str
    retrieval_event_id: str
    query: str
    text: str
    step_id: int
    expert: str
    task_id: str = ""
    record_hash: str = ""
    source_store: Optional[str] = None
    source_refs: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.record_hash:
            self.record_hash = record_hash(self.task_id, self.query, self.text,
                                           self.expert)

    def recompute_hash(self) -> str:
        return record_hash(self.task_id, self.query, self.text, self.expert)

    def verify(self) -> bool:
        return bool(self.record_hash) and self.record_hash == self.recompute_hash()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NodeBinding:
    node_id: str
    node_type: str
    label: str
    created_by_event: str = ""
    created_at_step: int = -1
    source_class: str = "UNKNOWN"
    semantic_kind: str = ""
    match_tier: str = "none"
    matched_record_id: str = ""
    matched_retrieval_event: str = ""
    match_coverage: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def guess_semantic_kind(label: str, node_type: str = "Evidence") -> str:
    """Optional, non-authoritative annotation (amendment §1 D2).

    Order matters and encodes a precedence: a limitation on what is *known*
    ("DIF was not available") is an absence claim even when it also reads like
    a recommendation. Absence and recommendation are checked before inference
    for the same reason.
    """
    if node_type == "Hypothesis":
        return "hypothesis"
    if _ABSENCE_RE.search(label):
        return "absence_claim"
    if _RECOMMEND_RE.search(label):
        return "recommendation"
    if _KNOWLEDGE_RE.search(label):
        return "domain_knowledge"
    if _INFERENCE_RE.search(label):
        return "agent_inference"
    return "clinical_evidence"


def classify_evidence(label: str,
                      records: List[RetrievalRecord],
                      cfg: Optional[Dict[str, Any]] = None
                      ) -> Tuple[str, str, Optional[RetrievalRecord], float]:
    """Return (source_class, match_tier, matched_record, coverage)."""
    cfg = cfg or {}
    min_verbatim = int(cfg.get("min_verbatim_chars", 25))
    min_tokens = int(cfg.get("min_content_tokens", 4))
    use_verbatim = cfg.get("verbatim", True)
    use_paraphrase = cfg.get("paraphrase", True)
    thr = float(cfg.get("paraphrase_min_token_coverage", 0.6))

    n_label = norm(label)
    best: Optional[RetrievalRecord] = None
    best_cov = 0.0

    if use_verbatim and len(n_label) >= min_verbatim:
        for rec in records:
            n_rec = norm(rec.text)
            if not n_rec:
                continue
            if n_label in n_rec or (len(n_rec) >= min_verbatim and n_rec in n_label):
                return "RETRIEVED_RECORD", "verbatim", rec, 1.0

    if use_paraphrase and len(content_tokens(label)) >= min_tokens:
        for rec in records:
            cov = token_coverage(label, rec.text)
            if cov > best_cov:
                best_cov, best = cov, rec
        if best is not None and best_cov >= thr:
            return "RETRIEVED_RECORD", "paraphrase", best, round(best_cov, 4)

    return "AGENT_GENERATED", "none", best, round(best_cov, 4)


class BindingRegistry:
    """Accumulates retrieval records and node bindings for one task."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.records: List[RetrievalRecord] = []
        self.bindings: Dict[str, NodeBinding] = {}
        self._record_seq = 0

    # ------------------------------------------------------------- retrieval
    def new_record_id(self) -> str:
        self._record_seq += 1
        return f"rec-{self.task_id}-{self._record_seq:04d}"

    def add_record(self, query: str, text: str, retrieval_event_id: str,
                   step_id: int, expert: str,
                   record_id: Optional[str] = None,
                   record_hash: Optional[str] = None,
                   source_store: Optional[str] = None,
                   source_refs: Optional[List[Dict[str, Any]]] = None) -> RetrievalRecord:
        """Register a retrieval record.

        `record_id` is allocated before the completed TOOL_CALL event is
        emitted. The record body stays in memory for binding. The event is the
        canonical persisted carrier for its text.
        """
        rec = RetrievalRecord(record_id=record_id or self.new_record_id(),
                              retrieval_event_id=retrieval_event_id,
                              query=query, text=text, step_id=step_id,
                              expert=expert, task_id=self.task_id,
                              record_hash=record_hash or "",
                              source_store=source_store,
                              source_refs=list(source_refs or []))
        self.records.append(rec)
        return rec

    # --------------------------------------------------------------- binding
    def bind(self, node_id: str, node_type: str, label: str,
             created_by_event: str, step_id: int,
             phase: str, cfg: Optional[Dict[str, Any]] = None) -> NodeBinding:
        """Classify and store a binding for a newly created causal node."""
        if node_type == "Signal":
            binding = NodeBinding(
                node_id=node_id, node_type=node_type, label=label,
                created_by_event=created_by_event, created_at_step=step_id,
                source_class="SYSTEM_GENERATED", semantic_kind="",
                match_tier="n/a")
        elif node_type == "Evidence" and phase == "ingest":
            binding = NodeBinding(
                node_id=node_id, node_type=node_type, label=label,
                created_by_event=created_by_event, created_at_step=step_id,
                source_class="INPUT_OBSERVATION",
                semantic_kind=guess_semantic_kind(label, node_type),
                match_tier="input")
        elif node_type == "Evidence":
            src, tier, rec, cov = classify_evidence(label, self.records, cfg)
            binding = NodeBinding(
                node_id=node_id, node_type=node_type, label=label,
                created_by_event=created_by_event, created_at_step=step_id,
                source_class=src,
                semantic_kind=guess_semantic_kind(label, node_type),
                match_tier=tier,
                matched_record_id=rec.record_id if rec else "",
                matched_retrieval_event=rec.retrieval_event_id if rec else "",
                match_coverage=cov)
        elif node_type == "Hypothesis":
            binding = NodeBinding(
                node_id=node_id, node_type=node_type, label=label,
                created_by_event=created_by_event, created_at_step=step_id,
                source_class="AGENT_GENERATED", semantic_kind="hypothesis",
                match_tier="n/a")
        else:
            binding = NodeBinding(
                node_id=node_id, node_type=node_type, label=label,
                created_by_event=created_by_event, created_at_step=step_id,
                source_class="UNKNOWN")

        self.bindings[node_id] = binding
        return binding

    # ---------------------------------------------------------------- export
    def grounding_summary(self) -> Dict[str, Any]:
        by_source: Dict[str, int] = {}
        by_kind: Dict[str, int] = {}
        evidence_total = 0
        evidence_bound = 0
        for b in self.bindings.values():
            by_source[b.source_class] = by_source.get(b.source_class, 0) + 1
            if b.semantic_kind:
                by_kind[b.semantic_kind] = by_kind.get(b.semantic_kind, 0) + 1
            if b.node_type == "Evidence":
                evidence_total += 1
                if b.source_class in ("RETRIEVED_RECORD", "INPUT_OBSERVATION"):
                    evidence_bound += 1
        return {
            "task_id": self.task_id,
            "n_nodes_bound": len(self.bindings),
            "by_source_class": by_source,
            "by_semantic_kind": by_kind,
            "evidence_total": evidence_total,
            "evidence_source_bound": evidence_bound,
            "evidence_source_bound_rate": (round(evidence_bound / evidence_total, 4)
                                           if evidence_total else None),
            "n_retrieval_records": len(self.records),
        }

    def to_dict(self, *, include_record_text: bool = False) -> Dict[str, Any]:
        records = []
        for record in self.records:
            row = record.to_dict()
            if not include_record_text:
                row.pop("text", None)
            records.append(row)
        return {
            "task_id": self.task_id,
            # Persist record identity and provenance metadata here. New event
            # rows carry the retrieval output, so do not duplicate it in this
            # cross-graph index.
            "records": records,
            "bindings": [b.to_dict() for b in self.bindings.values()],
            "grounding_summary": self.grounding_summary(),
        }


def records_from_events(
    events: List[Dict[str, Any]],
    legacy_records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Rebuild retrieval records from completed events.

    New traces put the returned text on the combined TOOL_CALL event. Older
    traces use EVIDENCE_READ and keep the text in bindings.json. Support both
    formats so frozen audit artifacts remain readable.
    """
    legacy_by_id = {
        str(record.get("record_id", "")): record
        for record in (legacy_records or [])
        if record.get("record_id")
    }
    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        event_type = event.get("event_type")
        if event_type not in {"TOOL_CALL", "EVIDENCE_READ"}:
            continue
        payload = event.get("payload") or {}
        refs = event.get("output_refs") or []
        for raw_id in refs:
            record_id = str(raw_id)
            if not record_id or record_id in seen:
                continue
            legacy = legacy_by_id.get(record_id, {})
            if event_type == "TOOL_CALL" and "retrieval_output" in payload:
                text = payload.get("retrieval_output", "") if payload.get("hit") else ""
            else:
                text = legacy.get("text", "")
            records.append({
                "record_id": record_id,
                "retrieval_event_id": event.get("event_id", ""),
                "query": payload.get("query", legacy.get("query", "")),
                "text": text,
                "step_id": event.get("step_id", legacy.get("step_id", -1)),
                "expert": event.get("actor_id", legacy.get("expert", "")),
                "task_id": event.get("task_id", legacy.get("task_id", "")),
                "record_hash": payload.get("record_hash", legacy.get("record_hash", "")),
                "source_store": payload.get("source_store", legacy.get("source_store")),
                "source_refs": payload.get("source_refs", legacy.get("source_refs", [])),
            })
            seen.add(record_id)

    # Keep legacy-only records visible. They have no usable event link, but may
    # exist in artifacts created before retrieval IDs were emitted correctly.
    for record_id, legacy in legacy_by_id.items():
        if record_id not in seen:
            records.append(dict(legacy))
    return records


def is_retrieval_result_event(event: Dict[str, Any]) -> bool:
    """True for a completed combined event or a legacy EVIDENCE_READ row."""
    if event.get("event_type") == "EVIDENCE_READ":
        return True
    payload = event.get("payload") or {}
    return event.get("event_type") == "TOOL_CALL" and "retrieval_output" in payload
