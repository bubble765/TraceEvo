"""S4B-B §7-§8: Audit-Triggered Grounding Guard (ATGG).

One rule, applied between parsing a graph-update proposal and committing it:

    candidate node type = Evidence
    AND provenance_tier = P3_UNSUPPORTED_ASSERTION
    AND the candidate proposes a SUPPORT or REFUTE edge for it

On trigger the guard asks the central agent once — `max_retry = 1` — to restate
the update using only things it can source. Whatever comes back is reclassified
by the same rule. If the node is no longer a decision-path P3 the revised delta
is committed; if it still is, the node and its support/refute edge are dropped
and the rest is committed; if the retry does not parse, the original delta is
committed minus the offending elements.

Three things this module deliberately does not do:

* **It does not re-tier anything.** `precommit_tiers` calls
  `evidence_auditor.classify_tier` — the frozen classifier, same arguments the
  post-hoc audit passes — so the guard cannot disagree with the audit about
  what P3 means. A guard with its own notion of "unsupported" would be a second
  rule set masquerading as an enforcement of the first.
* **It does not rewrite content.** §8.2: no turning an assertion into a medical
  fact, no inventing a source. The two outcomes are "the model re-sources it"
  and "it does not enter the graph".
* **It does not touch the ledger.** §7: the original response is recorded
  before the guard sees it, and the guard's own call is recorded as a separate
  entry. The raw candidate is preserved in the provenance record whether the
  outcome is RESOLVED, BLOCKED or RETRY_FAILED.

`evaluate` is pure: it classifies and decides, and returns a verdict. The
impure parts — the retry call and the event emission — live in `hooks.py`, so
the decision rule can be tested without a model.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

G_P3_SOURCE = "G_P3_SOURCE"
G_P1_SOFT = "G_P1_SOFT"
G_ROLE_RISK = "G_ROLE_RISK"

TRIGGER_RULE = ("candidate node type = Evidence AND provenance_tier = "
                "P3_UNSUPPORTED_ASSERTION AND the candidate proposes a "
                "SUPPORT or REFUTE edge for it")

RESOLVED = "RESOLVED"
BLOCKED = "BLOCKED"
RETRY_FAILED = "RETRY_FAILED"
NO_TRIGGER = "NO_TRIGGER"

MAX_RETRY = 1

# §8.1's correction, verbatim. Its hash goes in the policy manifest, so editing
# this string is visible as a policy change rather than as a prompt tweak.
RETRY_PROMPT = (
    "The proposed Evidence item cannot be traced to the patient input or any "
    "retrieved record.\n\n"
    "Do not treat it as clinical evidence unless a source can be identified.\n\n"
    "Revise this graph update using only:\n"
    "1. information present in the patient input;\n"
    "2. records actually retrieved by expert agents;\n"
    "3. explicitly labeled derived reasoning that is NOT used as external "
    "evidence.\n\n"
    "Return a corrected graph update."
)

SUPPORT_REFUTE = ("support", "refute")


def sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def candidate_hash(response_dict: Dict[str, Any]) -> str:
    """A stable digest of the proposal the guard judged.

    Sorted keys, so the same proposal hashes the same regardless of the order
    the model happened to emit its JSON in.
    """
    return sha_text(json.dumps(response_dict, sort_keys=True,
                               ensure_ascii=False))


# ------------------------------------------------------------------ §7 classify
def precommit_tiers(response_dict: Dict[str, Any],
                    records: Sequence[Dict[str, Any]],
                    analyses: Sequence[Dict[str, Any]],
                    input_pool: str,
                    cfg: Optional[Dict[str, Any]] = None
                    ) -> List[Dict[str, Any]]:
    """Tier each candidate Evidence node using the frozen classifier.

    The candidate is shaped into the same binding-like dict `audit_task`
    builds, so `classify_tier` sees what it would see post hoc. Two fields
    differ and both are recorded rather than glossed:

    * `source_class` comes from `cross_graph_binding.classify_evidence`, which
      is what the binder would assign on commit.
    * `used_in_reasoning` is passed as False. It is a graph fact that does not
      exist yet at pre-commit time, and the §8 trigger does not use it -- the
      trigger uses the *proposed* edge instead. Passing True would make every
      candidate P3 look like an anomaly regardless of its edges.
    """
    from traceevo.audit.cross_graph_binding import classify_evidence
    from traceevo.audit.evidence_auditor import classify_tier

    # The two frozen classifiers disagree about what a record *is*:
    # `classify_evidence` takes `RetrievalRecord` objects (it attributes them to
    # a binder) and `classify_tier` takes dicts (it reads them back out of
    # `_bindings.json`). The runtime holds objects and the post-hoc audit holds
    # dicts, so reusing both means converting between them here. Passing the
    # objects straight to `classify_tier` raises `AttributeError` rather than
    # silently mis-tiering, which is why this was found by running it -- but a
    # mis-shaped record that happened to have a `.get` would have tiered every
    # candidate P3 and the guard would have fired on everything.
    records_as_dicts = [
        r if isinstance(r, dict) else {
            "record_id": r.record_id,
            "retrieval_event_id": r.retrieval_event_id,
            "query": r.query, "text": r.text, "step_id": r.step_id,
            "expert": r.expert, "task_id": r.task_id,
            "record_hash": r.record_hash}
        for r in records]

    node_ids = {n.get("id") for n in (response_dict.get("nodes") or [])}
    # edges may reference ids that the model invented and never declared; those
    # are handled by the caller, which maps temp ids before applying
    edges = response_dict.get("edges") or []
    out = []
    for node in (response_dict.get("nodes") or []):
        if node.get("node_type") != "Evidence":
            continue
        label = node.get("label", "")
        src, tier_match, rec, cov = classify_evidence(label, records, cfg)
        shaped = {
            "node_id": node.get("id", ""),
            "node_type": "Evidence",
            "label": label,
            "source_class": src,
            "match_tier": tier_match,
            "matched_record_id": rec.record_id if rec else "",
            "case_id": "",
        }
        if src == "RETRIEVED_RECORD":
            shaped["evidence_record"] = shaped["matched_record_id"]
        d = classify_tier(shaped, records_as_dicts, analyses, input_pool,
                          False, cfg)
        out.append({
            "candidate_id": node.get("id", ""),
            "label": label,
            "source_class": src,
            "match_tier": tier_match,
            "provenance_tier": d.provenance_tier,
            "derivation": d.derivation,
            "token_coverage": d.token_coverage,
            "char_similarity": d.char_similarity,
            "best_analysis_event": d.best_analysis_event,
            "input_coverage": d.input_coverage,
            "in_response_nodes": node.get("id") in node_ids,
        })
    return out


def proposed_edges(response_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The SUPPORT/REFUTE edges the proposal intends, with their endpoints."""
    return [e for e in (response_dict.get("edges") or [])
            if str(e.get("relation", "")).lower() in SUPPORT_REFUTE]


