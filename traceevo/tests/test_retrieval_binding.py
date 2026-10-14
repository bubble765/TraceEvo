"""T3 (unit part): retrieval records get stable ids and Evidence nodes bind to
them deterministically (amendment §3.3, §6 T3)."""
import os

from traceevo.audit.cross_graph_binding import (
    BindingRegistry, classify_evidence, norm, token_coverage)

REPO_TEXT = ("- Imaging Studies: 1. Computed tomographic scan showed glottic and "
             "supraglottic swelling with no evidence of fractures. 2. Modified "
             "barium swallow showed an esophageal stricture.")

CFG = {"verbatim": True, "paraphrase": True,
       "paraphrase_min_token_coverage": 0.6, "min_verbatim_chars": 25,
       "min_content_tokens": 4}


def _registry_with_one_record():
    reg = BindingRegistry("case-0")
    reg.add_record(query="CT findings",
                   text=REPO_TEXT,
                   retrieval_event_id="evt-case-0-00006",
                   step_id=1, expert="Radiologist")
    return reg


def test_record_ids_are_stable_and_sequential():
    reg = BindingRegistry("case-7")
    r1 = reg.add_record("q1", "t1", "e1", 1, "Radiologist")
    r2 = reg.add_record("q2", "t2", "e2", 2, "Pathologist")
    assert r1.record_id == "rec-case-7-0001"
    assert r2.record_id == "rec-case-7-0002"
    assert r1.retrieval_event_id == "e1"


def test_explicit_record_id_is_honoured():
    reg = BindingRegistry("case-7")
    rid = reg.new_record_id()
    rec = reg.add_record("q", "t", "e", 1, "Radiologist", record_id=rid)
    assert rec.record_id == rid


def test_verbatim_quote_binds_to_the_record():
    reg = _registry_with_one_record()
    label = "Computed tomographic scan showed glottic and supraglottic swelling"
    src, tier, rec, cov = classify_evidence(label, reg.records, CFG)
    assert src == "RETRIEVED_RECORD"
    assert tier == "verbatim"
    assert rec.retrieval_event_id == "evt-case-0-00006"


def test_paraphrase_binds_via_token_coverage():
    reg = _registry_with_one_record()
    label = ("CT scan showed glottic and supraglottic swelling, and a modified "
             "barium swallow showed esophageal stricture")
    src, tier, rec, cov = classify_evidence(label, reg.records, CFG)
    assert src == "RETRIEVED_RECORD"
    assert tier == "paraphrase"
    assert cov >= 0.6


def test_unrelated_assertion_is_agent_generated():
    reg = _registry_with_one_record()
    label = "PAX8 positivity is classically associated with renal cell carcinoma"
    src, tier, rec, cov = classify_evidence(label, reg.records, CFG)
    assert src == "AGENT_GENERATED"
    assert tier == "none"


def test_absence_claims_are_not_treated_as_retrieved_records():
    reg = _registry_with_one_record()
    label = "Immunohistochemistry and TCR gene rearrangement results are absent"
    src, _, _, _ = classify_evidence(label, reg.records, CFG)
    assert src == "AGENT_GENERATED"


def test_no_records_means_everything_is_agent_generated():
    label = "Computed tomographic scan showed glottic and supraglottic swelling"
    src, tier, rec, cov = classify_evidence(label, [], CFG)
    assert src == "AGENT_GENERATED"
    assert tier == "none"
    assert cov == 0.0


def test_classification_is_deterministic():
    reg = _registry_with_one_record()
    label = "Direct laryngoscopy showed diffuse edema and sloughing"
    runs = set()
    for _ in range(5):
        src, tier, rec, cov = classify_evidence(label, reg.records, CFG)
        runs.add((src, tier, rec.record_id if rec else "", cov))
    assert len(runs) == 1, "classification must not depend on LLM or randomness"


