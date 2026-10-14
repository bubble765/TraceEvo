"""Import only DiagnosisArena ``Diagnostic Tests`` into the patient store.

Gold columns (Final Diagnosis / Options / Right Option) are never read. The
row position is used as ``case-{index}``, matching GoS and TraceEvo task IDs.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from traceevo.rag.patient_evidence.normalizer import (
    extract_exam_types,
    modality_for_exam_type,
)
from traceevo.rag.patient_evidence.records import make_record
from traceevo.rag.patient_evidence.store import PatientEvidenceStore


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = ROOT / "gos" / "datasets" / "DiagnosisArena" / "test_set.csv"
DEFAULT_DB = ROOT / "traceevo_runs" / "rag" / "patient_evidence.sqlite3"
_EMPTY_VALUES = {"", "-", "--", "n/a", "na", "nan", "none", "null"}

# Preserve source delimiters by splitting at zero-width boundaries. The source
# CSV is semi-structured: items are usually separated by numbered bullets or
# a new section label, but some rows are plain prose and remain one record.
_ITEM_BOUNDARY = re.compile(r"(?=\s+\d+[.)]\s+)")
_SECTION_BOUNDARY = re.compile(
    r"(?=\s+[-*]\s*(?=[A-Za-z][A-Za-z /()'-]{1,48}:))")
_NEWLINE_BOUNDARY = re.compile(r"(?<=\n)")


def _field(mapping: dict[str, Any], *names: str) -> str:
    by_key = {str(k).strip().casefold(): v for k, v in mapping.items()}
    for name in names:
        value = by_key.get(name.casefold())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _preferred_exam_types(kinds: list[str]) -> list[str]:
    specific = [kind for kind in kinds
                if kind not in {"imaging", "blood_test", "diagnostic_tests"}]
    return specific or kinds


def _structured_fragments(value: Any, path: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Flatten common JSON/Python-literal test records without using eval."""
    fragments: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            fragments.extend(_structured_fragments(item, path))
        return fragments
    if not isinstance(value, dict):
        return fragments

    test_type = _field(value, "test type", "exam type", "test", "modality")
    title = _field(value, "image title", "test title", "title", "name")
    description = _field(
        value, "image description", "description", "findings", "result", "text")
    if test_type or title or description:
        label = " ".join(x for x in (test_type, title, *path) if x)
        lines = []
        if test_type:
            lines.append(f"Test Type: {test_type}")
        if title:
            lines.append(f"Image Title: {title}")
        if description:
            lines.append(f"Image Description: {description}")
        text = "\n".join(lines).strip()
        kinds = _preferred_exam_types(extract_exam_types(label))
        if not kinds:
            kinds = _preferred_exam_types(extract_exam_types(description))
        if not kinds:
            kinds = ["diagnostic_tests"]
        fragments.extend((kind, text) for kind in kinds)
        return fragments

    for key, child in value.items():
        child_path = (*path, str(key))
        if isinstance(child, (dict, list)):
            fragments.extend(_structured_fragments(child, child_path))
    return fragments


def _parse_structured(raw: str) -> list[tuple[str, str]]:
    stripped = raw.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return []
    parsed: Any
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            return []
    return _structured_fragments(parsed)


def _plain_fragments(raw: str) -> list[tuple[str, str]]:
    parts = [raw.strip()]
    for splitter in (_SECTION_BOUNDARY, _ITEM_BOUNDARY, _NEWLINE_BOUNDARY):
        parts = [piece.strip() for part in parts for piece in splitter.split(part)
                 if piece.strip()]

    out: list[tuple[str, str]] = []
    for text in parts:
        kinds = extract_exam_types(text)
        kinds = _preferred_exam_types(kinds)
        # Do not persist a heading by itself as if it were an examination result.
        heading_only = re.sub(r"^[\s:*#-]+|[\s:*#-]+$", "", text).casefold()
        if heading_only in {
            "imaging studies", "laboratory tests", "lab tests", "diagnostic tests",
            "pathological findings", "biopsy findings", "endoscopic findings",
        }:
            continue
        if not kinds:
            kinds = ["diagnostic_tests"]
        out.extend((kind, text) for kind in kinds)
    return out


def records_from_diagnostic_tests(task_id: str, value: Any
                                  ) -> list:
    """Convert one raw source field into stable, typed evidence records."""
    if value is None:
        return []
    raw = str(value).strip()
    if raw.casefold() in _EMPTY_VALUES:
        return []

    fragments = _parse_structured(raw) or _plain_fragments(raw)
    records = []
    seen: set[tuple[str, str]] = set()
    for exam_type, text in fragments:
        text = text.strip()
        key = (exam_type, text)
        if not text or key in seen:
            continue
        seen.add(key)
        title = ("DiagnosisArena Diagnostic Tests" if exam_type == "diagnostic_tests"
                 else f"DiagnosisArena Diagnostic Tests · {exam_type}")
        records.append(make_record(
            task_id=task_id,
            modality=modality_for_exam_type(exam_type),
            exam_type=exam_type,
            title=title,
            text=text,
        ))
    return records


def iter_csv_records(csv_path: str | Path,
                     case_indices: Iterable[int] | None = None):
    selected = set(case_indices) if case_indices is not None else None
    with open(csv_path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "Diagnostic Tests" not in reader.fieldnames:
            raise ValueError("CSV must contain the 'Diagnostic Tests' column")
        # No gold-column names are dereferenced below.
        for row_index, row in enumerate(reader):
            if selected is not None and row_index not in selected:
                continue
            task_id = f"case-{row_index}"
            yield row_index, records_from_diagnostic_tests(
                task_id, row.get("Diagnostic Tests"))


def import_csv(csv_path: str | Path, db_path: str | Path,
               case_indices: Iterable[int] | None = None) -> dict[str, int]:
    n_tasks = 0
    records = []
    for _case_index, task_records in iter_csv_records(csv_path, case_indices):
        n_tasks += 1
        records.extend(task_records)
    with PatientEvidenceStore(db_path) as store:
        n_written = store.upsert_records(records)
        total = store.count()
    return {"tasks_seen": n_tasks, "records_written": n_written,
            "records_in_store": total}


def _parse_case_indices(value: str | None) -> list[int] | None:
    if not value:
        return None
    try:
        cases = [int(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("case indices must be comma-separated integers") from exc
    if any(index < 0 for index in cases):
        raise argparse.ArgumentTypeError("case indices must be non-negative")
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--case-indices",
        help="optional zero-based GoS row indices, e.g. 0,1,80; default imports all rows")
    args = parser.parse_args(argv)
    try:
        summary = import_csv(args.csv, args.db,
                             _parse_case_indices(args.case_indices))
    except (OSError, ValueError) as exc:
        print(f"Patient Evidence import failed: {exc}", file=sys.stderr)
        return 2
    print(f"CSV: {args.csv}")
    print(f"SQLite: {args.db}")
    print("  tasks {tasks_seen} · records upserted {records_written} · "
          "records currently stored {records_in_store}".format(**summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
