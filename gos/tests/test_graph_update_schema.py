import pytest

from belief.graph import BeliefGraph
from belief.graph_update_schema import (
    GraphUpdateProposal,
    GraphUpdateValidationError,
    validate_graph_update,
)


def _graph():
    graph = BeliefGraph()
    signal = graph.add_node("Signal", "Ankle pain")
    evidence = graph.add_node("Evidence", "Tenderness around the ankle")
    hypothesis = graph.add_node(
        "Hypothesis",
        "Soft-tissue injury",
        score=0.7,
        attrs={"level": 1, "why": "Initial clinical impression"},
    )
    graph.add_edge(evidence, hypothesis, "support")
    return graph, signal, evidence, hypothesis


def _proposal(hypothesis_id):
    return {
        "edit": [
            {
                "node_id": hypothesis_id,
                "confidence": 0.8,
                "why": "New examination findings are consistent with this diagnosis.",
            }
        ],
        "nodes": [
            {
                "id": "ev001",
                "node_type": "Evidence",
                "label": "No fracture seen on the X-ray",
            },
            {
                "id": "h001",
                "node_type": "Hypothesis",
                "label": "Ankle sprain",
                "confidence": 0.65,
                "why": "The mechanism and examination are compatible with a sprain.",
            },
        ],
        "edges": [
            {"src": "ev001", "dst": hypothesis_id, "relation": "support"},
            {"src": "ev001", "dst": "h001", "relation": "support"},
        ],
    }


def test_valid_delta_parses_and_keeps_enum_relation_as_json_string():
    graph, _signal, _evidence, hypothesis = _graph()

    parsed = validate_graph_update(_proposal(hypothesis), graph)

    assert parsed.model_dump(mode="json")["edges"][0]["relation"] == "support"


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda p: p["nodes"].append(dict(p["nodes"][0])), "duplicate temporary node id"),
        (lambda p: p["nodes"][0].update(id="node-99"), "String should match pattern"),
        (lambda p: p["nodes"][1].update(confidence=1.01), "less than or equal to 1"),
        (lambda p: p["nodes"][1].update(confidence=True), "not a boolean"),
        (lambda p: p["edges"][0].update(relation="causal"), "Input should be 'support' or 'refute'"),
        (lambda p: p["edges"][0].update(dst="node-999"), "edge destination does not exist"),
    ],
)
def test_rejects_invalid_schema_or_references(mutate, expected):
    graph, _signal, _evidence, hypothesis = _graph()
    proposal = _proposal(hypothesis)
    mutate(proposal)

    with pytest.raises(GraphUpdateValidationError, match=expected):
        validate_graph_update(proposal, graph)


def test_rejects_edit_of_non_hypothesis_and_duplicate_hypothesis_label():
    graph, _signal, evidence, hypothesis = _graph()
    proposal = _proposal(hypothesis)
    proposal["edit"][0]["node_id"] = evidence
    proposal["nodes"][1]["label"] = " Soft-tissue   injury "

    with pytest.raises(GraphUpdateValidationError) as exc_info:
        validate_graph_update(proposal, graph)

    assert "edit target is not a Hypothesis" in str(exc_info.value)
    assert "duplicate Hypothesis label" in str(exc_info.value)


def test_rejects_duplicate_or_overwriting_edge_pairs():
    graph, _signal, _evidence, hypothesis = _graph()
    proposal = _proposal(hypothesis)
    proposal["edges"] = [
        {"src": "ev001", "dst": hypothesis, "relation": "support"},
        {"src": "ev001", "dst": hypothesis, "relation": "refute"},
    ]

    with pytest.raises(GraphUpdateValidationError, match="duplicate/conflicting edge pair"):
        validate_graph_update(proposal, graph)

    proposal["edges"] = [
        {"src": _evidence, "dst": hypothesis, "relation": "refute"},
    ]
    with pytest.raises(GraphUpdateValidationError, match="overwrite an existing edge"):
        validate_graph_update(proposal, graph)


def test_rejects_temporary_id_collision_with_live_graph_id():
    graph, _signal, _evidence, hypothesis = _graph()
    graph.nodes["ev001"] = {"type": "Evidence", "label": "Existing evidence", "score": 1.0, "attrs": {}}

    with pytest.raises(GraphUpdateValidationError, match="collides with an existing graph id"):
        validate_graph_update(_proposal(hypothesis), graph)


def test_rejects_model_edge_that_would_overwrite_automatic_refines_edge():
    graph, signal, _evidence, hypothesis = _graph()
    proposal = _proposal(hypothesis)
    proposal["edges"].append(
        {"src": signal, "dst": "h001", "relation": "support"}
    )

    with pytest.raises(GraphUpdateValidationError, match="automatic symptom-to-hypothesis"):
        validate_graph_update(proposal, graph, symptom_node_id=signal)


def test_coerces_numeric_confidence_and_normalizes_unambiguous_enums():
    graph, _signal, _evidence, hypothesis = _graph()
    proposal = _proposal(hypothesis)
    proposal["edit"][0]["confidence"] = "0.82"
    proposal["nodes"][0]["node_type"] = "ＥＶＩＤＥＮＣＥ"
    proposal["edges"][0]["relation"] = " SUPPORTS "

    parsed = validate_graph_update(proposal, graph)

    dumped = parsed.model_dump(mode="json")
    assert dumped["edit"][0]["confidence"] == 0.82
    assert dumped["nodes"][0]["node_type"] == "Evidence"
    assert dumped["edges"][0]["relation"] == "support"


def test_initial_hypotheses_validate_evidence_and_candidate_references():
    from belief.graph_update_schema import validate_initial_hypotheses

    evidence_id = "node-2"
    parsed = validate_initial_hypotheses({
        "candidates": [
            {"id": "h001", "label": "Ankle sprain", "confidence": "0.7",
             "why": "Tenderness and mechanism support soft-tissue injury."},
            {"id": "h002", "label": "Ankle fracture", "confidence": 0.3,
             "why": "Fracture remains possible before imaging."},
        ],
        "edges": [
            {"src": evidence_id, "dst": "h001", "relation": "ＳＵＰＰＯＲＴ"},
            {"src": evidence_id, "dst": "h002", "relation": "refute"},
        ],
    }, [evidence_id])

    assert parsed.candidates[0].confidence == 0.7
    assert parsed.edges[0].relation.value == "support"

    with pytest.raises(GraphUpdateValidationError, match="every initial evidence"):
        validate_initial_hypotheses({
            "candidates": [
                {"id": "h001", "label": "Ankle sprain", "confidence": 0.7,
                 "why": "Possible."},
                {"id": "h002", "label": "Ankle fracture", "confidence": 0.3,
                 "why": "Possible."},
            ],
            "edges": [],
        }, [evidence_id])


def test_strict_response_schema_has_required_closed_objects():
    from belief.graph_update_schema import strict_json_schema_response_format

    response_format = strict_json_schema_response_format(
        GraphUpdateProposal, name="GraphUpdateProposal"
    )
    schema = response_format["json_schema"]["schema"]

    assert response_format["json_schema"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"edit", "nodes", "edges"}
    assert "oneOf" not in str(schema)
