"""T5 (part 1): the event hash chain detects tampering (amendment §3.1, §6 T5)."""
import copy
import json
import os

from traceevo.audit.event_logger import EventLogger
from traceevo.audit.event_schema import ProvenanceEvent
from traceevo.audit.integrity import verify_chain


def _log(tmp_path, n=5):
    path = os.path.join(str(tmp_path), "t_events.jsonl")
    logger = EventLogger(path, task_id="case-t")
    for i in range(n):
        logger.append(ProvenanceEvent(
            event_id=logger.next_event_id(), task_id="case-t", step_id=i,
            timestamp="2026-01-01T00:00:00.000Z",
            event_type="TOOL_CALL",
            actor_id="expert", actor_role="expert",
            payload={"i": i}))
    return path


def test_clean_chain_verifies(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    report = verify_chain(rows)
    assert report.ok, report.issues
    assert report.n_events == 5


def test_chain_links_are_contiguous(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    prev = "0" * 64
    for row in rows:
        assert row["prev_hash"] == prev
        prev = row["event_hash"]


def test_modified_event_is_detected(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    rows[2]["payload"]["i"] = 999          # tamper with the payload
    report = verify_chain(rows)
    assert not report.ok
    kinds = [i["kind"] for i in report.issues]
    assert "hash_mismatch" in kinds
    assert any(i["event_id"] == rows[2]["event_id"]
               for i in report.issues if i["kind"] == "hash_mismatch")


def test_deleted_event_breaks_the_chain(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    del rows[2]
    report = verify_chain(rows)
    assert not report.ok
    assert any(i["kind"] == "chain_break" for i in report.issues)


def test_reordered_events_break_the_chain(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    rows[1], rows[2] = rows[2], rows[1]
    report = verify_chain(rows)
    assert not report.ok
    assert any(i["kind"] == "chain_break" for i in report.issues)


def test_truncated_tail_is_detected_via_expected_tip(tmp_path):
    path = _log(tmp_path)
    rows = EventLogger.read(path)
    tip = rows[-1]["event_hash"]
    report = verify_chain(rows[:-2], expected_tip_hash=tip)
    assert not report.ok
    assert any(i["kind"] == "tip_mismatch" for i in report.issues)


def test_mutation_after_seal_is_detected():
    ev = ProvenanceEvent(event_id="e1", task_id="t", step_id=0,
                         timestamp="now", event_type="PLAN",
                         actor_id="a", actor_role="central")
    ev.seal()
    assert ev.verify()
    ev.payload["sneaky"] = True
    assert not ev.verify(), "post-seal mutation must invalidate the hash"
