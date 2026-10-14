"""Graph snapshots, deltas and replay (amendment §3.2).

Why this exists
---------------
`BeliefGraph.to_dict()` returns its live `nodes`/`edges` containers, so every
entry appended to `Central_Agent.graphs` is a view of the *same* object. S0
measured this: all snapshots within a case hash identically. The causal-graph
history therefore does not exist in memory and cannot be used as ground truth.

This module keeps its own history by deep-copying `graph.to_dict()` at each
instrumented point, and records the delta between consecutive states.

Invariant enforced by `replay()`:   Replay(G_t, delta_t) == G_(t+1)
where equality is canonical-hash equality (JSON with sorted keys, edges sorted
by (src, dst)).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .event_schema import canonical_json, sha256_hex


# --------------------------------------------------------------------- state
def snapshot(graph) -> Dict[str, Any]:
    """Deep-copied, canonicalisable snapshot of a BeliefGraph.

    Takes a BeliefGraph instance (or anything exposing to_dict()).
    """
    return copy.deepcopy(graph.to_dict())


def canonical_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Canonical form of a graph state for hashing/comparison."""
    nodes = state.get("nodes") or {}
    edges = state.get("edges") or []
    norm_edges = [
        {"src": e.get("src"), "dst": e.get("dst"),
         "type": e.get("type"), "attrs": e.get("attrs") or {}}
        for e in edges
    ]
    norm_edges.sort(key=lambda e: (str(e["src"]), str(e["dst"]), str(e["type"])))
    norm_nodes = {
        nid: {"type": n.get("type"), "label": n.get("label"),
              "score": n.get("score"), "attrs": n.get("attrs") or {}}
        for nid, n in nodes.items()
    }
    return {"nodes": norm_nodes, "edges": norm_edges,
            "start_signal_id": state.get("start_signal_id")}


def state_hash(state: Dict[str, Any]) -> str:
    return sha256_hex(canonical_json(canonical_state(state)))


# --------------------------------------------------------------------- delta
@dataclass
class GraphDelta:
    delta_id: str
    event_id: str
    added_nodes: List[str] = field(default_factory=list)
    updated_nodes: List[str] = field(default_factory=list)
    deleted_nodes: List[str] = field(default_factory=list)
    # Each entry is (src, dst, edge_type). The type is what tells a `support`
    # edge from a `refute` one; without it the edge deltas cannot answer "was
    # this node used to support or to refute?" -- which S2 §4.1 needs to decide
    # whether an ungrounded Evidence node reaches the conclusion. Legacy 2-tuple
    # entries still load (see from_dict) and simply carry type None.
    added_edges: List[List[str]] = field(default_factory=list)
    deleted_edges: List[List[str]] = field(default_factory=list)
    before_hash: str = ""
    after_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "event_id": self.event_id,
            "added_nodes": self.added_nodes,
            "updated_nodes": self.updated_nodes,
            "deleted_nodes": self.deleted_nodes,
            "added_edges": [list(e) for e in self.added_edges],
            "deleted_edges": [list(e) for e in self.deleted_edges],
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GraphDelta":
        return cls(
            delta_id=d["delta_id"], event_id=d.get("event_id", ""),
            added_nodes=list(d.get("added_nodes") or []),
            updated_nodes=list(d.get("updated_nodes") or []),
            deleted_nodes=list(d.get("deleted_nodes") or []),
            added_edges=[tuple(e) for e in (d.get("added_edges") or [])],
            deleted_edges=[tuple(e) for e in (d.get("deleted_edges") or [])],
            before_hash=d.get("before_hash", ""),
            after_hash=d.get("after_hash", ""),
        )

    def is_empty(self) -> bool:
        return not (self.added_nodes or self.updated_nodes or self.deleted_nodes
                    or self.added_edges or self.deleted_edges)


# -------------------------------------------------------------------- diffing
def _node_fingerprint(node: Dict[str, Any]) -> str:
    return canonical_json({"type": node.get("type"), "label": node.get("label"),
                           "score": node.get("score"),
                           "attrs": node.get("attrs") or {}})


