"""Offline test of whether sealed original graph updates met the frozen P3 guard rule.

This script reads only blinded execution traces, binding records and the
already-computed Match=0/evidence/P3 selection matrix. It does not read gold,
call a model, or modify the sealed traces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from traceevo import config as tx_config
from traceevo.audit.cross_graph_binding import (
    RetrievalRecord,
    is_retrieval_result_event,
    records_from_events,
)
from traceevo.evolution import grounding_guard
from traceevo.live_guard_recovery import (
    CASE_MATRIX,
    ROOT,
    S5B,
    eligible_cases,
)


TRACE_ROOT = (S5B / "heldout_original" / "traceevo" / "runs" /
              "core_v1_final")
DEFAULT_OUT = ROOT / "refine-logs" / "results" / "R003_live_p3_guard_pilot" / \
    "original_trace_trigger_audit.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_response(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("original graph-update event has no JSON object")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("graph-update response must be a JSON object")
    return parsed


def audit_case(case_id: int, cfg: Dict[str, Any]) -> Dict[str, Any]:
    task = f"case-{case_id}"
    events_path = TRACE_ROOT / f"{task}_events.jsonl"
    bindings_path = TRACE_ROOT / f"{task}_bindings.json"
    events = [json.loads(line) for line in events_path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    binding_data = json.loads(bindings_path.read_text(encoding="utf-8"))
    all_records = [RetrievalRecord(**row) for row in records_from_events(
        events, binding_data.get("records") or [])]
    bindings = binding_data.get("bindings") or []
    input_pool = "\n".join(
        binding.get("label", "") for binding in bindings
        if binding.get("source_class") == "INPUT_OBSERVATION"
        or binding.get("node_type") == "Signal")

    updates = []
    for index, event in enumerate(events):
        payload = event.get("payload") or {}
        if (event.get("event_type") != "GRAPH_UPDATE"
                or payload.get("phase") != "update_graph"):
            continue
        prefix = events[:index]
        prior_read_ids = {row.get("event_id") for row in prefix
                          if is_retrieval_result_event(row)}
        records = [record for record in all_records
                   if record.retrieval_event_id in prior_read_ids]
        analyses = [{
            "event_id": row.get("event_id", ""),
            "analysis": (row.get("payload") or {}).get("analysis", ""),
        } for row in prefix if row.get("event_type") == "ANALYSIS"]
        candidate = _parse_response(str(payload.get("response") or ""))
        decision = grounding_guard.evaluate(
            candidate, records, analyses, input_pool, cfg)
        updates.append({
            "event_id": event.get("event_id"),
            "step_id": event.get("step_id"),
            "trigger": bool(decision["trigger"]),
            "n_candidate_evidence": decision["n_candidate_evidence"],
            "n_p3_candidate_evidence": sum(
                tier.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION"
                for tier in decision["tiers"]),
            "n_guard_offenders": len(decision["offenders"]),
            "n_support_refute_edges": decision["n_support_refute_edges"],
            "candidate_sha256": decision["candidate_hash"],
        })

    return {
        "case": case_id,
        "n_original_update_graph_events": len(updates),
        "n_exact_rule_triggers": sum(bool(row["trigger"]) for row in updates),
        "updates": updates,
        "events_sha256": _sha(events_path),
        "bindings_sha256": _sha(bindings_path),
    }


def run(case_ids: Sequence[int], output_path: Path) -> Dict[str, Any]:
    eligible = eligible_cases()
    invalid = [case for case in case_ids if case not in eligible]
    if invalid:
        raise ValueError(f"cases outside original Match=0/evidence/P3 subset: {invalid}")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite {output_path}")

    cfg = tx_config.load(force_reload=True).get("source_classification", {})
    rows = [audit_case(case, cfg) for case in case_ids]
    total = sum(row["n_exact_rule_triggers"] for row in rows)
    result = {
        "status": "OFFLINE_RULE_AUDIT_COMPLETE",
        "cohort_status": "exposed S5B traces; offline counterfactual only",
        "selection": "original frozen GoS Match=0 AND evidence_signal=true AND P3-used>0",
        "cases": list(case_ids),
        "n_cases_with_at_least_one_exact_trigger": sum(
            row["n_exact_rule_triggers"] > 0 for row in rows),
        "n_exact_rule_triggers_total": total,
        "case_results": rows,
        "interpretation": (
            "This establishes which sealed proposals would have triggered the "
            "frozen pre-commit rule. It does not simulate the retry response, "
            "downstream reasoning, or diagnostic outcome."),
        "source_sha256": {
            str(CASE_MATRIX.relative_to(ROOT)): _sha(CASE_MATRIX),
            str((ROOT / "gos" / "configs" / "TraceEvo_Configs.yaml").relative_to(ROOT)):
                _sha(ROOT / "gos" / "configs" / "TraceEvo_Configs.yaml"),
            str((ROOT / "traceevo" / "evolution" / "grounding_guard.py").relative_to(ROOT)):
                _sha(ROOT / "traceevo" / "evolution" / "grounding_guard.py"),
        },
        "gold_access": "none",
        "model_calls": 0,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=",".join(map(str, sorted(eligible_cases()))))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    case_ids = [int(part.strip()) for part in args.cases.split(",") if part.strip()]
    result = run(case_ids, args.out)
    print(json.dumps({k: v for k, v in result.items() if k != "case_results"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
