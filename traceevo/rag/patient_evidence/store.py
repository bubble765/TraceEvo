"""SQLite store with an unconditional task-id boundary and no vector index."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

from traceevo.rag.common.schemas import PatientEvidenceRecord


_SCHEMA = """
CREATE TABLE IF NOT EXISTS patient_evidence (
    record_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    modality TEXT NOT NULL,
    exam_type TEXT NOT NULL,
    title TEXT,
    text TEXT NOT NULL,
    timestamp TEXT,
    record_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patient_task
    ON patient_evidence(task_id);
CREATE INDEX IF NOT EXISTS idx_patient_task_exam
    ON patient_evidence(task_id, exam_type);
CREATE INDEX IF NOT EXISTS idx_patient_task_modality
    ON patient_evidence(task_id, modality);
"""


class PatientEvidenceStore:
    """Small local record store. Every read requires one exact ``task_id``."""

    def __init__(self, db_path: str | os.PathLike[str], *, timeout: float = 5.0):
        self.db_path = str(db_path)
        self.timeout = timeout
        self._lock = threading.RLock()
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.db_path, timeout=timeout, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(_SCHEMA)
            if self.db_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "PatientEvidenceStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def upsert_records(self, records: Iterable[PatientEvidenceRecord]) -> int:
        rows = [self._as_row(r) for r in records]
        if not rows:
            return 0
        sql = """
        INSERT INTO patient_evidence (
            record_id, task_id, modality, exam_type, title, text, timestamp, record_hash
        ) VALUES (
            :record_id, :task_id, :modality, :exam_type, :title, :text, :timestamp, :record_hash
        )
        ON CONFLICT(record_id) DO UPDATE SET
            task_id=excluded.task_id,
            modality=excluded.modality,
            exam_type=excluded.exam_type,
            title=excluded.title,
            text=excluded.text,
            timestamp=excluded.timestamp,
            record_hash=excluded.record_hash
        """
        with self._lock, self._connection:
            self._connection.executemany(sql, rows)
        return len(rows)

    def retrieve(self, *, task_id: str, limit: int = 20,
                 exam_type: str | None = None,
                 modality: str | None = None) -> list[PatientEvidenceRecord]:
        """Read only one task; optional dimensions further narrow that task."""
        task_id = str(task_id or "").strip()
        if not task_id:
            raise ValueError("task_id is mandatory; cross-task search is forbidden")
        if limit < 1:
            raise ValueError("limit must be a positive integer")
        limit = min(int(limit), 100)

        clauses = ["task_id = ?"]
        params: list[object] = [task_id]
        if exam_type is not None:
            clauses.append("exam_type = ?")
            params.append(str(exam_type))
        if modality is not None:
            clauses.append("modality = ?")
            params.append(str(modality))
        params.append(limit)
        sql = (
            "SELECT record_id, task_id, modality, exam_type, title, text, timestamp, record_hash "
            "FROM patient_evidence WHERE " + " AND ".join(clauses) +
            " ORDER BY COALESCE(timestamp, ''), record_id LIMIT ?"
        )
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [PatientEvidenceRecord.model_validate(dict(row)) for row in rows]

    def count(self, *, task_id: str | None = None) -> int:
        """Count rows globally for diagnostics or within one task for callers."""
        with self._lock:
            if task_id is None:
                row = self._connection.execute(
                    "SELECT COUNT(*) AS n FROM patient_evidence").fetchone()
            else:
                if not str(task_id).strip():
                    raise ValueError("task_id must be non-empty when supplied")
                row = self._connection.execute(
                    "SELECT COUNT(*) AS n FROM patient_evidence WHERE task_id = ?",
                    (str(task_id),)).fetchone()
        return int(row["n"])

    @staticmethod
    def _as_row(record: PatientEvidenceRecord) -> dict[str, object]:
        model = (record if isinstance(record, PatientEvidenceRecord)
                 else PatientEvidenceRecord.model_validate(record))
        return model.model_dump()