def diff_states(before: Dict[str, Any], after: Dict[str, Any],
                delta_id: str, event_id: str = "") -> GraphDelta:
    """Structural delta from `before` to `after`."""
    b_nodes = before.get("nodes") or {}
    a_nodes = after.get("nodes") or {}
    b_fp = {nid: _node_fingerprint(n) for nid, n in b_nodes.items()}
    a_fp = {nid: _node_fingerprint(n) for nid, n in a_nodes.items()}

    added_nodes = sorted(set(a_fp) - set(b_fp))
    deleted_nodes = sorted(set(b_fp) - set(a_fp))
    updated_nodes = sorted(nid for nid in (set(a_fp) & set(b_fp))
                           if a_fp[nid] != b_fp[nid])

    def edge_set(state: Dict[str, Any]) -> Dict[Tuple[str, str], Tuple[str, str, str]]:
        out = {}
        for e in state.get("edges") or []:
            out[(e.get("src"), e.get("dst"))] = (
                e.get("src"), e.get("dst"), str(e.get("type")))
        return out

    b_edges, a_edges = edge_set(before), edge_set(after)
    # NOTE: iterating a dict yields its *keys*. The edge set arithmetic has to
    # be done on keys but the deltas must carry the *values*, i.e. the
    # (src, dst, type) triples -- `set(a_edges) - set(b_edges)` alone silently
    # drops the type again.
    added_edges = {a_edges[k] for k in set(a_edges) - set(b_edges)}
    deleted_edges = {b_edges[k] for k in set(b_edges) - set(a_edges)}

    # An edge can keep its (src, dst) key while changing type -- GoS flips an
    # evidence edge between support and refute when confidence is recalibrated.
    # Such an edge is neither "added" nor "deleted" by the key comparison above,
    # so the delta would be incomplete and replay would silently fail to
    # reconstruct the state. Record it as delete-then-add, which is expressible
    # within the schema and applied in that order by apply_delta().
    for key in set(b_edges) & set(a_edges):
        if b_edges[key] != a_edges[key]:
            deleted_edges.add(b_edges[key])
            added_edges.add(a_edges[key])

    added_edges = sorted(added_edges)
    deleted_edges = sorted(deleted_edges)

    return GraphDelta(
        delta_id=delta_id, event_id=event_id,
        added_nodes=added_nodes, updated_nodes=updated_nodes,
        deleted_nodes=deleted_nodes,
        added_edges=added_edges, deleted_edges=deleted_edges,
        before_hash=state_hash(before), after_hash=state_hash(after),
    )


# --------------------------------------------------------------------- replay
def apply_delta(state: Dict[str, Any], delta: GraphDelta,
                after_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Apply a delta to produce the next state.

    Node additions/updates/deletions come from the delta id lists. The *content*
    of added/updated nodes and of added edges cannot be reconstructed from ids
    alone, so when the full `after_state` is available it is used as the
    authoritative carrier and the delta is applied as a consistency check.
    Without it, replay reconstructs structure only and is validated on the
    node/edge id sets.
    """
    new_state = copy.deepcopy(state)
    nodes = dict(new_state.get("nodes") or {})

    for nid in delta.deleted_nodes:
        nodes.pop(nid, None)

    if after_state is not None:
        a_nodes = after_state.get("nodes") or {}
        for nid in delta.added_nodes:
            if nid in a_nodes:
                nodes[nid] = copy.deepcopy(a_nodes[nid])
        for nid in delta.updated_nodes:
            if nid in a_nodes:
                nodes[nid] = copy.deepcopy(a_nodes[nid])
    else:
        for nid in delta.added_nodes:
            nodes.setdefault(nid, {"type": None, "label": None, "score": None,
                                   "attrs": {}})
        for nid in delta.updated_nodes:
            nodes.setdefault(nid, {"type": None, "label": None, "score": None,
                                   "attrs": {}})

    new_state["nodes"] = nodes

    # Edges are stored as a list of dicts; rebuild from set arithmetic.
    edge_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for e in state.get("edges") or []:
        edge_map[(e.get("src"), e.get("dst"))] = copy.deepcopy(e)

    for key in delta.deleted_edges:
        edge_map.pop((key[0], key[1]), None)

    a_edge_map: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if after_state is not None:
        for e in after_state.get("edges") or []:
            a_edge_map[(e.get("src"), e.get("dst"))] = copy.deepcopy(e)

    for entry in delta.added_edges:
        entry = tuple(entry)
        key = (entry[0], entry[1])
        etype = entry[2] if len(entry) > 2 else None
        if key in a_edge_map:
            edge_map[key] = a_edge_map[key]
        else:
            edge_map.setdefault(key, {"src": key[0], "dst": key[1],
                                      "type": etype, "attrs": {}})

    new_state["edges"] = list(edge_map.values())
    return new_state


def replay(initial_state: Dict[str, Any],
           deltas: List[GraphDelta],
           after_states: Optional[List[Dict[str, Any]]] = None
           ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Replay a delta sequence; returns (final_state, per-step checks).

    Each check: {step, before_hash, expected_after_hash, replayed_hash, ok}
    """
    state = copy.deepcopy(initial_state)
    checks: List[Dict[str, Any]] = []
    for i, delta in enumerate(deltas):
        after = after_states[i] if after_states and i < len(after_states) else None
        state = apply_delta(state, delta, after_state=after)
        replayed_hash = state_hash(state)
        ok = replayed_hash == delta.after_hash
        checks.append({
            "step": i,
            "delta_id": delta.delta_id,
            "before_hash": delta.before_hash,
            "expected_after_hash": delta.after_hash,
            "replayed_hash": replayed_hash,
            "ok": ok,
        })
    return state, checks
