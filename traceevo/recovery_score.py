"""Post-run paired scoring with the frozen GoS Match/Relevant rubric.

This evaluator runs only after reference-blind generation artifacts are
complete. It rescoring-matches three outputs per originally failed case:
baseline GoS, generic revision, and the audit-conditioned policy. Cases without
an audit signal receive no audit intervention, so that policy retains the
baseline output for them. References enter this separate evaluation process
only and never enter repair generation.

Usage:
    python3 traceevo/recovery_score.py --pilot-dir refine-logs/results/R002_exploratory_repair_pilot --run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
GOS = ROOT / "gos"
GOLD_CSV = GOS / "datasets" / "DiagnosisArena" / "test_set.csv"
DIAGNOSTIC_CSV = ROOT / "evaluations" / "diagnosis_final" / "diagnostic_metrics.csv"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(GOS))
from traceevo.recovery_pilot import _completion_budget_status


def _rows(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_outputs(pilot_dir: Path, manifest: Dict[str, Any]
                  ) -> Dict[Tuple[int, str], Dict[str, Any]]:
    outputs: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for row in manifest.get("rows", []):
        path = ROOT / row["artifact"]
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if artifact.get("parse_ok") is not True:
            raise ValueError(f"unparseable repair response: {path}")
        if _sha_text(artifact.get("raw_response", "")) != artifact.get("response_sha256"):
            raise ValueError(f"repair response hash mismatch: {path}")
        if int(artifact["case_id"]) != int(row["case"]):
            raise ValueError(f"case-id mismatch in {path}")
        key = (int(artifact["case_id"]), str(artifact["variant"]))
        if key in outputs:
            raise ValueError(f"duplicate generated output for {key}")
        outputs[key] = artifact
    return outputs


def _candidate(case: int, variant: str, prediction: str, report: str,
               audit_signal: bool, source_variant: str) -> Dict[str, Any]:
    return {"case": case, "variant": variant, "prediction": prediction,
            "report": report, "audit_signal": audit_signal,
            "source_variant": source_variant}


def _pack_candidate_groups(groups: List[List[Dict[str, Any]]],
                           batch_size: int = 5) -> List[List[Dict[str, Any]]]:
    """Pack cases while keeping each generic/audit pair in the same judge call."""
    batches: List[List[Dict[str, Any]]] = []
    batch: List[Dict[str, Any]] = []
    for group in groups:
        if len(group) > batch_size:
            raise ValueError("a paired case group exceeds the frozen judge batch size")
        if batch and len(batch) + len(group) > batch_size:
            batches.append(batch)
            batch = []
        batch.extend(group)
    if batch:
        batches.append(batch)
    return batches


def _score_prompt_module():
    path = GOS / "evaluate_Medical_scoring.py"
    spec = importlib.util.spec_from_file_location("frozen_gos_match", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen GoS evaluator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score(pilot_dir: Path, run_judge: bool = False) -> Dict[str, Any]:
    manifest_path = pilot_dir / "generation_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dry_run") or manifest.get("n_generation_calls") == 0:
        raise ValueError("cannot score a dry-run or empty generation manifest")
    if manifest.get("source_artifacts_unchanged") is not True:
        raise ValueError("generation source integrity was not confirmed")

    outputs = _load_outputs(pilot_dir, manifest)
    cases = sorted({int(c) for c in manifest["cases"]})
    baseline_scores = {int(r["case"]): int(r["judge_score"])
                       for r in _rows(DIAGNOSTIC_CSV)}
    invalid = {c: baseline_scores.get(c) for c in cases
               if baseline_scores.get(c) != 0}
    if invalid:
        raise ValueError(f"E1 scoring requires original frozen GoS Match=0: {invalid}")

    candidates: List[Dict[str, Any]] = []
    candidate_groups: List[List[Dict[str, Any]]] = []
    for case in cases:
        generic = outputs.get((case, "generic"))
        if generic is None:
            raise ValueError(f"missing generic revision for case {case}")
        audit = outputs.get((case, "audit"))
        audit_signal = bool(generic.get("audit_signal"))
        if audit_signal and audit is None:
            raise ValueError(f"case {case} has audit signal but no audit revision")
        if not audit_signal and audit is not None:
            raise ValueError(f"case {case} has no audit signal but has an audit revision")

        generic_repair = generic["parsed_repair"]
        generic_candidate = _candidate(
            case, "generic",
            str(generic_repair.get("final_diagnosis") or ""),
            str(generic_repair.get("rationale") or ""),
            audit_signal, "generic_revision")
        candidates.append(generic_candidate)
        case_group = [generic_candidate]
        if audit_signal:
            audit_repair = audit["parsed_repair"]
            audit_candidate = _candidate(
                case, "audit_repair",
                str(audit_repair.get("final_diagnosis") or ""),
                str(audit_repair.get("rationale") or ""),
                audit_signal, "audit_revision")
            candidates.append(audit_candidate)
            case_group.append(audit_candidate)
        candidate_groups.append(case_group)
    candidate_batches = _pack_candidate_groups(candidate_groups)

    if not run_judge:
        return {"status": "PREPARED_NOT_SCORED",
                "n_cases": len(cases),
                "n_generation_outputs": len(outputs),
                "n_repair_outputs_to_score": len(candidates),
                "n_judge_requests_estimate": len(candidate_batches),
                "cases": cases,
                "note": "Gold labels have not been read by this process. Only generated revisions are scored; original Match=0 baseline scores are reused without stochastic rerating. Pass --run to use the frozen GoS rubric."}

    # This is a separate post-generation process; only here are references read.
    gold_rows = _rows(GOLD_CSV)
    references = {case: gold_rows[case].get("Final Diagnosis", "")
                  for case in cases}
    if any(not ref for ref in references.values()):
        raise ValueError("missing reference diagnosis in post-run scoring input")

    from traceevo.s2_cases import load_env_file
    load_env_file()
    from utils.public_function import llm_generate_response, load_llm_config
    # The GoS evaluator rubric and parser are frozen; effort=none and a 512-token
    # cap keep judge calls short. The local proxy clamps opted-in DeepSeek calls.
    load_llm_config()["send_max_tokens"] = True
    evaluator = _score_prompt_module()
    score_by_candidate: Dict[Tuple[int, str], Optional[int]] = {}
    raw_dir = pilot_dir / "judge_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for group_index, chunk in enumerate(candidate_batches, 1):
        combined = [f"{r['prediction']}, {r['report']}" for r in chunk]
        answers = [references[r["case"]] for r in chunk]
        user_prompt = evaluator.build_group_prompt(combined, answers, 0, len(chunk))
        response, meta = llm_generate_response(
            user_prompt=user_prompt,
            model_path="deepseek-v4-flash",
            temperature=1.0,
            max_tokens=512,
            system_prompt=evaluator.JUDGE_SYSTEM_PROMPT,
            return_meta=True,
            stage="RECOVERY_GOS_MATCH_SCORING",
            reasoning_effort="none")
        parsed = evaluator.parse_scores(response, len(chunk), 0, len(chunk))
        for index, candidate in enumerate(chunk, 1):
            score_value = parsed.get(index)
            score_by_candidate[(candidate["case"], candidate["variant"])] = score_value
        record = {
            "group": group_index,
            "case_variant_map": [{"judge_case": index, "case": r["case"],
                                  "variant": r["variant"]}
                                 for index, r in enumerate(chunk, 1)],
            "system_prompt": evaluator.JUDGE_SYSTEM_PROMPT,
            "user_prompt": user_prompt,
            "raw_response": response,
            "parsed_scores": parsed,
            "completion_budget_status": _completion_budget_status(meta, 512),
            "usage": {k: meta.get(k) for k in
                      ("prompt_tokens", "completion_tokens", "total_tokens",
                       "model", "price_rmb")},
        }
        raw_path = raw_dir / f"match-group-{group_index:03d}.json"
        if raw_path.exists():
            raise FileExistsError(raw_path)
        raw_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print(f"GoS Match group {group_index}: parsed {len(parsed)}/{len(chunk)}",
              flush=True)
        if record["completion_budget_status"] != "within_cap":
            raise RuntimeError(
                "frozen GoS judge completion usage is missing or exceeds the "
                f"512-token cap for group {group_index}; stopped before the "
                "next judge request")

    results = []
    for case in cases:
        generic_output = outputs[(case, "generic")]
        audit_signal = bool(generic_output.get("audit_signal"))
        baseline_prediction = str(generic_output.get("baseline_prediction") or "")
        baseline_report = str(generic_output.get("baseline_report") or "")
        generic_repair = generic_output["parsed_repair"]
        generic_score = score_by_candidate.get((case, "generic"))
        if audit_signal:
            audit_output = outputs[(case, "audit")]
            audit_repair = audit_output["parsed_repair"]
            audit_prediction = str(audit_repair.get("final_diagnosis") or "")
            audit_report = str(audit_repair.get("rationale") or "")
            audit_score = score_by_candidate.get((case, "audit_repair"))
            audit_source = "audit_revision"
            audit_score_source = "post_run_frozen_GoS_rubric"
        else:
            # The audit policy makes no change when there is no audit signal.
            # Reuse the original score instead of asking a stochastic judge to
            # score the identical baseline a second time.
            audit_score = baseline_scores[case]
            audit_prediction = baseline_prediction
            audit_report = baseline_report
            audit_source = "baseline_no_audit_signal"
            audit_score_source = "original_frozen_GoS_evaluation"
        scored_rows = [
            (_candidate(case, "baseline", baseline_prediction, baseline_report,
                        audit_signal, "original_trace"),
             baseline_scores[case], "original_frozen_GoS_evaluation"),
            (_candidate(case, "generic",
                        str(generic_repair.get("final_diagnosis") or ""),
                        str(generic_repair.get("rationale") or ""),
                        audit_signal, "generic_revision"),
             generic_score, "post_run_frozen_GoS_rubric"),
            (_candidate(case, "audit_policy", audit_prediction, audit_report,
                        audit_signal, audit_source),
             audit_score, audit_score_source),
        ]
        for candidate, score_value, score_source in scored_rows:
            results.append({
                **candidate,
                "original_frozen_match_score": baseline_scores[case],
                "paired_rescore": score_value,
                "score_source": score_source,
                "rescored_exact": score_value == 2,
                "rescored_relevant": score_value is not None and score_value >= 1,
            })

    def per_variant(variant: str) -> Dict[str, Any]:
        rows = [r for r in results if r["variant"] == variant]
        counts = Counter(str(r["paired_rescore"]) for r in rows)
        exact = sum(1 for r in rows if r["paired_rescore"] == 2)
        relevant = sum(1 for r in rows if r["paired_rescore"] is not None
                       and r["paired_rescore"] >= 1)
        return {"n": len(rows), "score_0": counts.get("0", 0),
                "score_1": counts.get("1", 0), "score_2": counts.get("2", 0),
                "unparsed": counts.get("None", 0),
                "exact_rescues_from_original_match0": exact,
                "relevant_rescues_from_original_match0": relevant,
                "conditional_exact_rescue_rate": (
                    exact / len(rows) if rows else None)}

    per_case = {case: {r["variant"]: r["paired_rescore"] for r in results
                       if r["case"] == case} for case in cases}
    signal_cases = {case for case in cases
                    if outputs[(case, "generic")].get("audit_signal")}
    audit_vs_generic = Counter()
    for case in signal_cases:
        a, g = per_case[case].get("audit_policy"), per_case[case].get("generic")
        if a is None or g is None:
            continue
        audit_vs_generic["audit_higher" if a > g else
                         "generic_higher" if g > a else "tie"] += 1

    summary = {
        "status": "EXPLORATORY_SCORED",
        "cohort_status": manifest["cohort_status"],
        "selection": "original frozen GoS Match=0 only; Match=1 broad-category cases are excluded",
        "n_original_failure_cases": len(cases),
        "n_with_audit_signal": len(signal_cases),
        "n_without_audit_signal": len(cases) - len(signal_cases),
        "per_variant": {v: per_variant(v)
                        for v in ("baseline", "generic", "audit_policy")},
        "audit_vs_generic_on_signal_subset": dict(audit_vs_generic),
        "case_variant_results": results,
        "evaluator": "frozen gos/evaluate_Medical_scoring.py prompt/parser; original baseline uses its frozen score; only generated revisions are rescored",
        "judge_request_settings": "deepseek-v4-flash; temperature=1.0; reasoning_effort=none; max_tokens=512; local proxy cap enabled",
        "gold_isolation": "References are read only by this post-run scorer after repair generation outputs exist.",
        "limitations": [
            "This is conditional recovery on cases selected by their original Match=0 outcome; it is not an overall-cohort or deployable-trigger accuracy claim.",
            "One paired GoS-style scoring pass is exploratory; broad-category Match=1 cases are kept outside the primary failure cohort.",
            "Audit-clean errors receive no audit intervention in the audit_policy arm; their original frozen Match=0 score is carried forward without stochastic rerating.",
            "S5B cases were already inspected and are development-only.",
        ],
    }
    csv_path = pilot_dir / "repair_scores.csv"
    if csv_path.exists():
        raise FileExistsError(csv_path)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    summary_path = pilot_dir / "repair_scores.json"
    if summary_path.exists():
        raise FileExistsError(summary_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return summary


def reconcile_existing(pilot_dir: Path) -> Dict[str, Any]:
    """Correctly interpret an earlier score pass that re-rated unchanged baselines."""
    manifest = json.loads((pilot_dir / "generation_manifest.json").read_text(
        encoding="utf-8"))
    raw_rows = _rows(pilot_dir / "repair_scores.csv")
    raw_scores: Dict[Tuple[int, str], Optional[int]] = {}
    raw_by_key: Dict[Tuple[int, str], Dict[str, str]] = {}
    for row in raw_rows:
        key = (int(row["case"]), row["variant"])
        if key in raw_scores:
            raise ValueError(f"duplicate raw score row: {key}")
        raw_by_key[key] = row
        value = row.get("paired_rescore")
        raw_scores[key] = None if value in (None, "") else int(value)

    cases = sorted(int(case) for case in manifest["cases"])
    baseline_scores = {int(row["case"]): int(row["judge_score"])
                       for row in _rows(DIAGNOSTIC_CSV)}
    if any(baseline_scores.get(case) != 0 for case in cases):
        raise ValueError("reconciliation requires the frozen Match=0 cohort")
    signal_cases = {int(case) for case, variants in
                    manifest["variants_by_case"].items() if "audit" in variants}

    results = []
    for case in cases:
        generic_key = (case, "generic")
        generic_row = raw_by_key.get(generic_key)
        if generic_row is None:
            raise ValueError(f"missing generic judge result for case {case}")
        generic_score = raw_scores[generic_key]
        baseline_score = baseline_scores[case]
        baseline_row = raw_by_key.get((case, "baseline"), generic_row)
        is_signal = case in signal_cases
        if is_signal:
            audit_row = raw_by_key.get((case, "audit_policy"))
            if audit_row is None:
                raise ValueError(f"missing audit judge result for signal case {case}")
            audit_score = raw_scores[(case, "audit_policy")]
            audit_prediction = audit_row.get("prediction", "")
            audit_report = audit_row.get("report", "")
            audit_source = "post_run_frozen_GoS_rubric"
            audit_lineage = "audit_revision"
        else:
            # No intervention means preserve the original outcome and original
            # frozen score. Do not use the duplicate candidate's stochastic re-score.
            audit_score = baseline_score
            audit_prediction = baseline_row.get("prediction", "")
            audit_report = baseline_row.get("report", "")
            audit_source = "original_frozen_GoS_evaluation"
            audit_lineage = "baseline_no_audit_signal"

        rows_for_case = [
            {"case": case, "variant": "baseline",
             "prediction": baseline_row.get("prediction", ""),
             "report": baseline_row.get("report", ""),
             "audit_signal": is_signal, "source_variant": "original_trace",
             "original_frozen_match_score": baseline_score,
             "paired_rescore": baseline_score,
             "score_source": "original_frozen_GoS_evaluation"},
            {"case": case, "variant": "generic",
             "prediction": generic_row.get("prediction", ""),
             "report": generic_row.get("report", ""),
             "audit_signal": is_signal, "source_variant": "generic_revision",
             "original_frozen_match_score": baseline_score,
             "paired_rescore": generic_score,
             "score_source": "post_run_frozen_GoS_rubric"},
            {"case": case, "variant": "audit_policy",
             "prediction": audit_prediction, "report": audit_report,
             "audit_signal": is_signal, "source_variant": audit_lineage,
             "original_frozen_match_score": baseline_score,
             "paired_rescore": audit_score,
             "score_source": audit_source},
        ]
        for row in rows_for_case:
            score_value = row["paired_rescore"]
            row["rescored_exact"] = score_value == 2
            row["rescored_relevant"] = score_value is not None and score_value >= 1
            results.append(row)

    def per_variant(variant: str) -> Dict[str, Any]:
        rows = [row for row in results if row["variant"] == variant]
        counts = Counter(str(row["paired_rescore"]) for row in rows)
        exact = counts.get("2", 0)
        relevant = counts.get("1", 0) + exact
        return {"n": len(rows), "score_0": counts.get("0", 0),
                "score_1": counts.get("1", 0), "score_2": exact,
                "unparsed": counts.get("None", 0),
                "exact_rescues_from_original_match0": exact,
                "relevant_rescues_from_original_match0": relevant,
                "conditional_exact_rescue_rate": exact / len(rows) if rows else None}

    corrected_signal = Counter()
    for case in signal_cases:
        audit = raw_scores.get((case, "audit_policy"))
        generic = raw_scores.get((case, "generic"))
        if audit is None or generic is None:
            continue
        corrected_signal["audit_higher" if audit > generic else
                         "generic_higher" if generic > audit else "tie"] += 1

    baseline_rerated = [raw_scores.get((case, "baseline")) for case in cases]
    baseline_drift = Counter(str(score_value) for score_value in baseline_rerated)
    no_signal_rerated = [raw_scores.get((case, "audit_policy"))
                         for case in cases if case not in signal_cases]
    no_signal_drift = Counter(str(score_value) for score_value in no_signal_rerated)
    summary = {
        "status": "EXPLORATORY_SCORED_RECONCILED",
        "supersedes": "repair_scores.json; baseline and no-signal audit-policy duplicate rows were stochastic reratings",
        "cohort_status": manifest["cohort_status"],
        "selection": "original frozen GoS Match=0 only; no unused/new cases included",
        "n_original_failure_cases": len(cases),
        "n_with_audit_signal": len(signal_cases),
        "n_without_audit_signal": len(cases) - len(signal_cases),
        "per_variant": {variant: per_variant(variant)
                        for variant in ("baseline", "generic", "audit_policy")},
        "audit_vs_generic_on_signal_subset": dict(corrected_signal),
        "baseline_rescore_consistency_check": {
            "original_frozen_scores_are_all_zero": True,
            "independent_rerating_distribution": {
                "score_0": baseline_drift.get("0", 0),
                "score_1": baseline_drift.get("1", 0),
                "score_2": baseline_drift.get("2", 0)},
            "interpretation": "Rerating the original baseline changed 6/36 labels; those reratings are not used as intervention outcomes."},
        "no_signal_duplicate_rerating_check": {
            "n_cases": len(no_signal_rerated),
            "independent_rerating_distribution": {
                "score_0": no_signal_drift.get("0", 0),
                "score_1": no_signal_drift.get("1", 0),
                "score_2": no_signal_drift.get("2", 0)},
            "interpretation": "Audit policy leaves these outputs unchanged; their policy scores are carried forward from the original frozen Match=0 result."},
        "case_variant_results": results,
        "evaluator": "frozen gos/evaluate_Medical_scoring.py prompt/parser; baseline values from original frozen evaluation; only generated revisions use the post-run judge pass",
        "judge_request_settings": "deepseek-v4-flash; temperature=1.0; reasoning_effort=none; max_tokens=512; local proxy cap enabled",
        "limitations": [
            "Conditional recovery on the original GoS Match=0 development cohort; not overall-cohort accuracy or a deployable-trigger estimate.",
            "Single stochastic judge pass for repairs; the frozen baseline rerating check shows nonzero judge variability.",
            "S5B cases are exposed development data and are exploratory only."],
    }
    csv_path = pilot_dir / "repair_scores_reconciled.csv"
    json_path = pilot_dir / "repair_scores_reconciled.json"
    if csv_path.exists() or json_path.exists():
        raise FileExistsError("reconciled score artifacts already exist")
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true",
                       help="read references post-generation and run the frozen GoS scoring prompt")
    mode.add_argument("--reconcile-existing", action="store_true",
                       help="offline-correct an existing score pass without making judge calls")
    args = parser.parse_args()
    if args.reconcile_existing:
        result = reconcile_existing(args.pilot_dir.resolve())
    else:
        result = score(args.pilot_dir.resolve(), run_judge=args.run)
    print(json.dumps({k: v for k, v in result.items()
                      if k != "case_variant_results"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
