"""T4 (unit part): Replay(G_t, delta_t) == G_(t+1) (amendment §3.2, §6 T4).

Uses the real BeliefGraph so the test breaks if upstream changes the graph's
container semantics.
"""
import copy
import sys

from traceevo.audit import graph_delta as gd


class _FakeGraph:
    """Minimal stand-in for BeliefGraph with the same to_dict() shape."""

    def __init__(self):
        self._d = {"nodes": {}, "edges": [], "start_signal_id": None}

    def to_dict(self):
        return self._d

    def add_node(self, nid, ntype, label, score=1.0, attrs=None):
        self._d["nodes"][nid] = {"type": ntype, "label": label, "score": score,
                                 "attrs": attrs or {}}

    def add_edge(self, src, dst, etype):
        self._d["edges"].append({"src": src, "dst": dst, "type": etype,
                                 "attrs": {}})

    def update_score(self, nid, score):
        self._d["nodes"][nid]["score"] = score

    def delete_node(self, nid):
        self._d["nodes"].pop(nid, None)
        self._d["edges"] = [e for e in self._d["edges"]
                            if e["src"] != nid and e["dst"] != nid]


def _scripted_run():
    """Produce a state list plus deltas the same way the hooks do."""
    g = _FakeGraph()
    states, deltas = [], []
    for step, mutate in enumerate([
        lambda: (g.add_node("n1", "Signal", "cough"),
                 g.add_node("n2", "Evidence", "fever")),
        lambda: (g.add_node("n3", "Hypothesis", "flu", 0.6),
                 g.add_edge("n1", "n3", "refines"),
                 g.add_edge("n2", "n3", "support")),
        lambda: (g.update_score("n3", 0.9),
                 g.add_node("n4", "Hypothesis", "pneumonia", 0.4)),
        lambda: g.delete_node("n4"),
    ]):
        before = states[-1] if states else {"nodes": {}, "edges": [],
                                            "start_signal_id": None}
        mutate()
        after = gd.snapshot(g)
        deltas.append(gd.diff_states(before, after, delta_id=f"d{step}"))
        states.append(after)
    return states, deltas


def test_delta_detects_each_kind_of_change():
    states, deltas = _scripted_run()
    assert deltas[0].added_nodes == ["n1", "n2"]
    assert deltas[1].added_nodes == ["n3"]
    assert [tuple(e)[:2] for e in deltas[1].added_edges] == [("n1", "n3"),
                                                             ("n2", "n3")]
    assert deltas[2].updated_nodes == ["n3"]
    assert deltas[3].deleted_nodes == ["n4"]


def test_deepcopy_snapshot_does_not_alias_the_live_graph():
    """Regression for the S0 finding: upstream to_dict() returns live containers."""
    g = _FakeGraph()
    g.add_node("n1", "Signal", "cough")
    snap = gd.snapshot(g)
    g.add_node("n2", "Evidence", "fever")
    assert "n2" not in snap["nodes"], (
        "snapshot aliased the live graph; the S0 history bug has returned")
    assert snap is not g.to_dict()


def test_replay_reconstructs_every_state():
    states, deltas = _scripted_run()
    empty = {"nodes": {}, "edges": [], "start_signal_id": None}
    final, checks = gd.replay(empty, deltas, after_states=states)
    assert all(c["ok"] for c in checks), checks
    assert gd.state_hash(final) == gd.state_hash(states[-1])


def test_replay_is_order_sensitive():
    """A delta applied to the wrong starting state must not verify."""
    states, deltas = _scripted_run()
    empty = {"nodes": {}, "edges": [], "start_signal_id": None}
    # Skip the first delta: the rest should no longer line up.
    _, checks = gd.replay(empty, deltas[1:], after_states=states[1:])
    assert not all(c["ok"] for c in checks)


def test_edge_type_change_on_a_surviving_key_is_recorded():
    """Regression: GoS flips evidence edges between support and refute while
    keeping the same (src, dst). The delta must capture that, or replay
    reconstructs the wrong graph -- this is what broke T4 on case 8."""
    before = {"nodes": {}, "start_signal_id": None,
              "edges": [{"src": "e1", "dst": "h1", "type": "support", "attrs": {}}]}
    after = copy.deepcopy(before)
    after["edges"][0]["type"] = "refute"

    delta = gd.diff_states(before, after, delta_id="d0")
    assert not delta.is_empty(), "a type change must not produce an empty delta"
    # The delta now carries the edge *type*, so the flip is visible on the
    # delta itself rather than only through the state hashes: the old support
    # edge is deleted and the new refute edge added on the same (src, dst).
    assert ("e1", "h1", "refute") in [tuple(e) for e in delta.added_edges]
    assert all(len(tuple(e)) == 3 for e in delta.added_edges), \
        "edge deltas must carry (src, dst, type)"
    assert ("e1", "h1", "support") in [tuple(e) for e in delta.deleted_edges]

    replayed = gd.apply_delta(before, delta, after_state=after)
    assert gd.state_hash(replayed) == gd.state_hash(after)


def test_edge_added_and_removed_are_still_detected():
    before = {"nodes": {}, "start_signal_id": None,
              "edges": [{"src": "e1", "dst": "h1", "type": "support", "attrs": {}}]}
    after = {"nodes": {}, "start_signal_id": None,
             "edges": [{"src": "e2", "dst": "h1", "type": "support", "attrs": {}}]}
    delta = gd.diff_states(before, after, delta_id="d0")
    assert [tuple(e)[:2] for e in delta.added_edges] == [("e2", "h1")]
    assert [tuple(e)[:2] for e in delta.deleted_edges] == [("e1", "h1")]
    replayed = gd.apply_delta(before, delta, after_state=after)
    assert gd.state_hash(replayed) == gd.state_hash(after)


def test_state_hash_ignores_edge_order():
    a = {"nodes": {"n1": {"type": "Signal", "label": "x", "score": 1.0, "attrs": {}}},
         "edges": [{"src": "n1", "dst": "n2", "type": "support", "attrs": {}},
                   {"src": "n1", "dst": "n3", "type": "refute", "attrs": {}}],
         "start_signal_id": None}
    b = copy.deepcopy(a)
    b["edges"] = list(reversed(b["edges"]))
    assert gd.state_hash(a) == gd.state_hash(b)


def test_real_belief_graph_round_trip():
    """The canonicaliser must accept the upstream BeliefGraph's actual output."""
    sys.path.insert(0, "gos")
    from belief.graph import BeliefGraph
    g = BeliefGraph()
    sig = g.add_node(node_type="Signal", label="chest pain")
    evi = g.add_node(node_type="Evidence", label="troponin elevated")
    hyp = g.add_node(node_type="Hypothesis", label="MI", score=0.7,
                     attrs={"why": "x", "level": 1})
    g.add_edge(evi, hyp, edge_type="support")
    g.update_level_nodes()

    snap = gd.snapshot(g)
    g.add_node(node_type="Evidence", label="added later")
    assert len(snap["nodes"]) == 3, "snapshot must not alias the live graph"

    empty = {"nodes": {}, "edges": [], "start_signal_id": None}
    delta = gd.diff_states(empty, snap, delta_id="d0")
    final, checks = gd.replay(empty, [delta], after_states=[snap])
    assert checks[0]["ok"]
    assert gd.state_hash(final) == gd.state_hash(snap)
