"""Execution-side anomaly detection: A0 (silent degradation) and A4
(corrupted intermediate analysis), plus the §3.2 DAG consistency check.

A0 has two faces and both are checked:

* the pipeline says so (`status=degraded`). This is what §3.1 D1 added.
* the pipeline does not (`status=success`) but returned upstream's fixed
  fallback text. This is the *silent* degradation that motivated D1, and it is
  still detectable after the fact.

A4 is the hard one. An expert's ANALYSIS legitimately ranges over the whole
graph state it can see, so "this sentence is not in the retrieved records" is
not evidence of anything -- measured on Dev-Small, per-sentence coverage flags
~74% of clean analyses. What *is* discriminative is the **quantity**: a
specific measurement ("11%", "32 mmHg", "42 ng/mL") either appears somewhere in
the material the expert had, or it was invented. On the ten clean Dev-Small
tasks, exactly one of ~30 numeric claims is ungrounded and it is a
language-mismatch artefact, so the rule has room to be strict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .cross_graph_binding import (content_tokens, guess_semantic_kind,
                                  norm, token_coverage)
from .injector import UPSTREAM_FALLBACK

CJK_RE = re.compile(r"[㐀-䶿一-鿿]")

# Quantities carry the falsifiable content of a clinical claim. CJK units are
# mapped so a Chinese analysis is compared on the same footing as an English
# one -- the numbers are language-independent even when the words are not.
_UNIT_ALIASES = {
    "次/分": "bpm", "毫米汞柱": "mmhg", "毫克": "mg", "毫升": "ml",
    "天": "days", "日": "days", "周": "weeks", "月": "months", "年": "years",
    "岁": "years", "小时": "hours",
}
_QTY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(%|mmHg|mmhg|mg/dL|mg|mL|Gy|ng/mL|ng|mmol/L|mmol|"
    r"g/dL|bpm|mm|cm|kg|hours|days|weeks|months|years|次/分|毫米汞柱|毫克|"
    r"毫升|天|日|周|月|年|岁|小时)",
    re.I)


# A bare decimal is a measurement too: "a standardized uptake value of 7.4"
# carries no unit and is still unambiguously a number the model had to get from
# somewhere. Two exclusions, both earned on clean Dev-Small data:
#   * bare integers -- counts and section numbers as often as findings;
#   * decimals <= 1 -- those are the model's own confidence scores, which it
#     writes inline as "(leading hypothesis, 0.45)" on several cases.
_BARE_DECIMAL_RE = re.compile(r"(?<![\d.])\d{1,3}[.,]\d+")

_MODALITY_RE = re.compile(
    r"\b(biopsy|histopatholog\w*|immunohistochem\w*|immunofluorescence|"
    r"microscopy|flow cytometr\w*|cytolog\w*|culture|serolog\w*|"
    r"tomograph\w*|ultrasound|echocardiograph\w*|radiograph\w*|endoscop\w*|"
    r"electrocardiogram|imaging|pet-?ct|mri|ct|assay|staining|tests?|panel|"
    r"screening)\b", re.I)
# Past tense only. A *reported result* is past tense ("the biopsy showed",
# "flow cytometry identified"); a *conclusion* is present tense ("findings
# sufficiently confirm the diagnosis"). Including the present-tense forms made
# the rule fire on the latter, which is interpretation, not a finding claim.
_ASSERT_RE = re.compile(
    r"\b(show(?:ed|n)|demonstrat(?:ed)|reveal(?:ed)|confirm(?:ed)|"
    r"identif(?:ied)|detect(?:ed)|found|document(?:ed)|"
    r"was positive|were positive)\b", re.I)

# Clauses that *interpret* rather than *report* are not factual assertions and
# must not be judged as if they were.
_INTERPRETIVE_KINDS = ("absence_claim", "recommendation", "agent_inference")


def quantities(text: str) -> List[str]:
    """Normalised measurement strings: ("42", "ng/mL") -> "42ng/ml"."""
    text = str(text or "")
    out = []
    for num, unit in _QTY_RE.findall(text):
        unit = _UNIT_ALIASES.get(unit, unit).lower()
        out.append(f"{num.replace(' ', '')}{unit}")
    if not out:
        out = [v for v in (m.group(0).replace(",", ".")
                           for m in _BARE_DECIMAL_RE.finditer(text))
               if float(v) > 1.0]
    return out


def claim_clauses(text: str, min_chars: int = 12) -> List[str]:
    """Split an analysis into clauses, without cutting inside parentheses.

    Naive splitting on ";" breaks "t(11;14)", which produced three phantom
    ungrounded claims on a clean task during development.
    """
    parts: List[str] = []
    depth = 0
    cur: List[str] = []
    for ch in str(text or ""):
        if ch in "([（":
            depth += 1
        elif ch in ")]）":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;.。；，":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if len(p.strip()) >= min_chars]


@dataclass
class ExecutionFinding:
    rule: str
    event_id: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "event_id": self.event_id, "detail": self.detail}


# ------------------------------------------------------------------ context
def analysis_pool(event: Dict[str, Any], events: Sequence[Dict[str, Any]],
                  bindings: Sequence[Dict[str, Any]],
                  records: Sequence[Dict[str, Any]]) -> str:
    """Everything the expert could legitimately have known at that step.

    The analysis prompt is built from the belief text and the retrieval
    history, so the pool is: every graph node created up to and including this
    step, plus the records this expert read. Using only its own `input_refs`
    would flag facts it learned from the graph as fabrications.
    """
    step = event.get("step_id", 0)
    labels = [n.get("label", "") for n in bindings
              if n.get("created_at_step", 0) <= step]
    rec_by_id = {r.get("record_id"): r.get("text", "") for r in records}
    own = [rec_by_id[r] for r in (event.get("input_refs") or []) if r in rec_by_id]
    return "\n".join(labels + own)


# ---------------------------------------------------------------- detectors
FALLBACK_SIGNATURES = (
    "insufficient information to complete the analysis",
    "max retrieval steps reached",
)


def looks_like_fallback(text: str) -> bool:
    n = norm(text)
    return any(sig in n for sig in FALLBACK_SIGNATURES)


def detect_a0(events: Sequence[Dict[str, Any]]) -> List[ExecutionFinding]:
    out = []
    for e in events:
        if e.get("event_type") != "ANALYSIS":
            continue
        text = (e.get("payload") or {}).get("analysis", "")
        status = e.get("status", "success")
        if status == "degraded":
            out.append(ExecutionFinding(
                rule="degraded_status", event_id=e["event_id"],
                detail={"reason_code": e.get("reason_code", ""),
                        "exit_cause": (e.get("payload") or {}).get("exit_cause", ""),
                        "declared": True}))
        elif status == "success" and looks_like_fallback(text):
            # The §3.1 D1 silent-degradation path: the run completed on a
            # fallback but nothing in the trace admits it.
            out.append(ExecutionFinding(
                rule="silent_fallback_text", event_id=e["event_id"],
                detail={"declared": False,
                        "reason_code": e.get("reason_code", "NONE"),
                        "matched_signature": next(
                            s for s in FALLBACK_SIGNATURES if s in norm(text))}))
    return out


def detect_a4(events: Sequence[Dict[str, Any]],
              bindings: Sequence[Dict[str, Any]],
              records: Sequence[Dict[str, Any]],
              *, min_ungrounded_quantities: int = 1,
              min_finding_coverage: float = 0.55
              ) -> List[ExecutionFinding]:
    """An ANALYSIS that asserts a measurement nothing in its reach supports."""
    out = []
    for e in events:
        if e.get("event_type") != "ANALYSIS":
            continue
        text = (e.get("payload") or {}).get("analysis", "")
        if e.get("status") == "degraded" or looks_like_fallback(text):
            continue           # A0, not A4
        pool = analysis_pool(e, events, bindings, records)
        known = set(quantities(pool))
        ungrounded: List[Dict[str, Any]] = []
        for clause in claim_clauses(text):
            qs = quantities(clause)
            missing = [q for q in qs if q not in known]
            if not missing:
                continue
            ungrounded.append({
                "clause": clause[:200],
                "quantities": qs,
                "missing": missing,
                "token_coverage": round(token_coverage(clause, pool), 4),
                "non_english": bool(CJK_RE.search(clause)),
            })
        if len(ungrounded) >= min_ungrounded_quantities:
            out.append(ExecutionFinding(
                rule="ungrounded_analysis_quantity", event_id=e["event_id"],
                detail={"actor_id": e.get("actor_id", ""),
                        "n_ungrounded": len(ungrounded),
                        "claims": ungrounded[:5],
                        "pool_quantities": len(known)}))

        # A fabricated finding need not carry a number: "immunofluorescence
        # microscopy demonstrated linear IgA deposition" is a specific
        # test-attributed result with nothing to retrieve. Measured over 344
        # clean clauses, this rule fires once and that one is an interpretation
        # ("confirm the diagnosis"), which `_INTERPRETIVE_KINDS` removes.
        findings: List[Dict[str, Any]] = []
        for clause in claim_clauses(text):
            if not (_MODALITY_RE.search(clause) and _ASSERT_RE.search(clause)):
                continue
            if guess_semantic_kind(clause) in _INTERPRETIVE_KINDS:
                continue
            cov = token_coverage(clause, pool)
            if cov < min_finding_coverage:
                findings.append({"clause": clause[:200],
                                 "token_coverage": round(cov, 4),
                                 "non_english": bool(CJK_RE.search(clause))})
        if findings:
            out.append(ExecutionFinding(
                rule="ungrounded_analysis_finding", event_id=e["event_id"],
                detail={"actor_id": e.get("actor_id", ""),
                        "n_ungrounded": len(findings),
                        "claims": findings[:5]}))
    return out


def detect_dag_issues(events: Sequence[Dict[str, Any]],
                      task_id: str = "") -> List[ExecutionFinding]:
    """Feed the §3.2 consistency report into the anomaly stream."""
    from .dependency_graph import check
    _dag, rep = check(events, task_id=task_id)
    return [ExecutionFinding(rule=f"dag_{i.kind}", event_id=i.event_id,
                             detail={"severity": i.severity, "detail": i.detail})
            for i in rep.issues if i.severity == "error"]


def audit_execution(events: Sequence[Dict[str, Any]],
                    bindings: Sequence[Dict[str, Any]],
                    records: Sequence[Dict[str, Any]],
                    task_id: str = "") -> Dict[str, List[ExecutionFinding]]:
    return {
        "A0": detect_a0(events),
        "A4": detect_a4(events, bindings, records),
        "dag": detect_dag_issues(events, task_id),
    }
