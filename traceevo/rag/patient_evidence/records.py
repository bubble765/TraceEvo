"""Stable patient-evidence identifiers and content hashes."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from traceevo.rag.common.schemas import PatientEvidenceRecord


def make_record(*, task_id: str, modality: str, exam_type: str, text: str,
                title: str | None = None, timestamp: str | None = None
                ) -> PatientEvidenceRecord:
    """Build deterministic IDs from source content; no diagnosis is involved."""
    task_id = str(task_id or "").strip()
    text = str(text or "").strip()
    modality = str(modality or "unknown").strip().lower()
    exam_type = str(exam_type or "diagnostic_tests").strip().lower()
    title = str(title).strip() if title is not None else None
    timestamp = str(timestamp).strip() if timestamp is not None else None
    if not task_id:
        raise ValueError("task_id is required for patient evidence")
    if not text:
        raise ValueError("empty patient evidence is not stored")

    hash_payload: dict[str, Any] = {
        "task_id": task_id,
        "modality": modality,
        "exam_type": exam_type,
        "title": title,
        "text": text,
        "timestamp": timestamp,
    }
    serialized = json.dumps(hash_payload, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"))
    record_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    id_hash = hashlib.sha256(
        f"{task_id}\0{record_hash}".encode("utf-8")).hexdigest()
    return PatientEvidenceRecord(
        record_id=f"pe-{id_hash[:24]}",
        record_hash=record_hash,
        **hash_payload,
    )
