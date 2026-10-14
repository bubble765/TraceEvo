"""Event-DAG view of a task's provenance trace.

The runtime graph has one node per recorded event. ``parent_event_ids`` are
serialized as plain directed ``src`` / ``dst`` edges, and ``causal_node_refs``
on each event node point into the separate causal graph. Retrieval text stays in
the originating TOOL_CALL payload; no synthetic Record or Evidence node is
created here.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


@dataclass
class ProvenanceEventNode:
    """One event plus its recorded fields, including causal-graph references."""

    event_id: str
    data: Dict[str, Any]

    def to_dict(self, *, include_parent_ids: bool = False) -> Dict[str, Any]:
        row = copy.deepcopy(self.data)
        # The graph edge list is the canonical parent/child representation.
        # The append-only event log still preserves parent_event_ids verbatim.
        if not include_parent_ids:
            row.pop("parent_event_ids", None)
        return row


@dataclass(frozen=True)
class ProvenanceEdge:
    """A dependency from a parent event to a child event."""

    src: str
    dst: str

    def to_dict(self) -> Dict[str, str]:
        return {"src": self.src, "dst": self.dst}


class ProvenanceGraph:
    """Mutable runtime graph that exports to a compact, typed-by-data JSON view."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.nodes: Dict[str, ProvenanceEventNode] = {}
        self.edges: List[ProvenanceEdge] = []
        self._edge_keys: Set[Tuple[str, str]] = set()

    def add_event(self, event: Dict[str, Any]) -> str:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise ValueError("provenance event must have a non-empty event_id")
        if event_id not in self.nodes:
            data = copy.deepcopy(event)
            data.setdefault("task_id", self.task_id)
            data.setdefault("causal_node_refs", [])
            self.nodes[event_id] = ProvenanceEventNode(event_id, data)
        return event_id

    def add_dependency(self, src: str, dst: str) -> None:
        if src not in self.nodes or dst not in self.nodes:
            raise ValueError(f"dependency endpoints must exist: {src!r} -> {dst!r}")
        key = (src, dst)
        if key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append(ProvenanceEdge(src, dst))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "n_nodes": len(self.nodes),
            "n_edges": len(self.edges),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as output:
            json.dump(self.to_dict(), output, ensure_ascii=False, indent=2)


def build_provenance_graph(task_id: str,
                           events: Iterable[Dict[str, Any]],
                           bindings: Optional[Dict[str, Any]] = None,
                           graph_states: Optional[List[Dict[str, Any]]] = None
                           ) -> ProvenanceGraph:
    """Build one event node per log row and dependency edges from parent IDs.

    ``bindings`` and ``graph_states`` remain accepted for call compatibility.
    Cross-graph association is carried by each event's ``causal_node_refs``;
    causal support/refute edges remain exclusively in the causal graph.
    """
    del bindings, graph_states
    rows = list(events)
    graph = ProvenanceGraph(task_id)
    for event in rows:
        graph.add_event(event)

    event_ids = set(graph.nodes)
    for event in rows:
        child = str(event.get("event_id") or "")
        for parent in event.get("parent_event_ids") or []:
            parent = str(parent)
            if parent in event_ids:
                graph.add_dependency(parent, child)
    return graph


def render_png(graph_dict: Dict[str, Any], out_path: str,
               max_nodes: int = 60) -> bool:
    """Render an event DAG. Returns False when optional renderers are missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx
    except Exception:
        return False

    event_colours = {
        "PLAN": "#2563eb", "AGENT_CALL": "#7c3aed", "TOOL_CALL": "#0d9488",
        "EVIDENCE_READ": "#0d9488", "ANALYSIS": "#64748b",
        "GRAPH_UPDATE": "#ea580c", "BACKTRACK": "#d97706",
        "DRILL_DOWN": "#ca8a04", "FINAL_REPORT": "#dc2626",
    }
    nodes = list(graph_dict.get("nodes") or [])[:max_nodes]
    keep = {str(node.get("event_id", "")) for node in nodes}
    graph = nx.DiGraph()
    for node in nodes:
        event_id = str(node.get("event_id", ""))
        event_type = str(node.get("event_type", "EVENT"))
        actor = str(node.get("actor_id", ""))
        step = node.get("step_id", "")
        causal_refs = list(node.get("causal_node_refs") or [])
        refs_label = ", ".join(str(ref) for ref in causal_refs[:3])
        if len(causal_refs) > 3:
            refs_label += ", …"
        causal_label = f"\ncausal: {refs_label}" if refs_label else ""
        graph.add_node(event_id, event_type=event_type,
                       label=f"{event_type}\n{actor}\nstep {step}{causal_label}")
    for edge in graph_dict.get("edges") or []:
        src, dst = str(edge.get("src", "")), str(edge.get("dst", ""))
        if src in keep and dst in keep:
            graph.add_edge(src, dst)

    if graph.number_of_nodes() == 0:
        return False

    fig, ax = plt.subplots(figsize=(15, 9))
    pos = nx.spring_layout(graph, k=0.65, iterations=90, seed=7)
    colours = [event_colours.get(graph.nodes[n]["event_type"], "#94a3b8")
               for n in graph]
    nx.draw_networkx_nodes(graph, pos, node_color=colours, node_size=900,
                           alpha=0.94, ax=ax)
    nx.draw_networkx_edges(graph, pos, alpha=0.38, arrows=True,
                           arrowsize=12, ax=ax)
    nx.draw_networkx_labels(
        graph, pos,
        labels={node_id: data["label"] for node_id, data in graph.nodes(data=True)},
        font_size=6.5, ax=ax,
    )
    ax.set_title(
        f"TraceEvo provenance event DAG — {graph_dict.get('task_id', '')}\n"
        f"{graph_dict.get('n_nodes', graph.number_of_nodes())} events / "
        f"{graph_dict.get('n_edges', graph.number_of_edges())} dependencies",
        fontsize=11,
    )
    ax.axis("off")
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    fig.savefig(out_path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return True
