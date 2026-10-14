"""S2 §3.2: the execution dependency DAG.

Each test breaks exactly one rule in an otherwise valid trace, so a passing
suite means every rule is actually enforced rather than merely satisfied by a
trace that happens to be well-formed.
"""
import pytest

from traceevo.audit.dependency_graph import (
    ExecutionDAG, PARENT_RULES, check, check_parent_rules, check_step_locality,
    check_structure, is_applied, load_events)


def ev(eid, etype, parents=(), step=0, actor="Primary_Physician",
       payload=None, **kw):
    return {"event_id": eid, "task_id": "t", "step_id": step, "timestamp": "now",
            "event_type": etype, "actor_id": actor, "actor_role": "central",
            "parent_event_ids": list(parents), "payload": payload or {},
            "tool_or_model": kw.pop("tool_or_model", ""), **kw}


def valid_trace():
    """One ingest, one full step, one backtrack, one more plan, then a report."""
    return [
        ev("g1", "GRAPH_UPDATE", payload={"phase": "ingest:basic_node"}),
        ev("g2", "GRAPH_UPDATE", ["g1"], payload={"phase": "ingest:l1_hypo"}),
        ev("p1", "PLAN", ["g2"], step=1),
        ev("a1", "AGENT_CALL", ["p1"], step=1, tool_or_model="Cardiologist"),
        ev("t1", "TOOL_CALL", ["a1"], step=1, actor="Cardiologist"),
        ev("e1", "EVIDENCE_READ", ["t1"], step=1, actor="Cardiologist"),
        ev("n1", "ANALYSIS", ["a1", "e1"], step=1, actor="Cardiologist"),
        ev("g3", "GRAPH_UPDATE", ["n1"], step=1,
           payload={"phase": "update_graph"}),
        ev("b1", "BACKTRACK", ["g3"], step=1, payload={"applied": True,
                                                       "deleted_levels": [2]}),
        ev("p2", "PLAN", ["b1"], step=2),
        ev("a2", "AGENT_CALL", ["p2"], step=2, tool_or_model="Radiologist"),
        ev("n2", "ANALYSIS", ["a2"], step=2, actor="Radiologist"),
        ev("g4", "GRAPH_UPDATE", ["n2"], step=2,
           payload={"phase": "update_graph"}),
        ev("f1", "FINAL_REPORT", ["g4"]),
    ]


def issues(events, fn):
    return fn(ExecutionDAG(events))


def kinds(events, fn):
    return {i.kind for i in issues(events, fn)}


# --------------------------------------------------------------- happy path
def test_valid_trace_passes_every_check():
    dag, rep = check(valid_trace())
    assert rep.ok, rep.issues
    assert rep.n_events == 14
    assert [i for i in rep.issues if i.severity == "error"] == []
    assert rep.topo_order[0] == "g1"
    assert rep.topo_order[-1] == "f1"


def test_rule_table_covers_every_event_type():
    from traceevo.audit.event_schema import EVENT_TYPES
    assert set(PARENT_RULES) == set(EVENT_TYPES)


# --------------------------------------------------------------- per-rule
def test_dangling_parent_is_reported():
    trace = valid_trace()
    trace[6]["parent_event_ids"] = ["a1", "does-not-exist"]
    assert "dangling_parent" in kinds(trace, check_parent_rules)


def test_illegal_parent_type_is_reported():
    trace = valid_trace()
    trace[2]["parent_event_ids"] = ["t1"]        # PLAN cannot depend on a TOOL_CALL
    assert "illegal_parent_type" in kinds(trace, check_parent_rules)


def test_missing_required_parent_is_reported():
    trace = valid_trace()
    trace[3]["parent_event_ids"] = []            # AGENT_CALL lost its PLAN
    assert "missing_required_parent" in kinds(trace, check_parent_rules)


def test_analysis_without_its_agent_call_is_reported():
    trace = valid_trace()
    trace[6]["parent_event_ids"] = ["e1"]        # reads only, no dispatch
    assert "missing_required_parent" in kinds(trace, check_parent_rules)


def test_graph_update_must_depend_on_this_steps_analyses():
    trace = valid_trace()
    trace[7]["parent_event_ids"] = []            # silently dropped its analysis
    assert "step_parent_mismatch" in kinds(trace, check_step_locality)


def test_graph_update_must_not_borrow_another_steps_analysis():
    trace = valid_trace()
    trace[12]["parent_event_ids"] = ["n1"]       # step 2 borrowing step 1
    assert "step_parent_mismatch" in kinds(trace, check_step_locality)


def test_stale_plan_parent_is_reported():
    trace = valid_trace()
    trace[9]["parent_event_ids"] = ["g3"]        # ignores the backtrack b1
    assert "stale_parent" in kinds(trace, check_step_locality)


def test_tool_call_must_point_at_its_own_dispatch():
    trace = valid_trace()
    trace[5]["parent_event_ids"] = ["a1"]        # EVIDENCE_READ -> AGENT_CALL
    assert "stale_parent" in kinds(trace, check_step_locality)


def test_parent_logged_after_child_is_reported():
    trace = valid_trace()
    trace.insert(3, ev("p1b", "PLAN", ["g2"], step=1))
    trace[2]["parent_event_ids"] = ["p1b"]       # p1b comes later in the log
    assert "parent_not_before_child" in kinds(trace, check_parent_rules)


