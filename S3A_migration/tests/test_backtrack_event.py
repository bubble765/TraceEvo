"""T6: BACKTRACK events are recorded on a deterministically forced backtrack.

Per amendment §2 D1 this replaces a δ/η sweep: instead of hoping a live case
backtracks, build a minimal BeliefGraph + FSM state whose ancestor is no longer
the highest-confidence hypothesis, which is exactly the trigger condition in
`Central_Agent.backtrack_frontier_chain()`.
"""
import os
import sys

import pytest

from traceevo import hooks
from traceevo.audit.event_logger import EventLogger
from traceevo.audit.graph_delta import GraphDelta, replay, snapshot, state_hash


@pytest.fixture()
def wired(tmp_path):
    """A Central_Agent over a hand-built graph, with provenance enabled."""
    from belief.graph import BeliefGraph
    from belief.fsm import BeliefFSM
    from agents.central_agent import Central_Agent

    os.environ.pop("TRACEEVO_DISABLED", None)
    from traceevo import config as tx_config
    tx_config.load(force_reload=True)
    ctx = hooks.start_task("case-backtrack", str(tmp_path / "run"))
    assert ctx is not None, "provenance must be enabled for this test"

    g = BeliefGraph()
    f = BeliefFSM()

    sig = g.add_node(node_type="Signal", label="chest pain")
    # Level 1: two competing hypotheses.
    h_a = g.add_node(node_type="Hypothesis", label="MI", score=0.9,
                     attrs={"why": "initial", "level": 1})
    h_b = g.add_node(node_type="Hypothesis", label="GERD", score=0.3,
                     attrs={"why": "alternative", "level": 1})
    g.add_edge(sig, h_a, edge_type="refines")
    g.add_edge(sig, h_b, edge_type="refines")
    g.update_level_nodes()

    # Level 2: a refinement of the (soon to be demoted) MI hypothesis.
    h_c = g.add_node(node_type="Hypothesis", label="anterior STEMI", score=0.8,
                     attrs={"why": "refined", "level": 2})
    g.add_edge(h_a, h_c, edge_type="refines")
    g.update_level_nodes()

    central = Central_Agent(
        name="Primary_Physician", graph=g, fsm=f, dataset="DiagnosisArena",
        log_path=str(tmp_path / "trace.txt"), case_id=0, case_symptom="chest pain",
        llm_args={"model_path": "stub", "temperature": 0.0, "max_tokens": 16,
                  "return_meta": True, "session_max_steps": 3,
                  "max_retrieval_steps": 3},
        cfg_domain={}, experts=[], evidence_text="")

    f.set_state(2)
    central.frontier = {
        "node_id": h_c, "label": "anterior STEMI", "why": "refined",
        "score": 0.8, "level": 2, "supports": 0, "refutes": 0,
    }
    # Evidence arrives that refutes MI, demoting it below GERD at level 1.
    g.update_node(node_id=h_a, score=0.2, why={"why": "refuted by troponin"})
    g.update_level_nodes()

    return {"ctx": ctx, "graph": g, "fsm": f, "central": central,
            "ids": {"h_a": h_a, "h_b": h_b, "h_c": h_c}, "tmp": tmp_path}


def test_backtrack_fires_and_records_an_event(wired):
    central, fsm = wired["central"], wired["fsm"]
    central.backtrack_frontier_chain()

    assert fsm.get_state() == 1, "state machine should reset to the demoted level"
    events = wired["ctx"].events
    backtracks = [e for e in events if e["event_type"] == "BACKTRACK"]
    assert len(backtracks) == 1, f"expected exactly one BACKTRACK, got {len(backtracks)}"
    bt = backtracks[0]
    assert bt["payload"]["level_to"] == 1
    assert bt["payload"]["level_from"] == 2
    assert bt["payload"]["deleted_levels"] == [2]
    assert wired["ids"]["h_c"] in bt["causal_node_refs"]


def test_backtrack_deletes_the_descendant_level(wired):
    central, graph = wired["central"], wired["graph"]
    central.backtrack_frontier_chain()
    assert wired["ids"]["h_c"] not in graph.nodes
    assert wired["ids"]["h_a"] in graph.nodes, "demoted ancestor stays in the graph"
    assert wired["ids"]["h_b"] in graph.nodes


def test_backtrack_produces_a_replayable_delta(wired):
    central, graph, ctx = wired["central"], wired["graph"], wired["ctx"]

    # Establish the pre-backtrack state the way production does (ingest seals
    # first). Without this the very first delta would be measured against an
    # empty graph and could not describe a deletion.
    hooks.on_step(3)
    ctx.seal_graph(graph, "evt-setup", "ingest")
    before = snapshot(graph)

    hooks.on_step(4)
    central.backtrack_frontier_chain()
    hooks.on_seal_graph(graph, "post_backtrack")
    after = snapshot(graph)

    deltas = ctx.deltas
    assert len(deltas) == 2, "one setup seal + one post-backtrack seal"
    last = deltas[-1]
    assert wired["ids"]["h_c"] in last.deleted_nodes
    assert last.deleted_edges, "edges into the deleted node must be removed"

    initial = {"nodes": {}, "edges": [], "start_signal_id": None}
    restored, checks0 = replay(initial, deltas[:-1],
                               after_states=ctx.graph_states[:-1])
    assert all(c["ok"] for c in checks0), checks0
    assert state_hash(restored) == state_hash(before), "state before the last delta"

    final, checks = replay(initial, deltas, after_states=ctx.graph_states)
    assert checks[-1]["ok"], checks[-1]
    assert state_hash(final) == state_hash(after)


def test_backtrack_events_are_hash_chained(wired):
    central = wired["central"]
    hooks.on_step(2)
    central.backtrack_frontier_chain()
    hooks.on_seal_graph(wired["graph"], "post_backtrack")

    ctx = wired["ctx"]
    from traceevo.audit.integrity import verify_chain
    report = verify_chain(ctx.events)
    assert report.ok, report.issues


def test_no_backtrack_event_when_ancestors_are_still_top_ranked(wired):
    """Sanity: the hook must fire only on a real regression."""
    central, graph = wired["central"], wired["graph"]
    # Restore MI to the top of level 1 — no regression, no backtrack.
    graph.update_node(node_id=wired["ids"]["h_a"], score=0.95, why={"why": "restored"})
    graph.update_level_nodes()
    before = len(wired["ctx"].events)
    central.backtrack_frontier_chain()
    after = [e for e in wired["ctx"].events[before:] if e["event_type"] == "BACKTRACK"]
    assert after == []
    assert wired["ids"]["h_c"] in graph.nodes
