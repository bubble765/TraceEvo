"""Controlled anomaly injection A0-A5 (S2 §5, §8.2).

Two mechanisms, chosen per §8.2's cost rule:

* **artifact mutation** (default, zero LLM calls). The recorded run is edited
  and made *internally consistent* again: the event hash chain is re-sealed and
  the graph-delta hashes are recomputed. That re-sealing is not cheating -- a
  fabrication produced by a real run yields exactly this: a trace whose hashes
  all verify and whose semantics are still wrong. If the chain were left broken
  the auditor would report A5 and the A1 measurement would be confounded.

* **response substitution** (needs the LLM). The recorded response for one call
  is replaced and the run continues live, so the pipeline itself produces the
  injected graph, deltas and downstream prompts. Used for the end-to-end A4
  case studies, where §5 explicitly wants the error to reach Graph Update /
  Hypothesis / Final Report rather than be simulated after the fact.

Ground truth is recorded as a `Injection`: which event/node is the root, what
the injector changed, and (for A4) which downstream events the execution DAG
says are reachable from it. The downstream sets are *derived from the DAG*, not
hand-written, so they cannot drift from the trace.
"""
from __future__ import annotations

import copy
import json
import os
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import graph_delta as gd
from .event_schema import GENESIS_HASH, canonical_json, sha256_hex, utc_now
from .cross_graph_binding import record_hash, records_from_events

# The fixed fallback string upstream returns when an expert cannot analyse.
UPSTREAM_FALLBACK = ("Insufficient information to complete the analysis "
                     "(max retrieval steps reached or invalid decision/response)")

ANOMALY_TYPES = ("A0", "A1", "A2", "A3", "A4", "A5")

# A finding that is squarely "usable as a clinical fact" (§5 A1): a measurement
# with a value, attributed to a named test. Deliberately *not* an absence claim
# and *not* a recommendation -- §5 forbids using those for A1.
FABRICATED_FINDINGS = [
    "Serum tryptase was elevated at 42 ng/mL and the bone marrow aspirate "
    "showed mast cell infiltration",
    "Immunofluorescence microscopy demonstrated linear IgA deposition along "
    "the basement membrane",
    "Positron emission tomography showed increased uptake in the left "
    "adrenal gland with a standardized uptake value of 7.4",
    "Peripheral blood flow cytometry identified a clonal T-cell population "
    "comprising 18% of lymphocytes",
]


@dataclass
class Injection:
    injection_id: str
    case_id: str
    anomaly_type: str
    variant: str
    injector_rule: str
    # S2.1 §2.2 keeps two concepts apart. The *latent* root is where the
    # damage was done; the *observable* root is where a detector can see it.
    # They coincide except for A5 delete/truncate, where the latent root is an
    # event that no longer exists in the trace and no detector could name it.
    # Detection is scored against the observable root, and the metric is
    # reported as "observable-root localization", never as "exact injected
    # root localization".
    root_event_id: str = ""            # == observable_root_event_id
    root_node_id: str = ""
    latent_root_event_id: str = ""
    observable_root_event_id: str = ""
    affected_events: List[str] = field(default_factory=list)
    affected_nodes: List[str] = field(default_factory=list)
    path_to_conclusion: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ------------------------------------------------------------------ artifacts