# --------------------------------------------------------------- structure
def test_cycle_is_reported():
    trace = valid_trace()
    trace[0]["parent_event_ids"] = ["g2"]        # g1 <-> g2
    dag, rep = check(trace)
    assert not rep.ok
    assert "cycle" in rep.by_kind()


def test_replayable_multistep_dag_is_acyclic():
    """A long chain must topologically sort, and both traversals must agree."""
    trace = valid_trace()
    dag = ExecutionDAG(trace)
    order = dag.topo_order()
    assert order is not None
    pos = {eid: i for i, eid in enumerate(order)}
    for child, parents in dag.parents.items():
        for p in parents:
            assert pos[p] < pos[child], f"{p} must precede {child}"

    # ancestors(f1) is exactly the reasoning that produced it.
    anc = set(dag.ancestors("f1"))
    assert {"g4", "p2", "b1", "g3", "n1", "a1", "p1", "g2", "g1"} <= anc
    # descendants(g1) reaches the report.
    assert "f1" in dag.descendants("g1")
    # shortest chain from the report back to the case input.
    chain = dag.shortest_chain("f1", "g1")
    assert chain == ["f1", "g4", "n2", "a2", "p2", "b1", "g3", "n1", "a1",
                     "p1", "g2", "g1"]
    assert dag.shortest_chain("g1", "f1") is None


def test_final_report_unreachable_from_the_case_input_is_reported():
    trace = valid_trace()
    trace = [e for e in trace if e["event_type"] != "FINAL_REPORT"]
    trace.append(ev("orphan", "ANALYSIS", [], actor="Radiologist"))
    trace.append(ev("f2", "FINAL_REPORT", ["orphan"]))
    assert "unreachable_final_report" in kinds(trace, check_structure)


def test_missing_and_duplicate_final_report_are_reported():
    without = [e for e in valid_trace() if e["event_type"] != "FINAL_REPORT"]
    assert "no_final_report" in kinds(without, check_structure)

    doubled = valid_trace() + [ev("f2", "FINAL_REPORT", ["g4"])]
    assert "multiple_final_reports" in kinds(doubled, check_structure)


# ------------------------------------------------------- no-op state changes
def test_noop_backtrack_is_informational_not_an_error():
    """Case 2 fires a backtrack 1 -> 1 that deletes nothing. That is real and
    worth surfacing, but nothing downstream depends on it."""
    trace = valid_trace()
    trace[8]["payload"] = {"applied": False, "deleted_levels": []}
    trace[9]["parent_event_ids"] = ["g3"]         # PLAN correctly skips it
    dag, rep = check(trace)
    assert rep.ok, rep.issues
    infos = [i for i in rep.issues if i.severity == "info"]
    assert [i.kind for i in infos] == ["noop_state_change"]
    assert infos[0].event_id == "b1"


def test_noop_state_change_must_not_be_a_parent():
    trace = valid_trace()
    trace[8]["payload"] = {"applied": False, "deleted_levels": []}
    trace[9]["parent_event_ids"] = ["b1"]         # PLAN depends on a non-event
    assert "stale_parent" in kinds(trace, check_step_locality)


def test_applied_defaults_to_true_for_events_without_the_flag():
    assert is_applied(ev("g", "GRAPH_UPDATE"))
    assert is_applied(ev("b", "BACKTRACK", payload={"applied": True}))
    assert not is_applied(ev("b", "BACKTRACK", payload={"applied": False}))


# ------------------------------------------------- end-to-end against hooks
def test_hooks_emit_a_trace_that_satisfies_the_rules(tmp_path):
    """The checker is only useful if what the hooks *actually emit* passes it."""
    import os

    from traceevo import hooks
    from belief.graph import BeliefGraph

    os.environ.pop("TRACEEVO_DISABLED", None)
    from traceevo import config as tx_config
    tx_config.load(force_reload=True)
    ctx = hooks.start_task("case-dag", str(tmp_path / "case-dag"))
    assert ctx is not None

    g = BeliefGraph()
    hooks.on_step(0)
    sig = g.add_node(node_type="Signal", label="chest pain")
    hooks.on_ingest(g, "Primary_Physician", "{}", "basic_node")
    g.add_node(node_type="Hypothesis", label="ACS", score=0.6,
               attrs={"why": "w", "level": 1})
    hooks.on_ingest(g, "Primary_Physician", "{}", "l1_hypo")

    hooks.on_step(1)
    hooks.on_focus({"node_id": sig, "label": "chest pain", "level": 0})
    hooks.on_plan("Primary_Physician", "[]")
    hooks.on_agent_call("Primary_Physician", "Cardiologist")
    hooks.on_tool_call("troponin")
    hooks.on_evidence_read("troponin", "Troponin elevated at 4.2 ng/mL")
    hooks.on_analysis("Elevated troponin supports ACS.", "success", "NONE")
    g.add_node(node_type="Evidence", label="Troponin elevated at 4.2 ng/mL")
    hooks.on_graph_update(g, "Primary_Physician", "{}")
    hooks.on_seal_graph(g, "post_backtrack")
    hooks.on_final_report("Primary_Physician", "ACS", "report", g)

    dag, rep = check(ctx.events, task_id="case-dag")
    assert rep.ok, [i.to_dict() for i in rep.issues]
    assert rep.n_events == len(ctx.events)
    # The report really does reach the case input through the whole chain.
    assert "g1" not in dag.ancestors(ctx.events[-1]["event_id"])
    assert set(dag.ancestors(ctx.events[-1]["event_id"])) & set(dag.of_type("GRAPH_UPDATE"))
