import pytest

from traceevo.audit.post_report_recovery import (
    AuditFinding,
    AuditValidationError,
    build_audit_packet,
    compute_impact_closure,
    prune_graph_state,
    select_checkpoint,
    validate_audit_response,
)
from traceevo.audit.event_logger import EventLogger
from traceevo.audit.event_schema import ProvenanceEvent
from traceevo.audit.integrity import verify_chain


def _events():
    return [
        {"event_id": "e0", "event_type": "PLAN", "parent_event_ids": [],
         "causal_node_refs": ["h0"]},
        {"event_id": "e1", "event_type": "GRAPH_UPDATE", "parent_event_ids": ["e0"],
         "causal_node_refs": ["ev1"], "payload": {"phase": "update_graph"}},
        {"event_id": "e2", "event_type": "GRAPH_UPDATE", "parent_event_ids": ["e1"],
         "causal_node_refs": ["h1"]},
        {"event_id": "e3", "event_type": "DRILL_DOWN", "parent_event_ids": ["e2"],
         "causal_node_refs": ["h2"]},
        {"event_id": "e4", "event_type": "FINAL_REPORT", "parent_event_ids": ["e3"],
         "causal_node_refs": ["h2"]},
    ]


def _bindings():
    return [
        {"node_id": "h0", "node_type": "Hypothesis", "label": "parent",
         "created_by_event": "e0"},
        {"node_id": "ev1", "node_type": "Evidence", "label": "invented result",
         "created_by_event": "e1"},
        {"node_id": "h1", "node_type": "Hypothesis", "label": "hypothesis 1",
         "created_by_event": "e2"},
        {"node_id": "h2", "node_type": "Hypothesis", "label": "child hypothesis",
         "created_by_event": "e3"},
    ]


def test_audit_packet_contains_both_graph_views_and_source_text():
    packet = build_audit_packet(
        task_id="case-1",
        case_input={"complaint": "ankle pain"},
        graph_state={"nodes": {"ev1": {"label": "invented result"}},
                     "edges": []},
        events=_events(), bindings=_bindings(),
        records=[{"record_id": "r1", "text": "Radiographs show no fracture."}],
        deltas=[], prediction="sprain", report="No fracture seen.",
    )
    assert packet["final_causal_graph"]["nodes"]["ev1"]["label"] == "invented result"
    assert {"src": "e0", "dst": "e1"} in packet["provenance_graph"]["edges"]
    assert packet["provenance_graph"]["nodes"][1]["causal_node_refs"] == ["ev1"]
    assert all(set(edge) == {"src", "dst"}
               for edge in packet["provenance_graph"]["edges"])
    assert "provenance_events" not in packet
    assert packet["retrieval_records"][0]["text"] == "Radiographs show no fracture."
    assert packet["gold_answer_available"] is False


def test_closure_walks_event_and_causal_descendants_and_updated_nodes():
    deltas = [
        {"event_id": "e1", "added_nodes": ["ev1"], "updated_nodes": [],
         "added_edges": [["ev1", "h1", "support"]], "deleted_edges": []},
        {"event_id": "e2", "added_nodes": [], "updated_nodes": ["h1"],
         "added_edges": [], "deleted_edges": []},
        {"event_id": "e3", "added_nodes": ["h2"], "updated_nodes": [],
         "added_edges": [["h1", "h2", "refines"]], "deleted_edges": []},
    ]
    graph_state = {
        "nodes": {row["node_id"]: {} for row in _bindings()},
        "edges": [
            {"src": "ev1", "dst": "h1", "type": "support"},
            {"src": "h1", "dst": "h2", "type": "refines"},
        ],
    }
    closure = compute_impact_closure(
        findings=[AuditFinding("unsupported_evidence", "e1", "ev1", "claim",
                               "not in source", confidence=0.9)],
        events=_events(), bindings=_bindings(), deltas=deltas,
        graph_state=graph_state,
    )
    assert closure.affected_event_ids == ["e1", "e2", "e3", "e4"]
    assert closure.affected_node_ids == ["ev1", "h1", "h2"]
    assert closure.report_event_ids == ["e4"]
    assert closure.removed_edge_ids == [["ev1", "h1", "support"],
                                        ["h1", "h2", "refines"]]

    cleaned = prune_graph_state(graph_state, closure)
    assert cleaned["nodes"] == {"h0": {}}
    assert cleaned["edges"] == []


def test_checkpoint_is_selected_before_earliest_root_event():
    checkpoints = [
        {"checkpoint_id": "before-run", "event_count": 0},
        {"checkpoint_id": "before-step-2", "event_count": 2},
        {"checkpoint_id": "after-root", "event_count": 3},
    ]
    from traceevo.audit.post_report_recovery import ImpactClosure
    selected = select_checkpoint(
        checkpoints, _events(), ImpactClosure(root_event_ids=["e2"]),
    )
    assert selected["checkpoint_id"] == "before-step-2"


