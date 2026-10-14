"""S2 §4: the P0-P3 evidence audit boundary.

Two things have to hold at once, and most of these tests pin one of them:

  * a node with no retrieval source must not be called fabricated when it is
    an absence claim, a recommendation, domain knowledge, or an inference from
    the case -- otherwise the clean-trace FPR explodes;
  * a node that asserts a clinical finding nothing supports must be called
    fabricated, or the auditor cannot detect A1 at all.
"""
import json
import os

import pytest

from traceevo.audit.evidence_auditor import (
    CSV_COLUMNS, DEFAULT_CFG, LBL_ANOMALY, LBL_OK, LBL_REVIEW, LBL_UNUSED,
    P0, P1, P2, P3, audit_task, best_record, char_similarity, classify_tier,
    longest_common_span_ratio, summarize, write_csv)


def node(label, node_type="Evidence", source_class="AGENT_GENERATED", **kw):
    return dict(node_id=kw.pop("node_id", "n1"), label=label,
                node_type=node_type, source_class=source_class, **kw)


def decide(label, records=(), analyses=(), input_pool="", used=True,
           node_type="Evidence", source_class="AGENT_GENERATED", **kw):
    return classify_tier(node(label, node_type, source_class, **kw),
                         list(records), list(analyses), input_pool, used)


REC = [{"record_id": "rec-1", "text":
        "Computed tomographic scan showed glottic and supraglottic swelling "
        "with no evidence of fractures. Direct laryngoscopy showed diffuse "
        "edema and sloughing of pharyngeal and supraglottic mucosa."}]


# ------------------------------------------------------------------- P0
def test_case_input_is_p0():
    d = decide("Chest pain after acupuncture", node_type="Signal",
               source_class="SYSTEM_GENERATED")
    assert d.provenance_tier == P0 and d.audit_label == LBL_OK
    assert d.derivation == "case_input"

    d = decide("Symptoms persisted after acupuncture treatment",
               source_class="INPUT_OBSERVATION")
    assert d.provenance_tier == P0 and d.audit_label == LBL_OK


def test_verbatim_record_binding_is_p0():
    d = decide("Direct laryngoscopy showed diffuse edema and sloughing of "
               "pharyngeal and supraglottic mucosa", records=REC,
               match_tier="verbatim")
    assert d.provenance_tier == P0
    assert d.derivation == "record_verbatim"


def test_near_quote_of_a_record_is_p0():
    d = decide("Direct laryngoscopy showed diffuse edema and sloughing of "
               "pharyngeal and subglottic mucosa", records=REC)
    assert d.provenance_tier == P0
    assert d.derivation == "record_quote"


# ------------------------------------------------------------------- P1
def test_similar_but_unquoted_is_soft_grounded():
    """§4.1 P1: a candidate record exists but the text is not a match."""
    d = decide("CT showed glottic swelling and mucosal sloughing of the "
               "pharynx, with subglottic extension noted on direct exam",
               records=REC)
    assert d.provenance_tier == P1
    assert d.audit_label == LBL_REVIEW, "P1 is a review flag, not an anomaly"
    assert d.best_record_id == "rec-1"


# ------------------------------------------------------------------- P2
@pytest.mark.parametrize("label", [
    "Direct immunofluorescence (DIF) was not available",
    "Autoimmune serology tests were not obtained (no ANA/anti-Ro/anti-La)",
    "Extended immunohistochemistry and PET-CT have not been performed",
    "Cyclin D1 and SOX11 test results are not yet available",
    "Microbiological cultures and inflammatory markers remain unavailable",
    "Flame figures were not confirmed on biopsy",
    "Laboratory tests were normal, not supporting a metabolic cause",
    "Retrieval attempts have not identified an extrahepatic primary site",
])
def test_absence_and_limitation_claims_are_derived_not_anomalies(label):
    d = decide(label)
    assert d.provenance_tier == P2, label
    assert d.audit_label == LBL_OK, label
    assert d.semantic_kind == "absence_claim"


@pytest.mark.parametrize("label", [
    "Radiologist recommends further DSA or vessel wall MRI if clinically indicated",
    "Pathologist recommends expedited biopsy and clinical correlation",
])
def test_recommendations_are_derived(label):
    d = decide(label)
    assert (d.provenance_tier, d.semantic_kind) == (P2, "recommendation")
    assert d.audit_label == LBL_OK