def test_short_labels_do_not_bind_on_substring_luck():
    """A 3-word label can appear inside a long record by accident; the
    min_verbatim_chars and min_content_tokens guards must prevent binding."""
    reg = _registry_with_one_record()
    src, tier, _, _ = classify_evidence("scan showed", reg.records, CFG)
    assert src == "AGENT_GENERATED"


def test_bind_records_phase_and_source():
    reg = _registry_with_one_record()
    b1 = reg.bind("node-1", "Signal", "cough", "evt-1", 0, "ingest", CFG)
    assert b1.source_class == "SYSTEM_GENERATED"
    b2 = reg.bind("node-2", "Evidence", "fever and chills", "evt-1", 0,
                  "ingest", CFG)
    assert b2.source_class == "INPUT_OBSERVATION"
    b3 = reg.bind("node-3", "Evidence",
                  "Computed tomographic scan showed glottic swelling",
                  "evt-2", 1, "update_graph", CFG)
    assert b3.source_class == "RETRIEVED_RECORD"
    b4 = reg.bind("node-4", "Hypothesis", "MI", "evt-3", 1, "update_graph", CFG)
    assert b4.source_class == "AGENT_GENERATED"

    summary = reg.grounding_summary()
    # node-1 is a Signal; only node-2 and node-3 are Evidence nodes.
    assert summary["evidence_total"] == 2
    assert summary["evidence_source_bound"] == 2
    assert summary["evidence_source_bound_rate"] == 1.0
    assert summary["by_source_class"]["RETRIEVED_RECORD"] == 1
    assert summary["by_source_class"]["INPUT_OBSERVATION"] == 1


# --------------------------------------------------------------- S2 §3.3
def test_record_hash_verify_and_tamper_detection():
    """A2 `record_tampering`: the hash is over the record's content, so editing
    the stored text while keeping the record_id is detectable."""
    reg = BindingRegistry("case-hash")
    rec = reg.add_record(query="ct", text="CT showed glottic swelling",
                         retrieval_event_id="evt-1", step_id=1,
                         expert="Radiologist")
    assert rec.verify()

    rec.record_id = "rec-case-hash-0001"      # renaming alone is not a mutation
    assert rec.verify(), "the hash covers content, not the identifier"

    rec.text = "CT was entirely normal"        # the tamper A2 models
    assert not rec.verify()
    assert rec.recompute_hash() != rec.record_hash


def test_combined_retrieval_event_keeps_the_chain_intact(tmp_path):
    """One TOOL_CALL carries the raw retrieval result and its content hash;
    bindings.json retains only the record index, not a second copy of text."""
    import os
    import json

    from traceevo import hooks
    from traceevo.audit.integrity import verify_chain
    from traceevo.audit.provenance_graph import build_provenance_graph

    os.environ.pop("TRACEEVO_DISABLED", None)
    from traceevo import config as tx_config
    tx_config.load(force_reload=True)
    ctx = hooks.start_task("case-chain", str(tmp_path / "case-chain"))
    assert ctx is not None

    hooks.on_step(1)
    hooks.on_plan("Primary_Physician", "[]")
    hooks.on_agent_call("Primary_Physician", "Radiologist")
    hooks.on_tool_call("ct findings")
    retrieved_text = "CT showed glottic swelling"
    record_id = hooks.on_evidence_read("ct findings", retrieved_text)

    report = verify_chain(ctx.events)
    assert report.ok, report.issues

    retrieval_events = [e for e in ctx.events
                        if e["event_type"] in ("TOOL_CALL", "EVIDENCE_READ")]
    assert len(retrieval_events) == 1
    ev = retrieval_events[0]
    assert ev["event_type"] == "TOOL_CALL"
    assert ev["output_refs"] == [record_id]
    assert ev["payload"]["query"] == "ct findings"
    assert ev["payload"]["retrieval_output"] == retrieved_text
    rec = ctx.bindings.records[0]
    assert ev["payload"]["record_hash"] == rec.record_hash
    assert rec.verify()

    # The TOOL_CALL event node is the durable home of retrieved text. The
    # cross-graph bindings artifact keeps identity/hash metadata only.
    bindings = ctx.bindings.to_dict()
    assert "text" not in bindings["records"][0]
    pgraph = build_provenance_graph(ctx.task_id, ctx.events, bindings)
    event_node = pgraph.nodes[ev["event_id"]]
    assert event_node.data["event_type"] == "TOOL_CALL"
    assert event_node.data["payload"]["retrieval_output"] == retrieved_text

    hooks.finish_task(render_png=False)
    saved_bindings = json.loads(
        (tmp_path / "case-chain" / "case-chain_bindings.json").read_text()
    )
    assert "text" not in saved_bindings["records"][0]
    saved_graph = json.loads(
        (tmp_path / "case-chain" / "case-chain_provenance_graph.json").read_text()
    )
    saved_event = next(n for n in saved_graph["nodes"]
                       if n["event_id"] == ev["event_id"])
    assert saved_event["payload"]["retrieval_output"] == retrieved_text
    assert "type" not in saved_event
    assert all(set(edge) == {"src", "dst"} for edge in saved_graph["edges"])


