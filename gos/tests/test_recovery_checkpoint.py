import copy

from belief.fsm import BeliefFSM
from belief.graph import BeliefGraph


def test_graph_restore_keeps_node_ids_attributes_and_next_id():
    graph = BeliefGraph()
    signal = graph.add_node("Signal", "ankle pain")
    hypo = graph.add_node("Hypothesis", "fracture", score=0.7,
                          attrs={"why": "pain and swelling", "level": 1})
    graph.add_edge(signal, hypo, "refines", attrs={"source": "initial"})
    state = copy.deepcopy(graph.to_dict())
    state["edges"][0]["conf"] = 0.8

    graph.add_node("Evidence", "temporary")
    graph.restore(state, belief="checkpoint belief", next_node_index=graph.idx)

    assert set(graph.nodes) == {signal, hypo}
    assert graph.nodes[hypo]["score"] == 0.7
    assert graph.nodes[hypo]["attrs"]["why"] == "pain and swelling"
    assert graph.edges[(signal, hypo)]["conf"] == 0.8
    assert graph.belief == "checkpoint belief"
    assert graph.add_node("Evidence", "new evidence") == "node-4"


def test_fsm_restore_preserves_state_history_thresholds_and_stage_budget():
    fsm = BeliefFSM({"gap_delta": 0.2, "min_support": 2, "max_steps": 4})
    fsm.tick_step(2)
    fsm.set_state(2)
    fsm.tick_step(1)
    state = fsm.to_dict()

    restored = BeliefFSM()
    restored.restore(state)

    assert restored.to_dict() == state