class TaskArtifacts:
    """One task's recorded provenance, mutable in memory."""

    def __init__(self, task_id: str, bindings: Dict[str, Any],
                 events: List[Dict[str, Any]], deltas: List[Dict[str, Any]]):
        self.task_id = task_id
        self.bindings = bindings
        self.events = events
        self.deltas = deltas
        # Reconstructed post-delta graph states. Persisted so the injected
        # trace can be replayed with `after_states` exactly like a real run,
        # which is what makes "the injection did not break the replay chain"
        # a checkable claim rather than an assertion.
        self.states: List[Dict[str, Any]] = []

    @classmethod
    def load(cls, run_dir: str, task_id: str) -> "TaskArtifacts":
        with open(os.path.join(run_dir, f"{task_id}_bindings.json"),
                  "r", encoding="utf-8") as f:
            bindings = json.load(f)
        events = [json.loads(l) for l in
                  open(os.path.join(run_dir, f"{task_id}_events.jsonl"),
                       encoding="utf-8") if l.strip()]
        # New traces keep retrieval bodies on their completed TOOL_CALL event.
        # Rehydrate the in-memory view expected by the mutation/audit helpers.
        bindings["records"] = records_from_events(
            events, bindings.get("records") or [])
        deltas = [json.loads(l) for l in
                  open(os.path.join(run_dir, f"{task_id}_deltas.jsonl"),
                       encoding="utf-8") if l.strip()]
        return cls(task_id, bindings, events, deltas)

    def save(self, out_dir: str, task_id: Optional[str] = None) -> str:
        task = task_id or self.task_id
        os.makedirs(out_dir, exist_ok=True)
        self.bindings["task_id"] = task
        # Persist only record identity and audit metadata. New TOOL_CALL events
        # are the canonical text carrier, so injected copies must not re-add
        # records[].text to bindings.json.
        persisted_bindings = dict(self.bindings)
        persisted_bindings["records"] = []
        for record in self.bindings.get("records") or []:
            row = dict(record)
            row.pop("text", None)
            persisted_bindings["records"].append(row)
        with open(os.path.join(out_dir, f"{task}_bindings.json"), "w",
                  encoding="utf-8") as f:
            json.dump(persisted_bindings, f, ensure_ascii=False, indent=1)
        with open(os.path.join(out_dir, f"{task}_events.jsonl"), "w",
                  encoding="utf-8") as f:
            for e in self.events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        with open(os.path.join(out_dir, f"{task}_deltas.jsonl"), "w",
                  encoding="utf-8") as f:
            for d in self.deltas:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        if self.states:
            with open(os.path.join(out_dir, f"{task}_graph_states.jsonl"), "w",
                      encoding="utf-8") as f:
                for s in self.states:
                    f.write(json.dumps(s, ensure_ascii=False) + "\n")
        self.verify_replay(out_dir, task)
        return out_dir

    def verify_replay(self, out_dir: str, task: str) -> Dict[str, Any]:
        """Re-derive `Replay(G_t, delta_t) == G_(t+1)` on the mutated trace.

        Only meaningful once `rebuild_delta_hashes()` has run, which every
        semantic injection (A0-A4) calls. A5 deliberately leaves the trace
        broken, so for those the report says so instead of pretending to check.
        """
        from .graph_delta import GraphDelta, replay
        if not self.states:
            report = {"task_id": task, "applicable": False,
                      "n_deltas": len(self.deltas),
                      "reason": ("trace tampering is not re-sealed; the delta "
                                 "chain is left exactly as recorded")}
            with open(os.path.join(out_dir, f"{task}_replay.json"), "w",
                      encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=1)
            return report
        initial = {"nodes": {}, "edges": [], "start_signal_id": None}
        deltas = [GraphDelta.from_dict(d) for d in self.deltas]
        _state, checks = replay(initial, deltas, after_states=self.states)
        report = {"task_id": task, "applicable": True, "n_deltas": len(deltas),
                  "all_ok": all(c["ok"] for c in checks), "checks": checks}
        with open(os.path.join(out_dir, f"{task}_replay.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        return report

    # ------------------------------------------------------------- accessors
    @property
    def nodes(self) -> List[Dict[str, Any]]:
        return self.bindings.get("bindings") or []

    @property
    def records(self) -> List[Dict[str, Any]]:
        return self.bindings.get("records") or []

    def node(self, node_id: str) -> Optional[Dict[str, Any]]:
        for n in self.nodes:
            if n.get("node_id") == node_id:
                return n
        return None

    def record(self, record_id: str) -> Optional[Dict[str, Any]]:
        for r in self.records:
            if r.get("record_id") == record_id:
                return r
        return None

    def of_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]

    def events_for_delta(self, delta: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_id"] == delta.get("event_id")]

    def next_node_id(self, tag: str) -> str:
        used = {n.get("node_id") for n in self.nodes}
        i = 1
        while f"node-{tag}{i}" in used:
            i += 1
        return f"node-{tag}{i}"

    def next_event_id(self, tag: str = "inj") -> str:
        used = {e["event_id"] for e in self.events}
        i = 1
        while f"evt-{self.task_id}-{tag}{i:03d}" in used:
            i += 1
        return f"evt-{self.task_id}-{tag}{i:03d}"

    # --------------------------------------------------------------- re-seal
    def reseal_events(self) -> None:
        """Recompute prev_hash/event_hash over the (mutated) event list.

        Every injection except A5 must end with this, otherwise the tampering
        it introduced is indistinguishable from an A5 integrity break.
        """
        prev = GENESIS_HASH
        for e in self.events:
            e["prev_hash"] = prev
            e.pop("event_hash", None)
            e["event_hash"] = sha256_hex(canonical_json(e))
            prev = e["event_hash"]

    def rebuild_delta_hashes(self) -> None:
        """Recompute the delta chain's before/after hashes.

        Node content comes from the bindings (type and label); score/attrs are
        not carried in any persisted artifact, so the reconstructed states are
        *structurally* faithful rather than byte-identical to the originals.
        Every hash in the chain is recomputed from that same reconstruction, so
        `Replay(G_t, delta_t) == G_(t+1)` still holds and `replay()` verifies.
        """
        content = {n["node_id"]: {"type": n.get("node_type"),
                                  "label": n.get("label"),
                                  "score": 0.0, "attrs": {}}
                   for n in self.nodes}
        state = {"nodes": {}, "edges": [], "start_signal_id": None}
        self.states = []
        prev_hash = gd.state_hash(state)
        for d in self.deltas:
            for nid in d.get("deleted_nodes") or []:
                state["nodes"].pop(nid, None)
            for nid in (d.get("added_nodes") or []) + (d.get("updated_nodes") or []):
                if nid in content:
                    state["nodes"][nid] = copy.deepcopy(content[nid])
            keyed = {(e["src"], e["dst"]): e for e in state["edges"]}
            for e in d.get("deleted_edges") or []:
                keyed.pop((e[0], e[1]), None)
            for e in d.get("added_edges") or []:
                keyed[(e[0], e[1])] = {
                    "src": e[0], "dst": e[1],
                    "type": e[2] if len(e) > 2 else "", "attrs": {}}
            state["edges"] = list(keyed.values())
            d["before_hash"] = prev_hash
            d["after_hash"] = gd.state_hash(state)
            prev_hash = d["after_hash"]
            self.states.append(copy.deepcopy(state))

    def final_state(self) -> Dict[str, Any]:
        """Graph state after the whole delta chain (used for edge lookups)."""
        state = {"nodes": {}, "edges": [], "start_signal_id": None}
        for d in self.deltas:
            for nid in d.get("deleted_nodes") or []:
                state["nodes"].pop(nid, None)
            for nid in (d.get("added_nodes") or []) + (d.get("updated_nodes") or []):
                state["nodes"].setdefault(nid, {})
            keyed = {(e["src"], e["dst"]): e for e in state["edges"]}
            for e in d.get("deleted_edges") or []:
                keyed.pop((e[0], e[1]), None)
            for e in d.get("added_edges") or []:
                keyed[(e[0], e[1])] = {"src": e[0], "dst": e[1],
                                       "type": e[2] if len(e) > 2 else ""}
            state["edges"] = list(keyed.values())
        return state


# ----------------------------------------------------------- downstream sets
def downstream_events(art: TaskArtifacts, root_event_id: str) -> List[str]:
    """Every event transitively dependent on `root_event_id` (S2 §3.2 DAG)."""
    from .dependency_graph import ExecutionDAG
    dag = ExecutionDAG(art.events)
    return dag.descendants(root_event_id)


def downstream_nodes(art: TaskArtifacts, root_event_id: str) -> List[str]:
    """Graph nodes created by the root event or any event depending on it."""
    ids = [root_event_id] + downstream_events(art, root_event_id)
    out: List[str] = []
    for e in art.events:
        if e["event_id"] in ids:
            out.extend(e.get("causal_node_refs") or [])
    return sorted(set(out))


def path_to_conclusion(art: TaskArtifacts, root_event_id: str) -> List[str]:
    """Shortest parent chain from FINAL_REPORT back to the root, if any.

    This is the *execution* path, not a claim about the model's reasoning. §7
    is explicit that dependency must not be reported as mental causation.
    """
    from .dependency_graph import ExecutionDAG
    dag = ExecutionDAG(art.events)
    reports = dag.of_type("FINAL_REPORT")
    if not reports:
        return []
    chain = dag.shortest_chain(reports[0], root_event_id)
    return chain or []


def _finish(art: TaskArtifacts, inj: Injection) -> Injection:
    inj.observable_root_event_id = inj.observable_root_event_id or inj.root_event_id
    inj.latent_root_event_id = inj.latent_root_event_id or inj.root_event_id
    inj.root_event_id = inj.observable_root_event_id
    inj.affected_events = downstream_events(art, inj.root_event_id)
    inj.affected_nodes = downstream_nodes(art, inj.root_event_id)
    inj.path_to_conclusion = path_to_conclusion(art, inj.root_event_id)
    art.reseal_events()
    art.rebuild_delta_hashes()
    return inj


# ------------------------------------------------------------------ injectors
def inject_a0(art: TaskArtifacts, case_id: str, *,
              variant: str = "status_marked") -> Injection:
    """Silent degradation (§5 A0).

    `status_marked`  -- what the pipeline produces once §3.1 D1 is implemented:
                        the fallback text *and* status=degraded.
    `silent`         -- the pre-D1 world: the fallback text with status=success.
                        Nothing in the trace announces it; only the text
                        signature gives it away. This is the variant that makes
                        the detection claim meaningful rather than a field read.
    """
    analyses = art.of_type("ANALYSIS")
    if not analyses:
        raise ValueError("task has no ANALYSIS to degrade")
    # Not the last one: the degradation should still propagate to a report.
    target = analyses[0] if len(analyses) > 1 else analyses[-1]
    target["payload"]["analysis"] = UPSTREAM_FALLBACK
    target["payload"]["exit_cause"] = "budget" if variant == "status_marked" else ""
    if variant == "status_marked":
        target["status"] = "degraded"
        target["reason_code"] = "BUDGET_EXHAUSTED"
    else:
        target["status"] = "success"
        target["reason_code"] = "NONE"

    inj = Injection(
        injection_id=f"{case_id}-A0-{variant}", case_id=case_id,
        anomaly_type="A0", variant=variant,
        injector_rule=("set an ANALYSIS to the upstream fallback text; "
                       + ("mark status=degraded/BUDGET_EXHAUSTED"
                          if variant == "status_marked"
                          else "leave status=success")),
        root_event_id=target["event_id"],
        detail={"status_set": target["status"],
                "reason_code_set": target["reason_code"],
                "text_is_upstream_fallback": True})
    return _finish(art, inj)


def inject_a1(art: TaskArtifacts, case_id: str, *, rng: random.Random,
              variant: str = "add_node") -> Injection:
    """Fabricated / unsupported evidence (§5 A1).

    Adds an Evidence node asserting a specific clinical finding that exists
    neither in the case input nor in any retrieval record, and wires it to a
    hypothesis with a support edge so it reaches reasoning. The node is
    introduced by the *first real* update-graph event, which maximises the
    downstream set the propagation metrics are measured against.
    """
    updates = [e for e in art.events
               if e["event_type"] == "GRAPH_UPDATE"
               and not str((e.get("payload") or {}).get("phase", "")).startswith("ingest")]
    hypos = [n for n in art.nodes if n.get("node_type") == "Hypothesis"]
    if not updates or not hypos:
        raise ValueError("task needs an update-graph event and a hypothesis")

    graph_event = updates[0]
    hypo = rng.choice(hypos)
    finding = FABRICATED_FINDINGS[rng.randrange(len(FABRICATED_FINDINGS))]

    new_id = art.next_node_id("fab")
    art.nodes.append({
        "node_id": new_id, "node_type": "Evidence", "label": finding,
        "created_by_event": graph_event["event_id"],
        "created_at_step": graph_event["step_id"],
        "source_class": "AGENT_GENERATED", "semantic_kind": "",
        "match_tier": "none", "matched_record_id": "",
        "matched_retrieval_event": "", "match_coverage": 0.0,
    })
    graph_event.setdefault("causal_node_refs", []).append(new_id)

    delta = next((d for d in art.deltas
                  if d.get("event_id") == graph_event["event_id"]), None)
    if delta is None:
        raise ValueError("no delta is bound to the update-graph event")
    delta.setdefault("added_nodes", []).append(new_id)
    delta.setdefault("added_edges", []).append([new_id, hypo["node_id"], "support"])

    inj = Injection(
        injection_id=f"{case_id}-A1-{variant}", case_id=case_id,
        anomaly_type="A1", variant=variant,
        injector_rule=("append a fabricated Evidence node to the first "
                       "update-graph event, wired to a hypothesis with a "
                       "support edge"),
        root_event_id=graph_event["event_id"], root_node_id=new_id,
        detail={"fabricated_label": finding,
                "attached_to_hypothesis": hypo["node_id"],
                "edge_type": "support",
                "in_input": False, "in_records": False})
    return _finish(art, inj)


def inject_a2(art: TaskArtifacts, case_id: str, *,
              variant: str = "wrong_record_binding",
              rng: Optional[random.Random] = None) -> Injection:
    """Evidence misbinding / tampering (§5 A2)."""
    if variant == "wrong_record_binding":
        bound = [n for n in art.nodes
                 if n.get("source_class") == "RETRIEVED_RECORD"
                 and n.get("matched_record_id")]
        if not bound or len(art.records) < 2:
            raise ValueError("needs a bound Evidence node and >=2 records")
        target = bound[0]
        correct = target["matched_record_id"]
        others = [r["record_id"] for r in art.records if r["record_id"] != correct]
        wrong = (rng or random.Random(0)).choice(others)
        target["matched_record_id"] = wrong
        target["match_tier"] = "verbatim"
        inj = Injection(
            injection_id=f"{case_id}-A2-{variant}", case_id=case_id,
            anomaly_type="A2", variant=variant,
            injector_rule=("repoint an Evidence node's binding to a retrieval "
                           "record that does not contain its text"),
            root_event_id=target.get("created_by_event", ""),
            root_node_id=target["node_id"],
            detail={"node_label": target["label"],
                    "record_id_before": correct, "record_id_after": wrong,
                    "records_n": len(art.records)})
        return _finish(art, inj)

    if variant == "record_tampering":
        if not art.records:
            raise ValueError("task has no retrieval records")
        rec = art.records[0]
        original = rec["text"]
        tampered = ("Chest radiograph was unremarkable and no acute "
                    "abnormality was identified.")
        rec["text"] = tampered
        # New traces keep the record body on the combined TOOL_CALL event.
        # Mutate that canonical carrier and leave record_hash unchanged.
        event = next((row for row in art.events
                      if row.get("event_id") == rec.get("retrieval_event_id")), None)
        if event and event.get("event_type") == "TOOL_CALL" \
                and "retrieval_output" in (event.get("payload") or {}):
            event.setdefault("payload", {})["retrieval_output"] = tampered
        elif event and event.get("event_type") == "EVIDENCE_READ":
            # Legacy traces stored the body in bindings.json.
            pass
        # record_hash is deliberately NOT updated: that mismatch is the
        # detectable trace, and it is what A2 exploits.
        inj = Injection(
            injection_id=f"{case_id}-A2-{variant}", case_id=case_id,
            anomaly_type="A2", variant=variant,
            injector_rule=("overwrite a retrieval record's text while keeping "
                           "its record_id and record_hash"),
            root_event_id=rec.get("retrieval_event_id", ""),
            root_node_id="",
            detail={"record_id": rec["record_id"],
                    "hash_recorded": rec.get("record_hash", ""),
                    "hash_recomputed": record_hash(
                        art.task_id, rec.get("query", ""), rec["text"],
                        rec.get("expert", "")),
                    "text_chars_before": len(original),
                    "text_chars_after": len(rec["text"])})
        return _finish(art, inj)

    raise ValueError(f"unknown A2 variant {variant!r}")


def inject_a3(art: TaskArtifacts, case_id: str, *,
              variant: str = "unresolvable_expert",
              requested: str = "Laboratory Physician",
              available: Optional[Sequence[str]] = None) -> Injection:
    """Invalid capability invocation (§5 A3).

    Reproduces the real S1 failure: the planner names "Laboratory Physician"
    while the only agent in the roster is "Laboratory_Physician". The failed
    dispatch is recorded *between* the PLAN and whatever the plan went on to
    do, exactly where the upstream exception would have been raised.
    """
    plans = art.of_type("PLAN")
    if not plans:
        raise ValueError("task has no PLAN event")
    plan = plans[0]
    roster = list(available or [n["tool_or_model"] for n in art.events
                                if n["event_type"] == "AGENT_CALL"
                                and n.get("status") == "success"])

    eid = art.next_event_id("a3")
    event = {
        "event_id": eid, "task_id": art.task_id, "step_id": plan["step_id"],
        "timestamp": utc_now(), "event_type": "AGENT_CALL",
        "actor_id": plan["actor_id"], "actor_role": "central",
        "tool_or_model": requested, "input_refs": [], "output_refs": [],
        "causal_node_refs": list(plan.get("causal_node_refs") or []),
        "parent_event_ids": [plan["event_id"]],
        "source_class": "AGENT_GENERATED", "model_id": "deepseek-v4-flash",
        "model_version": None, "version_observable": False,
        "status": "failed", "reason_code": "INVALID_EXPERT_DISPATCH",
        "prev_hash": GENESIS_HASH, "event_hash": "",
        "payload": {"expert": requested, "available": roster,
                    "error": f"Not founded, current list：{roster}",
                    "dispatch": "failed"},
    }
    idx = art.events.index(plan) + 1
    art.events.insert(idx, event)

    inj = Injection(
        injection_id=f"{case_id}-A3-{variant}", case_id=case_id,
        anomaly_type="A3", variant=variant,
        injector_rule=("insert a failed AGENT_CALL naming an expert that is "
                       "not in the roster, immediately after the PLAN"),
        root_event_id=eid,
        detail={"requested_expert": requested, "available": roster,
                "parent_plan": plan["event_id"],
                "status": "failed", "reason_code": "INVALID_EXPERT_DISPATCH"})
    return _finish(art, inj)


def inject_a4(art: TaskArtifacts, case_id: str, *, rng: random.Random,
              variant: str = "corrupt_analysis",
              corrupt_text: Optional[str] = None) -> Injection:
    """Corrupted intermediate analysis (§5 A4).

    Rewrites one expert's ANALYSIS to assert a finding the expert never
    obtained, keeping the event, its parents and its retrievals untouched --
    which is what makes it corrupted rather than missing.
    """
    analyses = art.of_type("ANALYSIS")
    if not analyses:
        raise ValueError("task has no ANALYSIS event")
    target = analyses[0] if len(analyses) > 1 else analyses[-1]
    original = target["payload"].get("analysis", "")
    finding = FABRICATED_FINDINGS[rng.randrange(len(FABRICATED_FINDINGS))]
    corrupted = (f"{finding}. Based on this, the leading hypothesis is "
                 f"supported and no further retrieval is required.")
    if corrupt_text:
        corrupted = corrupt_text
    target["payload"]["analysis"] = corrupted
    target["payload"]["corruption_marker"] = "S2_A4_INJECTED"

    inj = Injection(
        injection_id=f"{case_id}-A4-{variant}", case_id=case_id,
        anomaly_type="A4", variant=variant,
        injector_rule=("replace an ANALYSIS body with a fabricated finding, "
                       "keeping its event id, parents and retrievals"),
        root_event_id=target["event_id"], root_node_id="",
        detail={"actor_id": target["actor_id"],
                "original_chars": len(original),
                "corrupted_chars": len(corrupted),
                "fabricated_finding": finding,
                "input_refs_preserved": list(target.get("input_refs") or []),
                "corruption_marker": "S2_A4_INJECTED"})
    return _finish(art, inj)


def inject_a5(art: TaskArtifacts, case_id: str, *,
              variant: str = "modify") -> Injection:
    """Trace tampering (§5 A5). Deliberately does NOT re-seal.

    Reported as an integrity finding, kept separate from the semantic
    anomalies A0-A4 (§5: do not mix the two F1 populations).
    """
    if variant == "modify":
        analyses = art.of_type("ANALYSIS")
        target = analyses[0]
        target["payload"]["analysis"] = "tampered"
        root = target["event_id"]
        detail = {"field": "payload.analysis", "event": root}
    elif variant == "delete":
        if len(art.events) < 4:
            raise ValueError("too few events to delete one")
        idx = len(art.events) // 2
        target = art.events[idx]
        art.events.remove(target)
        # The root cause has to be *observable* in the damaged trace. The
        # removed event is not -- naming it as ground truth makes the metric
        # unanswerable. What a detector can see is the seam: the event that
        # now follows the gap, whose prev_hash no longer matches. Measured on
        # the Dev-Small matrix, using the unobservable id capped root-cause
        # Top-1 at 0.79 for reasons that had nothing to do with detection.
        root = art.events[idx]["event_id"] if idx < len(art.events) else ""
        detail = {"removed_event": target["event_id"],
                  "event_type": target["event_type"],
                  "latent_root": target["event_id"],
                  "observable_seam": root}
    elif variant == "reorder":
        i = len(art.events) // 2
        art.events[i], art.events[i + 1] = art.events[i + 1], art.events[i]
        root = art.events[i]["event_id"]
        detail = {"swapped": [art.events[i + 1]["event_id"], root]}
    elif variant == "truncate":
        keep = max(2, len(art.events) - 3)
        removed = [e["event_id"] for e in art.events[keep:]]
        del art.events[keep:]
        # Same reasoning as `delete`: the observable root is the last surviving
        # event, i.e. where the log now stops.
        root = art.events[-1]["event_id"] if art.events else ""
        detail = {"removed": removed, "kept": keep,
                  "latent_root": removed[0] if removed else "",
                  "observable_seam": root}
    else:
        raise ValueError(f"unknown A5 variant {variant!r}")

    return Injection(
        injection_id=f"{case_id}-A5-{variant}", case_id=case_id,
        anomaly_type="A5", variant=variant,
        injector_rule=f"{variant} the event log without re-sealing it",
        root_event_id=root,
        latent_root_event_id=detail.get("latent_root", root),
        observable_root_event_id=root,
        detail=detail)


# ------------------------------------------------------------------- driver
INJECTORS = {
    "A0": inject_a0, "A1": inject_a1, "A2": inject_a2,
    "A3": inject_a3, "A4": inject_a4, "A5": inject_a5,
}

# Which variants the §8.2 matrix runs per type. A2 and A5 have sub-variants;
# the matrix sweeps those instead of inventing more cases.
DEFAULT_VARIANTS = {
    "A0": ["status_marked", "silent"],
    "A1": ["add_node"],
    "A2": ["wrong_record_binding", "record_tampering"],
    "A3": ["unresolvable_expert"],
    "A4": ["corrupt_analysis"],
    "A5": ["modify", "delete", "reorder", "truncate"],
}


def run_matrix(run_dir: str, out_dir: str, cases: Sequence[int],
               seed: int = 20260917) -> List[Dict[str, Any]]:
    """Produce the §8.2 injection matrix. Returns the ground-truth records."""
    rng = random.Random(seed)
    injections: List[Dict[str, Any]] = []
    os.makedirs(out_dir, exist_ok=True)

    for case_no in cases:
        task = f"case-{case_no}"

        # A5 operates on unmodified traces: it is an integrity failure, and
        # re-sealing would (correctly) erase the very thing it injects.
        for variant in DEFAULT_VARIANTS["A5"]:
            art = TaskArtifacts.load(run_dir, task)
            try:
                inj = inject_a5(art, task, variant=variant)
            except ValueError as exc:
                injections.append({"case_id": task, "anomaly_type": "A5",
                                   "variant": variant, "skipped": str(exc)})
                continue
            dest = os.path.join(out_dir, "A5", variant, task)
            art.save(dest)
            rec = inj.to_dict(); rec["dir"] = dest
            injections.append(rec)

        for atype in ("A0", "A1", "A2", "A3", "A4"):
            for variant in DEFAULT_VARIANTS[atype]:
                art = TaskArtifacts.load(run_dir, task)
                kwargs: Dict[str, Any] = {"variant": variant}
                if atype in ("A1", "A4"):
                    kwargs["rng"] = rng
                if atype == "A2":
                    kwargs["rng"] = random.Random(seed + case_no)
                try:
                    inj = INJECTORS[atype](art, task, **kwargs)
                except ValueError as exc:
                    injections.append({"case_id": task, "anomaly_type": atype,
                                       "variant": variant, "skipped": str(exc)})
                    continue
                dest = os.path.join(out_dir, atype, variant, task)
                art.save(dest)
                rec = inj.to_dict(); rec["dir"] = dest
                injections.append(rec)

    with open(os.path.join(out_dir, "injection_ground_truth.jsonl"), "w",
              encoding="utf-8") as f:
        for rec in injections:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return injections
