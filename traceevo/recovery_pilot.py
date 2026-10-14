"""Reference-blind, one-call repair pilot for already-recorded GoS traces.

This generation script intentionally does NOT read outcome_results.csv or the
gold-bearing DiagnosisArena file. Case ids are supplied explicitly by the
operator after the offline cohort selection step. S5B-derived runs are
development-only. It creates generic-revision and audit-conditioned child
artifacts without changing the original GoS trace.

Usage:
    python3 traceevo/recovery_pilot.py --cases 80,90 --run
    python3 traceevo/recovery_pilot.py --cases 80,90 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
S5B = ROOT / "evaluations" / "diagnosis_final"
BLINDED = S5B / "dataset_blinded_80_137.csv"
TRACE_DIR = S5B / "heldout_original" / "traceevo" / "runs" / "core_v1_final"
GRAPH_DIR = S5B / "heldout_original" / "graphs"
AUDIT_CSV = S5B / "natural_audit_summary.csv"
DIAGNOSTIC_CSV = S5B / "diagnostic_metrics.csv"
DEFAULT_OUT = ROOT / "refine-logs" / "results" / "R002_exploratory_repair_pilot"
SYSTEM_PROMPT = (
    "You are a cautious clinical decision-support reviewer in a research "
    "evaluation. Reassess the proposed diagnosis using the patient facts and "
    "the supplied reasoning graph. Do not assume the original diagnosis is "
    "wrong. Do not invent patient findings, test results, retrieved records, "
    "or citations. Separate documented facts from medical inference. If the "
    "available information is insufficient, preserve uncertainty rather than "
    "guessing. Do not provide hidden chain-of-thought; return a concise "
    "rationale only. Return one JSON object matching the requested schema."
)
BASE_REQUEST = """Review the GoS diagnosis using only the case facts and supplied causal reasoning graph.

Return JSON with exactly these top-level fields:
{
  "decision": "revise" | "retain" | "insufficient_information",
  "final_diagnosis": "short diagnosis or empty string if insufficient",
  "rationale": "at most 80 words; distinguish patient facts from inference",
  "graph_patch": {
    "confidence_updates": [{"node_id": "existing id", "confidence": 0.0, "why": "..."}],
    "remove_node_ids": ["existing id"],
    "remove_edges": [{"src": "existing id", "dst": "existing id", "type": "existing relation"}],
    "add_nodes": [],
    "add_edges": []
  },
  "additional_information_needed": ["..."],
  "evidence_refs": ["case field, graph node id, or event id actually supplied above"]
}

