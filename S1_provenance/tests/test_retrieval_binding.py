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