def test_provenance_graph_uses_dependency_edges_and_causal_refs_only():
    from traceevo.audit.provenance_graph import build_provenance_graph

    events = [
        {"event_id": "e1", "event_type": "PLAN", "task_id": "task",
         "parent_event_ids": [], "causal_node_refs": ["h1"]},
        {"event_id": "e2", "event_type": "GRAPH_UPDATE", "task_id": "task",
         "parent_event_ids": ["e1"], "causal_node_refs": ["ev1", "h1"]},
    ]
    state = build_provenance_graph("task", events).to_dict()

    assert state["nodes"][1]["causal_node_refs"] == ["ev1", "h1"]
    assert "parent_event_ids" not in state["nodes"][1]
    assert state["edges"] == [{"src": "e1", "dst": "e2"}]
    assert "node_type_counts" not in state
    assert "edge_type_counts" not in state


def test_medical_knowledge_result_is_on_the_combined_tool_event(tmp_path):
    """The single retrieval event preserves both its rendered output and PMC
    provenance without creating a duplicate EVIDENCE_READ event."""
    import os

    from traceevo import hooks
    from traceevo.audit.integrity import verify_chain

    os.environ.pop("TRACEEVO_DISABLED", None)
    from traceevo import config as tx_config
    tx_config.load(force_reload=True)
    ctx = hooks.start_task("case-medical-rag", str(tmp_path / "case-medical-rag"))
    assert ctx is not None

    hooks.on_step(1)
    hooks.on_plan("Primary_Physician", "[]")
    hooks.on_agent_call("Primary_Physician", "Laboratory_Physician")
    hooks.on_tool_call(
        "urine AFB testing", "Medical_Knowledge", source_store="medical_knowledge"
    )
    record_id = hooks.on_evidence_read(
        "urine AFB testing",
        "Urine testing can be used in the work-up of genitourinary tuberculosis.",
        source_store="medical_knowledge",
        source_refs=[{
            "document_id": "PMC-12846603",
            "title": "Tuberculosis: Clinical Laboratory Diagnostic Techniques",
            "source_uri": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12846603/",
            "license_id": "CC BY",
        }],
        tool_name="Medical_Knowledge",
        ledger_stage=None,
    )

    assert verify_chain(ctx.events).ok
    retrieval_events = [e for e in ctx.events
                        if e["event_type"] in ("TOOL_CALL", "EVIDENCE_READ")]
    assert len(retrieval_events) == 1
    tool = retrieval_events[0]
    assert tool["event_type"] == "TOOL_CALL"
    assert tool["tool_or_model"] == "Medical_Knowledge"
    assert tool["output_refs"] == [record_id]
    assert tool["payload"]["source_store"] == "medical_knowledge"
    assert tool["payload"]["retrieval_output"].startswith("Urine testing can be used")
    assert tool["payload"]["source_refs"][0]["document_id"] == "PMC-12846603"
    assert ctx.bindings.records[0].verify()
