"""Evidence provenance tiers P0-P3 (S2 §4).

The S1 layer answered a binary question -- "does this Evidence node have a
retrieval source?" -- with a single 0.6 token-overlap threshold. §4.1 rejects
that as a ground-truth label, because a node with no retrieval source is not
automatically an anomaly: an absence claim, a recommendation, domain knowledge
or an inference drawn from the given case are all legitimate.

This module assigns a *tier* instead:

    P0 DIRECT_GROUNDED          the claim is in the initial input, or quoted
                                from / explicitly bound to a retrieval record
    P1 SOFT_GROUNDED            a candidate record is similar but not a match
    P2 DERIVED                  a legitimate derived class (see below)
    P3 UNSUPPORTED_ASSERTION    none of the above

The P2 classes, all decided deterministically (no LLM judgement):

    absence_claim      "" / recommendation / domain_knowledge / agent_inference
                       -- self-declared, from `guess_semantic_kind`
    expert_synthesis   the node restates a dispatched expert's ANALYSIS; the
                       ANALYSIS event is a recorded antecedent (S2 §3.2)
    input_inference    the node fuses material the model was given (the chief
                       complaint and the physical-exam Evidence nodes)

Only P3 is the "fabricated / unsourced evidence" target of A1, and even then
only when the node is actually *used* (supports/refutes a hypothesis or is on
the final-report path); an unused ungrounded node is reported as `unused`
rather than counted as a clean-trace false positive. That distinction is the
one §4.1 draws when it defines P3 to require usage.

Thresholds are development configuration and are swept by `sensitivity()`; the
defaults are chosen on Dev-Small 0-9 (see S2_report.md) and re-validated in S5.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cross_graph_binding import (classify_evidence, content_tokens,
                                  records_from_events,
                                  guess_semantic_kind, norm, token_coverage)

P0 = "P0_DIRECT_GROUNDED"
P1 = "P1_SOFT_GROUNDED"
P2 = "P2_DERIVED"
P3 = "P3_UNSUPPORTED_ASSERTION"
TIERS = (P0, P1, P2, P3)

# audit_label values. Only UNSUPPORTED_ASSERTION counts as a clean-trace
# anomaly; the others are recorded so nothing is silently dropped.
LBL_OK = "ok"
LBL_REVIEW = "soft_grounded_review"
LBL_UNUSED = "unsupported_unused"
LBL_ANOMALY = "unsupported_assertion"

DEFAULT_CFG: Dict[str, Any] = {
    # P0: verbatim containment, or coverage this high that the label is
    # effectively a quote of the record.
    "direct_min_coverage": 0.85,
    # P1: a candidate record must clear one of these.
    "soft_min_coverage": 0.35,
    "soft_min_char_similarity": 0.35,
    # P2: node restates a dispatched expert's ANALYSIS.
    "expert_synthesis_min_coverage": 0.5,
    # P2: node fuses material given in the case input.
    "input_inference_min_coverage": 0.7,
    "min_content_tokens": 4,
}


# ------------------------------------------------------------------ measures
def char_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio over normalised text (§4.1 asks for it explicitly)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    return round(SequenceMatcher(None, na, nb).ratio(), 4)


def longest_common_span_ratio(label: str, text: str) -> float:
    """Length of the longest shared substring as a fraction of the label.

    This is the measure that actually separates a quote from a coincidence:
    token coverage counts words wherever they appear, while a quoted phrase
    survives as one contiguous span. "Peripheral blood eosinophilia is 11%"
    against a record containing "peripheral blood eosinophilia of 11%" scores
    ~0.83 here and would score 0.67 on tokens.
    """
    a, b = norm(label), norm(text)
    if not a or not b:
        return 0.0
    m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return round(m.size / len(a), 4)


def best_record(label: str, records: Sequence[Dict[str, Any]]
                ) -> Tuple[str, str, float, float]:
    """(record_id, record_text, token_coverage, longest_span_ratio) of the best."""
    best = ("", "", 0.0, 0.0)
    for rec in records:
        text = rec.get("text", "")
        cov = token_coverage(label, text)
        span = longest_common_span_ratio(label, text)
        if (cov, span) > (best[2], best[3]):
            best = (rec.get("record_id", ""), text, round(cov, 4),
                    round(span, 4))
    return best


def best_analysis(label: str, analyses: Sequence[Dict[str, Any]]
                  ) -> Tuple[str, float]:
    """(analysis_event_id, token_coverage) of the analysis this node restates."""
    best_id, best_cov = "", 0.0
    for a in analyses:
        cov = token_coverage(label, a.get("analysis", ""))
        if cov > best_cov:
            best_id, best_cov = a.get("event_id", ""), cov
    return best_id, round(best_cov, 4)


# ------------------------------------------------------------------- decision
@dataclass
class TierDecision:
    case_id: str
    node_id: str
    label: str
    node_type: str
    source_class: str
    semantic_kind: str
    provenance_tier: str
    audit_label: str
    derivation: str
    best_record_id: str = ""
    best_record_hash: str = ""
    token_coverage: float = 0.0
    char_similarity: float = 0.0
    best_span_ratio: float = 0.0
    best_analysis_event: str = ""
    analysis_coverage: float = 0.0
    input_coverage: float = 0.0
    used_in_reasoning: bool = False
    usage: str = ""
    evidence_record: str = ""
    # S2.1 §1 D1. Two dimensions that must be reported *alongside* the tier,
    # because "P2" alone lets a downstream consumer treat an expert's own
    # restatement as if it were external evidence.
    #   externally_grounded -- the content appears in the case input or in a
    #     retrieval record. True only for P0.
    #   lineage_complete -- there is a concrete recorded antecedent for the
    #     node's *content*. True for case input, a bound record, and an expert
    #     ANALYSIS; False when the only provenance is "an agent asserted it".
    externally_grounded: bool = False
    lineage_complete: bool = False
    lineage_source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Which derivations have a concrete recorded antecedent for the node's
# *content* (as opposed to a recorded event that merely attests that an agent
# generated it). S2.1 §1 D1.
CONTENT_LINEAGE = {
    "case_input": "the ingest GRAPH_UPDATE",
    "record_verbatim": "the retrieval TOOL_CALL that returned the record",
    "record_paraphrase": "the retrieval TOOL_CALL that returned the record",
    "record_bound": "the retrieval TOOL_CALL that returned the record",
    "record_quote": "the retrieval TOOL_CALL that returned the record",
    "expert_synthesis": "the ANALYSIS event the node restates",
}

# Derivations whose only provenance is "an agent asserted it". The execution
# lineage is complete (the GRAPH_UPDATE exists) but no antecedent for the
# *content* does, and S3's Experience Gate must not read that as grounding.
SELF_DECLARED = {
    "absence_claim": "the node reports missing information",
    "recommendation": "the node is advice, not a finding",
    "domain_knowledge": "the node is the model's parametric knowledge",
    "agent_inference": "the node is an interpretation of other material",
    "hypothesis": "the node is a candidate the agent proposed",
    "input_inference": ("the node recombines case input in the agent's own "
                        "words; the input is external but this node is not "
                        "directly supported by it"),
}


def classify_tier(node: Dict[str, Any], records: Sequence[Dict[str, Any]],
                  analyses: Sequence[Dict[str, Any]], input_pool: str,
                  used_in_reasoning: bool, cfg: Optional[Dict[str, Any]] = None,
                  record_hashes: Optional[Dict[str, str]] = None
                  ) -> TierDecision:
    """Assign one node to a §4.1 tier.

    Cascade order is deliberate. Structural facts (this node *is* the case
    input; this node restates that recorded ANALYSIS) are checked before fuzzy
    similarity, so as few nodes as possible rest on a threshold.
    """
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    label = node.get("label", "")
    node_type = node.get("node_type", "Evidence")
    source_class = node.get("source_class", "UNKNOWN")
    kind = guess_semantic_kind(label, node_type)

    # All three similarity measures are reported against the *same* record --
    # the one the (coverage, span) pair picks -- otherwise the CSV columns
    # would describe three different comparisons.
    rec_id, best_text, cov, span = best_record(label, records)
    sim = char_similarity(label, best_text) if best_text else 0.0

    an_id, an_cov = best_analysis(label, analyses)
    in_cov = token_coverage(label, input_pool) if input_pool else 0.0

    evidence_record = node.get("evidence_record", "")
    tier, label_, derivation, detail = P3, LBL_ANOMALY, "none", ""

    if node_type == "Signal":
        tier, label_, derivation = P0, LBL_OK, "case_input"
    elif source_class == "INPUT_OBSERVATION":
        tier, label_, derivation = P0, LBL_OK, "case_input"
    elif node.get("match_tier") == "verbatim" or evidence_record:
        # Explicit binding recorded at ingestion time.
        tier, label_, derivation = P0, LBL_OK, "record_" + (
            node.get("match_tier") or "bound")
    elif node_type == "Hypothesis":
        tier, label_, derivation = P2, LBL_OK, "hypothesis"
    elif kind in ("absence_claim", "recommendation", "domain_knowledge",
                  "agent_inference"):
        tier, label_, derivation = P2, LBL_OK, kind
    elif an_cov >= cfg["expert_synthesis_min_coverage"]:
        tier, label_, derivation = P2, LBL_OK, "expert_synthesis"
    elif in_cov >= cfg["input_inference_min_coverage"]:
        tier, label_, derivation = P2, LBL_OK, "input_inference"
    elif cov >= cfg["direct_min_coverage"] or span >= cfg["direct_min_coverage"]:
        tier, label_, derivation = P0, LBL_OK, "record_quote"
    elif cov >= cfg["soft_min_coverage"] or sim >= cfg["soft_min_char_similarity"]:
        tier, label_, derivation = P1, LBL_REVIEW, "record_paraphrase"
    else:
        tier = P3
        label_ = LBL_ANOMALY if used_in_reasoning else LBL_UNUSED
        derivation = "none"
        detail = ("used in reasoning with no source and no legitimate "
                  "derivation" if used_in_reasoning else
                  "ungrounded but unused; not counted as an anomaly")

    # The two D1 dimensions, derived from the tier and derivation rather than
    # stored independently, so they cannot drift from them.
    externally_grounded = (tier == P0)
    lineage_complete = derivation in CONTENT_LINEAGE
    if derivation in CONTENT_LINEAGE:
        lineage_source = (an_id if derivation == "expert_synthesis"
                          else (rec_id if rec_id else "ingest GRAPH_UPDATE"))
    elif derivation in SELF_DECLARED:
        lineage_source = ""
    else:
        lineage_source = ""

    return TierDecision(
        case_id=node.get("case_id", ""), node_id=node.get("node_id", ""),
        label=label, node_type=node_type, source_class=source_class,
        semantic_kind=kind, provenance_tier=tier, audit_label=label_,
        derivation=derivation, best_record_id=rec_id,
        best_record_hash=(record_hashes or {}).get(rec_id, ""),
        token_coverage=cov, char_similarity=sim, best_span_ratio=span,
        best_analysis_event=an_id, analysis_coverage=an_cov,
        input_coverage=round(in_cov, 4), used_in_reasoning=used_in_reasoning,
        usage=detail, externally_grounded=externally_grounded,
        lineage_complete=lineage_complete, lineage_source=lineage_source,
    )


# ----------------------------------------------------------------- task audit
CSV_COLUMNS = [
    "case_id", "node_id", "label", "source_class", "semantic_kind",
    "best_record_id", "token_coverage", "char_similarity", "provenance_tier",
    "audit_label",
    # extras (the amendment asks for "at least" these; these make the tier
    # auditable and feed §7 path tracing)
    "node_type", "derivation", "best_span_ratio", "best_analysis_event",
    "analysis_coverage", "input_coverage", "used_in_reasoning", "usage",
    # S2.1 §1 D1
    "externally_grounded", "lineage_complete", "lineage_source",
]


def _edges_from_deltas(path: str) -> List[Tuple[str, str, str]]:
    """Final BeliefGraph edge set, rebuilt by replaying the delta chain.

    Edge types matter: §4.1 makes "used to support or refute" a precondition
    for P3, which cannot be decided from (src, dst) alone.
    """
    edges: Dict[Tuple[str, str], Tuple[str, str, str]] = {}
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            for e in d.get("deleted_edges") or []:
                edges.pop((e[0], e[1]), None)
            for e in d.get("added_edges") or []:
                t = e[2] if len(e) > 2 else ""
                edges[(e[0], e[1])] = (e[0], e[1], t)
    return list(edges.values())


def load_task(task_dir: str, task_id: str) -> Dict[str, Any]:
    """Read the canonical artifacts a task audit needs."""
    def p(suffix):
        return os.path.join(task_dir, f"{task_id}{suffix}")

    with open(p("_bindings.json"), "r", encoding="utf-8") as f:
        bindings = json.load(f)
    events: List[Dict[str, Any]] = []
    with open(p("_events.jsonl"), "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    records = records_from_events(events, bindings.get("records") or [])
    return {
        "task_id": task_id,
        "bindings": bindings.get("bindings") or [],
        "records": records,
        "events": events,
        "edges": _edges_from_deltas(p("_deltas.jsonl")),
    }


def audit_task(task_dir: str, task_id: str,
               cfg: Optional[Dict[str, Any]] = None) -> List[TierDecision]:
    """Tier every node of one task."""
    data = load_task(task_dir, task_id)
    records, events = data["records"], data["events"]

    analyses = [{"event_id": e["event_id"],
                 "analysis": (e.get("payload") or {}).get("analysis", "")}
                for e in events if e["event_type"] == "ANALYSIS"]
    # record_id -> integrity hash, taken from the completed retrieval event.
    record_hashes = {}
    for e in events:
        if e["event_type"] not in ("TOOL_CALL", "EVIDENCE_READ"):
            continue
        payload = e.get("payload") or {}
        if e["event_type"] == "TOOL_CALL" and "retrieval_output" not in payload:
            continue
        refs = e.get("output_refs") or []
        if refs:
            record_hashes[refs[0]] = payload.get("record_hash", "")
    inputs = [n["label"] for n in data["bindings"]
              if n.get("source_class") in ("INPUT_OBSERVATION",)
              or n.get("node_type") == "Signal"]
    input_pool = "\n".join(inputs)

    # A node is "used" when it carries a support/refute edge or appears on the
    # final-report path. Both are graph facts, not similarity judgements.
    used_src = {e[0] for e in data["edges"] if e[2] in ("support", "refute")}
    report = [e for e in events if e["event_type"] == "FINAL_REPORT"]
    on_report_path = set()
    for r in report:
        on_report_path.update(r.get("causal_node_refs") or [])
        on_report_path.update(r.get("output_refs") or [])

    out: List[TierDecision] = []
    for node in data["bindings"]:
        nid = node.get("node_id", "")
        used = nid in used_src or nid in on_report_path
        node = dict(node, case_id=data["task_id"])
        if node.get("source_class") == "RETRIEVED_RECORD":
            node = dict(node, evidence_record=node.get("matched_record_id") or "")
        out.append(classify_tier(node, records, analyses, input_pool, used, cfg,
                                 record_hashes))
    return out


def summarize(decisions: Sequence[TierDecision]) -> Dict[str, Any]:
    """Aggregate counts and the clean-trace false-positive rate (§4.2)."""
    by_tier: Dict[str, int] = {}
    by_label: Dict[str, int] = {}
    by_derivation: Dict[str, int] = {}
    evidence = [d for d in decisions if d.node_type == "Evidence"]
    for d in decisions:
        by_tier[d.provenance_tier] = by_tier.get(d.provenance_tier, 0) + 1
        by_label[d.audit_label] = by_label.get(d.audit_label, 0) + 1
        by_derivation[d.derivation] = by_derivation.get(d.derivation, 0) + 1
    flagged = [d for d in evidence if d.audit_label == LBL_ANOMALY]
    p3 = [d for d in evidence if d.provenance_tier == P3]
    # Two numbers, because they answer two different questions:
    #   p3_rate  -- how often the auditor refuses to source a clean node. This
    #               is the §4.2 clean-trace false-positive rate and the one the
    #               gate is stated against.
    #   fpr      -- the subset that is also *used*, i.e. the high-risk A1
    #               population the amendment singles out.
    # They coincide when nothing ungrounded is left dangling.
    return {
        "n_nodes": len(decisions),
        "n_evidence": len(evidence),
        "by_tier": by_tier,
        # S2.1 §1 D1: how much of the corpus is externally grounded, and how
        # much has a content lineage at all. Two numbers a downstream consumer
        # needs in order not to read P2 as P0.
        "externally_grounded": sum(1 for d in decisions if d.externally_grounded),
        "lineage_complete": sum(1 for d in decisions if d.lineage_complete),
        "not_externally_grounded_evidence": sum(
            1 for d in evidence if not d.externally_grounded),
        "by_audit_label": by_label,
        "by_derivation": by_derivation,
        "anomalies": len(flagged),
        "p3_total": len(p3),
        "p3_rate": round(len(p3) / len(evidence), 4) if evidence else 0.0,
        "fpr": round(len(flagged) / len(evidence), 4) if evidence else 0.0,
    }


def sensitivity(base_cfg: Optional[Dict[str, Any]] = None,
                grid: Optional[Dict[str, Sequence[float]]] = None
                ) -> List[Dict[str, Any]]:
    """Sweep the thresholds §4.2 requires to be reported.

    Returns one row per setting; the caller runs it over the Dev-Small tasks.
    """
    grid = grid or {
        "expert_synthesis_min_coverage": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        "input_inference_min_coverage": [0.5, 0.6, 0.7, 0.8, 0.9],
        "soft_min_coverage": [0.25, 0.35, 0.45, 0.55, 0.65],
        "direct_min_coverage": [0.75, 0.85, 0.95],
    }
    rows: List[Dict[str, Any]] = []
    for key, values in grid.items():
        for v in values:
            rows.append({"threshold": key, "value": v,
                         "cfg": {**DEFAULT_CFG, **(base_cfg or {}), key: v}})
    return rows


def write_csv(decisions: Sequence[TierDecision], path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for d in decisions:
            row = d.to_dict()
            w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})


def count_unavailable(label: str) -> bool:
    """True when the node reports missing information (an absence claim).

    Kept as a named helper because A1 injection flips it: a fabricated node
    asserts a *finding*, never an absence.
    """
    return guess_semantic_kind(label) == "absence_claim"
