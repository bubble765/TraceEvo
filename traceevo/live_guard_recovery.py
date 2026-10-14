"""Isolated exploratory counterfactual for the live P3 grounding guard.

This runner is intentionally separate from the frozen S5B driver. It reads the
already-blinded case table, accepts only original GoS Match=0 cases with a
P3/evidence signal, and writes guard-off/guard-on child runs under a new output
directory. It never reads the reference diagnosis or modifies S5B artifacts.

Example:
    python -m traceevo.live_guard_recovery --cases 90 --run
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
GOS = ROOT / "gos"
S5B = ROOT / "S5B_final_heldout"
BLINDED = S5B / "dataset_blinded_80_137.csv"
CASE_MATRIX = ROOT / "refine-logs" / "results" / "R000_baseline_opportunity" / "case_matrix.csv"
DEFAULT_OUT = ROOT / "refine-logs" / "results" / "R003_live_p3_guard_pilot"
MAX_OUTPUT_TOKENS = 2048
DEFAULT_TIMEOUT_SECONDS = 1200
PROXY_SERVER = (Path.home() / ".local" / "share" /
                "claude-prefix-proxy" / "server.py")

SOURCE_FILES = (
    BLINDED,
    CASE_MATRIX,
    GOS / "main.py",
    GOS / "agents" / "central_agent.py",
    GOS / "utils" / "public_function.py",
    GOS / "configs" / "LLM_Configs.yaml",
    GOS / "configs" / "TraceEvo_Configs.yaml",
    ROOT / "traceevo" / "hooks.py",
    ROOT / "traceevo" / "config.py",
    ROOT / "traceevo" / "evolution" / "grounding_guard.py",
    ROOT / "traceevo" / "live_guard_recovery.py",
    PROXY_SERVER,
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_key(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_case_ids(spec: str) -> List[int]:
    cases: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            cases.extend(range(start, end + 1))
        else:
            cases.append(int(part))
    if not cases or len(cases) != len(set(cases)):
        raise ValueError("case list must be non-empty and contain no duplicates")
    return cases


def eligible_cases() -> Dict[int, Dict[str, str]]:
    """Only cases originally wrong and independently flagged for P3 evidence."""
    with CASE_MATRIX.open(newline="", encoding="utf-8") as file:
        rows = csv.DictReader(file)
        return {
            int(row["case"]): row for row in rows
            if int(row["gos_match_score"]) == 0
            and str(row.get("evidence_signal", "")).lower() == "true"
            and int(row.get("p3_used", "0") or 0) > 0
        }


def validate_cases(case_ids: Sequence[int]) -> None:
    eligible = eligible_cases()
    invalid = [case for case in case_ids if case not in eligible]
    if invalid:
        raise ValueError(
            "live P3 guard study accepts only cases with original GoS Match=0, "
            "an evidence audit signal, and P3-used evidence; rejected "
            f"case ids: {invalid}")
    missing = [path for path in SOURCE_FILES if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing required source artifacts: {missing}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _completion_usage(log_text: str) -> List[int]:
    values: List[int] = []
    for line in log_text.splitlines():
        # Ignore `[LLM-API] Total` at end of a GoS run. That line reports the
        # sum over all calls, not one response, and must not be checked against
        # the per-request 2,048-token budget.
        if "Generation Completed" not in line:
            continue
        match = re.search(r"Output token nums[:：]\s*(\d+)", line)
        if match:
            values.append(int(match.group(1)))
    return values


def _guard_actions(trace_root: Path) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    for path in trace_root.rglob("guard_actions.jsonl"):
        with path.open(encoding="utf-8") as file:
            for line in file:
                if line.strip():
                    actions.append(json.loads(line))
    return actions


def _run_arm(case_id: int, guard_on: bool, arm_dir: Path,
             timeout_seconds: int) -> Dict[str, Any]:
    arm_dir.mkdir(parents=True, exist_ok=False)
    trace_root = arm_dir / "traceevo"
    trace_root.mkdir()
    tag = f"r003-case-{case_id}-guard-{'on' if guard_on else 'off'}"
    log_path = arm_dir / "run.log"
    command = [
        sys.executable, "-u", "main.py", "--cases", str(case_id),
        "--out-csv", str(arm_dir / "results.csv"),
        "--html-dir", str(arm_dir / "html"),
        "--json-dir", str(arm_dir / "graphs"),
        "--log-dir", str(arm_dir / "logs"),
        "--tag", tag,
    ]
    from traceevo.s2_cases import load_env_file
    load_env_file()
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": os.pathsep.join(
            [str(ROOT)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])),
        "TRACEEVO_GOS_DIR": str(GOS),
        "TRACEEVO_ENABLED": "1",
        "TRACEEVO_LEDGER": "1",
        "TRACEEVO_REPLAY_MODE": "off",
        "TRACEEVO_MEMORY": "0",
        "TRACEEVO_GUARD": "1" if guard_on else "0",
        "TRACEEVO_GUARD_MAX_RETRY": "1",
        "TRACEEVO_DATASET_CSV": str(BLINDED),
        "TRACEEVO_OUTPUT_DIR": str(trace_root),
        "TRACEEVO_LLM_REASONING_EFFORT": "none",
        "TRACEEVO_LLM_MAX_ATTEMPTS": "1",
        "TRACEEVO_MAX_OUTPUT_TOKENS": str(MAX_OUTPUT_TOKENS),
    })
    started = _now()
    try:
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.run(
                command, cwd=str(GOS), env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=timeout_seconds,
                check=False)
        return_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        return_code = None
        timed_out = True

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    task_id = f"case-{case_id}"
    integrity = trace_root / "runs" / tag / f"{task_id}_integrity.json"
    event_file = trace_root / "runs" / tag / f"{task_id}_events.jsonl"
    result_csv = arm_dir / "results.csv"
    actions = _guard_actions(trace_root)
    usage = _completion_usage(log_text)
    within_budget = bool(usage) and max(usage) <= MAX_OUTPUT_TOKENS
    return {
        "case": case_id,
        "arm": "guard_on" if guard_on else "guard_off",
        "started_at": started,
        "finished_at": _now(),
        "return_code": return_code,
        "timed_out": timed_out,
        "complete": bool(integrity.is_file() and event_file.is_file()
                          and result_csv.is_file() and return_code == 0
                          and within_budget),
        "tag": tag,
        "runner_sha256": _sha(ROOT / "traceevo" / "live_guard_recovery.py"),
        "artifact_dir": str(arm_dir.relative_to(ROOT)),
        "event_sha256": _sha(event_file) if event_file.is_file() else None,
        "integrity_sha256": _sha(integrity) if integrity.is_file() else None,
        "results_sha256": _sha(result_csv) if result_csv.is_file() else None,
        "n_llm_outputs_observed_in_log": len(usage),
        "max_completion_tokens_observed": max(usage) if usage else None,
        "within_output_budget": within_budget,
        "n_guard_actions": len(actions),
        "guard_triggers": sum(1 for item in actions
                              if item.get("guard_event") == "GUARD_TRIGGER"),
        "guard_results": [item.get("guard_result") for item in actions
                          if item.get("guard_result")],
        "guard_actions": actions,
        "failure_excerpt": (log_text[-2000:] if return_code != 0 or timed_out
                            else ""),
    }


def _refresh_saved_arm(result: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute completion from immutable artifacts during a safe resume."""
    arm_dir = ROOT / result["artifact_dir"]
    trace_root = arm_dir / "traceevo"
    task_id = f"case-{result['case']}"
    tag = result["tag"]
    log_path = arm_dir / "run.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") \
        if log_path.is_file() else ""
    usage = _completion_usage(log_text)
    integrity = trace_root / "runs" / tag / f"{task_id}_integrity.json"
    event_file = trace_root / "runs" / tag / f"{task_id}_events.jsonl"
    result_csv = arm_dir / "results.csv"
    actions = _guard_actions(trace_root)
    within_budget = bool(usage) and max(usage) <= MAX_OUTPUT_TOKENS
    result.update({
        "n_llm_outputs_observed_in_log": len(usage),
        "max_completion_tokens_observed": max(usage) if usage else None,
        "within_output_budget": within_budget,
        "complete": bool(integrity.is_file() and event_file.is_file()
                          and result_csv.is_file()
                          and result.get("return_code") == 0
                          and within_budget),
        "event_sha256": _sha(event_file) if event_file.is_file() else None,
        "integrity_sha256": _sha(integrity) if integrity.is_file() else None,
        "results_sha256": _sha(result_csv) if result_csv.is_file() else None,
        "n_guard_actions": len(actions),
        "guard_triggers": sum(1 for item in actions
                              if item.get("guard_event") == "GUARD_TRIGGER"),
        "guard_results": [item.get("guard_result") for item in actions
                          if item.get("guard_result")],
        "guard_actions": actions,
    })
    if result["complete"]:
        result["failure_excerpt"] = ""
    return result