Keep the complete JSON under 300 words. Only cite ids/fields present in this request. Do not claim that a proposed graph patch was applied; this is a repair proposal for evaluation, not an automatic graph mutation.
"""


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def parse_case_ids(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            out.extend(range(start, end + 1))
        else:
            out.append(int(part))
    if len(out) != len(set(out)):
        raise ValueError("case ids must be unique")
    if not out or any(c < 80 or c > 137 for c in out):
        raise ValueError("exploratory S5B case ids must be within 80..137")
    return out


def _load_case_inputs(case_id: int) -> Dict[str, str]:
    with BLINDED.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if case_id >= len(rows):
        raise ValueError(f"case {case_id} is outside the blinded dataset")
    row = rows[case_id]  # GoS --cases uses the dataset row position.
    forbidden = {"Final Diagnosis", "Right Option"} & set(row)
    if forbidden:
        raise ValueError(f"gold columns found in blinded input: {sorted(forbidden)}")
    return {str(k): str(v or "") for k, v in row.items()}


def _load_trace(case_id: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any],
                                         Dict[str, Any], List[Path]]:
    task = f"case-{case_id}"
    events_path = TRACE_DIR / f"{task}_events.jsonl"
    graph_path = GRAPH_DIR / f"case{case_id}_graphs.json"
    audit_path = AUDIT_CSV
    for p in (events_path, graph_path, audit_path, BLINDED):
        if not p.is_file():
            raise FileNotFoundError(p)
    with events_path.open(encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    with graph_path.open(encoding="utf-8") as f:
        snapshots = json.load(f)
    if not snapshots:
        raise ValueError(f"case {case_id} has no causal graph snapshots")
    final_report = next((e.get("payload") or {} for e in reversed(events)
                         if e.get("event_type") == "FINAL_REPORT"), None)
    if not final_report:
        raise ValueError(f"case {case_id} has no final report event")
    with audit_path.open(newline="", encoding="utf-8") as f:
        audit_rows = list(csv.DictReader(f))
    audit = next((r for r in audit_rows if int(r["case"]) == case_id), None)
    if audit is None:
        raise ValueError(f"case {case_id} has no natural audit row")
    return events, snapshots[-1], {**audit, **final_report}, [
        events_path, graph_path, audit_path, BLINDED, DIAGNOSTIC_CSV,
        ROOT / "gos" / "configs" / "LLM_Configs.yaml"]


def _validate_match_zero_cases(case_ids: Sequence[int]) -> None:
    with DIAGNOSTIC_CSV.open(newline="", encoding="utf-8") as f:
        scores = {int(r["case"]): int(r["judge_score"])
                  for r in csv.DictReader(f)}
    invalid = {case: scores.get(case) for case in case_ids
               if scores.get(case) != 0}
    if invalid:
        raise ValueError(
            "E1 only accepts cases with frozen GoS Match=0; "
            f"invalid case scores: {invalid}")


def _root_context(events: Sequence[Dict[str, Any]], audit: Dict[str, Any]
                  ) -> List[Dict[str, Any]]:
    root_ids = {x for x in str(audit.get("observable_roots") or "").split("|") if x}
    selected = []
    for event in events:
        if event.get("event_id") not in root_ids:
            continue
        payload = event.get("payload") or {}
        safe_payload = {}
        for key in ("analysis", "response", "phase", "exit_cause"):
            value = payload.get(key)
            if value is not None:
                safe_payload[key] = str(value)[:5000]
        selected.append({
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "step_id": event.get("step_id"),
            "actor_role": event.get("actor_role"),
            "causal_node_refs": event.get("causal_node_refs") or [],
            "parent_event_ids": event.get("parent_event_ids") or [],
            "source_class": event.get("source_class"),
            "status": event.get("status"),
            "reason_code": event.get("reason_code"),
            "payload_excerpt": safe_payload,
        })
    return selected


def build_prompt(case_id: int, variant: str,
                 case_input: Dict[str, str], prediction: str, report: str,
                 graph: Dict[str, Any], findings: Dict[str, Any],
                 root_events: List[Dict[str, Any]]) -> Tuple[str, str]:
    if variant not in {"generic", "audit"}:
        raise ValueError(f"unsupported repair variant: {variant}")
    sections = [
        BASE_REQUEST,
        "## Patient case input\n" + json.dumps(
            case_input, ensure_ascii=False, sort_keys=True),
        "## Original GoS output\n" + json.dumps(
            {"prediction": prediction, "report": report}, ensure_ascii=False),
        "## Final causal reasoning graph\n" + json.dumps(
            graph, ensure_ascii=False, sort_keys=True),
    ]
    if variant == "audit":
        audit_context = {
            "audit_alerts": int(findings.get("audit_alerts") or 0),
            "anomaly_types": [x for x in
                              str(findings.get("anomaly_types") or "").split("|")
                              if x],
            "p3_used": int(findings.get("p3_used") or 0),
            "observable_root_event_ids": [x for x in
                                           str(findings.get("observable_roots") or "").split("|")
                                           if x],
            "root_event_context": root_events,
        }
        sections.append(
            "## Post-run audit feedback\n"
            "These are process/evidence audit signals, not proof that the "
            "diagnosis is medically wrong. Independently assess whether they "
            "affect the conclusion; do not blindly delete a finding.\n"
            + json.dumps(audit_context, ensure_ascii=False, sort_keys=True))
    user_prompt = (f"Case index: {case_id}\n" + "\n\n".join(sections))
    if re.search(r"Final Diagnosis|Right Option|verified_incorrect|judge_score",
                 user_prompt, flags=re.IGNORECASE):
        raise ValueError("reference/outcome marker detected in repair prompt")
    return SYSTEM_PROMPT, user_prompt


def parse_repair_response(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text,
                      flags=re.IGNORECASE | re.DOTALL)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model response is not a JSON object")
        parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("repair response must be a JSON object")
    required = {"decision", "final_diagnosis", "rationale", "graph_patch",
                "additional_information_needed", "evidence_refs"}
    if not required <= set(parsed):
        raise ValueError(f"repair response missing keys: {sorted(required - set(parsed))}")
    if parsed["decision"] not in {"revise", "retain", "insufficient_information"}:
        raise ValueError("repair response has invalid decision")
    if not isinstance(parsed["graph_patch"], dict):
        raise ValueError("graph_patch must be an object")
    for key in ("confidence_updates", "remove_node_ids", "remove_edges",
                "add_nodes", "add_edges"):
        if key not in parsed["graph_patch"] or not isinstance(parsed["graph_patch"][key], list):
            raise ValueError(f"graph_patch.{key} must be a list")
    for key in ("additional_information_needed", "evidence_refs"):
        if not isinstance(parsed[key], list):
            raise ValueError(f"{key} must be a list")
    return parsed


def _completion_budget_status(meta: Dict[str, Any], max_tokens: int) -> str:
    """Fail closed when the gateway omits usage or exceeds the requested cap."""
    value = meta.get("completion_tokens")
    try:
        completion_tokens = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if completion_tokens <= 0:
        return "unknown"
    return "within_cap" if completion_tokens <= max_tokens else "exceeded_cap"


def _request(system_prompt: str, user_prompt: str,
             max_tokens: int = 2048) -> Tuple[str, Dict[str, Any]]:
    import sys
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "gos"))
    from traceevo.s2_cases import load_env_file
    load_env_file()
    from utils.public_function import llm_generate_response, load_llm_config
    # The shared GoS config intentionally omits max_tokens to reproduce its
    # historical behavior. This separate repair request is bounded to prevent
    # unbounded revisions; the override is process-local and never writes the
    # shared YAML file.
    load_llm_config()["send_max_tokens"] = True
    return llm_generate_response(
        user_prompt=user_prompt, model_path="deepseek-v4-flash",
        temperature=0.0, max_tokens=max_tokens, system_prompt=system_prompt,
        return_meta=True, stage="DIAGNOSTIC_RECOVERY_PILOT",
        reasoning_effort="none")


def run(case_ids: Sequence[int], output_dir: Path, dry_run: bool = False,
        seed: int = 20260929) -> Dict[str, Any]:
    _validate_match_zero_cases(case_ids)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []
    source_hashes: Dict[str, str] = {}
    variants_by_case: Dict[str, List[str]] = {}

    def make_manifest(run_status: str, stop_reason: str = "") -> Dict[str, Any]:
        return {
            "cohort_status": "exposed S5B development data; exploratory only",
            "cases": list(case_ids),
            "selection_criterion": "original frozen GoS evaluator Match=0",
            "variants_by_case": variants_by_case,
            "n_generation_calls": 0 if dry_run else len(all_rows),
            "dry_run": dry_run,
            "run_status": run_status,
            "stop_reason": stop_reason,
            "request_budget": (
                "reasoning_effort=none; proxy clamps max_tokens at 2048; "
                "pilot stops after the first missing/over-cap completion usage; "
                "shared GoS YAML unchanged"
                if not dry_run else "dry-run; no LLM request"),
            "seed": seed,
            "source_sha256": source_hashes,
            "source_artifacts_unchanged": True,
            "rows": all_rows,
            "evaluation_note": (
                "Gold labels are not read by this script. Score in a separate "
                "post-run process only after all generation outputs are written."),
        }

    for case_id in case_ids:
        events, graph, trace_summary, source_paths = _load_trace(case_id)
        case_input = _load_case_inputs(case_id)
        prediction = str(trace_summary.get("prediction") or "")
        report = str(trace_summary.get("report") or "")
        root_events = _root_context(events, trace_summary)
        has_audit_signal = (int(trace_summary.get("audit_alerts") or 0) > 0
                            or int(trace_summary.get("p3_used") or 0) > 0)
        variants = ["generic"] + (["audit"] if has_audit_signal else [])
        variants_by_case[str(case_id)] = variants
        for path in source_paths:
            source_hashes[str(path.relative_to(ROOT))] = _sha(path)
        # Fixed, case-specific order so paired variants do not systematically
        # benefit from always being called first or last.
        order = list(variants)
        random.Random(seed + case_id).shuffle(order)
        for variant in order:
            system_prompt, user_prompt = build_prompt(
                case_id, variant, case_input, prediction, report, graph,
                trace_summary, root_events)
            prompt_hash = hashlib.sha256(user_prompt.encode("utf-8")).hexdigest()
            artifact = {
                "case_id": case_id,
                "variant": variant,
                "cohort_status": "exposed S5B development data; exploratory only",
                "gold_access": "none in this generation process",
                "model": "deepseek-v4-flash",
                "temperature": 0.0,
                "max_tokens": 2048,
                "max_tokens_sent": not dry_run,
                "reasoning_effort": "none",
                "gateway_output_cap": 2048,
                "baseline_prediction": prediction,
                "baseline_report": report,
                "audit_signal": has_audit_signal,
                "audit_alerts": int(trace_summary.get("audit_alerts") or 0),
                "anomaly_types": [x for x in
                                  str(trace_summary.get("anomaly_types") or "").split("|")
                                  if x],
                "p3_used": int(trace_summary.get("p3_used") or 0),
                "prompt_sha256": prompt_hash,
                "source_sha256": source_hashes,
            }
            budget_status = "not_applicable" if dry_run else "unknown"
            if dry_run:
                artifact["dry_run"] = True
            else:
                artifact["system_prompt"] = system_prompt
                artifact["user_prompt"] = user_prompt
                response, meta = _request(system_prompt, user_prompt,
                                          max_tokens=2048)
                artifact["raw_response"] = response
                artifact["usage"] = {k: meta.get(k) for k in
                                     ("prompt_tokens", "completion_tokens",
                                      "total_tokens", "price_rmb", "model")}
                budget_status = _completion_budget_status(meta, 2048)
                artifact["completion_budget_status"] = budget_status
                try:
                    parsed = parse_repair_response(response)
                    artifact["parsed_repair"] = parsed
                    artifact["parse_ok"] = True
                except (ValueError, json.JSONDecodeError) as exc:
                    artifact["parse_ok"] = False
                    artifact["parse_error"] = str(exc)
            artifact["response_sha256"] = hashlib.sha256(
                str(artifact.get("raw_response") or "").encode("utf-8")).hexdigest()
            path = output_dir / f"case-{case_id}_{variant}.json"
            if path.exists():
                raise FileExistsError(f"refusing to overwrite {path}")
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            all_rows.append({"case": case_id, "variant": variant,
                             "prompt_sha256": prompt_hash,
                             "artifact": str(path.relative_to(ROOT)),
                             "parse_ok": artifact.get("parse_ok", None),
                             "completion_budget_status": budget_status,
                             "usage": artifact.get("usage", {})})
            print(f"case-{case_id} {variant}: "
                  f"{'prompt saved' if dry_run else ('parsed' if artifact.get('parse_ok') else 'unparsed')}",
                  flush=True)
            if not dry_run and budget_status != "within_cap":
                changed = [p for p, digest in source_hashes.items()
                           if _sha(ROOT / p) != digest]
                if changed:
                    raise RuntimeError(
                        f"source artifacts changed during pilot: {changed}")
                manifest_path = output_dir / "generation_manifest.json"
                if manifest_path.exists():
                    raise FileExistsError(f"refusing to overwrite {manifest_path}")
                reason = (f"completion usage {budget_status} for case {case_id} "
                          f"variant {variant}; stopped before the next model call")
                manifest_path.write_text(
                    json.dumps(make_manifest("stopped_on_output_budget", reason),
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                raise RuntimeError(reason)

    changed = [p for p, digest in source_hashes.items()
               if _sha(ROOT / p) != digest]
    if changed:
        raise RuntimeError(f"source artifacts changed during pilot: {changed}")
    manifest = make_manifest("completed")
    manifest_path = output_dir / "generation_manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"refusing to overwrite {manifest_path}")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True,
                        help="explicit S5B development case ids, e.g. 80,90")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true",
                      help="make the configured LLM requests")
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--allow-full-failure-cohort", action="store_true",
                        help="allow >4 explicit Match=0 cases; the E1 ceiling is 36")
    args = parser.parse_args()
    cases = parse_case_ids(args.cases)
    if len(cases) > 36:
        raise SystemExit("S5B frozen GoS Match=0 cohort contains at most 36 cases")
    if len(cases) > 4 and not args.allow_full_failure_cohort:
        raise SystemExit("pilot capped at four cases; pass --allow-full-failure-cohort after reviewing the cap test")
    result = run(cases, args.out, dry_run=args.dry_run, seed=args.seed)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
