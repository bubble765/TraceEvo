"""S3B-E §10: where admitted experience lives, and what is kept out of reach.

Four partitions, and the separation between them is the safety property:

    success/        retrievable, injectable
    failure/        retrievable, injectable
    manual_review/  stored for a human -- **never retrievable**
    rejected/       stored for the record, never retrievable

§6.3 is explicit that `manual_review` must not quietly become a soft yes, so
`retrievable_ids()` reads only the first two partitions. A candidate cannot
reach the retriever by being well-behaved; it has to have been admitted.

Every entry carries what §10 requires: the source trace hash, the audit summary
hash, the outcome verdict, the gate reasons, the extractor version and a
creation timestamp. `memory_audit_links.jsonl` is the flat index of those, so
"which trace is this memory from, and why was it admitted" is one lookup.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Optional

PARTITIONS = ("success", "failure", "manual_review", "rejected")
RETRIEVABLE = ("success", "failure")

# gate eligibility -> partition
_PARTITION = {
    "eligible": "success",
    "failure_experience": "failure",
    "manual_review": "manual_review",
    "reject": "rejected",
}


def partition_for(eligibility: str) -> str:
    return _PARTITION.get(eligibility, "rejected")


class MemoryStore:
    def __init__(self, root: str, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        if enabled:
            for p in PARTITIONS:
                os.makedirs(os.path.join(root, p), exist_ok=True)
            self.audit_log = os.path.join(root, "memory_audit_links.jsonl")
        else:
            self.audit_log = ""

    # ------------------------------------------------------------------ write
    def add(self, experience, eligibility: str,
            enabled: bool = True) -> Dict[str, Any]:
        """Store one candidate in the partition its verdict implies."""
        part = partition_for(eligibility)
        record = experience.to_dict()
        record["partition"] = part
        if not self.enabled:
            # §15: memory off must restore plain GoS. Nothing is written and
            # the caller still gets an answer, so the code path is identical.
            return {"stored": False, "partition": part,
                    "path": "", "reason": "memory disabled"}
        path = os.path.join(self.root, part, f"{experience.memory_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        if enabled:
            self._append_audit(record, path)
        return {"stored": True, "partition": part, "path": path,
                "reason": ""}

    def _append_audit(self, record: Dict[str, Any], path: str) -> None:
        if not self.audit_log:
            return
        line = {
            "memory_id": record["memory_id"],
            "partition": record["partition"],
            "source_trace_id": record["source_trace_id"],
            "source_trace_hashes": record.get("source_trace_hashes", {}),
            "audit_summary_hash": record["audit_summary_hash"],
            "outcome_verdict": record["outcome_verdict"],
            "gate_reasons": record.get("gate_reasons", []),
            "extractor_version": record.get("extractor_version", ""),
            "created_at": record.get("created_at", ""),
            "path": os.path.abspath(path),
        }
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------- read
    def entries(self, partition: str) -> List[Dict[str, Any]]:
        d = os.path.join(self.root, partition)
        if not os.path.isdir(d):
            return []
        out = []
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
        return out

    def retrievable(self) -> List[Dict[str, Any]]:
        """§6.3/§10: only success and failure are reachable by retrieval."""
        out: List[Dict[str, Any]] = []
        for p in RETRIEVABLE:
            out.extend(self.entries(p))
        return out

    def retrievable_ids(self) -> List[str]:
        return [e["memory_id"] for e in self.retrievable()]

    def audit_links(self) -> List[Dict[str, Any]]:
        if not self.audit_log or not os.path.exists(self.audit_log):
            return []
        with open(self.audit_log, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]

    def clear(self) -> None:
        for p in PARTITIONS:
            d = os.path.join(self.root, p)
            if os.path.isdir(d):
                shutil.rmtree(d)
            os.makedirs(d, exist_ok=True)
        if self.audit_log and os.path.exists(self.audit_log):
            os.remove(self.audit_log)
