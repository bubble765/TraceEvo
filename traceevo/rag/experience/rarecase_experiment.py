"""Run a sealed RareArena build cohort and test gated Experience retrieval.

The online GoS process receives only the blinded CSV. Outcome labels are read
by the separate ``--verify`` / ``--admit`` phases after a run is sealed. Every
retry gets its own directory; partial traces and failed attempts are retained.
"""
from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
GOS = ROOT / "gos"
EXPERIMENT_ROOT = ROOT / "traceevo_runs/rag/experience_external/raredisease_rdc_v1"
PREPARED = EXPERIMENT_ROOT / "prepared"
DEFAULT_OUT = EXPERIMENT_ROOT / "isolated_build_v1"
INDEPENDENT_ROOT = EXPERIMENT_ROOT / "independent_cohort_v11"
INDEPENDENT_PREPARED = INDEPENDENT_ROOT / "prepared"
INDEPENDENT_OUT = INDEPENDENT_ROOT / "isolated_build"
SELECTION_HISTORY = EXPERIMENT_ROOT / "selection_history"
PREVIOUS_INDEPENDENT_PREPARED = SELECTION_HISTORY / "independent_cohort_v2" / "prepared"
PREVIOUS_INDEPENDENT_V3_PREPARED = SELECTION_HISTORY / "independent_cohort_v3" / "prepared"
PREVIOUS_INDEPENDENT_V4_PREPARED = SELECTION_HISTORY / "independent_cohort_v4" / "prepared"
PREVIOUS_INDEPENDENT_V5_PREPARED = SELECTION_HISTORY / "independent_cohort_v5" / "prepared"
PREVIOUS_INDEPENDENT_V6_PREPARED = SELECTION_HISTORY / "independent_cohort_v6" / "prepared"
PREVIOUS_INDEPENDENT_V7_PREPARED = SELECTION_HISTORY / "independent_cohort_v7" / "prepared"
PREVIOUS_INDEPENDENT_V8_PREPARED = SELECTION_HISTORY / "independent_cohort_v8" / "prepared"
PREVIOUS_INDEPENDENT_V9_PREPARED = SELECTION_HISTORY / "independent_cohort_v9" / "prepared"
PREVIOUS_INDEPENDENT_V10_PREPARED = SELECTION_HISTORY / "independent_cohort_v10" / "prepared"
DATASET = PREPARED / "dataset_blinded_build.csv"
TASK_PREFIX = "case-"
ARTIFACT_SUFFIXES = (
    "_events.jsonl", "_bindings.json", "_deltas.jsonl", "_integrity.json",
    "_invocations.jsonl", "_injections.jsonl", "_provenance_graph.json",
    "_provenance_graph.png", "_replay.json", "_summary.json",
    "_graph_states.jsonl",
)
_TRANSIENT_RE = re.compile(
    r"LLMTransientError|HTTP\s+(?:429|500|502|503|504)|"
    r"timed?\s*out|timeout|EOF occurred|connection reset|"
    r"remote end closed|temporarily unavailable|api link error",
    re.IGNORECASE,
)


def _json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_cases(spec: str, n_rows: int) -> list[int]:
    cases: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            cases.extend(range(int(start_text), int(end_text) + 1))
        else:
            cases.append(int(part))
    if not cases or len(cases) != len(set(cases)):
        raise ValueError("case selection must be non-empty and contain no duplicates")
    if any(case < 0 or case >= n_rows for case in cases):
        raise ValueError(f"case indices must be in [0, {n_rows - 1}]")
    return cases


def _attempt_dirs(case_dir: Path) -> list[Path]:
    if not case_dir.is_dir():
        return []
    return sorted(case_dir.glob("attempt-*"))


def _artifact_dir(attempt_dir: Path) -> Path:
    trace_root = attempt_dir / "traceevo" / "runs"
    candidates = sorted(path for path in trace_root.glob("*") if path.is_dir())
    return candidates[0] if candidates else trace_root / "missing"


def _task_complete(artifact_dir: Path, task_id: str) -> tuple[bool, str]:
    events = _jsonl(artifact_dir / f"{task_id}_events.jsonl")
    event_types = {event.get("event_type") for event in events}
    required = [
        f"{task_id}_events.jsonl", f"{task_id}_bindings.json",
        f"{task_id}_integrity.json", f"{task_id}_replay.json",
        f"{task_id}_summary.json", f"{task_id}_invocations.jsonl",
    ]
    missing = [name for name in required if not (artifact_dir / name).is_file()]
    if missing:
        return False, "missing=" + ",".join(missing)
    if "FINAL_REPORT" not in event_types:
        return False, "no_FINAL_REPORT"
    integrity = _json(artifact_dir / f"{task_id}_integrity.json", {})
    replay = _json(artifact_dir / f"{task_id}_replay.json", {})
    if not integrity.get("ok"):
        return False, "integrity_not_ok"
    if not replay.get("all_ok"):
        return False, "graph_replay_not_ok"
    return True, "complete"