@pytest.mark.parametrize("label", [
    "PAX8 is characteristically expressed in renal cell carcinoma and Müllerian tumors",
    "Axitinib is a targeted therapy primarily used for advanced renal cell carcinoma",
])
def test_domain_knowledge_is_derived(label):
    d = decide(label)
    assert (d.provenance_tier, d.semantic_kind) == (P2, "domain_knowledge")


def test_interpretation_is_agent_inference_not_recommendation():
    """"suggest" is an interpretation. S1 classified it as a recommendation,
    which mislabels the `semantic_kind` column the CSV reports."""
    d = decide("ECG changes suggest atrial infiltration")
    assert d.provenance_tier == P2
    assert d.semantic_kind == "agent_inference"


def test_node_restating_an_expert_analysis_is_derived():
    """The ANALYSIS event is a recorded antecedent (S2 §3.2), so the node is
    derived even though it has no retrieval source of its own."""
    analyses = [{"event_id": "evt-9", "analysis":
                 "Elevated troponin and regional wall motion abnormality "
                 "support an acute coronary syndrome."}]
    d = decide("Elevated troponin and regional wall motion abnormality",
               analyses=analyses)
    assert d.provenance_tier == P2
    assert d.derivation == "expert_synthesis"
    assert d.best_analysis_event == "evt-9"
    assert d.audit_label == LBL_OK


# The real case-4 input pool: the Signal node plus the three Evidence nodes
# the ingest step created from the case text.
CASE4_POOL = (
    "Chest pain while breathing and shortness of breath after acupuncture "
    "treatment\n"
    "A 26-year-old male visited Korean Medicine Hospital for acute "
    "indigestion; initially presented with acute indigestion, tenderness, and "
    "pain in the midback after lunch\n"
    "Social history includes smoking 0.1 box/day and alcohol consumption\n"
    "Symptoms persisted after acupuncture treatment, and the patient had "
    "difficulty walking even 20 steps")


def test_node_fusing_the_case_input_is_derived():
    """A node that is a near-verbatim recombination of the case input is
    derived, not unsourced -- the model was *given* this material."""
    d = decide("Symptoms persisted after acupuncture treatment, and the "
               "patient had difficulty walking", input_pool=CASE4_POOL)
    assert d.input_coverage > 0.7
    assert d.provenance_tier == P2
    assert d.derivation == "input_inference"


def test_input_inference_threshold_does_not_reach_the_natural_cluster():
    """Case 4 node-12 is the one clean-trace P3 and it is a *boundary* case.

    The model restated the case history in its own words ("Patient received
    acupuncture for midback pain before symptom onset" vs "...visited Korean
    Medicine Hospital ... pain in the midback" + "Symptoms persisted after
    acupuncture treatment"). Token coverage against the real input pool is
    0.43. Lowering `input_inference_min_coverage` to 0.4 would reclassify it,
    but the whole natural cluster of ungrounded nodes sits just below that
    (0.00-0.29), so the threshold would have to move *into* the cluster -- the
    overfitting §4.2 forbids. The node is reported as P3 and flagged for human
    adjudication instead.
    """
    d = decide("Patient received acupuncture for midback pain before symptom "
               "onset", input_pool=CASE4_POOL)
    assert d.input_coverage == pytest.approx(0.43, abs=0.01)
    assert d.provenance_tier == P3
    assert d.audit_label == LBL_ANOMALY, "it is used, so it is high-risk"
    assert DEFAULT_CFG["input_inference_min_coverage"] == 0.7


def test_hypotheses_are_derived_not_anomalies():
    d = decide("Acupuncture-induced right pneumothorax", node_type="Hypothesis")
    assert d.provenance_tier == P2 and d.audit_label == LBL_OK
    assert d.semantic_kind == "hypothesis"


# ------------------------------------------------------------------- P3
def test_fabricated_finding_is_an_unsupported_assertion():
    """The A1 shape: a specific clinical finding with no source anywhere."""
    d = decide("Serum tryptase was elevated at 42 ng/mL and the bone marrow "
               "biopsy confirmed mast cell infiltration", records=REC)
    assert d.provenance_tier == P3
    assert d.audit_label == LBL_ANOMALY
    assert d.used_in_reasoning


def test_unused_unsourced_node_is_p3_but_not_counted_as_an_anomaly():
    """§4.1 defines P3 to require that the node reaches reasoning. The tier is
    the same either way; only the label distinguishes the high-risk case."""
    text = ("Serum tryptase was elevated at 42 ng/mL and the bone marrow "
            "biopsy confirmed mast cell infiltration")
    used = decide(text, records=REC, used=True)
    unused = decide(text, records=REC, used=False)
    assert used.provenance_tier == unused.provenance_tier == P3
    assert used.audit_label == LBL_ANOMALY
    assert unused.audit_label == LBL_UNUSED


