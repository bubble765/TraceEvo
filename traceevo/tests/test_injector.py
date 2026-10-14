"""S2 §5/§8.2: controlled anomaly injection.

The property that makes the injection matrix usable is that A0-A4 leave the
trace *internally consistent*: the hash chain verifies and the delta chain
replays. Without that the auditor would report A5 for every injected trace and
the semantic metrics would measure nothing. A5 is the deliberate exception.
"""
import json
import os
import random

import pytest

from traceevo.audit.cross_graph_binding import record_hash
from traceevo.audit.dependency_graph import ExecutionDAG
from traceevo.audit.injector import (
    FABRICATED_FINDINGS, UPSTREAM_FALLBACK, TaskArtifacts, inject_a0,
    inject_a1, inject_a2, inject_a3, inject_a4, inject_a5)
from traceevo.audit.integrity import verify_chain

RUN_DIR = os.path.join("traceevo", "tests", "fixtures", "audit_clean")
needs_run = pytest.mark.skipif(
    not os.path.isdir(RUN_DIR),
    reason="run the T1 driver to produce Dev-Small artifacts")


def art(case="case-8"):
    return TaskArtifacts.load(RUN_DIR, case)


@needs_run
def test_resealing_keeps_the_chain_consistent_after_every_injection():
    """A0-A4 must be invisible to the integrity check, or the semantic
    detectors are measuring tampering instead of semantics."""
    for fn, kwargs, in ((inject_a0, {"variant": "status_marked"}),
                        (inject_a0, {"variant": "silent"}),
                        (inject_a1, {"rng": random.Random(1)}),
                        (inject_a2, {"variant": "record_tampering"}),
                        (inject_a3, {}),
                        (inject_a4, {"rng": random.Random(1)})):
        a = art()
        fn(a, a.task_id, **kwargs)
        report = verify_chain(a.events)
        assert report.ok, (fn.__name__, kwargs, report.issues[:2])


@needs_run
def test_a1_injects_a_fabricated_finding_wired_into_reasoning():
    a = art()
    inj = inject_a1(a, a.task_id, rng=random.Random(7))
    node = a.node(inj.root_node_id)
    assert node is not None, "the fabricated node must exist"
    assert node["label"] in FABRICATED_FINDINGS
    assert node["source_class"] == "AGENT_GENERATED"

    state = a.final_state()
    edges = {(e["src"], e["dst"]): e["type"] for e in state["edges"]}
    assert (inj.root_node_id, inj.detail["attached_to_hypothesis"]) in edges
    assert edges[(inj.root_node_id, inj.detail["attached_to_hypothesis"])] == "support"

    # It was introduced by a real update-graph event, not by a synthetic one.
    creator = [e for e in a.events if e["event_id"] == inj.root_event_id][0]
    assert creator["event_type"] == "GRAPH_UPDATE"
    assert inj.root_node_id in creator["causal_node_refs"]


@needs_run
def test_a1_is_tiered_p3_by_the_evidence_auditor(tmp_path):
    """The whole point of A1: a trace whose hashes all verify but whose
    semantics contain an unsourced factual assertion."""
    import shutil

    from traceevo.audit.evidence_auditor import P3, audit_task

    a = art()
    inj = inject_a1(a, a.task_id, rng=random.Random(3))
    a.save(str(tmp_path / "run"))
    for extra in ("_events", "_deltas"):
        pass
    decisions = audit_task(str(tmp_path / "run"), a.task_id)
    hit = [d for d in decisions if d.node_id == inj.root_node_id]
    assert hit, "the injected node must be audited"
    assert hit[0].provenance_tier == P3
    assert hit[0].used_in_reasoning


@needs_run
def test_a1_ground_truth_downstream_is_derived_from_the_dag():
    a = art()
    inj = inject_a1(a, a.task_id, rng=random.Random(11))
    dag = ExecutionDAG(a.events)
    assert set(inj.affected_events) == set(dag.descendants(inj.root_event_id))
    assert inj.root_node_id in inj.affected_nodes
    assert inj.path_to_conclusion[-1] == inj.root_event_id, \
        "the chain is [FINAL_REPORT, ..., root]"


@needs_run
def test_a0_status_marked_and_silent_differ_only_in_the_status_field():
    marked = art()
    inj_m = inject_a0(marked, marked.task_id, variant="status_marked")
    ev_m = [e for e in marked.events if e["event_id"] == inj_m.root_event_id][0]

    silent = art()
    inj_s = inject_a0(silent, silent.task_id, variant="silent")
    ev_s = [e for e in silent.events if e["event_id"] == inj_s.root_event_id][0]

    assert ev_m["payload"]["analysis"] == ev_s["payload"]["analysis"] == UPSTREAM_FALLBACK
    assert (ev_m["status"], ev_m["reason_code"]) == ("degraded", "BUDGET_EXHAUSTED")
    assert (ev_s["status"], ev_s["reason_code"]) == ("success", "NONE")


