"""JSONL event log with a per-task hash chain (amendment §3.1).

Events for one task form a chain: event[i].prev_hash == event[i-1].event_hash.
The first event's prev_hash is GENESIS_HASH. The log is append-only and
replayable in file order.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterator, List, Optional

from .event_schema import GENESIS_HASH, ProvenanceEvent


class EventLogger:
    """Appends ProvenanceEvents to a JSONL file and maintains the hash chain."""

    def __init__(self, path: str, task_id: str, fsync: bool = False,
                 initial_events: Optional[List[Dict[str, Any]]] = None):
        self.path = path
        self.task_id = task_id
        self.fsync = fsync
        initial_events = list(initial_events or [])
        for row in initial_events:
            if row.get("task_id") != task_id:
                raise ValueError("seed event belongs to another task")
            if not ProvenanceEvent.from_dict(row).verify():
                raise ValueError("seed event has an invalid event hash")
        self._seq = len(initial_events)
        self._prev_hash = (initial_events[-1].get("event_hash", "")
                           if initial_events else GENESIS_HASH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # A recovery attempt starts in a new directory with an intact prefix
        # from the original hash chain. The original attempt file is untouched.
        with open(self.path, "w", encoding="utf-8") as f:
            for row in initial_events:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------ write
    def next_event_id(self) -> str:
        self._seq += 1
        return f"evt-{self.task_id}-{self._seq:05d}"

    def append(self, event: ProvenanceEvent) -> ProvenanceEvent:
        """Seal and append. Caller must NOT set prev_hash/event_hash itself."""
        with self._lock:
            event.prev_hash = self._prev_hash
            event.seal()
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                if self.fsync:
                    f.flush()
                    os.fsync(f.fileno())
            self._prev_hash = event.event_hash
            return event

    @property
    def last_hash(self) -> str:
        return self._prev_hash

    @property
    def count(self) -> int:
        return self._seq

    # ------------------------------------------------------------------- read
    @staticmethod
    def read(path: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    @staticmethod
    def stream(path: str) -> Iterator[Dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    @staticmethod
    def to_events(rows: List[Dict[str, Any]]) -> List[ProvenanceEvent]:
        return [ProvenanceEvent.from_dict(r) for r in rows]
