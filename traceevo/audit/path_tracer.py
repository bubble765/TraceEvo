"""Path tracing and propagation analysis (S2 §7).

Walks two graphs together from a detected root:

    Execution Dependency DAG          GoS Causal Graph
    (ProvenanceEvent -> parents)      (Evidence/Hypothesis -> support/refute)
            |                                   ^
            +-------- cross binding ------------+
              (event.causal_node_refs,
               node.created_by_event)

and answers the six questions §7 lists:

    1. earliest anomalous event
    2. the causal node it corresponds to
    3. downstream affected events
    4. downstream affected hypotheses / evidence
    5. whether it reaches the FINAL_REPORT
    6. the shortest root -> conclusion path

Naming caveat, stated once here because §7 insists on it: an edge in the
execution DAG means "this event was produced after, and its inputs included,
that event". It is *not* a claim that one caused the other in the model's
internal reasoning, and nothing here should be reported that way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .dependency_graph import ExecutionDAG


@dataclass
class Trace:
    task_id: str
    root_event_id: str = ""
    root_node_id: str = ""
    earliest_event_id: str = ""
    earliest_step: int = -1
    causal_nodes: List[str] = field(default_factory=list)
    affected_events: List[str] = field(default_factory=list)
    affected_nodes: List[str] = field(default_factory=list)
    affected_hypotheses: List[str] = field(default_factory=list)
    reaches_report: bool = False
    report_event_id: str = ""
    conclusion_path: List[str] = field(default_factory=list)
    graph_path: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "root_event_id": self.root_event_id,
            "root_node_id": self.root_node_id,
            "earliest_event_id": self.earliest_event_id,
            "earliest_step": self.earliest_step,
            "causal_nodes": self.causal_nodes,
            "affected_events": self.affected_events,
            "affected_nodes": self.affected_nodes,
            "affected_hypotheses": self.affected_hypotheses,
            "reaches_report": self.reaches_report,
            "report_event_id": self.report_event_id,
            "conclusion_path": self.conclusion_path,
            "graph_path": self.graph_path,
        }


class PathTracer:
    """Joint traversal of the execution DAG and the causal graph."""

    def __init__(self, events: Sequence[Dict[str, Any]],
                 bindings: Sequence[Dict[str, Any]],
                 edges: Sequence[Sequence[str]]):
        self.events = list(events)
        self.dag = ExecutionDAG(self.events)
        self.by_id = {e["event_id"]: e for e in self.events}
        self.nodes = {n.get("node_id"): n for n in bindings}
        # node -> node adjacency for support/refute edges (evidence -> hypothesis)
        self.out_edges: Dict[str, List[Tuple[str, str]]] = {}
        self.in_edges: Dict[str, List[Tuple[str, str]]] = {}
        for e in edges:
            src, dst = e[0], e[1]
            etype = e[2] if len(e) > 2 else ""
            self.out_edges.setdefault(src, []).append((dst, etype))
            self.in_edges.setdefault(dst, []).append((src, etype))

    # ------------------------------------------------------------ causal side
    def nodes_of_event(self, event_id: str) -> List[str]:
        ev = self.by_id.get(event_id) or {}
        return list(ev.get("causal_node_refs") or [])

    def event_of_node(self, node_id: str) -> str:
        n = self.nodes.get(node_id) or {}
        return n.get("created_by_event", "")

    def downstream_nodes(self, node_ids: Sequence[str],
                         max_depth: int = 6) -> List[str]:
        """Causal-graph descendants (a node reached *via* the given evidence)."""
        seen: Set[str] = set()
        frontier = [n for n in node_ids if n in self.nodes]
        depth = 0
        while frontier and depth < max_depth:
            nxt: List[str] = []
            for n in frontier:
                for dst, _t in self.out_edges.get(n, []):
                    if dst not in seen and dst in self.nodes:
                        seen.add(dst)
                        nxt.append(dst)
            frontier = nxt
            depth += 1
        return sorted(seen)

    def graph_path_to_report(self, node_id: str) -> List[str]:
        """Shortest support/refute path from a node to a node the report cites."""
        targets: Set[str] = set()
        for e in self.events:
            if e.get("event_type") == "FINAL_REPORT":
                targets.update(e.get("causal_node_refs") or [])
                targets.update(e.get("output_refs") or [])
        if not targets or node_id in targets:
            return [node_id] if node_id in targets else []
        prev: Dict[str, str] = {}
        queue = [node_id]
        seen = {node_id}
        while queue:
            nxt: List[str] = []
            for n in queue:
                for dst, _t in self.out_edges.get(n, []):
                    if dst in seen:
                        continue
                    seen.add(dst)
                    prev[dst] = n
                    if dst in targets:
                        chain = [dst]
                        cur = n
                        while cur != node_id:
                            chain.append(cur)
                            cur = prev[cur]
                        chain.append(node_id)
                        return list(reversed(chain))
                    nxt.append(dst)
            queue = nxt
        return []

    # -------------------------------------------------------------- tracing
    def trace(self, root_event_id: str = "", root_node_id: str = "",
              task_id: str = "") -> Trace:
        if not root_event_id and root_node_id:
            root_event_id = self.event_of_node(root_node_id)
        tr = Trace(task_id=task_id, root_event_id=root_event_id,
                   root_node_id=root_node_id)
        if not root_event_id or root_event_id not in self.by_id:
            return tr

        tr.earliest_event_id = root_event_id
        tr.earliest_step = self.by_id[root_event_id].get("step_id", -1)

        # 2. the causal node(s) this event produced
        tr.causal_nodes = self.nodes_of_event(root_event_id)
        if root_node_id and root_node_id not in tr.causal_nodes:
            tr.causal_nodes.append(root_node_id)

        # 3/4. downstream in both graphs
        tr.affected_events = self.dag.descendants(root_event_id)
        node_ids = set(tr.causal_nodes)
        for eid in tr.affected_events:
            node_ids.update(self.nodes_of_event(eid))
        tr.affected_nodes = sorted(node_ids)
        tr.affected_hypotheses = sorted(
            n for n in self.downstream_nodes(sorted(node_ids))
            if (self.nodes.get(n) or {}).get("node_type") == "Hypothesis")

        # 5. does it reach the report?
        reports = [e["event_id"] for e in self.events
                   if e.get("event_type") == "FINAL_REPORT"]
        chain = self.dag.shortest_chain(reports[0], root_event_id) if reports else None
        tr.reaches_report = bool(chain)
        tr.report_event_id = reports[0] if reports else ""
        tr.conclusion_path = chain or []

        # 6. the causal-graph path, if the root has a node
        if root_node_id and root_node_id in self.nodes:
            tr.graph_path = self.graph_path_to_report(root_node_id)
        return tr