@needs_run
def test_a2_record_tampering_breaks_the_record_hash():
    a = art()
    inj = inject_a2(a, a.task_id, variant="record_tampering")
    rec = a.record(inj.detail["record_id"])
    assert rec["record_hash"] == inj.detail["hash_recorded"]
    assert rec["record_hash"] != record_hash(a.task_id, rec["query"], rec["text"],
                                             rec["expert"])
    # The event that read it still carries the old hash, which is the
    # cross-check an auditor uses.
    assert inj.detail["hash_recorded"] != inj.detail["hash_recomputed"]


@needs_run
def test_a2_wrong_binding_points_at_a_record_that_lacks_the_text():
    a = art()
    inj = inject_a2(a, a.task_id, variant="wrong_record_binding",
                    rng=random.Random(0))
    node = a.node(inj.root_node_id)
    assert node["matched_record_id"] == inj.detail["record_id_after"]
    assert node["matched_record_id"] != inj.detail["record_id_before"]
    wrong = a.record(node["matched_record_id"])
    assert node["label"][:40].lower() not in wrong["text"].lower()


@needs_run
def test_a3_records_a_failed_dispatch_under_the_plan():
    a = art()
    inj = inject_a3(a, a.task_id)
    ev = [e for e in a.events if e["event_id"] == inj.root_event_id][0]
    assert ev["event_type"] == "AGENT_CALL"
    assert (ev["status"], ev["reason_code"]) == ("failed", "INVALID_EXPERT_DISPATCH")
    assert ev["payload"]["expert"] == "Laboratory Physician"
    plan = [e for e in a.events if e["event_id"] == ev["parent_event_ids"][0]][0]
    assert plan["event_type"] == "PLAN"
    assert a.events.index(ev) == a.events.index(plan) + 1
    # The dispatch did not fabricate a successful call to the real expert.
    successes = [e for e in a.events if e["event_type"] == "AGENT_CALL"
                 and e["status"] == "success"]
    assert all(e["tool_or_model"] != "Laboratory Physician" for e in successes)


@needs_run
def test_a4_keeps_the_event_identity_and_its_retrievals():
    a = art()
    before = {e["event_id"]: (e["parent_event_ids"], e["input_refs"],
                              e["actor_id"], e["status"])
              for e in a.events}
    inj = inject_a4(a, a.task_id, rng=random.Random(2))
    ev = [e for e in a.events if e["event_id"] == inj.root_event_id][0]
    assert ev["event_type"] == "ANALYSIS"
    assert ev["payload"]["corruption_marker"] == "S2_A4_INJECTED"
    assert before[inj.root_event_id] == (ev["parent_event_ids"], ev["input_refs"],
                                         ev["actor_id"], ev["status"])
    assert inj.detail["fabricated_finding"] in ev["payload"]["analysis"]


@needs_run
@pytest.mark.parametrize("variant", ["modify", "delete", "reorder"])
def test_a5_breaks_the_chain_and_is_not_resealed(variant):
    a = art()
    inject_a5(a, a.task_id, variant=variant)
    assert not verify_chain(a.events).ok, \
        "A5 must remain detectable as an integrity failure"
    assert a.states == [], "A5 must not re-seal the delta chain"


@needs_run
def test_a5_truncation_is_invisible_to_the_hash_chain():
    """A suffix truncation leaves a chain in which every remaining event still
    points at its predecessor, so `verify_chain` returns ok.

    This is a real limitation of chain-only integrity and the reason the S2
    integrity auditor adds completeness checks on top: a log that no longer
    ends in a FINAL_REPORT has been cut, whatever the hashes say.
    """
    a = art()
    assert a.events[-1]["event_type"] == "FINAL_REPORT"
    inject_a5(a, a.task_id, variant="truncate")
    assert verify_chain(a.events).ok, "documented limitation, not a bug"
    assert not any(e["event_type"] == "FINAL_REPORT" for e in a.events)


@needs_run
def test_a5_replay_report_says_it_is_not_applicable(tmp_path):
    a = art()
    inject_a5(a, a.task_id, variant="truncate")
    a.save(str(tmp_path / "run"))
    rep = json.load(open(tmp_path / "run" / f"{a.task_id}_replay.json"))
    assert rep["applicable"] is False and "all_ok" not in rep


@needs_run
def test_injected_traces_still_replay(tmp_path):
    """The delta chain of an A0-A4 injection must still satisfy
    Replay(G_t, delta_t) == G_(t+1) -- §9's DAG/replay consistency gate."""
    a = art()
    inject_a1(a, a.task_id, rng=random.Random(5))
    a.save(str(tmp_path / "run"))
    rep = json.load(open(tmp_path / "run" / f"{a.task_id}_replay.json"))
    assert rep["applicable"] is True and rep["all_ok"] is True, rep["checks"]


