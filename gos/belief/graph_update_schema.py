"""Pydantic schemas and graph-aware checks for central-agent graph deltas.

The LLM's ``evNNN`` / ``hNNN`` identifiers are temporary references within one
proposal.  ``BeliefGraph.add_node`` assigns the persistent ``node-N`` IDs only
after the complete proposal has passed validation.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from difflib import get_close_matches
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)


_Text = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
_EvidenceId = Annotated[
    str,
    StringConstraints(pattern=r"^ev\d{3}$", strict=True),
]
_HypothesisId = Annotated[
    str,
    StringConstraints(pattern=r"^h\d{3}$", strict=True),
]


class NodeType(str, Enum):
    EVIDENCE = "Evidence"
    HYPOTHESIS = "Hypothesis"


class EdgeRelation(str, Enum):
    SUPPORT = "support"
    REFUTE = "refute"


class _ProposalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_boolean_confidence(value: object) -> object:
    # Pydantic's normal float coercion accepts bool because bool subclasses int.
    # Numeric strings such as "0.7" are useful to repair; booleans are not.
    if isinstance(value, bool):
        raise ValueError("confidence must be a number, not a boolean")
    return value


class InitialEvidenceExtraction(_ProposalModel):
    symptom_node: _Text
    isolated_evidence: list[_Text] = Field(max_length=5)


class InitialCandidateProposal(_ProposalModel):
    id: _HypothesisId
    label: Annotated[str, StringConstraints(strip_whitespace=True,
                                            min_length=1, max_length=30,
                                            strict=True)]
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    why: _Text

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        return _reject_boolean_confidence(value)


class InitialCandidateEdgeProposal(_ProposalModel):
    src: _Text
    dst: _HypothesisId
    relation: EdgeRelation


class InitialHypothesisProposal(_ProposalModel):
    candidates: list[InitialCandidateProposal] = Field(min_length=2, max_length=4)
    edges: list[InitialCandidateEdgeProposal]


class EvidenceNodeProposal(_ProposalModel):
    id: _EvidenceId
    node_type: Literal[NodeType.EVIDENCE]
    label: _Text


class HypothesisNodeProposal(_ProposalModel):
    id: _HypothesisId
    node_type: Literal[NodeType.HYPOTHESIS]
    label: _Text
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    why: _Text

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        return _reject_boolean_confidence(value)


GraphNodeProposal = Annotated[
    Union[EvidenceNodeProposal, HypothesisNodeProposal],
    Field(discriminator="node_type"),
]


class HypothesisEditProposal(_ProposalModel):
    node_id: _Text
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    why: _Text

    @field_validator("confidence", mode="before")
    @classmethod
    def reject_boolean_confidence(cls, value: object) -> object:
        return _reject_boolean_confidence(value)


class GraphEdgeProposal(_ProposalModel):
    src: _Text
    dst: _Text
    relation: EdgeRelation


class GraphUpdateProposal(_ProposalModel):
    edit: list[HypothesisEditProposal]
    nodes: list[GraphNodeProposal]
    edges: list[GraphEdgeProposal]


def _enum_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"[\s_-]+", "", value)


_NODE_TYPE_ALIASES = {
    "evidence": "Evidence", "evidencenode": "Evidence",
    "证据": "Evidence", "证据节点": "Evidence",
    "hypothesis": "Hypothesis", "hypothesisnode": "Hypothesis",
    "假设": "Hypothesis", "假设节点": "Hypothesis",
}
_RELATION_ALIASES = {
    "support": "support", "supports": "support", "supporting": "support",
    "supported": "support", "支持": "support", "支持关系": "support",
    "refute": "refute", "refutes": "refute", "refuting": "refute",
    "refutation": "refute", "contradict": "refute",
    "contradicts": "refute", "反驳": "refute", "反驳关系": "refute",
    "证伪": "refute",
}
_ENUM_FUZZY_CUTOFF = 0.92


def _canonical_enum(value: object, aliases: dict[str, str]) -> object:
    if not isinstance(value, str):
        return value
    key = _enum_key(value)
    normalized_aliases = {_enum_key(alias): canonical
                          for alias, canonical in aliases.items()}
    if key in normalized_aliases:
        return normalized_aliases[key]
    candidates = sorted(set(aliases.values()))
    normalized_candidates = {_enum_key(candidate): candidate
                             for candidate in candidates}
    match = get_close_matches(key, list(normalized_candidates), n=1,
                              cutoff=_ENUM_FUZZY_CUTOFF)
    if match:
        return normalized_candidates[match[0]]
    return value


def normalize_graph_update_enums(raw: object) -> tuple[object, list[str]]:
    """Normalize only known enum fields; leave ambiguous values for rejection.

    Unicode NFKC handles full-width forms, aliases handle common wording, and
    fuzzy matching is limited to the two known enum vocabularies at >=0.92.
    No embeddings or clinical free-text fields are modified.
    """
    if not isinstance(raw, dict):
        return raw, []
    normalized = copy.deepcopy(raw)
    changes: list[str] = []
    for index, node in enumerate(normalized.get("nodes") or []):
        if not isinstance(node, dict) or not isinstance(node.get("node_type"), str):
            continue
        old = node["node_type"]
        new = _canonical_enum(old, _NODE_TYPE_ALIASES)
        if new != old:
            node["node_type"] = new
            changes.append(f"nodes[{index}].node_type: {old!r} -> {new!r}")
    for index, edge in enumerate(normalized.get("edges") or []):
        if not isinstance(edge, dict) or not isinstance(edge.get("relation"), str):
            continue
        old = edge["relation"]
        new = _canonical_enum(old, _RELATION_ALIASES)
        if new != old:
            edge["relation"] = new
            changes.append(f"edges[{index}].relation: {old!r} -> {new!r}")
    return normalized, changes


def strict_json_schema_response_format(model: type[BaseModel], *,
                                       name: str) -> dict[str, object]:
    """Build an OpenAI-compatible strict JSON Schema response format."""
    def normalize(schema: dict[str, object]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in schema.items():
            if key in {"title", "description", "default", "discriminator"}:
                continue
            if key == "const":
                output["enum"] = [value]
                continue
            if key == "oneOf":
                output["anyOf"] = [normalize(item) for item in value]
                continue
            if key == "$defs" and isinstance(value, dict):
                output[key] = {def_name: normalize(def_schema)
                               for def_name, def_schema in value.items()}
                continue
            if key == "properties" and isinstance(value, dict):
                output[key] = {prop_name: normalize(prop_schema)
                               for prop_name, prop_schema in value.items()}
                output["required"] = list(value)
                output["additionalProperties"] = False
                continue
            if key == "items" and isinstance(value, dict):
                output[key] = normalize(value)
                continue
            if key in {"anyOf", "allOf"} and isinstance(value, list):
                output[key] = [normalize(item) for item in value]
                continue
            output[key] = value
        if output.get("type") == "object":
            output.setdefault("additionalProperties", False)
            if "properties" in output:
                output["required"] = list(output["properties"])
        return output

    schema = normalize(model.model_json_schema())
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", name).lower()
    return {
        "type": "json_schema",
        "json_schema": {"name": safe_name, "strict": True, "schema": schema},
    }


class GraphUpdateValidationError(ValueError):
    """A parsed LLM response is not safe to apply to the current graph."""


def _normalise_label(label: str) -> str:
    return " ".join(label.casefold().split())


def validate_graph_update(
    raw: object,
    graph: object,
    symptom_node_id: str | None = None,
) -> GraphUpdateProposal:
    """Validate proposal shape and references without mutating ``graph``.

    Besides Pydantic's field/type/enum checks, this verifies IDs against the
    live graph, rejects duplicate proposal IDs/labels and edges that would
    overwrite an existing (src, dst) pair. The caller should invoke this once
    before applying *any* edits, nodes, or edges.
    """
    try:
        normalized, _changes = normalize_graph_update_enums(raw)
        proposal = GraphUpdateProposal.model_validate(normalized)
    except ValidationError as exc:
        raise GraphUpdateValidationError(str(exc)) from exc

    existing_nodes = getattr(graph, "nodes", {})
    existing_edges = getattr(graph, "edges", {})
    errors: list[str] = []

    new_nodes_by_id: dict[str, GraphNodeProposal] = {}
    label_keys: set[tuple[str, str]] = set()
    for existing in existing_nodes.values():
        node_type = existing.get("type")
        label = existing.get("label")
        if node_type in {"Evidence", "Hypothesis"} and isinstance(label, str):
            label_keys.add((node_type, _normalise_label(label)))

    for node in proposal.nodes:
        node_id = node.id
        if node_id in new_nodes_by_id:
            errors.append(f"duplicate temporary node id: {node_id}")
        else:
            new_nodes_by_id[node_id] = node

        if node_id in existing_nodes:
            errors.append(f"new node id collides with an existing graph id: {node_id}")

        node_type = node.node_type.value
        label_key = (node_type, _normalise_label(node.label))
        if label_key in label_keys:
            errors.append(f"duplicate {node_type} label: {node.label!r}")
        label_keys.add(label_key)

    edited_ids: set[str] = set()
    for edit in proposal.edit:
        if edit.node_id in edited_ids:
            errors.append(f"duplicate edit for node: {edit.node_id}")
        edited_ids.add(edit.node_id)
        existing = existing_nodes.get(edit.node_id)
        if existing is None:
            errors.append(f"edit references a node that does not exist: {edit.node_id}")
        elif existing.get("type") != "Hypothesis":
            errors.append(f"edit target is not a Hypothesis node: {edit.node_id}")

    available_ids = set(existing_nodes) | set(new_nodes_by_id)
    seen_edge_pairs: set[tuple[str, str]] = set()
    new_hypothesis_ids = {
        node.id
        for node in proposal.nodes
        if isinstance(node, HypothesisNodeProposal)
    }
    for edge in proposal.edges:
        pair = (edge.src, edge.dst)
        if edge.src not in available_ids:
            errors.append(f"edge source does not exist in graph or proposal: {edge.src}")
        if edge.dst not in available_ids:
            errors.append(f"edge destination does not exist in graph or proposal: {edge.dst}")
        if edge.src == edge.dst:
            errors.append(f"self-loop is not allowed: {edge.src}")
        if pair in seen_edge_pairs:
            errors.append(f"duplicate/conflicting edge pair: {edge.src} -> {edge.dst}")
        if pair in existing_edges:
            errors.append(f"edge would overwrite an existing edge: {edge.src} -> {edge.dst}")
        seen_edge_pairs.add(pair)

        # The current graph-update path automatically adds this refines edge
        # for every newly-created hypothesis. A model-proposed support/refute
        # edge on the same pair would overwrite it in BeliefGraph's pair-keyed
        # edge dictionary.
        if symptom_node_id and edge.src == symptom_node_id and edge.dst in new_hypothesis_ids:
            errors.append(
                f"edge conflicts with the automatic symptom-to-hypothesis refines edge: "
                f"{edge.src} -> {edge.dst}"
            )

    if errors:
        raise GraphUpdateValidationError("; ".join(errors))
    return proposal


def validate_initial_evidence_extraction(raw: object) -> InitialEvidenceExtraction:
    try:
        return InitialEvidenceExtraction.model_validate(raw)
    except ValidationError as exc:
        raise GraphUpdateValidationError(str(exc)) from exc


def validate_initial_hypotheses(
    raw: object,
    evidence_node_ids: list[str],
) -> InitialHypothesisProposal:
    normalized, _changes = normalize_graph_update_enums(raw)
    try:
        proposal = InitialHypothesisProposal.model_validate(normalized)
    except ValidationError as exc:
        raise GraphUpdateValidationError(str(exc)) from exc

    errors: list[str] = []
    candidate_ids = [candidate.id for candidate in proposal.candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("candidate temporary IDs must be unique")
    labels = [_normalise_label(candidate.label) for candidate in proposal.candidates]
    if len(labels) != len(set(labels)):
        errors.append("candidate hypothesis labels must be unique")

    evidence_ids = set(evidence_node_ids)
    candidate_id_set = set(candidate_ids)
    edge_pairs: set[tuple[str, str]] = set()
    connected_evidence: set[str] = set()
    for edge in proposal.edges:
        pair = (edge.src, edge.dst)
        if edge.src not in evidence_ids:
            errors.append(f"initial edge source is not an extracted evidence node: {edge.src}")
        else:
            connected_evidence.add(edge.src)
        if edge.dst not in candidate_id_set:
            errors.append(f"initial edge destination is not a candidate ID: {edge.dst}")
        if pair in edge_pairs:
            errors.append(f"duplicate/conflicting initial edge pair: {edge.src} -> {edge.dst}")
        edge_pairs.add(pair)
    missing = sorted(evidence_ids - connected_evidence)
    if missing:
        errors.append("every initial evidence node must connect to a candidate: "
                      + ", ".join(missing))
    if errors:
        raise GraphUpdateValidationError("; ".join(errors))
    return proposal