def test_auditor_schema_checks_real_ids_and_exact_source_quotes():
    events = _events()
    bindings = _bindings()
    records = [{"record_id": "r1", "text": "Radiographs show no fracture."}]
    raw = ('{"status":"FINDINGS","summary":"unsupported","findings":[' \
        '{"category":"unsupported_evidence","root_event_id":"e1",' \
        '"root_node_id":"ev1","claim":"radiographs show fracture",' \
        '"reason":"claim does not match record",' \
        '"checked_record_ids":["r1"],"citations":' \
        '[{"record_id":"r1","quote":"Radiographs show no fracture."}],'
        '"confidence":0.9}]}')
    checked = validate_audit_response(raw, task_id="case-1", events=events,
                                      bindings=bindings, records=records)
    assert checked.status == "FINDINGS"
    assert checked.findings[0].root_event_id == "e1"

    fake_quote = raw.replace("Radiographs show no fracture.", "X-ray confirms fracture.")
    with pytest.raises(AuditValidationError, match="exact source excerpt"):
        validate_audit_response(fake_quote, task_id="case-1", events=events,
                                bindings=bindings, records=records)


def test_unknown_anomaly_roots_are_rejected():
    raw = '{"status":"FINDINGS","summary":"x","findings":[' \
        '{"category":"unsupported_evidence","root_event_id":"invented",' \
        '"root_node_id":"","claim":"x","reason":"x",' \
        '"checked_record_ids":[],"citations":[],"confidence":0.99}]}'
    with pytest.raises(AuditValidationError, match="unknown event"):
        validate_audit_response(raw, task_id="case-1", events=_events(),
                                bindings=_bindings(), records=[])


def test_event_logger_can_fork_a_verified_prefix_without_editing_original(tmp_path):
    original = EventLogger(str(tmp_path / "original.jsonl"), "case-fork")
    first = ProvenanceEvent(
        event_id=original.next_event_id(), task_id="case-fork", step_id=0,
        timestamp="t0", event_type="PLAN", actor_id="Central",
        actor_role="central", payload={"focus": "initial"})
    original.append(first)
    second = ProvenanceEvent(
        event_id=original.next_event_id(), task_id="case-fork", step_id=1,
        timestamp="t1", event_type="AGENT_CALL", actor_id="Central",
        actor_role="central", parent_event_ids=[first.event_id])
    original.append(second)
    prefix = [first.to_dict(), second.to_dict()]

    retry = EventLogger(str(tmp_path / "retry.jsonl"), "case-fork",
                        initial_events=prefix)
    third = ProvenanceEvent(
        event_id=retry.next_event_id(), task_id="case-fork", step_id=1,
        timestamp="t2", event_type="ANALYSIS", actor_id="Radiologist",
        actor_role="expert", parent_event_ids=[second.event_id])
    retry.append(third)

    original_rows = EventLogger.read(str(tmp_path / "original.jsonl"))
    retry_rows = EventLogger.read(str(tmp_path / "retry.jsonl"))
    assert len(original_rows) == 2
    assert len(retry_rows) == 3
    assert retry_rows[0]["event_hash"] == original_rows[0]["event_hash"]
    assert verify_chain(retry_rows).ok


def test_recovery_checkpoint_forks_context_without_mutating_parent(tmp_path, monkeypatch):
    from traceevo import hooks
    from traceevo import config as tx_config

    monkeypatch.delenv("TRACEEVO_DISABLED", raising=False)
    monkeypatch.setenv("TRACEEVO_POST_REPORT_AUDIT", "1")
    tx_config.load(force_reload=True)
    parent = hooks.start_task("case-fork", str(tmp_path / "original"))
    assert parent is not None
    event = parent.emit("PLAN", "Central", "central")

    class Graph:
        belief = ""
        def to_dict(self):
            return {"nodes": {}, "edges": [], "start_signal_id": None}

    class Fsm:
        state = 1
        history = [1]
        _stage_steps = {1: 0}
        thresholds = {"gap_delta": 0.15}
        def to_dict(self):
            return {"state": 1, "history": [1], "stage_steps": {1: 0},
                    "thresholds": {"gap_delta": 0.15}}

    class Expert:
        name = "Radiologist"
        retrieval_history = [("x-ray", "no fracture")]

    checkpoint = hooks.on_recovery_checkpoint(
        Graph(), Fsm(), [Expert()], 0, "before_step")
    assert checkpoint["event_count"] == 1
    hooks.end_task()
    retry = hooks.start_recovery_task(parent, checkpoint, 1, "audit feedback")
    assert retry is not None
    assert retry.recovery_feedback == "audit feedback"
    assert len(retry.events) == 1
    assert retry.logger.next_event_id() == "evt-case-fork-00002"
    assert verify_chain(retry.events).ok
    assert len(parent.events) == 1
    hooks.end_task()
    monkeypatch.setenv("TRACEEVO_POST_REPORT_AUDIT", "0")
    tx_config.load(force_reload=True)