# ---------------------------------------------- aborted-task audit (S2.1 §4)
def _aborted_events():
    """The shape GoS leaves when it dies before `finish_task`."""
    from traceevo.audit.event_schema import GENESIS_HASH, canonical_json, sha256_hex
    base = [
        {"event_id": "e1", "task_id": "case-1", "step_id": 0, "timestamp": "t",
         "event_type": "GRAPH_UPDATE", "actor_id": "Primary_Physician",
         "actor_role": "central", "tool_or_model": "", "input_refs": [],
         "output_refs": [], "causal_node_refs": [], "parent_event_ids": [],
         "source_class": "INPUT_OBSERVATION", "model_id": "m",
         "model_version": None, "version_observable": False, "status": "success",
         "reason_code": "NONE", "payload": {"phase": "ingest:basic_node"}},
        {"event_id": "e3", "task_id": "case-1", "step_id": 1, "timestamp": "t",
         "event_type": "PLAN", "actor_id": "Primary_Physician",
         "actor_role": "central", "tool_or_model": "", "input_refs": [],
         "output_refs": [], "causal_node_refs": [], "parent_event_ids": ["e1"],
         "source_class": "AGENT_GENERATED", "model_id": "m",
         "model_version": None, "version_observable": False, "status": "success",
         "reason_code": "NONE", "payload": {}},
        {"event_id": "e4", "task_id": "case-1", "step_id": 1, "timestamp": "t",
         "event_type": "AGENT_CALL", "actor_id": "Primary_Physician",
         "actor_role": "central", "tool_or_model": "Laboratory Physician",
         "input_refs": [], "output_refs": [], "causal_node_refs": [],
         "parent_event_ids": ["e3"], "source_class": "AGENT_GENERATED",
         "model_id": "m", "model_version": None, "version_observable": False,
         "status": "failed", "reason_code": "INVALID_EXPERT_DISPATCH",
         "payload": {"expert": "Laboratory Physician",
                     "available": ["Laboratory_Physician", "Pathologist"],
                     "error": "Not founded"}},
    ]
    prev = GENESIS_HASH
    for e in base:
        e["prev_hash"] = prev
        e["event_hash"] = sha256_hex(canonical_json(e))
        prev = e["event_hash"]
    return base


def test_roster_is_derivable_from_a_failed_dispatch_alone():
    """S2.1 §4 requires the auditor to name the near-miss from the trace. An
    aborted task has no successful dispatch to learn the roster from, so the
    `available` list on the failure is the only source."""
    from traceevo.audit.policy import CapabilityPolicy
    policy = CapabilityPolicy.from_events(_aborted_events())
    assert policy.roster == ["Laboratory_Physician", "Pathologist"]
    check = policy.resolve("Laboratory Physician")
    assert check.near_miss == "Laboratory_Physician"
    assert check.detail["cause"] == "name_form_mismatch"


def test_aborted_task_is_auditable_from_events_alone(tmp_path):
    """Only the event log survives a crash; the audit must not need the
    bindings and deltas that `finish_task` never wrote."""
    from traceevo.audit.anomaly_detector import (detect_aborted_task,
                                                 detect_task)
    (tmp_path / "case-1_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in _aborted_events()))

    findings = detect_aborted_task(str(tmp_path), "case-1")
    rules = {f["detector_rule"] for f in findings}
    assert "missing_final_report" in rules, "the crash signature"
    # With the roster recovered from `available`, the rule is the sharper one:
    # a name *form* mismatch, which is the actionable diagnosis. Before the fix
    # it degraded to `invalid_expert_dispatch` with no near-miss.
    assert "name_form_mismatch" in rules
    a3 = [f for f in findings if f["type"] == "A3"][0]
    assert a3["root_event_id"] == "e4"
    assert a3["evidence"][0]["near_miss"] == "Laboratory_Physician"

    # One finding per fact: the DAG check reports the same absent report.
    assert sum(1 for f in findings if "final_report" in f["detector_rule"]) == 1

    # The full detector cannot run here, and says so by failing rather than by
    # inventing results.
    with pytest.raises(Exception):
        detect_task(str(tmp_path), "case-1")


def test_semantic_detectors_are_not_run_without_bindings(tmp_path):
    """A crash leaves no bindings; A1/A2/A4 read them, so they are skipped
    explicitly instead of silently returning nothing."""
    import traceevo.s2_cases as sc
    (tmp_path / "case-1_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in _aborted_events()))
    rep = sc.audit_and_write(str(tmp_path), "case-1", str(tmp_path),
                             aborted=True)
    assert rep["aborted"] is True
    assert set(rep["detectors_skipped"]) == {"A1", "A2", "A4"}
    assert any(a["detector_rule"] == "missing_final_report"
               for a in rep["anomalies"])
