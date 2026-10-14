"""S3B-C §6: the admission contract, as a decision procedure.

§6 asks for a **deterministic contract, not a trust score**. That is a design
constraint with teeth: every branch below is a boolean over artifacts that
already exist, and any reader can recompute the verdict from the trace. A
weighted score would be unfalsifiable — a candidate that got in because its
score was 0.71 could not be argued with.

The four outcomes, and what each is allowed to become:

    eligible            -> Validated Success, retrievable, injectable
    failure_experience  -> Validated Failure, retrievable, injectable
    manual_review       -> stored for a human, **never retrievable** (§6.3)
    reject              -> not stored

`manual_review` is emphatically not a soft yes. §6.3 says it must not enter the
retrieval index, and that is enforced in `memory_store`, not here.

Two rules are easy to get backwards and are called out in the code:

* **§6.1: an uncommitted decision call is normal.** Every expert decision is
  uncommitted by construction — the decision stage produces no event. Treating
  that as an incomplete invocation would reject every trace in the corpus. Only
  a call left `pending`, or one that `failed`, counts.
* **§6.1: P2 may be kept but never promoted.** The Gate does not rewrite a
  tier; it records the profile, and `memory_formatter` is the component that
  must not turn `externally_grounded=false` into a fact. There is a test for
  that, because it is the failure D1 was written to prevent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ELIGIBLE = "eligible"
FAILURE_EXPERIENCE = "failure_experience"
MANUAL_REVIEW = "manual_review"
REJECT = "reject"

BLOCKING_SEVERITIES = {"high", "critical"}


def _ledger_state(ledger_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pending = [r for r in ledger_rows if r["parse_status"] == "pending"]
    failed = [r for r in ledger_rows if r["parse_status"] == "failed"]
    discarded = [r for r in ledger_rows if r["parse_status"] == "discarded"]
    uncommitted = [r for r in ledger_rows if not r["committed_to_state"]]
    return {
        "n_calls": len(ledger_rows),
        "pending": len(pending),
        "failed": len(failed),
        "discarded": len(discarded),
        "uncommitted": len(uncommitted),
        # §6.1: normal uncommitted decisions do not count against completeness.
        "complete": not pending and not failed,
    }


def evaluate(candidate, audit: Dict[str, Any], ledger_rows: List[Dict[str, Any]],
             anomalies: List[Dict[str, Any]],
             integrity_ok: bool,
             semantic_complete: bool,
             forbidden_hits: Optional[List[str]] = None) -> Dict[str, Any]:
    """Return the eligibility verdict and the reasons for it.

    `candidate` is an `experience.Experience`; `audit` is the evidence-auditor
    summary; `anomalies` are the detector findings on the sealed trace.
    """
    reasons: List[str] = []
    led = _ledger_state(ledger_rows)

    used_p1 = [d for d in audit.get("decisions", [])
               if d.get("provenance_tier") == "P1_SOFT_GROUNDED"
               and d.get("used_in_reasoning")]
    used_p3 = [d for d in audit.get("decisions", [])
               if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
               and d.get("used_in_reasoning")]
    blocking = [a for a in anomalies
                if (a.get("severity") or "high") in BLOCKING_SEVERITIES]
    unlocalized_blocking = [a for a in blocking if not a.get("root_event_id")]

    # §5.2's "must never be stored" list is a hard reject, not a note. The
    # first version only *recorded* the hit and then fell through to the
    # success path, so a payload carrying a reference diagnosis would have
    # been admitted with a reason string attached -- a failure that reads like
    # a pass in any summary that only counts admissions.
    if forbidden_hits:
        reasons.append(f"forbidden content in the payload: {forbidden_hits}")
        return {"eligibility": REJECT, "reasons": reasons, "ledger": led}

    # ------------------------------------------------------------- §6.3 first
    # A candidate that fails a hard gate is not "reviewable"; it is out.
    if not led["complete"]:
        reasons.append(
            f"invocation ledger incomplete: {led['pending']} pending, "
            f"{led['failed']} failed")
    if not integrity_ok:
        reasons.append("trace integrity does not verify")
    if not semantic_complete:
        reasons.append("semantic trace incomplete: no FINAL_REPORT, or the "
                       "dependency DAG is broken")
    if unlocalized_blocking:
        reasons.append("high/critical anomaly with no localizable root")

    if not led["complete"] or not integrity_ok or not semantic_complete:
        return {"eligibility": REJECT, "reasons": reasons, "ledger": led}
    if unlocalized_blocking:
        return {"eligibility": REJECT, "reasons": reasons, "ledger": led}

    # ------------------------------------------------- safety-critical failures
    # A located high/critical anomaly remains a prevention experience, never a
    # successful collaboration strategy. Medium-severity issues can be kept
    # beside a verified success as a caution flag instead of making the whole
    # trace unreachable.
    if blocking:
        reasons.append(
            "localized high/critical anomaly root(s): "
            + ", ".join(f"{a.get('type')}/{a.get('detector_rule')}@"
                        f"{a.get('root_event_id')}" for a in blocking))
        return {"eligibility": FAILURE_EXPERIENCE, "reasons": reasons,
                "ledger": led}

    # ------------------------------------------------- §6.1 validated success
    if used_p3:
        reasons.append(
            f"{len(used_p3)} P3 node(s) used in reasoning are unresolved; "
            f"§6.1 forbids admitting them as a success")
        return {"eligibility": MANUAL_REVIEW, "reasons": reasons, "ledger": led}

    if used_p1:
        reasons.append(
            f"caveat: {len(used_p1)} P1 soft-grounded node(s) were used; "
            "the stored strategy must retain a provenance-check warning")

    verdict = candidate.outcome_verdict
    if verdict != "verified_success":
        reasons.append(
            f"outcome verdict is {verdict!r}; §6.1 requires verified_success, "
            f"and §4.2 does not admit score 1 or a divided judge")
        return {"eligibility": MANUAL_REVIEW, "reasons": reasons, "ledger": led}

    medium = [a for a in anomalies if a not in blocking]
    if medium:
        reasons.append(
            f"caveat: {len(medium)} medium-severity anomaly/anomalies retained "
            "as a strategy warning")
    reasons.append("outcome verified_success, integrity and invocation complete, "
                   "no high/critical anomaly, no P3 on the decision path")
    return {"eligibility": ELIGIBLE, "reasons": reasons, "ledger": led}


# ---------------------------------------------------------------- baselines
def baseline_naive(candidate, **_) -> Dict[str, Any]:
    """B0: admit everything."""
    return {"eligibility": ELIGIBLE, "reasons": ["naive: all candidates"],
            "ledger": {}}


def baseline_outcome_only(candidate, **_) -> Dict[str, Any]:
    """B1: the result was right, so the experience is good."""
    if candidate.outcome_verdict == "verified_success":
        return {"eligibility": ELIGIBLE,
                "reasons": ["outcome-only: verified_success"], "ledger": {}}
    return {"eligibility": REJECT,
            "reasons": [f"outcome-only: {candidate.outcome_verdict}"],
            "ledger": {}}


def baseline_audit_only(candidate, audit, ledger_rows, anomalies, **_) -> Dict[str, Any]:
    """B2: the process looked clean, so the experience is good.

    Ignores the outcome verdict entirely — which is the point of the
    comparison. A confidently-worded wrong answer from a clean process is
    admitted here and must not be admitted by the full contract.
    """
    led = _ledger_state(ledger_rows)
    if anomalies:
        return {"eligibility": REJECT,
                "reasons": ["audit-only: anomalies present"], "ledger": led}
    used_p3 = [d for d in audit.get("decisions", [])
               if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
               and d.get("used_in_reasoning")]
    if used_p3:
        return {"eligibility": MANUAL_REVIEW,
                "reasons": ["audit-only: unresolved P3 on the decision path"],
                "ledger": led}
    if not led["complete"]:
        return {"eligibility": REJECT,
                "reasons": ["audit-only: incomplete ledger"], "ledger": led}
    return {"eligibility": ELIGIBLE,
            "reasons": ["audit-only: clean process (outcome not consulted)"],
            "ledger": led}


def baseline_integrity_only(candidate, integrity_ok=True, **_) -> Dict[str, Any]:
    """B3 (optional): only the hash chain is checked."""
    if integrity_ok:
        return {"eligibility": ELIGIBLE,
                "reasons": ["integrity-only: chain verifies"], "ledger": {}}
    return {"eligibility": REJECT,
            "reasons": ["integrity-only: chain broken"], "ledger": {}}


BASELINES = {
    "B0_Naive": baseline_naive,
    "B1_OutcomeOnly": baseline_outcome_only,
    "B2_AuditOnly": baseline_audit_only,
    "B3_IntegrityOnly": baseline_integrity_only,
}
