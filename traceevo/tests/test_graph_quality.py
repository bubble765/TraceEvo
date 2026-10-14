import copy
import json
import pytest

from traceevo.evaluation.graph_quality import check_structure
from traceevo.evaluation.graph_quality import validate_graph_semantics


def graph():
    return {"nodes": {
        "s": {"type": "Signal", "label": "symptom", "score": 1, "attrs": {}},
        "e": {"type": "Evidence", "label": "observation", "score": 1, "attrs": {}},
        "h1": {"type": "Hypothesis", "label": "broad", "score": .7, "attrs": {"level": 1, "why": "evidence"}},
        "h2": {"type": "Hypothesis", "label": "specific", "score": .6, "attrs": {"level": 2, "why": "refinement"}}},
        "edges": [{"src": "s", "dst": "h1", "type": "refines"},
                  {"src": "h1", "dst": "h2", "type": "refines"},
                  {"src": "e", "dst": "h1", "type": "support"}]}


def test_legal_structure_passes_without_mutating_source():
    state = graph()
    original = copy.deepcopy(state)
    assert check_structure(state) == []
    assert state == original


def test_dangling_duplicate_edges_and_scores_are_detected():
    state = graph()
    state["edges"].append(dict(state["edges"][0]))
    state["edges"].append({"src": "missing", "dst": "h1", "type": "support"})
    state["nodes"]["h1"]["score"] = float("nan")
    rules = {x["rule"] for x in check_structure(state)}
    assert {"duplicate_edge_pair", "dangling_edge", "invalid_confidence"} <= rules


def test_refinement_cycles_levels_and_missing_parents_are_detected():
    state = graph()
    state["edges"].append({"src": "h2", "dst": "h1", "type": "refines"})
    rules = {x["rule"] for x in check_structure(state)}
    assert {"invalid_refinement_level", "refinement_cycle"} <= rules
    state = graph()
    state["edges"] = [x for x in state["edges"] if x["src"] != "h1"]
    assert "missing_parent_hypothesis" in {x["rule"] for x in check_structure(state)}


def test_evidence_relation_target_and_duplicate_hypotheses_are_detected():
    state = graph()
    state["nodes"]["h2"]["label"] = "Broad"
    state["edges"].append({"src": "h1", "dst": "e", "type": "support"})
    rules = {x["rule"] for x in check_structure(state)}
    assert {"duplicate_hypothesis_label", "invalid_evidence_relation_target"} <= rules


def test_malformed_containers_are_scored_without_crashing():
    assert check_structure(None)[0]["rule"] == "invalid_graph_container"
    state = graph()
    state["nodes"]["h1"] = "bad"
    assert "invalid_node" in {x["rule"] for x in check_structure(state)}
    state = graph()
    state["nodes"]["h1"]["attrs"] = ["bad"]
    assert "invalid_node_attrs" in {x["rule"] for x in check_structure(state)}


def test_graph_semantic_references_and_status_are_validated():
    report = {"status": "ISSUES", "findings": [{"dimension": "confidence",
        "node_ids": ["h1"], "edges": [], "reason": "graph-local conflict"}],
        "limitations": ["source authenticity not evaluated"]}
    assert validate_graph_semantics(json.dumps(report), graph())["status"] == "ISSUES"
    report["findings"][0]["node_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="unknown causal node"):
        validate_graph_semantics(json.dumps(report), graph())
    report["findings"] = []
    with pytest.raises(ValueError, match="ISSUES"):
        validate_graph_semantics(json.dumps(report), graph())


def test_graph_semantic_input_contains_only_causal_graph(tmp_path, monkeypatch):
    import utils.public_function as public
    from traceevo.evaluation.graph_quality import semantic_audit
    seen = {}
    def llm(**kw):
        seen.update(json.loads(kw["user_prompt"]))
        return json.dumps({"status": "UNCERTAIN", "findings": [],
                           "limitations": ["limited graph context"]}), {}
    monkeypatch.setattr(public, "llm_generate_response", llm)
    monkeypatch.setattr(public, "default_model_path", lambda: "test-model")
    state = graph()
    original = copy.deepcopy(state)
    result = semantic_audit(tmp_path, "case-1", state, tmp_path, 0, tmp_path)
    assert seen == {"causal_graph": state}
    assert result["status"] == "UNCERTAIN"
    assert state == original