def _make_seal(attempt_dir: Path, artifact_dir: Path, task_id: str,
               case_index: int, dataset_sha256: str) -> dict[str, Any]:
    files = sorted(path for path in artifact_dir.glob(f"{task_id}_*") if path.is_file())
    hashes = {path.name: _sha256(path) for path in files}
    sealed_hash = hashlib.sha256(json.dumps(
        sorted(hashes.items()), separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {
        "case_index": case_index,
        "task_id": task_id,
        "artifact_dir": str(artifact_dir.relative_to(attempt_dir)),
        "artifacts": hashes,
        "sealed_artifact_hash": sealed_hash,
        "blinded_dataset_sha256": dataset_sha256,
        "sealed_at_epoch": __import__("time").time(),
    }


def check_seal(attempt_dir: Path,
               expected_dataset_hash: str | None = None) -> tuple[bool, str]:
    seal_path = attempt_dir / "seal.json"
    seal = _json(seal_path)
    if not isinstance(seal, dict):
        return False, "seal_missing"
    artifact_dir = attempt_dir / str(seal.get("artifact_dir") or "")
    if (expected_dataset_hash is not None
            and seal.get("blinded_dataset_sha256") != expected_dataset_hash):
        return False, "blinded_dataset_hash_mismatch"
    sealed_names = set((seal.get("artifacts") or {}).keys())
    current_names = {path.name for path in artifact_dir.glob(
        f"{seal.get('task_id', '')}_*") if path.is_file()}
    if sealed_names != current_names:
        return False, "artifact_set_changed"
    current: dict[str, str] = {}
    for filename, sealed_digest in (seal.get("artifacts") or {}).items():
        path = artifact_dir / filename
        if not path.is_file():
            return False, f"artifact_missing:{filename}"
        current[filename] = _sha256(path)
        if current[filename] != sealed_digest:
            return False, f"artifact_changed:{filename}"
    current_seal = hashlib.sha256(json.dumps(
        sorted(current.items()), separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if current_seal != seal.get("sealed_artifact_hash"):
        return False, "manifest_hash_mismatch"
    complete, reason = _task_complete(artifact_dir, seal["task_id"])
    return (True, "verified") if complete else (False, reason)


def _valid_sealed_attempts(case_dir: Path,
                           expected_dataset_hash: str | None = None) -> list[Path]:
    valid: list[Path] = []
    for attempt_dir in _attempt_dirs(case_dir):
        if ((attempt_dir / "seal.json").is_file()
                and check_seal(attempt_dir, expected_dataset_hash)[0]):
            valid.append(attempt_dir)
    return valid


def run_build(out_dir: Path, cases_spec: str, attempts: int,
              stall_timeout: int, prepared_dir: Path = PREPARED) -> dict[str, Any]:
    import pandas as pd
    from traceevo.s2_cases import load_env_file

    # GoS runs with ``cwd=GOS``; resolve all paths here so relative CLI paths
    # cannot redirect reports, logs, or audit traces into the GoS source tree.
    out_dir = out_dir.resolve()
    prepared_dir = prepared_dir.resolve()
    dataset_path = prepared_dir / "dataset_blinded_build.csv"
    if not dataset_path.is_file():
        raise FileNotFoundError(f"blinded build CSV missing: {dataset_path}")
    dataset_hash = _sha256(dataset_path)
    dataframe = pd.read_csv(dataset_path)
    expected_columns = ["Case Information", "Physical Examination", "Diagnostic Tests"]
    if list(dataframe.columns) != expected_columns:
        raise ValueError(
            "the GoS build input must contain exactly the three blinded case fields; "
            f"found columns: {list(dataframe.columns)}"
        )
    manifest = _json(prepared_dir / "selection_manifest.json", {})
    if manifest.get("blinded_build_csv_sha256") != dataset_hash:
        raise ValueError("blinded build CSV does not match its selection manifest")
    cases = parse_cases(cases_spec, len(dataframe))
    load_env_file()
    if not os.environ.get("TRACEEVO_API_KEY", "").strip():
        raise RuntimeError("TRACEEVO_API_KEY is missing; refusing to start model calls")
    out_dir.mkdir(parents=True, exist_ok=True)
    status_rows: list[dict[str, Any]] = []

    for case_index in cases:
        task_id = f"{TASK_PREFIX}{case_index}"
        case_dir = out_dir / "cases" / f"case-{case_index:03d}"
        if _valid_sealed_attempts(case_dir, dataset_hash):
            print(f"{task_id}: already has a valid sealed attempt; skipped", flush=True)
            status_rows.append({"case": case_index, "status": "already_sealed"})
            continue
        existing = _attempt_dirs(case_dir)
        next_attempt = max((int(path.name.rsplit("-", 1)[-1]) for path in existing),
                          default=0) + 1
        completed = False
        for offset in range(attempts):
            attempt_number = next_attempt + offset
            attempt_dir = case_dir / f"attempt-{attempt_number:02d}"
            # Never overwrite a prior run, including a partial failure.
            attempt_dir.mkdir(parents=True, exist_ok=False)
            tag = f"rare_{case_index:03d}_a{attempt_number:02d}"
            artifact_root = attempt_dir / "traceevo"
            env = dict(os.environ)
            env.update({
                "PYTHONPATH": str(ROOT),
                "TRACEEVO_ENABLED": "1",
                "TRACEEVO_LEDGER": "1",
                "TRACEEVO_REPLAY_MODE": "off",
                "TRACEEVO_MEMORY": "0",
                "TRACEEVO_DATASET_CSV": str(dataset_path.resolve()),
                "TRACEEVO_OUTPUT_DIR": str(artifact_root),
            })
            for optional in ("TRACEEVO_GUARD", "TRACEEVO_POST_REPORT_AUDIT",
                             "TRACEEVO_CAPABILITY_PROFILE"):
                env[optional] = "0"
            cmd = [
                sys.executable, "-u", "main.py", "--dataset", "DiagnosisArena",
                "--cases", str(case_index),
                "--out-csv", str(attempt_dir / "gos_results.csv"),
                "--html-dir", str(attempt_dir / "html"),
                "--json-dir", str(attempt_dir / "graphs"),
                "--log-dir", str(attempt_dir / "logs"),
                "--tag", tag,
            ]
            log_path = attempt_dir / "run.log"
            started = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat()
            print(f"{task_id}: attempt {attempt_number} started", flush=True)
            timed_out = False
            return_code = -1
            try:
                with log_path.open("w", encoding="utf-8") as log:
                    process = subprocess.run(
                        cmd, cwd=GOS, env=env, stdout=log,
                        stderr=subprocess.STDOUT, timeout=stall_timeout,
                        check=False,
                    )
                    return_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
            artifact_dir = _artifact_dir(attempt_dir)
            complete, complete_reason = _task_complete(artifact_dir, task_id)
            if complete:
                seal = _make_seal(attempt_dir, artifact_dir, task_id,
                                  case_index, dataset_hash)
                _write_json(attempt_dir / "seal.json", seal)
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
            # Only retry when the tail shows a transient provider/network
            # failure. A long-running or logically stuck diagnostic loop is
            # preserved for review; the outer runner must not mistake every
            # wall-clock timeout for an infrastructure fault.
            recent_log = log_text[-8000:]
            transient = bool(_TRANSIENT_RE.search(recent_log))
            attempt_record = {
                "case": case_index,
                "source_record_id": _jsonl(prepared_dir / "build_case_map.jsonl")[case_index].get("source_record_id"),
                "attempt": attempt_number,
                "started_at": started,
                "finished_at_epoch": __import__("time").time(),
                "returncode": return_code,
                "timed_out": timed_out,
                "complete": complete,
                "completion_check": complete_reason,
                "transient_error_seen": transient,
                "retry_reason": (
                    "transient network/provider failure"
                    if transient and not complete else ""),
                "artifact_dir": str(artifact_dir.relative_to(attempt_dir)),
                "seal_path": "seal.json" if complete else "",
            }
            _append_jsonl(out_dir / "attempt_history.jsonl", attempt_record)
            if complete:
                print(f"{task_id}: sealed attempt {attempt_number}", flush=True)
                completed = True
                status_rows.append({"case": case_index, "status": "sealed",
                                    "attempt": attempt_number})
                break
            print(f"{task_id}: incomplete ({complete_reason}); "
                  f"transient={transient}", flush=True)
            if not transient:
                status_rows.append({"case": case_index, "status": "stopped_non_transient",
                                    "attempt": attempt_number,
                                    "reason": complete_reason})
                break
        if not completed and not any(row.get("case") == case_index for row in status_rows):
            status_rows.append({"case": case_index, "status": "attempt_limit_reached"})

    summary = {
        "dataset": "RareArena RDC",
        "blinded_dataset_sha256": dataset_hash,
        "cases_requested": cases,
        "sealed_cases": [row["case"] for row in status_rows
                         if row["status"] in {"sealed", "already_sealed"}],
        "incomplete_cases": [row for row in status_rows
                              if row["status"] not in {"sealed", "already_sealed"}],
        "attempts": status_rows,
        "label_vault_read": False,
        "memory_injection": False,
    }
    _write_json(out_dir / "run_summary.json", summary)
    return summary


def _load_sealed_runs(
    out_dir: Path, prepared_dir: Path = PREPARED,
) -> list[tuple[int, Path, dict[str, Any]]]:
    rows = []
    cases_root = out_dir / "cases"
    if not cases_root.is_dir():
        return rows
    for case_dir in sorted(cases_root.glob("case-*")):
        try:
            case_index = int(case_dir.name.split("-")[-1])
        except ValueError:
            continue
        dataset_path = prepared_dir / "dataset_blinded_build.csv"
        expected_dataset_hash = _sha256(dataset_path) if dataset_path.is_file() else None
        for attempt_dir in _valid_sealed_attempts(case_dir, expected_dataset_hash):
            rows.append((case_index, attempt_dir, _json(attempt_dir / "seal.json", {})))
            break
    return sorted(rows, key=lambda row: row[0])


def verify_outcomes(out_dir: Path, prepared_dir: Path = PREPARED) -> dict[str, Any]:
    """Verify outcome only after checking every selected run's seal."""
    from traceevo.s2_cases import load_env_file
    from traceevo.evolution.outcome_verifier import make_judge_fn, verify_trace

    out_dir = out_dir.resolve()
    prepared_dir = prepared_dir.resolve()
    sealed = _load_sealed_runs(out_dir, prepared_dir)
    if not sealed:
        raise RuntimeError("no valid sealed runs are available for post-run verification")
    # The label vault is deliberately not opened until the seals above pass.
    labels_path = prepared_dir / "label_vault" / "build_gold.jsonl"
    labels = {int(row["case_index"]): row for row in _jsonl(labels_path)}
    load_env_file()
    judge = make_judge_fn()
    raw_dir = out_dir / "outcome_judge_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "outcome_results.csv"
    cached = {}
    if result_path.is_file():
        with result_path.open(encoding="utf-8", newline="") as stream:
            cached = {int(row["case"]): row for row in csv.DictReader(stream)}
    if not set(cached).issubset({case_index for case_index, _attempt, _seal in sealed}):
        raise ValueError("cached outcomes include a case without a valid current seal")
    rows_by_case: dict[int, dict[str, Any]] = dict(cached)
    for case_index, attempt_dir, seal in sealed:
        task_id = seal["task_id"]
        artifact_dir = attempt_dir / seal["artifact_dir"]
        events = _jsonl(artifact_dir / f"{task_id}_events.jsonl")
        report_event = next((event for event in reversed(events)
                             if event.get("event_type") == "FINAL_REPORT"), None)
        payload = (report_event or {}).get("payload") or {}
        gold = labels.get(case_index)
        if gold is None:
            raise RuntimeError(f"gold label missing for sealed case {case_index}")
        reference_hash = hashlib.sha256(str(gold["diagnosis"]).encode("utf-8")).hexdigest()
        previous = cached.get(case_index)
        if previous is not None:
            if (previous.get("source_record_id") != gold["source_record_id"]
                    or previous.get("sealed_artifact_hash") != seal["sealed_artifact_hash"]
                    or previous.get("reference_sha256") != reference_hash):
                raise ValueError(f"cached outcome provenance mismatch for case-{case_index}")
            if previous.get("judge_raw_sha256"):
                raw_path = raw_dir / f"case-{case_index}.raw.json"
                if not raw_path.is_file() or _sha256(raw_path) != previous["judge_raw_sha256"]:
                    raise ValueError(f"cached judge transcript changed for case-{case_index}")
            rows_by_case[case_index] = previous
            print(f"case-{case_index}: cached {previous['outcome_verdict']}; not rejudged", flush=True)
            continue
        if not payload.get("prediction"):
            rows_by_case[case_index] = {
                "case": case_index, "source_record_id": gold["source_record_id"],
                "outcome_verdict": "uncertain", "judge_scores": "",
                "reason": "sealed run has no prediction", "reference_sha256": reference_hash,
                "sealed_artifact_hash": seal["sealed_artifact_hash"],
            }
            _write_csv(result_path, [rows_by_case[key] for key in sorted(rows_by_case)])
            continue
        result = verify_trace(
            {"prediction": payload.get("prediction", ""),
             "report": payload.get("report", "")},
            str(gold["diagnosis"]), judge, case_index, raw_dir=str(raw_dir),
        )
        rows_by_case[case_index] = {
            "case": case_index,
            "source_record_id": gold["source_record_id"],
            "prediction": payload.get("prediction", ""),
            "outcome_verdict": result["outcome_verdict"],
            "judge_scores": "|".join("" if score is None else str(score)
                                      for score in result["judge_scores"]),
            "judge_unanimous": result["judge_unanimous"],
            "verdict_reason": result["verdict_reason"],
            "reference_sha256": result["reference_sha256"],
            "sealed_artifact_hash": seal["sealed_artifact_hash"],
            "judge_raw_sha256": _sha256(raw_dir / f"case-{case_index}.raw.json"),
        }
        # Persist each completed verdict, including uncertain verdicts; resume
        # only unjudged sealed cases rather than seeking a different verdict.
        _write_csv(result_path, [rows_by_case[key] for key in sorted(rows_by_case)])
        print(f"case-{case_index}: {result['outcome_verdict']} "
              f"scores={result['judge_scores']}", flush=True)

    rows = [rows_by_case[key] for key in sorted(rows_by_case)]
    _write_csv(result_path, rows)
    from collections import Counter
    summary = {
        "n_sealed": len(sealed),
        "verdict_counts": dict(Counter(row["outcome_verdict"] for row in rows)),
        "outcomes": str(result_path.relative_to(ROOT)),
        "gold_opened_after_seal_verification": True,
    }
    _write_json(out_dir / "outcome_summary.json", summary)
    return summary


def prepare_clean_retrieval_data() -> dict[str, Any]:
    """Create a fresh, test-result-free feature/query split over sealed cases."""
    from traceevo.rag.experience.rarecase_adapter import prepare_clean_retrieval_split

    clean_dir = EXPERIMENT_ROOT / "prepared_clean_v22"
    return prepare_clean_retrieval_split(
        EXPERIMENT_ROOT / "RDC_benchmark.jsonl",
        PREPARED,
        clean_dir,
        query_n=10,
        seed=20261009,
        knowledge_dir=ROOT / "traceevo_runs/rag/medical_knowledge/normalized/schema-v5",
    )


def prepare_independent_cohort_data() -> dict[str, Any]:
    """Select a new build/query cohort disjoint from the frozen pilot."""
    from traceevo.rag.experience.rarecase_adapter import prepare_independent_cohort

    return prepare_independent_cohort(
        EXPERIMENT_ROOT / "RDC_benchmark.jsonl",
        INDEPENDENT_PREPARED,
        previous_prepared_dirs=[
            PREPARED, PREVIOUS_INDEPENDENT_PREPARED,
            PREVIOUS_INDEPENDENT_V3_PREPARED, PREVIOUS_INDEPENDENT_V4_PREPARED,
            PREVIOUS_INDEPENDENT_V5_PREPARED, PREVIOUS_INDEPENDENT_V6_PREPARED,
            PREVIOUS_INDEPENDENT_V7_PREPARED, PREVIOUS_INDEPENDENT_V8_PREPARED,
            PREVIOUS_INDEPENDENT_V9_PREPARED, PREVIOUS_INDEPENDENT_V10_PREPARED,
        ],
        build_n=20,
        query_n=10,
        seed=20261010,
        knowledge_dir=ROOT / "traceevo_runs/rag/medical_knowledge/normalized/schema-v5",
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    if not keys:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def _build_clean_success_store(
    memory_sources: list[tuple[Path, Path]],
    out_dir: Path,
) -> dict[str, Any]:
    """Copy admitted successes with only their audited pre-test retrieval keys.

    Source memory files and sealed traces are read-only. The output is a
    separate derived store; entries without a clean feature are omitted.
    """
    from traceevo.rag.experience.rarecase_adapter import (
        _EARLY_TEST_RE, _INITIAL_CLINICAL_CUE_RE, _RETRIEVAL_CUTOFF_RE,
        pretest_retrieval_text,
    )

    if out_dir.exists():
        raise FileExistsError(f"refusing to overwrite clean retrieval store: {out_dir}")
    out_dir.mkdir(parents=True)
    success_dir = out_dir / "success"
    success_dir.mkdir()
    (out_dir / "failure").mkdir()
    (out_dir / "manual_review").mkdir()
    (out_dir / "rejected").mkdir()

    included: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    audit_links: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for memory_root, feature_path in memory_sources:
        features = {
            int(item["case_index"]): item for item in _jsonl(feature_path)
        }
        audit_by_id = {
            str(item.get("memory_id") or ""): item
            for item in _jsonl(memory_root / "memory_audit_links.jsonl")
        }
        for source_path in sorted((memory_root / "success").glob("*.json")):
            entry = _json(source_path, {})
            task_id = str(entry.get("task_id") or "")
            match = re.fullmatch(r"case-(\d+)", task_id)
            if match is None:
                omitted.append({"memory_id": entry.get("memory_id", ""),
                                "reason": "task_id_not_case_indexed"})
                continue
            feature = features.get(int(match.group(1)), {})
            feature_key = str(feature.get("retrieval_text") or "")
            if (not feature.get("retrieval_eligible") or not feature_key.strip()):
                omitted.append({"memory_id": entry.get("memory_id", ""),
                                "reason": "no_clean_pretest_feature"})
                continue
            feature_key_hash = hashlib.sha256(feature_key.encode("utf-8")).hexdigest()
            if feature.get("retrieval_text_sha256") != feature_key_hash:
                raise ValueError(f"clean feature hash mismatch for {task_id}")
            clean_key = pretest_retrieval_text(feature_key)["text"]
            if (len(clean_key) < 50 or not _INITIAL_CLINICAL_CUE_RE.search(clean_key)
                    or _EARLY_TEST_RE.search(clean_key)
                    or _RETRIEVAL_CUTOFF_RE.search(clean_key)):
                omitted.append({"memory_id": entry.get("memory_id", ""),
                                "reason": "current_pretest_cleaning_rejected_feature"})
                continue
            key_hash = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()
            memory_id = str(entry.get("memory_id") or "")
            if not memory_id or memory_id in seen_ids:
                raise ValueError(f"missing or duplicate experience ID: {memory_id!r}")
            seen_ids.add(memory_id)
            audit_link = audit_by_id.get(memory_id)
            if not audit_link:
                raise ValueError(f"missing source audit link for {memory_id}")
            old_key = str(entry.get("task_retrieval_key") or "")
            old_key_hash = hashlib.sha256(old_key.encode("utf-8")).hexdigest()
            entry["task_retrieval_key"] = clean_key
            entry["partition"] = "success"
            entry["retrieval_source_record_id"] = feature.get("source_record_id", "")
            entry["retrieval_source_store"] = str(memory_root)
            target = success_dir / source_path.name
            _write_json(target, entry)
            copied_link = dict(audit_link)
            copied_link["path"] = str(target.resolve())
            audit_links.append(copied_link)
            included.append({
                "memory_id": memory_id,
                "task_id": task_id,
                "source_trace_id": entry.get("source_trace_id", ""),
                "original_key_sha256": old_key_hash,
                "clean_key_sha256": key_hash,
                "feature_key_sha256": feature_key_hash,
                "feature_recleaned": feature_key != clean_key,
                "clean_key_source_record_id": feature.get("source_record_id", ""),
                "source_memory_file_sha256": _sha256(source_path),
            })

    with (out_dir / "memory_audit_links.jsonl").open("w", encoding="utf-8") as stream:
        for row in audit_links:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "policy": "derived success-only store; task retrieval keys replaced with clean pre-test prefixes",
        "memory_sources": [
            {"memory_store": str(memory_root),
             "memory_store_success_count": len(list((memory_root / "success").glob("*.json"))),
             "feature_file": str(feature_path),
             "feature_file_sha256": _sha256(feature_path)}
            for memory_root, feature_path in memory_sources
        ],
        "included_successes": len(included),
        "omitted_successes": len(omitted),
        "included": included,
        "omitted": omitted,
        "source_stores_modified": False,
    }
    _write_json(out_dir / "clean_store_manifest.json", manifest)
    return manifest


def _reconcile_unsupported_schema_probe(
    ledger_rows: list[dict[str, Any]], attempt_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Reclassify only a proved, no-response strict-schema capability probe.

    This is for the already-sealed RareArena pilot, whose original GoS code
    closed an expected unsupported-format probe as ``failed``. The raw ledger
    is never edited. A sidecar records the exact call and log hash. New runs
    use the corrected GoS hook and do not need this reconciliation.
    """
    failed = [row for row in ledger_rows if row.get("parse_status") == "failed"]
    if not failed:
        return copy.deepcopy(ledger_rows), None
    if len(failed) != 1:
        return copy.deepcopy(ledger_rows), None
    probe = failed[0]
    if (probe.get("stage") != "GRAPH_UPDATE"
            or probe.get("response_hash")
            or probe.get("discard_reason") != "OTHER"):
        return copy.deepcopy(ledger_rows), None

    log_files = sorted(attempt_dir.glob("logs_*/Case_*/*.txt"))
    if len(log_files) != 1:
        return copy.deepcopy(ledger_rows), None
    log_path = log_files[0]
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    unsupported_marker = (
        "Structured output mode json_schema_strict is unsupported; "
        "falling back to json_object"
    )
    fallback_marker = "LLM structured output used compatibility mode: json_object"
    if unsupported_marker not in log_text or fallback_marker not in log_text:
        return copy.deepcopy(ledger_rows), None
    fallback_calls = [
        row for row in ledger_rows
        if row.get("stage") == "GRAPH_UPDATE"
        and row.get("parse_status") == "accepted"
        and bool(row.get("response_hash"))
        and row.get("call_id") != probe.get("call_id")
    ]
    if not fallback_calls:
        return copy.deepcopy(ledger_rows), None

    reconciled = copy.deepcopy(ledger_rows)
    target = next(row for row in reconciled
                  if row.get("call_id") == probe.get("call_id"))
    target["parse_status"] = "discarded"
    target["discard_reason"] = "UNSUPPORTED_RESPONSE_FORMAT"
    evidence = {
        "task_id": probe.get("task_id", ""),
        "call_id": probe.get("call_id", ""),
        "stage": probe.get("stage", ""),
        "original_status": "failed",
        "reconciled_status": "discarded",
        "discard_reason": "UNSUPPORTED_RESPONSE_FORMAT",
        "response_hash_was_empty": True,
        "log_path": str(log_path.relative_to(attempt_dir)),
        "log_sha256": _sha256(log_path),
        "unsupported_marker_found": True,
        "compatibility_fallback_marker_found": True,
        "accepted_graph_update_call_ids": [row.get("call_id", "")
                                            for row in fallback_calls],
        "method": "rarecase-capability-probe-reconciliation-v1",
        "raw_sealed_ledger_modified": False,
    }
    return reconciled, evidence


def admit_experiences(
    out_dir: Path,
    *,
    reconcile_capability_probe: bool = False,
    prepared_dir: Path = PREPARED,
) -> dict[str, Any]:
    """Run the existing deterministic trace + outcome gate on sealed runs.

    The optional reconciliation is narrowly scoped to the gateway's known
    strict-schema capability probe. It changes only a derived ledger copy,
    never a sealed source artifact, and saves a hash-linked explanation.
    """
    import pandas as pd
    from traceevo.audit.event_schema import utc_now
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import build_candidate, forbidden_content
    from traceevo.evolution.leakage_guard import scan_prompt
    from traceevo.evolution.memory_formatter import render
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.s3b_gate import _integrity_ok, audit_bundle
    from traceevo.rag.experience.rarecase_adapter import _leak_reasons

    outcomes = {int(row["case"]): row for row in
                csv.DictReader((out_dir / "outcome_results.csv").open(encoding="utf-8"))}
    sealed = _load_sealed_runs(out_dir, prepared_dir)
    build_rows = pd.read_csv(prepared_dir / "dataset_blinded_build.csv")
    build_gold = _jsonl(prepared_dir / "label_vault" / "build_gold.jsonl")
    query_gold = _jsonl(prepared_dir / "label_vault" / "query_gold.jsonl")
    all_gold = build_gold + query_gold
    if prepared_dir.resolve() != PREPARED.resolve():
        all_gold += (
            _jsonl(PREPARED / "label_vault" / "build_gold.jsonl")
            + _jsonl(PREPARED / "label_vault" / "query_gold.jsonl")
        )
    source_map = {int(row["case_index"]): row
                  for row in _jsonl(prepared_dir / "build_case_map.jsonl")}
    retrieval_features = {
        int(row["case_index"]): row
        for row in _jsonl(prepared_dir / "retrieval_build_features_clean.jsonl")
    }
    prediction_by_case = {case: str(row.get("prediction") or "")
                          for case, row in outcomes.items()}
    suffix = "_reconciled" if reconcile_capability_probe else ""
    store_dir = out_dir / f"admitted_memory_store{suffix}"
    result_csv = out_dir / f"experience_admission{suffix}.csv"
    summary_path = out_dir / f"experience_admission_summary{suffix}.json"
    reconciliation_path = out_dir / "ledger_reconciliation.jsonl"
    if store_dir.exists() or result_csv.exists():
        raise FileExistsError(
            f"refusing to overwrite existing admission artifacts: {store_dir}"
        )
    store = MemoryStore(str(store_dir), enabled=True,
                        initial_plan_partitions=("success",))
    admission_rows: list[dict[str, Any]] = []
    reconciliation_rows: list[dict[str, Any]] = []
    for case_index, attempt_dir, seal in sealed:
        task_id = seal["task_id"]
        artifact_dir = attempt_dir / seal["artifact_dir"]
        bundle = audit_bundle(str(artifact_dir), task_id)
        ledger_rows = bundle["ledger"]
        reconciliation = None
        if reconcile_capability_probe:
            ledger_rows, reconciliation = _reconcile_unsupported_schema_probe(
                ledger_rows, attempt_dir
            )
            if reconciliation:
                reconciliation_rows.append(reconciliation)
        row = build_rows.iloc[case_index]
        retrieval_feature = retrieval_features.get(case_index, {})
        if retrieval_features:
            if (not retrieval_feature.get("retrieval_eligible")
                    or not str(retrieval_feature.get("retrieval_text") or "").strip()):
                raise RuntimeError(
                    f"case-{case_index} has no clean, pre-test experience retrieval key"
                )
            retrieval_key = str(retrieval_feature["retrieval_text"])
        else:
            # Backward-compatible path for the original frozen pilot, created
            # before retrieval features were separated from full case input.
            retrieval_key = "\n".join((str(row["Case Information"]),
                                       str(row["Physical Examination"]))).strip()
        verdict = outcomes.get(case_index, {}).get("outcome_verdict", "uncertain")
        trace_hashes = {
            suffix.lstrip("_"): _sha256(artifact_dir / f"{task_id}{suffix}")
            for suffix in ARTIFACT_SUFFIXES
            if (artifact_dir / f"{task_id}{suffix}").is_file()
        }
        candidate = build_candidate(
            task_id, trace_hashes, retrieval_key, verdict,
            bundle["events"], ledger_rows, bundle["summary"],
            [anomaly.get("type", "") for anomaly in bundle["anomalies"]],
        )
        source_gold = next((item for item in build_gold
                            if int(item["case_index"]) == case_index), {})
        input_leak_hits = _leak_reasons({
            "case_report": str(row["Case Information"]),
            "test_results": str(row["Diagnostic Tests"]),
            "diagnosis": source_gold.get("diagnosis", ""),
            "Orpha_name": source_gold.get("orpha_name", ""),
        })
        own_hits = forbidden_content(
            candidate,
            {"final_diagnosis": source_gold.get("diagnosis", "")},
            prediction_by_case.get(case_index, ""),
        )
        rendered = render([candidate.to_dict()])
        cross_hits: list[str] = []
        for other_gold in all_gold:
            same_source_case = (
                other_gold.get("source_record_id")
                == source_gold.get("source_record_id")
            )
            references = [other_gold.get("diagnosis", ""),
                          other_gold.get("orpha_name", "")]
            for reference in dict.fromkeys(str(item).strip() for item in references if item):
                check = scan_prompt(
                    rendered,
                    {"final_diagnosis": reference,
                     "case_id": other_gold.get("source_record_id", "")},
                    source_predictions=(
                        [prediction_by_case.get(case_index, "")]
                        if same_source_case else []
                    ),
                    source_case_text=retrieval_key if same_source_case else "",
                )
                for leak in check["leaks"]:
                    cross_hits.append(
                        f"{other_gold.get('source_record_id')}:{leak['kind']}:{reference[:80]}"
                    )
        forbidden = own_hits + sorted(set(cross_hits)) + [
            f"source_input_label_leak:{reason}" for reason in input_leak_hits
        ]
        admission = gate.evaluate(
            candidate, {"decisions": bundle["decisions"]}, ledger_rows,
            bundle["anomalies"], _integrity_ok(bundle),
            bool(bundle["semantic_complete"]), forbidden_hits=forbidden,
        )
        candidate.eligibility = admission["eligibility"]
        candidate.gate_reasons = admission["reasons"]
        candidate.created_at = utc_now()
        stored = store.add(candidate, admission["eligibility"])
        admission_rows.append({
            "case": case_index,
            "source_record_id": source_map.get(case_index, {}).get("source_record_id", ""),
            "outcome_verdict": verdict,
            "eligibility": admission["eligibility"],
            "partition": stored["partition"],
            "stored": stored["stored"],
            "gate_reasons": " | ".join(admission["reasons"]),
            "gold_leak_hits": " | ".join(forbidden),
            "ledger_reconciled": bool(reconciliation),
            "ledger_reconciliation_call_id": (
                reconciliation.get("call_id", "") if reconciliation else ""),
            "expert_sequence": " -> ".join(
                candidate.payload.get("strategy", {}).get("expert_sequence", [])),
        })
    _write_csv(result_csv, admission_rows)
    if reconciliation_rows:
        with reconciliation_path.open("x", encoding="utf-8") as stream:
            for item in reconciliation_rows:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    from collections import Counter
    counts = Counter(row["eligibility"] for row in admission_rows)
    summary = {
        "cases_audited": len(admission_rows),
        "eligibility_counts": dict(counts),
        "admitted_successes": len(store.entries("success")),
        "admitted_failures": len(store.entries("failure")),
        "manual_review": len(store.entries("manual_review")),
        "rejected": len(store.entries("rejected")),
        "reconciled_unsupported_response_format_probes": len(reconciliation_rows),
        "reconciliation_sidecar": (
            str(reconciliation_path.relative_to(ROOT))
            if reconciliation_rows else ""),
        "admission_results": str(result_csv.relative_to(ROOT)),
        "store_dir": str(store_dir.relative_to(ROOT)),
    }
    _write_json(summary_path, summary)
    return summary


def evaluate_retrieval(out_dir: Path, qdrant_dir: Path,
                       device: str = "auto", *,
                       reconciled: bool = False,
                       memory_dir: Path | None = None,
                       query_path: Path | None = None,
                       result_suffix: str | None = None,
                       collection_name: str | None = None) -> dict[str, Any]:
    """Rank blinded holdout presentations; scores are diagnostic, not calibrated."""
    from traceevo.rag.experience.embedding import Qwen3ExperienceEmbedder
    from traceevo.rag.experience.indexer import ExperienceIndexer
    from traceevo.rag.experience.retriever import ExperienceRetriever
    from traceevo.rag.experience.vector_store import QdrantExperienceStore
    from traceevo.evolution.memory_store import MemoryStore

    suffix = "_reconciled" if reconciled else ""
    output_suffix = result_suffix if result_suffix is not None else suffix.lstrip("_")
    output_suffix = f"_{output_suffix}" if output_suffix else ""
    memory_path = memory_dir or (out_dir / f"admitted_memory_store{suffix}")
    summary_path = out_dir / f"retrieval_summary{output_suffix}.json"
    rankings_path = out_dir / f"retrieval_rankings{output_suffix}.csv"
    success_entries = sorted((memory_path / "success").glob("*.json"))
    if not success_entries:
        summary = {"indexed_successes": 0, "n_queries": 0,
                   "status": "no_verified_success_experiences_to_retrieve"}
        _write_json(summary_path, summary)
        return summary
    memories = MemoryStore(str(memory_path), enabled=True,
                           initial_plan_partitions=("success",))
    embedder = Qwen3ExperienceEmbedder(device=device)
    vector_store = QdrantExperienceStore(
        qdrant_dir,
            collection_name=(collection_name or (
                "traceevo_rarecase_experience_reconciled_v1"
                if reconciled else "traceevo_rarecase_experience_v1"
            )),
        dimension=embedder.dimension,
    )
    try:
        indexed = ExperienceIndexer(embedder, vector_store).index_memory_store(memories)
        if indexed.validated_success == 0:
            summary = {"indexed_successes": 0, "n_queries": 0,
                       "status": "no_success_partition_records_passed_index_contract"}
            _write_json(summary_path, summary)
            return summary
        from traceevo.rag.medical_knowledge.reranker import Qwen3Reranker
        from traceevo.rag.experience.prompts import EXPERIENCE_RERANK_INSTRUCTION
        reranker = Qwen3Reranker(
            device=device, instruction=EXPERIENCE_RERANK_INSTRUCTION,
        )
        retriever = ExperienceRetriever(
            embedder, vector_store, memories, reranker=reranker,
            retrieve_k=max(20, indexed.validated_success), final_k=5,
            min_reranker_score=-1_000_000.0,
        )
        queries = _jsonl(query_path or (PREPARED / "retrieval_queries_blinded.jsonl"))
        source_entries_by_id = {
            json.loads(path.read_text(encoding="utf-8")).get("memory_id"): json.loads(
                path.read_text(encoding="utf-8")
            )
            for path in success_entries
        }
        result_rows: list[dict[str, Any]] = []
        score_values: list[float] = []
        for query in queries:
            result = retriever.retrieve(
                query["initial_presentation"], query_id=query["query_id"],
                channel="initial_plan",
            )
            for rank, hit in enumerate(result.hits, start=1):
                score = float(hit.metadata["reranker_score"])
                score_values.append(score)
                # The source summary is kept in this offline audit file only.
                source_entry = source_entries_by_id.get(hit.source_id, {})
                result_rows.append({
                    "query_id": query["query_id"],
                    "query_source_record_id": query["source_record_id"],
                    "rank": rank,
                    "experience_id": hit.source_id,
                    "source_trace_id": "|".join(hit.source_trace_ids),
                    "experience_source_record_id": source_entry.get("retrieval_source_record_id", ""),
                    "experience_source_store": source_entry.get("retrieval_source_store", ""),
                    "reranker_score": score,
                    "vector_score": hit.metadata.get("vector_score"),
                    "source_initial_presentation_for_review": source_entry.get("task_retrieval_key", ""),
                    "injected_strategy": hit.text,
                })
            print(f"{query['query_id']}: {len(result.hits)} ranked success memories",
                  flush=True)
        _write_csv(rankings_path, result_rows)
        nonempty = len({row["query_id"] for row in result_rows})
        try:
            qdrant_label = str(qdrant_dir.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            qdrant_label = str(qdrant_dir.resolve())
        summary = {
            "n_queries": len(queries),
            "queries_with_ranked_success": nonempty,
            "indexed_successes": indexed.validated_success,
            "indexed_failures": indexed.validated_failure,
            "returned_top_k": 5,
            "reranker_threshold": "disabled for ranking inspection; not calibrated",
            "reranker_score_kind": "cross_encoder_logit; not a probability",
            "ranked_hits": len(result_rows),
            "mean_returned_reranker_score": (
                sum(score_values) / len(score_values) if score_values else None),
            "quality_metrics": (
                "Recall@K/Precision@K not computed: no human relevance labels. "
                f"Inspect {rankings_path.name} and annotate task-strategy relevance first."),
            "qdrant_dir": qdrant_label,
            "collection": vector_store.collection_name,
            "query_file": str((query_path or (PREPARED / "retrieval_queries_blinded.jsonl"))),
            "query_file_sha256": _sha256(query_path or (PREPARED / "retrieval_queries_blinded.jsonl")),
        }
        _write_json(summary_path, summary)
        return summary
    finally:
        vector_store.close()


def evaluate_independent_clean_retrieval(
    qdrant_dir: Path | None = None,
    device: str = "auto",
) -> dict[str, Any]:
    """Evaluate accumulated clean-key successes on a fresh blind cohort."""
    old_features = EXPERIMENT_ROOT / "prepared_clean_v22" / "retrieval_build_features_clean.jsonl"
    new_features = INDEPENDENT_PREPARED / "retrieval_build_features_clean.jsonl"
    old_memory = DEFAULT_OUT / "admitted_memory_store_reconciled"
    new_memory = INDEPENDENT_OUT / "admitted_memory_store_reconciled"
    required = (old_features, new_features, old_memory, new_memory,
                INDEPENDENT_PREPARED / "retrieval_queries_clean_blinded.jsonl")
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("independent clean retrieval inputs missing: " + ", ".join(missing))

    clean_memory = INDEPENDENT_OUT / "clean_initial_plan_memory_store_v1"
    store_manifest = _build_clean_success_store(
        [(old_memory, old_features), (new_memory, new_features)], clean_memory,
    )
    if not store_manifest["included_successes"]:
        raise RuntimeError("no success experiences have eligible clean pre-test keys")
    qdrant_dir = qdrant_dir or (INDEPENDENT_ROOT / "qdrant_experience_clean_v11")
    summary = evaluate_retrieval(
        INDEPENDENT_OUT,
        qdrant_dir,
        device,
        memory_dir=clean_memory,
        query_path=INDEPENDENT_PREPARED / "retrieval_queries_clean_blinded.jsonl",
        result_suffix="clean_independent_v11",
        collection_name="traceevo_rarecase_experience_clean_independent_v11",
    )
    summary["clean_success_store"] = str(clean_memory.relative_to(ROOT))
    summary["clean_successes_included"] = store_manifest["included_successes"]
    summary["clean_successes_omitted"] = store_manifest["omitted_successes"]
    summary["old_sealed_artifacts_modified"] = False
    summary["prior_heldout_cases_rerun"] = False
    summary_path = INDEPENDENT_OUT / "retrieval_summary_clean_independent_v11.json"
    _write_json(summary_path, summary)
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_mutually_exclusive_group(required=True)
    phases.add_argument("--run", action="store_true")
    phases.add_argument("--verify", action="store_true")
    phases.add_argument("--prepare-clean-retrieval", action="store_true")
    phases.add_argument("--prepare-independent-cohort", action="store_true")
    phases.add_argument("--admit", action="store_true")
    phases.add_argument("--admit-reconciled", action="store_true")
    phases.add_argument("--retrieve", action="store_true")
    phases.add_argument("--retrieve-reconciled", action="store_true")
    phases.add_argument("--retrieve-independent-clean", action="store_true")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--prepared-dir", type=Path, default=PREPARED)
    parser.add_argument("--cases", default="0-19")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--stall-timeout", type=int, default=7200)
    parser.add_argument("--qdrant-dir", type=Path, default=None)
    parser.add_argument("--device", default="auto",
                        choices=("auto", "cpu", "mps", "cuda"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.run:
        result = run_build(args.out, args.cases, args.attempts,
                           args.stall_timeout, args.prepared_dir)
    elif args.verify:
        result = verify_outcomes(args.out, args.prepared_dir)
    elif args.prepare_clean_retrieval:
        result = prepare_clean_retrieval_data()
    elif args.prepare_independent_cohort:
        result = prepare_independent_cohort_data()
    elif args.admit:
        result = admit_experiences(args.out, reconcile_capability_probe=False,
                                   prepared_dir=args.prepared_dir)
    elif args.admit_reconciled:
        result = admit_experiences(args.out, reconcile_capability_probe=True,
                                   prepared_dir=args.prepared_dir)
    elif args.retrieve_independent_clean:
        result = evaluate_independent_clean_retrieval(args.qdrant_dir, args.device)
    elif args.retrieve_reconciled:
        result = evaluate_retrieval(
            args.out, args.qdrant_dir or (EXPERIMENT_ROOT / "qdrant_experience_reconciled_v1"),
            args.device, reconciled=True)
    else:
        result = evaluate_retrieval(
            args.out, args.qdrant_dir or (EXPERIMENT_ROOT / "qdrant_experience_v1"),
            args.device)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