def evaluate(response_dict: Dict[str, Any],
             records: Sequence[Dict[str, Any]],
             analyses: Sequence[Dict[str, Any]],
             input_pool: str,
             cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§8's trigger, and nothing else. Pure; no model, no I/O."""
    tiers = precommit_tiers(response_dict, records, analyses, input_pool, cfg)
    edges = proposed_edges(response_dict)
    edge_srcs = {e.get("src") for e in edges}

    offenders = [t for t in tiers
                 if t["provenance_tier"] == "P3_UNSUPPORTED_ASSERTION"
                 and t["candidate_id"] in edge_srcs]
    return {
        "trigger": bool(offenders),
        "guard_id": G_P3_SOURCE,
        "trigger_rule": TRIGGER_RULE,
        "n_candidate_evidence": len(tiers),
        "n_support_refute_edges": len(edges),
        "offenders": offenders,
        "tiers": tiers,
        "candidate_hash": candidate_hash(response_dict),
    }


# -------------------------------------------------------------- §8.2 outcomes
def strip_offenders(response_dict: Dict[str, Any],
                    offenders: Sequence[Dict[str, Any]]
                    ) -> Tuple[Dict[str, Any], List[str]]:
    """§8.2's BLOCKED / RETRY_FAILED action: drop the node and its edges.

    Only the offending Evidence node and the SUPPORT/REFUTE edges it is the
    source of are removed. Everything else in the proposal commits unchanged --
    a guard that discarded the whole delta would be a much larger intervention
    than the one §8 authorises, and would make the arms incomparable.
    """
    bad = {o["candidate_id"] for o in offenders}
    revised = dict(response_dict)
    kept_nodes, dropped = [], []
    for n in (response_dict.get("nodes") or []):
        if n.get("id") in bad:
            dropped.append(f"node:{n.get('id')}")
        else:
            kept_nodes.append(n)
    kept_edges = []
    for e in (response_dict.get("edges") or []):
        if str(e.get("relation", "")).lower() in SUPPORT_REFUTE \
                and e.get("src") in bad:
            dropped.append(f"edge:{e.get('src')}->{e.get('dst')}")
        else:
            kept_edges.append(e)
    revised["nodes"] = kept_nodes
    revised["edges"] = kept_edges
    return revised, dropped


def action_record(decision: Dict[str, Any], result: str,
                  retry_prompt: str = "", retry_response: str = "",
                  dropped: Optional[List[str]] = None,
                  retry_decision: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """§8.1's required provenance for one guard intervention."""
    rec = {
        "guard_id": decision.get("guard_id", G_P3_SOURCE),
        "guard_result": result,
        "original_candidate_hash": decision.get("candidate_hash", ""),
        "trigger_rule": decision.get("trigger_rule", TRIGGER_RULE),
        "offending_nodes": [o["candidate_id"] for o in decision["offenders"]],
        "offending_labels": [o["label"] for o in decision["offenders"]],
        "offending_tiers": [o["provenance_tier"] for o in decision["offenders"]],
        "max_retry": MAX_RETRY,
        "dropped": dropped or [],
    }
    if retry_prompt:
        rec["retry_prompt_hash"] = sha_text(retry_prompt)
    if retry_response:
        rec["retry_response_hash"] = sha_text(retry_response)
    if retry_decision is not None:
        rec["retry_candidate_hash"] = retry_decision.get("candidate_hash", "")
        rec["retry_still_p3"] = retry_decision.get("trigger", None)
    return rec


def apply_decision(original: Dict[str, Any], decision: Dict[str, Any],
                   retry_dict: Optional[Dict[str, Any]],
                   retry_decision: Optional[Dict[str, Any]]
                   ) -> Tuple[Dict[str, Any], str, List[str]]:
    """Which delta gets committed, and under which §8.2 label.

        retry parsed and clean        -> retry delta,  RESOLVED
        retry parsed and still P3     -> stripped origin, BLOCKED
        retry unparseable             -> stripped origin, RETRY_FAILED
    """
    if retry_dict is not None and retry_decision is not None \
            and not retry_decision["trigger"]:
        return retry_dict, RESOLVED, []
    stripped, dropped = strip_offenders(original, decision["offenders"])
    result = BLOCKED if retry_dict is not None else RETRY_FAILED
    return stripped, result, dropped