# ------------------------------------------------------------------ measures
def test_longest_common_span_separates_a_quote_from_a_coincidence():
    quote = "direct laryngoscopy showed diffuse edema and sloughing of pharyngeal mucosa"
    assert longest_common_span_ratio(quote, REC[0]["text"]) > 0.8
    coincidence = "the patient had no fever and the mucosa was normal"
    assert longest_common_span_ratio(coincidence, REC[0]["text"]) < 0.3


def test_char_similarity_is_bounded_and_symmetric_in_range():
    assert char_similarity("", "x") == 0.0
    assert char_similarity("abc", "abc") == 1.0
    assert 0.0 < char_similarity("acute coronary syndrome", REC[0]["text"]) < 1.0


def test_best_record_reports_the_same_record_for_all_three_measures():
    recs = [{"record_id": "r0", "text": "nothing relevant here at all"},
            {"record_id": "r1", "text":
             "direct laryngoscopy showed diffuse edema and sloughing"}]
    rid, text, cov, span = best_record("direct laryngoscopy showed diffuse edema",
                                       recs)
    assert rid == "r1" and cov > 0.5 and span > 0.5


# --------------------------------------------------------------- task-level
def _task_dir(tmp_path, task_id="case-0"):
    d = tmp_path / task_id
    d.mkdir()
    bindings = {
        "task_id": task_id,
        "records": REC,
        "bindings": [
            node("Chest pain", node_type="Signal",
                 source_class="SYSTEM_GENERATED", node_id="node-1"),
            node("Symptoms persisted after acupuncture treatment",
                 source_class="INPUT_OBSERVATION", node_id="node-2"),
            node("Acupuncture-induced right pneumothorax",
                 node_type="Hypothesis", node_id="node-3"),
            node("Direct laryngoscopy showed diffuse edema and sloughing",
                 source_class="RETRIEVED_RECORD", node_id="node-4",
                 match_tier="verbatim", matched_record_id="rec-1"),
            node("Serum tryptase was elevated at 42 ng/mL", node_id="node-5"),
        ],
    }
    (d / f"{task_id}_bindings.json").write_text(json.dumps(bindings))
    events = [
        {"event_id": "evt-1", "event_type": "ANALYSIS",
         "payload": {"analysis": "no relevant analysis"}, "output_refs": []},
        {"event_id": "evt-2", "event_type": "EVIDENCE_READ",
         "output_refs": ["rec-1"], "payload": {"record_hash": "deadbeef"}},
        {"event_id": "evt-3", "event_type": "FINAL_REPORT",
         "causal_node_refs": ["node-3"], "output_refs": []},
    ]
    (d / f"{task_id}_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events))
    deltas = [
        {"added_edges": [["node-4", "node-3", "support"],
                         ["node-5", "node-3", "support"]],
         "deleted_edges": []},
    ]
    (d / f"{task_id}_deltas.jsonl").write_text(
        "\n".join(json.dumps(x) for x in deltas))
    return str(d)


def test_audit_task_tiers_every_node(tmp_path):
    decisions = audit_task(_task_dir(tmp_path), "case-0")
    by_id = {d.node_id: d for d in decisions}
    assert by_id["node-1"].provenance_tier == P0        # Signal
    assert by_id["node-2"].provenance_tier == P0        # input observation
    assert by_id["node-3"].provenance_tier == P2        # hypothesis
    assert by_id["node-4"].provenance_tier == P0        # verbatim record
    assert by_id["node-5"].provenance_tier == P3        # fabricated
    assert by_id["node-5"].audit_label == LBL_ANOMALY
    assert by_id["node-5"].used_in_reasoning, "support edge makes it used"
    assert by_id["node-5"].best_record_hash == "deadbeef"

    s = summarize(decisions)
    assert s["n_evidence"] == 3                          # nodes 2, 4, 5
    assert s["p3_total"] == 1
    assert s["fpr"] == pytest.approx(1 / 3, abs=1e-4)


def test_write_csv_has_every_required_column(tmp_path):
    decisions = audit_task(_task_dir(tmp_path), "case-0")
    out = tmp_path / "evidence_boundary_analysis.csv"
    write_csv(decisions, str(out))
    header = out.read_text().splitlines()[0]
    required = ["case_id", "node_id", "label", "source_class", "semantic_kind",
                "best_record_id", "token_coverage", "char_similarity",
                "provenance_tier", "audit_label"]
    for col in required:
        assert f'"{col}"' in header, f"{col} missing from the CSV header"
    assert list(json.loads('{}').keys()) == []
    rows = out.read_text().splitlines()[1:]
    assert len(rows) == len(decisions)
    assert len(header.split(",")) == len(CSV_COLUMNS)


# ------------------------------------------------------- clean-trace FPR gate
RUN_DIR = os.path.join("S2A_runs", "s2a_dev10", "C_prov_on_replay", "traceevo",
                       "runs", "C_prov_on_replay")


@pytest.mark.skipif(not os.path.isdir(RUN_DIR),
                    reason="run the T1 driver to produce Dev-Small artifacts")
def test_clean_trace_fpr_is_within_the_s4_gate():
    """§4.2: clean-trace Evidence anomaly FPR <= 10% (ideal <= 5%).

    This is the gate that stops the tiering from being tuned until every
    natural node is "sourced": if the P2 classes were too narrow, legitimate
    derived nodes would fall through to P3 and this test would fail.
    """
    decisions = []
    for i in range(10):
        decisions += audit_task(RUN_DIR, f"case-{i}")
    s = summarize(decisions)
    assert s["n_evidence"] > 50, "the gate needs a real sample"
    assert s["p3_rate"] <= 0.10, (s["p3_rate"], [
        (d.case_id, d.node_id, d.label) for d in decisions
        if d.provenance_tier == P3])
    assert s["fpr"] <= 0.05, s["fpr"]


# ------------------------------------------------------- S2.1 §1 D1
def test_expert_synthesis_is_derived_but_not_externally_grounded():
    """The distinction D1 exists to make: a node restating a dispatched
    expert's ANALYSIS has a complete lineage and is not an external fact.

    Reading P2 as if it were P0 would let an agent's own restatement enter
    long-term memory with the same standing as a retrieved record.
    """
    analyses = [{"event_id": "evt-9", "analysis":
                 "Elevated troponin and regional wall motion abnormality "
                 "support an acute coronary syndrome."}]
    d = decide("Elevated troponin and regional wall motion abnormality",
               analyses=analyses)
    assert d.provenance_tier == P2
    assert d.derivation == "expert_synthesis"
    assert d.externally_grounded is False
    assert d.lineage_complete is True, "the ANALYSIS event is a real antecedent"
    assert d.lineage_source == "evt-9"


def test_case_input_and_bound_records_are_externally_grounded():
    d = decide("Symptoms persisted after acupuncture treatment",
               source_class="INPUT_OBSERVATION")
    assert (d.provenance_tier, d.externally_grounded) == (P0, True)
    assert d.lineage_complete is True

    d = decide("Direct laryngoscopy showed diffuse edema and sloughing of "
               "pharyngeal and supraglottic mucosa", records=REC,
               match_tier="verbatim")
    assert (d.provenance_tier, d.externally_grounded) == (P0, True)


def test_self_declared_derivations_have_no_content_lineage():
    """An absence claim's only provenance is that an agent asserted it. That
    is not a content lineage, and the caller has to be able to tell."""
    d = decide("Direct immunofluorescence (DIF) was not available")
    assert (d.provenance_tier, d.derivation) == (P2, "absence_claim")
    assert d.externally_grounded is False
    assert d.lineage_complete is False
    assert d.lineage_source == ""


def test_unsupported_assertion_is_neither_grounded_nor_traceable():
    d = decide("Serum tryptase was elevated at 42 ng/mL and the bone marrow "
               "biopsy confirmed mast cell infiltration", records=REC)
    assert d.provenance_tier == P3
    assert d.externally_grounded is False
    assert d.lineage_complete is False


def test_soft_grounded_is_not_externally_grounded():
    """P1 is a similarity, not a source; it must not pass as grounding."""
    d = decide("CT showed glottic swelling and mucosal sloughing of the "
               "pharynx, with subglottic extension noted on direct exam",
               records=REC)
    assert d.provenance_tier == P1
    assert d.externally_grounded is False


def test_d1_columns_are_in_the_csv():
    assert "externally_grounded" in CSV_COLUMNS
    assert "lineage_complete" in CSV_COLUMNS
    assert "lineage_source" in CSV_COLUMNS
