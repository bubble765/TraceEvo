"""Offline coverage analysis for audit-conditioned diagnostic recovery.

This deliberately reads only already-produced S5B summary CSVs. It never
loads reference diagnoses or patient narrative text into a repair prompt; the
output matrix contains case ids and aggregate evaluation signals only.
S5B is an exposed development cohort here, not a fresh test set.

Usage:
    python3 traceevo/recovery_analysis.py
    python3 traceevo/recovery_analysis.py --diagnostic ... --out ...
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIAGNOSTIC = ROOT / "evaluations" / "diagnosis_final" / "diagnostic_metrics.csv"
DEFAULT_OUTCOMES = ROOT / "evaluations" / "diagnosis_final" / "outcome_results.csv"
DEFAULT_AUDIT = ROOT / "evaluations" / "diagnosis_final" / "natural_audit_summary.csv"
DEFAULT_OUTPUT = ROOT / "refine-logs" / "results" / "R000_baseline_opportunity"
PROCESS_TYPES = {"A2", "A3", "A4", "A5"}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _index_unique(rows: Iterable[Dict[str, str]], key: str,
                  source: str) -> Dict[int, Dict[str, str]]:
    out: Dict[int, Dict[str, str]] = {}
    for row in rows:
        if key not in row or not row[key]:
            raise ValueError(f"{source}: missing required key {key!r}")
        case_id = int(row[key])
        if case_id in out:
            raise ValueError(f"{source}: duplicate case id {case_id}")
        out[case_id] = row
    return out


def feedback_channel(audit: Dict[str, str]) -> Dict[str, Any]:
    """Assign an exclusive *opportunity* bucket, not a clinical root cause."""
    types = {t for t in (audit.get("anomaly_types") or "").split("|") if t}
    p3_used = int(audit.get("p3_used") or 0)
    evidence = p3_used > 0 or "A1" in types
    process = bool(types & PROCESS_TYPES)
    if evidence and process:
        channel = "evidence_and_process_signal"
    elif evidence:
        channel = "evidence_signal"
    elif process:
        channel = "posthoc_process_signal"
    else:
        channel = "audit_clean_no_feedback_signal"
    return {"channel": channel,
            "evidence_signal": evidence,
            "posthoc_process_signal": process,
            "anomaly_types": sorted(types),
            "p3_used": p3_used}


def build_case_matrix(diagnostic_rows: List[Dict[str, str]],
                      outcome_rows: List[Dict[str, str]],
                      audit_rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    diagnostic = _index_unique(diagnostic_rows, "case", "diagnostic")
    outcomes = _index_unique(outcome_rows, "case", "outcomes")
    audits = _index_unique(audit_rows, "case", "audit")
    case_sets = {"diagnostic": set(diagnostic), "outcomes": set(outcomes),
                 "audit": set(audits)}
    if not (case_sets["diagnostic"] == case_sets["outcomes"] == case_sets["audit"]):
        raise ValueError(
            "case-id mismatch across files: "
            + ", ".join(f"{name}={sorted(ids)}" for name, ids in case_sets.items()))

    matrix: List[Dict[str, Any]] = []
    for case_id in sorted(diagnostic):
        d, o, a = diagnostic[case_id], outcomes[case_id], audits[case_id]
        score = int(d["judge_score"])
        outcome = o["outcome_verdict"]
        signals = feedback_channel(a)
        matrix.append({
            "case": case_id,
            "gos_match_score": score,
            "gos_exact_match": score == 2,
            "gos_relevant": score >= 1,
            "outcome_verdict": outcome,
            "feedback_channel": signals["channel"],
            "evidence_signal": signals["evidence_signal"],
            "posthoc_process_signal": signals["posthoc_process_signal"],
            "audit_alerts": int(a.get("audit_alerts") or 0),
            "anomaly_types": "|".join(signals["anomaly_types"]),
            "p3_used": signals["p3_used"],
            "oracle_selected_failure": outcome == "verified_incorrect",
        })
    return matrix


def summarize(matrix: List[Dict[str, Any]]) -> Dict[str, Any]:
    outcomes = Counter(r["outcome_verdict"] for r in matrix)
    scores = Counter(str(r["gos_match_score"]) for r in matrix)
    channels = Counter(r["feedback_channel"] for r in matrix)
    outcome_by_channel: Dict[str, Dict[str, int]] = defaultdict(Counter)
    score_by_channel: Dict[str, Dict[str, int]] = defaultdict(Counter)
    for row in matrix:
        outcome_by_channel[row["feedback_channel"]][row["outcome_verdict"]] += 1
        score_by_channel[row["feedback_channel"]][str(row["gos_match_score"])] += 1

    errors = [r for r in matrix if r["outcome_verdict"] == "verified_incorrect"]
    error_channels = Counter(r["feedback_channel"] for r in errors)
    return {
        "cohort": "S5B cases 80-137; exposed development/exploratory only",
        "n_cases": len(matrix),
        "gos_match_score_counts": dict(sorted(scores.items())),
        "outcome_counts": dict(sorted(outcomes.items())),
        "feedback_channel_counts": dict(sorted(channels.items())),
        "outcomes_by_feedback_channel": {
            channel: dict(sorted(counts.items()))
            for channel, counts in sorted(outcome_by_channel.items())},
        "match_scores_by_feedback_channel": {
            channel: dict(sorted(counts.items()))
            for channel, counts in sorted(score_by_channel.items())},
        "verified_incorrect_by_feedback_channel": dict(sorted(error_channels.items())),
        "verified_incorrect_without_audit_signal": error_channels.get(
            "audit_clean_no_feedback_signal", 0),
        "metric_warning": (
            "GoS Match/Relevant and OutcomeVerifier verdicts are separate "
            "instruments. These buckets measure audit-signal coverage, not "
            "whether an anomaly caused a diagnosis error."),
        "repair_selection_warning": (
            "oracle_selected_failure is for exploratory conditional-recovery "
            "analysis only; do not expose the label or reference to a repair model."),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def run(diagnostic_path: Path, outcome_path: Path, audit_path: Path,
        output_dir: Path) -> Dict[str, Any]:
    matrix = build_case_matrix(_read_csv(diagnostic_path),
                               _read_csv(outcome_path),
                               _read_csv(audit_path))
    summary = summarize(matrix)
    summary["input_sha256"] = {
        "diagnostic_metrics.csv": _sha256(diagnostic_path),
        "outcome_results.csv": _sha256(outcome_path),
        "natural_audit_summary.csv": _sha256(audit_path),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    matrix_path = output_dir / "case_matrix.csv"
    with matrix_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(matrix[0]) if matrix else []
        writer = csv.DictWriter(f, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(matrix)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", type=Path, default=DEFAULT_DIAGNOSTIC)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOMES)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run(args.diagnostic, args.outcomes, args.audit, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
