"""Execution dependency DAG over the provenance event log (S2 §3.2).

The hooks already emit `parent_event_ids`; this module *checks* that what was
emitted actually obeys the nine rules, and exposes the graph structure the
path tracer walks.

The nine rules (§3.2), restated as a table
-----------------------------------------
    child           parent(s)                          required?
    ------------------------------------------------------------------
    AGENT_CALL      the PLAN of this step                yes
    TOOL_CALL       the AGENT_CALL that dispatched it    yes
                    (completed retrieval, including output)
    EVIDENCE_READ   legacy result event from old traces   TOOL_CALL yes
    ANALYSIS        that expert's AGENT_CALL (+ completed  AGENT_CALL yes,
                    retrieval TOOL_CALLs it consumed)      reads optional
    GRAPH_UPDATE    this step's ANALYSIS event(s)         yes, except ingest
    BACKTRACK        the preceding GRAPH_UPDATE           yes
    DRILL_DOWN       the preceding GRAPH_UPDATE           yes
    PLAN             the preceding state change           yes, except the
                     (GRAPH_UPDATE | BACKTRACK |            very first event
                      DRILL_DOWN)
    FINAL_REPORT     the latest state change              yes

Two deliberate readings of the amendment, both because they are what the
semantics require rather than what a literal reading gives:

* "the preceding GRAPH_UPDATE" for PLAN/FINAL_REPORT is implemented as "the
  preceding *state change*", i.e. BACKTRACK and DRILL_DOWN also count. A
  backtrack deletes graph levels and a drill-down adds hypotheses, so a report
  written afterwards genuinely depends on them. Restricting the rule to
  GRAPH_UPDATE would have made the parent pointer wrong, not stricter.
* `GRAPH_UPDATE` has *all* of the step's ANALYSIS events as parents, not one.
  A step may dispatch several experts (`act(plan)` loops), and the graph edit
  is generated from the concatenation of their analyses.

New ANALYSIS parents are `AGENT_CALL` + the completed retrieval TOOL_CALLs
*this expert* performed for this focus. Legacy traces use EVIDENCE_READ parents.
An expert that analyses without retrieving is legal and simply has one parent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# child event_type -> (allowed parent event_types, required parent event_types)
PARENT_RULES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "PLAN": {"allow": ("GRAPH_UPDATE", "BACKTRACK", "DRILL_DOWN"), "require": ()},
    "AGENT_CALL": {"allow": ("PLAN",), "require": ("PLAN",)},
    "TOOL_CALL": {"allow": ("AGENT_CALL",), "require": ("AGENT_CALL",)},
    "EVIDENCE_READ": {"allow": ("TOOL_CALL",), "require": ("TOOL_CALL",)},
    "ANALYSIS": {"allow": ("AGENT_CALL", "TOOL_CALL", "EVIDENCE_READ"),
                 "require": ("AGENT_CALL",)},
    "GRAPH_UPDATE": {"allow": ("ANALYSIS",), "require": ()},
    "BACKTRACK": {"allow": ("GRAPH_UPDATE",), "require": ("GRAPH_UPDATE",)},
    "DRILL_DOWN": {"allow": ("GRAPH_UPDATE",), "require": ("GRAPH_UPDATE",)},
    "FINAL_REPORT": {"allow": ("GRAPH_UPDATE", "BACKTRACK", "DRILL_DOWN"),
                     "require": ()},
}

# Events that mark the start of a reasoning step or of the task itself.
STATE_CHANGES = ("GRAPH_UPDATE", "BACKTRACK", "DRILL_DOWN")


def is_applied(event: Dict[str, Any]) -> bool:
    """Did this state-change event actually change the graph?

    A BACKTRACK is emitted whenever `backtrack_frontier_chain()` decides the
    frontier regressed, but the deletion loop only runs for levels *above* the
    demoted one. Case 2 backtracks 1 -> 1 and deletes nothing; that is not a
    state change anything downstream can depend on. The hooks record `applied`
    explicitly so the checker does not have to re-derive it from the payload.
    Events without the flag (GRAPH_UPDATE) are always applied.
    """
    return bool((event.get("payload") or {}).get("applied", True))


def is_ingest(event: Dict[str, Any]) -> bool:
    return ingest_phase(event) != ""


def ingest_phase(event: Dict[str, Any]) -> str:
    """'' | 'basic_node' | 'l1_hypo' for the ingest GRAPH_UPDATEs."""
    phase = str((event.get("payload") or {}).get("phase", ""))
    return phase.split(":", 1)[1] if phase.startswith("ingest:") else ""


@dataclass
class Issue:
    kind: str
    event_id: str = ""
    detail: str = ""
    severity: str = "error"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "event_id": self.event_id,
                "detail": self.detail, "severity": self.severity}


@dataclass
class DagReport:
    task_id: str = ""
    n_events: int = 0
    n_edges: int = 0
    issues: List[Issue] = field(default_factory=list)
    topo_order: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not [i for i in self.issues if i.severity == "error"]

    def by_kind(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for i in self.issues:
            out[i.kind] = out.get(i.kind, 0) + 1
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "n_events": self.n_events,
                "n_edges": self.n_edges, "ok": self.ok,
                "issues_by_kind": self.by_kind(),
                "issues": [i.to_dict() for i in self.issues],
                "topo_ok": not any(i.kind == "cycle" for i in self.issues)}


# --------------------------------------------------------------------- graph
class ExecutionDAG:
    """Directed edges child -> parent (`parent_event_ids`) plus reverse lookup."""

    def __init__(self, events: Sequence[Dict[str, Any]]):
        self.events = list(events)
        self.by_id: Dict[str, Dict[str, Any]] = {e["event_id"]: e for e in self.events}
        self.order: Dict[str, int] = {e["event_id"]: i
                                      for i, e in enumerate(self.events)}
        self.parents: Dict[str, List[str]] = {}
        self.children: Dict[str, List[str]] = {e["event_id"]: [] for e in self.events}
        for e in self.events:
            eid = e["event_id"]
            ps = [p for p in (e.get("parent_event_ids") or []) if p in self.by_id]
            self.parents[eid] = ps
            for p in ps:
                self.children[p].append(eid)

    # ------------------------------------------------------------- traversal
    @property
    def roots(self) -> List[str]:
        return [eid for eid in self.order if not self.parents[eid]]

    @property
    def leaves(self) -> List[str]:
        return [eid for eid in self.order if not self.children[eid]]

    def of_type(self, event_type: str) -> List[str]:
        return [e["event_id"] for e in self.events
                if e["event_type"] == event_type]

    def ancestors(self, event_id: str) -> List[str]:
        """Every event this one transitively depends on (nearest first)."""
        seen: List[str] = []
        marked = set()
        frontier = list(self.parents.get(event_id, []))
        while frontier:
            nxt: List[str] = []
            for n in frontier:
                if n in marked:
                    continue
                marked.add(n)
                seen.append(n)
                nxt.extend(self.parents.get(n, []))
            frontier = nxt
        return seen

    def descendants(self, event_id: str) -> List[str]:
        """Every event transitively derived from this one (nearest first)."""
        seen: List[str] = []
        marked = set()
        frontier = list(self.children.get(event_id, []))
        while frontier:
            nxt: List[str] = []
            for n in frontier:
                if n in marked:
                    continue
                marked.add(n)
                seen.append(n)
                nxt.extend(self.children.get(n, []))
            frontier = nxt
        return seen

    def path_to_root(self, event_id: str) -> List[List[str]]:
        """All parent chains from `event_id` up to the roots (DFS, nearest first)."""
        out: List[List[str]] = []

        def walk(node: str, acc: List[str]) -> None:
            ps = self.parents.get(node, [])
            if not ps:
                out.append(list(acc))
                return
            for p in ps:
                if p in acc:          # cycle guard; cycles are reported separately
                    out.append(list(acc) + [p])
                    continue
                walk(p, acc + [p])

        walk(event_id, [event_id])
        return out

    def shortest_chain(self, src: str, dst: str) -> Optional[List[str]]:
        """Shortest chain `[src, ..., dst]` following parent edges.

        Ordering matches `path_to_root`: the queried event first, the cause
        last, so a caller can read it as "this happened *because of* that".
        """
        if src == dst:
            return [src]
        prev: Dict[str, str] = {}
        queue = [src]
        seen = {src}
        while queue:
            nxt: List[str] = []
            for n in queue:
                for p in self.parents.get(n, []):
                    if p in seen:
                        continue
                    seen.add(p)
                    prev[p] = n
                    if p == dst:
                        chain = [dst]
                        cur = n
                        while cur != src:
                            chain.append(cur)
                            cur = prev[cur]
                        chain.append(src)
                        chain.reverse()      # [src, ..., dst], like path_to_root
                        return chain
                    nxt.append(p)
            queue = nxt
        return None

    # -------------------------------------------------------------- topology
    def topo_order(self) -> Optional[List[str]]:
        """Parents before children. None when the graph has a cycle."""
        indeg = {eid: 0 for eid in self.order}
        for eid, ps in self.parents.items():
            indeg[eid] = len(ps)
        # `parents` already drops dangling refs, so indeg == number of edges in.
        ready = [eid for eid, d in indeg.items() if d == 0]
        ready.sort(key=lambda e: self.order[e])
        out: List[str] = []
        while ready:
            nid = ready.pop(0)
            out.append(nid)
            for c in self.children[nid]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
        if len(out) != len(self.events):
            return None
        return out


# -------------------------------------------------------------------- checks
def check_parent_rules(dag: ExecutionDAG) -> List[Issue]:
    """The nine rules, checked literally on the emitted parent pointers."""
    issues: List[Issue] = []
    for e in dag.events:
        eid, etype = e["event_id"], e["event_type"]
        if etype not in PARENT_RULES:
            continue
        rule = PARENT_RULES[etype]
        raw = list(e.get("parent_event_ids") or [])
        present = [p for p in raw if p in dag.by_id]

        for p in raw:
            if p not in dag.by_id:
                issues.append(Issue(
                    "dangling_parent", eid,
                    f"{etype} names parent {p!r} which is not in this task's log"))

        # The first ingest is the task's initial event and depends on nothing.
        # The L1 pass is built from the nodes the first one created, so it must
        # depend on it (rule 10 -- see the module docstring).
        if etype == "GRAPH_UPDATE" and is_ingest(e):
            if ingest_phase(e) == "basic_node":
                if present:
                    issues.append(Issue(
                        "unexpected_parent", eid,
                        "the first ingest GRAPH_UPDATE must have no parent"))
            else:
                if not present:
                    issues.append(Issue(
                        "missing_required_parent", eid,
                        "the L1 ingest GRAPH_UPDATE must depend on the first "
                        "ingest GRAPH_UPDATE"))
                for p in present:
                    pv = dag.by_id[p]
                    if not (pv["event_type"] == "GRAPH_UPDATE" and is_ingest(pv)):
                        issues.append(Issue(
                            "illegal_parent_type", eid,
                            f"the L1 ingest may only depend on the first "
                            f"ingest, got {pv['event_type']}"))
            continue

        kinds = [dag.by_id[p]["event_type"] for p in present]
        bad = [f"{p}({dag.by_id[p]['event_type']})"
               for p in present if dag.by_id[p]["event_type"] not in rule["allow"]]
        if bad:
            issues.append(Issue(
                "illegal_parent_type", eid,
                f"{etype} may only depend on {rule['allow']}, got {bad}"))

        for want in rule["require"]:
            if want not in kinds:
                issues.append(Issue(
                    "missing_required_parent", eid,
                    f"{etype} must depend on at least one {want}; "
                    f"parents are {kinds or 'none'}"))

        for p in present:
            if dag.order[p] >= dag.order[eid]:
                issues.append(Issue(
                    "parent_not_before_child", eid,
                    f"parent {p} was logged after its child"))
    return issues


def check_step_locality(dag: ExecutionDAG) -> List[Issue]:
    """`parent` must be the *most recent* matching event, not just any one.

    Without this, a hook that kept a stale pointer would still pass the rule
    table above. Concretely:
      AGENT_CALL -> the latest PLAN before it
      TOOL_CALL / EVIDENCE_READ -> the latest call of the current dispatch
      GRAPH_UPDATE -> exactly the ANALYSIS events since the last PLAN
    """
    issues: List[Issue] = []
    last_plan: Optional[str] = None
    step_analyses: List[str] = []
    last_by_type: Dict[str, str] = {}
    # expert name -> AGENT_CALL id. The expert is named by `tool_or_model` (and
    # `payload.expert`); `actor_id` is the *central* agent that dispatched, so
    # keying on actor_id would silently never match a TOOL_CALL.
    dispatch_of: Dict[str, str] = {}

    for e in dag.events:
        eid, etype = e["event_id"], e["event_type"]
        ps = dag.parents[eid]
        payload = e.get("payload") or {}

        if etype == "PLAN":
            if last_plan is not None:
                last_state = _latest_state_change_before(dag, eid)
                if last_state and ps != [last_state]:
                    issues.append(Issue(
                        "stale_parent", eid,
                        f"PLAN should point at the latest state change "
                        f"{last_state}, points at {ps}"))
            last_plan = eid
            step_analyses = []

        elif etype == "AGENT_CALL":
            if ps != [last_plan] and not (ps == [] and last_plan is None):
                issues.append(Issue(
                    "stale_parent", eid,
                    f"AGENT_CALL should point at the latest PLAN {last_plan}, "
                    f"points at {ps}"))
            expert = (payload.get("expert") or e.get("tool_or_model") or "")
            if expert:
                dispatch_of[expert] = eid

        elif etype == "TOOL_CALL":
            want = dispatch_of.get(e.get("actor_id", ""))
            if ps != [want]:
                issues.append(Issue(
                    "stale_parent", eid,
                    f"TOOL_CALL should point at the dispatching AGENT_CALL "
                    f"{want}, points at {ps}"))

        elif etype == "EVIDENCE_READ":
            want = last_by_type.get("TOOL_CALL")
            if ps != [want]:
                issues.append(Issue(
                    "stale_parent", eid,
                    f"EVIDENCE_READ should point at the latest TOOL_CALL "
                    f"{want}, points at {ps}"))

        elif etype == "ANALYSIS":
            dispatch = dispatch_of.get(e.get("actor_id", ""))
            if dispatch and dispatch not in ps:
                issues.append(Issue(
                    "stale_parent", eid,
                    f"ANALYSIS should include its AGENT_CALL {dispatch}, "
                    f"points at {ps}"))
            step_analyses.append(eid)

        elif etype == "GRAPH_UPDATE":
            if not is_ingest(e) and sorted(ps) != sorted(step_analyses):
                issues.append(Issue(
                    "step_parent_mismatch", eid,
                    f"GRAPH_UPDATE should depend on exactly this step's "
                    f"ANALYSIS events {sorted(step_analyses)}, points at "
                    f"{sorted(ps)}"))
            if is_ingest(e):
                last_plan = None
                step_analyses = []

        elif etype in ("BACKTRACK", "DRILL_DOWN"):
            want = last_by_type.get("GRAPH_UPDATE")
            if ps != [want]:
                issues.append(Issue(
                    "stale_parent", eid,
                    f"{etype} should point at the preceding GRAPH_UPDATE "
                    f"{want}, points at {ps}"))

        last_by_type[etype] = eid
    return issues


def _latest_state_change_before(dag: ExecutionDAG, event_id: str) -> Optional[str]:
    """Most recent event a PLAN could legitimately depend on.

    The L1 ingest counts: the first PLAN is chosen from the hypotheses it
    created. A parentless first ingest does not, since nothing followed it.
    """
    best = None
    for e in dag.events[:dag.order[event_id]]:
        if e["event_type"] in STATE_CHANGES and is_applied(e):
            best = e["event_id"]
    return best


def check_structure(dag: ExecutionDAG) -> List[Issue]:
    """Dangling parents, cycles, unreachable reports -- amendment §3.2."""
    issues: List[Issue] = []

    order = dag.topo_order()
    if order is None:
        issues.append(Issue("cycle", "", "the parent relation contains a cycle"))

    for e in dag.events:
        if e["event_type"] in ("BACKTRACK", "DRILL_DOWN") and not is_applied(e):
            issues.append(Issue(
                "noop_state_change", e["event_id"],
                f"{e['event_type']} fired but mutated nothing "
                f"(payload={e.get('payload')}); downstream events correctly do "
                f"not depend on it", severity="info"))

    reports = dag.of_type("FINAL_REPORT")
    if not reports:
        issues.append(Issue("no_final_report", "",
                            "a task must end with exactly one FINAL_REPORT"))
    elif len(reports) > 1:
        issues.append(Issue("multiple_final_reports", "",
                            f"expected one FINAL_REPORT, found {len(reports)}"))
    else:
        # The report must descend from the case input. Checking a specific event
        # would be wrong when the task has several roots, so require the
        # report's ancestor set to contain the ingest GRAPH_UPDATE.
        ingests = [e["event_id"] for e in dag.events
                   if e["event_type"] == "GRAPH_UPDATE" and is_ingest(e)]
        ancestors = set(dag.ancestors(reports[0]))
        if not ancestors & set(ingests):
            issues.append(Issue(
                "unreachable_final_report", reports[0],
                "no parent chain from FINAL_REPORT back to the ingest of the "
                f"case (ingest events: {ingests})"))

    for eid in dag.order:
        if eid not in dag.by_id:
            issues.append(Issue("dangling_parent", eid,
                                "event references a parent outside this task"))
    return issues


def initial_event(dag: ExecutionDAG) -> Optional[str]:
    """The first event that produced graph content (the ingest GRAPH_UPDATE).

    Used by the path tracer as the natural root when it has to explain an
    event whose own chain is incomplete.
    """
    for e in dag.events:
        if e["event_type"] == "GRAPH_UPDATE" and is_ingest(e):
            return e["event_id"]
    return dag.events[0]["event_id"] if dag.events else None


def check(events: Iterable[Dict[str, Any]],
          task_id: str = "") -> Tuple[ExecutionDAG, DagReport]:
    """Full §3.2 consistency check. Returns the DAG too, for reuse."""
    dag = ExecutionDAG(list(events))
    issues = (check_parent_rules(dag) + check_step_locality(dag)
              + check_structure(dag))
    rep = DagReport(task_id=task_id or (dag.events[0]["task_id"]
                                        if dag.events else ""),
                    n_events=len(dag.events),
                    n_edges=sum(len(v) for v in dag.parents.values()),
                    issues=issues,
                    topo_order=dag.topo_order() or [])
    return dag, rep


# -------------------------------------------------------------------- loader
def load_events(path: str) -> List[Dict[str, Any]]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