def run(case_ids: Sequence[int], output_dir: Path, dry_run: bool = False,
        seed: int = 20260929,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        resume: bool = False) -> Dict[str, Any]:
    validate_cases(case_ids)
    if dry_run:
        return {
            "status": "DRY_RUN_VALIDATED",
            "cases": list(case_ids),
            "eligible_evidence_p3_cases": sorted(eligible_cases()),
            "n_model_calls": 0,
            "note": "No GoS or LLM process was started.",
        }

    output_dir = output_dir.resolve()
    manifest_path = output_dir / "guard_run_manifest.json"
    source_paths = {_source_key(path): path for path in SOURCE_FILES}
    current_hashes = {key: _sha(path)
                      for key, path in source_paths.items()}
    runner_key = "traceevo/live_guard_recovery.py"
    if resume:
        if not manifest_path.is_file():
            raise FileNotFoundError(
                "--resume requires an existing guard_run_manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("cases") != list(case_ids):
            raise ValueError("resume case list must exactly match the saved manifest")
        original_hashes = manifest.get("source_sha256") or {}
        changed = [key for key, digest in original_hashes.items()
                   if key != runner_key and current_hashes.get(key) != digest]
        if changed:
            raise RuntimeError(
                f"source/config changed since the partial run: {changed}")
        old_runner_hash = original_hashes.get(runner_key)
        new_runner_hash = current_hashes.get(runner_key)
        if old_runner_hash != new_runner_hash:
            manifest["controller_resume_note"] = (
                "The per-call usage parser was corrected to ignore the final "
                "aggregate total line; no GoS source, data, guard policy, "
                "model configuration, or proxy code changed. Existing arm "
                "outputs were rechecked from their logs before continuing.")
            manifest["controller_runner_sha256_before_resume"] = old_runner_hash
            manifest["controller_runner_sha256_after_resume"] = new_runner_hash
        for result in manifest.get("runs", []):
            if not result.get("runner_sha256"):
                result["runner_sha256"] = old_runner_hash
            _refresh_saved_arm(result)
        if any(not result.get("complete") for result in manifest.get("runs", [])):
            raise RuntimeError(
                "saved arm is still incomplete or over budget after offline "
                "usage reconciliation; refusing to rerun it")
        manifest["source_artifacts_unchanged"] = True
        manifest["status"] = "RUNNING"
        manifest.pop("stop_reason", None)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "status": "RUNNING",
            "cohort_status": "exposed S5B development cases; separate exploratory rerun",
            "selection_criterion": (
                "original frozen GoS Match=0 AND evidence_signal=true "
                "AND P3-used>0"),
            "cases": list(case_ids),
            "arms": ["guard_off", "guard_on"],
            "model_request_settings": {
                "reasoning_effort": "none",
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "max_attempts_per_request": 1,
                "guard_max_retry": 1,
                "memory": "off",
            },
            "source_sha256": current_hashes,
            "source_artifacts_unchanged": True,
            "runs": [],
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    monitor_hashes = current_hashes

    for case_id in case_ids:
        arm_order = [False, True]
        random.Random(seed + case_id).shuffle(arm_order)
        for guard_on in arm_order:
            arm_name = "guard_on" if guard_on else "guard_off"
            existing = next((row for row in manifest["runs"]
                             if int(row["case"]) == case_id
                             and row["arm"] == arm_name), None)
            if existing is not None:
                if existing.get("complete"):
                    continue
                raise RuntimeError(
                    f"existing {case_id}/{arm_name} run is incomplete; refusing overwrite")
            arm_dir = output_dir / f"case-{case_id}" / arm_name
            arm_dir.parent.mkdir(parents=True, exist_ok=True)
            if arm_dir.exists():
                raise FileExistsError(
                    f"arm output already exists without a complete manifest entry: {arm_dir}")
            result = _run_arm(case_id, guard_on, arm_dir, timeout_seconds)
            manifest["runs"].append(result)
            changed = [key for key, digest in monitor_hashes.items()
                       if _sha(source_paths[key]) != digest]
            if changed:
                manifest["status"] = "STOPPED_SOURCE_CHANGED"
                manifest["source_artifacts_unchanged"] = False
                manifest["source_changes"] = changed
            elif not result["complete"]:
                manifest["status"] = "STOPPED_INCOMPLETE_OR_BUDGET_FAILURE"
                manifest["stop_reason"] = (
                    f"{case_id}/{arm_name} did not complete cleanly; no later "
                    "arm/case was started")
            else:
                manifest["status"] = "RUNNING"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            if manifest["status"] != "RUNNING":
                raise RuntimeError(manifest["stop_reason"]
                                   if "stop_reason" in manifest
                                   else "source artifacts changed during run")

    manifest["status"] = "COMPLETED"
    manifest["finished_at"] = _now()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)
                             + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True,
                        help="eligible original Match=0 P3 cases, e.g. 90")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--run", action="store_true")
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--timeout-seconds", type=int,
                        default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--resume", action="store_true",
                        help="reconcile a saved arm from per-call log lines and continue missing arms")
    args = parser.parse_args()
    case_ids = parse_case_ids(args.cases)
    result = run(case_ids, args.out, dry_run=args.dry_run, seed=args.seed,
                 timeout_seconds=args.timeout_seconds, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
