"""Hash-chain integrity checking (amendment §3.1, §6 T5).

Detects, per task:
  * a mutated event          -> event_hash mismatch
  * a deleted event          -> prev_hash link break
  * a reordered event        -> prev_hash link break
  * a truncated tail         -> chain ends before the recorded tip hash
  * an inserted event        -> link break downstream
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .event_schema import GENESIS_HASH, ProvenanceEvent


@dataclass
class IntegrityReport:
    task_id: str
    n_events: int
    ok: bool
    issues: List[Dict[str, Any]] = field(default_factory=list)
    tip_hash: str = ""
    expected_tip_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "n_events": self.n_events,
            "ok": self.ok,
            "n_issues": len(self.issues),
            "issues": self.issues,
            "tip_hash": self.tip_hash,
            "expected_tip_hash": self.expected_tip_hash,
        }

    def summary(self) -> str:
        status = "OK" if self.ok else f"{len(self.issues)} ISSUE(S)"
        return f"{self.task_id}: {self.n_events} events, chain {status}"


def verify_chain(rows: List[Dict[str, Any]],
                 expected_tip_hash: Optional[str] = None) -> IntegrityReport:
    """Verify the hash chain of a single task's event rows (file order)."""
    task_id = rows[0]["task_id"] if rows else "<empty>"
    issues: List[Dict[str, Any]] = []
    prev = GENESIS_HASH

    for i, row in enumerate(rows):
        ev = ProvenanceEvent.from_dict(row)
        eid = ev.event_id

        if row.get("prev_hash") != prev:
            issues.append({
                "kind": "chain_break",
                "index": i,
                "event_id": eid,
                "expected_prev_hash": prev,
                "actual_prev_hash": row.get("prev_hash"),
                "detail": "previous event was deleted, reordered or inserted",
            })
        if not ev.verify():
            issues.append({
                "kind": "hash_mismatch",
                "index": i,
                "event_id": eid,
                "recorded_hash": ev.event_hash,
                "recomputed_hash": ev.compute_hash(),
                "detail": "event contents were modified after logging",
            })
        prev = row.get("event_hash", "")

    tip = prev
    if expected_tip_hash is not None and tip != expected_tip_hash:
        issues.append({
            "kind": "tip_mismatch",
            "index": len(rows),
            "event_id": "",
            "expected_tip_hash": expected_tip_hash,
            "actual_tip_hash": tip,
            "detail": "events were appended or removed at the tail",
        })

    return IntegrityReport(task_id=task_id, n_events=len(rows), ok=not issues,
                           issues=issues, tip_hash=tip,
                           expected_tip_hash=expected_tip_hash)
