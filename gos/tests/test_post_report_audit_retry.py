import json
from types import SimpleNamespace

from agents.central_agent import Central_Agent
from belief.fsm import BeliefFSM
from belief.graph import BeliefGraph
from main import run_post_report_audit_recovery
from traceevo.audit.cross_graph_binding import BindingRegistry, NodeBinding


class _Context:
    def __init__(self, task_id, out_dir, events, bindings, graph_states,
                 deltas, checkpoints):
        self.task_id = task_id
        self.out_dir = str(out_dir)
        self.events = events
        self.bindings = bindings
        self.graph_states = graph_states
        self.deltas = deltas
        self.recovery_checkpoints = checkpoints


def test_post_report_anomaly_restores_checkpoint_and_retries(tmp_path, monkeypatch):
    import traceevo.audit.anomaly_detector as anomaly_detector
    import traceevo.audit.integrity as integrity
    import traceevo.audit.post_report_recovery as post_audit
    import main as gos_main

    graph = BeliefGraph()
    signal = graph.add_node("Signal", "ankle pain")
    bad_evidence = graph.add_node("Evidence", "x-ray shows fracture")
    hypo = graph.add_node("Hypothesis", "ankle fracture", score=0.7,
                          attrs={"why": "x-ray", "level": 1})
    graph.add_edge(bad_evidence, hypo, "support")
    final_state = graph.to_dict()
    clean_state = {"nodes": {signal: graph.nodes[signal]}, "edges": [],
                   "start_signal_id": signal}

    root_event = {"event_id": "event-root", "event_type": "GRAPH_UPDATE",
                  "task_id": "case-6", "step_id": 1, "timestamp": "t1",
                  "actor_id": "Central", "actor_role": "central",
                  "parent_event_ids": [], "causal_node_refs": [bad_evidence],
                  "input_refs": [], "output_refs": [], "payload": {"phase": "update_graph"}}
    report_event = {"event_id": "event-report", "event_type": "FINAL_REPORT",
                    "task_id": "case-6", "step_id": 1, "timestamp": "t2",
                    "actor_id": "Central", "actor_role": "central",
                    "parent_event_ids": ["event-root"],
                    "causal_node_refs": [hypo], "input_refs": [],
                    "output_refs": [], "payload": {"prediction": "fracture",
                                                      "report": "Fracture."}}
    registry = BindingRegistry("case-6")
    registry.bind(signal, "Signal", "ankle pain", "event-root", 0, "ingest")
    registry.bind(bad_evidence, "Evidence", "x-ray shows fracture",
                  "event-root", 1, "update_graph")
    registry.bind(hypo, "Hypothesis", "ankle fracture", "event-root", 1,
                  "update_graph")
    checkpoint_bindings = BindingRegistry("case-6")
    checkpoint_bindings.bind(signal, "Signal", "ankle pain", "event-root", 0,
                             "ingest")
    checkpoint = {
        "checkpoint_id": "ckpt-0001", "phase": "before_step",
        "loop_step": 0, "event_count": 0, "step_id": 0,
        "graph_state": clean_state, "next_node_index": 2,
        "graph_belief": "ankle pain", "fsm_state": BeliefFSM().to_dict(),
        "expert_histories": {}, "bindings": checkpoint_bindings,
        "graph_states": [], "deltas": [], "focus": {},
        "last_state_change": "", "last_graph_update": "",
        "last_plan": "", "last_ingest": "", "injections": [], "warnings": [],
    }
    original = _Context("case-6", tmp_path / "original",
                        [root_event, report_event], registry, [final_state], [],
                        [checkpoint])
    retry = None
    state = {"current": original}

    def fake_emit(name, *args, **kwargs):
        nonlocal retry
        if name == "current":
            return state["current"]
        if name == "finish_task":
            return {"integrity_ok": True, "replay_all_ok": True}
        if name == "end_task":
            state["current"] = None
            return None
        if name == "start_recovery_task":
            parent_ctx, selected, _attempt, feedback = args
            assert selected["checkpoint_id"] == "ckpt-0001"
            assert "unsupported_evidence" in feedback
            retry = _Context("case-6", tmp_path / "original" / "recovery-1",
                             [], selected["bindings"], [], [], [])
            state["current"] = retry
            return retry
        return None

    monkeypatch.setattr(gos_main, "_txemit", fake_emit)
    monkeypatch.setattr(anomaly_detector, "detect_task", lambda *_a, **_k: [])
    monkeypatch.setattr(integrity, "verify_chain",
                        lambda rows: SimpleNamespace(ok=True))
    answers = [
        json.dumps({"status": "FINDINGS", "summary": "unsupported evidence",
                    "findings": [{
                        "category": "unsupported_evidence",
                        "root_event_id": "event-root",
                        "root_node_id": bad_evidence,
                        "claim": "x-ray shows fracture",
                        "reason": "no patient or retrieval source supports it",
                        "checked_record_ids": [], "citations": [],
                        "confidence": 0.95,
                    }]}),
        json.dumps({"status": "PASS", "summary": "revised graph passed", "findings": []}),
    ]
    monkeypatch.setattr(post_audit, "call_auditor",
                        lambda *_a, **_k: (answers.pop(0), "a" * 64))

    class Central:
        def __init__(self):
            self.graphs = [final_state]
            self.frontier = {}
            self.log_path = ""
        def run(self, start_step=0):
            assert set(graph.nodes) == {signal}
            retry.events.append({"event_id": "retry-report", "event_type": "FINAL_REPORT",
                                 "task_id": "case-6", "step_id": 1,
                                 "parent_event_ids": [], "causal_node_refs": [signal],
                                 "output_refs": [], "payload": {"report": "clean"}})
            return "sprain", "Clean report"

    args = SimpleNamespace(post_report_audit_max_retries=1,
                           log_dir=str(tmp_path / "logs"), tag=None)
    answer, final_report, central = run_post_report_audit_recovery(
        args, 6, {"case_description": "ankle pain"}, "fracture", "Original report",
        Central(), graph, BeliefFSM(), [], {"model_path": "stub", "max_tokens": 256})

    assert answer == "sprain"
    assert final_report == "Clean report"
    assert central.graphs
    assert (tmp_path / "original" / "post_report_recovery.json").is_file()
    manifest = json.loads((tmp_path / "original" / "post_report_recovery.json").read_text())
    assert manifest["status"] == "PASSED"
    assert manifest["attempts"][0]["impact_closure"]["affected_node_ids"] == [
        bad_evidence, hypo]
