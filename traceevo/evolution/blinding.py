"""S3B §2/§13: keep the benchmark label out of the reasoning process.

§4.1 requires that steps 1-4 of a build case — GoS execution, FINAL_REPORT,
trace sealing, artifact hashing — run without access to the gold answer, and
that this be *tested*, not asserted. §13 then requires that no gold ever reach
a planner prompt.

Discipline by convention ("the run just doesn't look at that column") is not
testable, and the whole point of this stage is to stop trusting things that are
not testable. So the run is handed a **blinded copy** of the dataset with the
gold columns physically removed:

    gos/datasets/DiagnosisArena/test_set.csv          (gold present)
        --make_blinded_dataset-->  <run>/dataset_blinded.csv   (gold absent)

and `TRACEEVO_DATASET_CSV` points the loader at the blinded file. During the
run, the label is not in the process's inputs at all. The OutcomeVerifier is a
separate step, after sealing, reading the original file.

The manifest records the hash of both files and the columns removed, so the
claim "this run was blinded" is checkable from the artifacts afterwards.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, List

GOLD_COLUMNS = ("Final Diagnosis", "Right Option")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_blinded_dataset(src_csv: str, dst_csv: str) -> Dict[str, Any]:
    """Write `src_csv` without the gold columns. Returns the manifest."""
    import pandas as pd

    data = pd.read_csv(src_csv)
    present = [c for c in GOLD_COLUMNS if c in data.columns]
    if not present:
        raise ValueError(
            f"{src_csv} has none of {GOLD_COLUMNS}; blinding would be vacuous "
            f"and the isolation claim would be false")
    blinded = data.drop(columns=list(present))
    os.makedirs(os.path.dirname(dst_csv) or ".", exist_ok=True)
    blinded.to_csv(dst_csv, index=False)
    return {
        "source_csv": os.path.abspath(src_csv),
        "source_sha256": sha256_file(src_csv),
        "blinded_csv": os.path.abspath(dst_csv),
        "blinded_sha256": sha256_file(dst_csv),
        "columns_removed": list(present),
        "columns_kept": list(blinded.columns),
        "n_cases": int(len(blinded)),
    }


def verify_blinded(path: str) -> Dict[str, Any]:
    """Confirm a file really has no gold column in it."""
    import pandas as pd

    cols = list(pd.read_csv(path, nrows=1).columns)
    leaked = [c for c in GOLD_COLUMNS if c in cols]
    return {"path": os.path.abspath(path), "columns": cols,
            "gold_columns_present": leaked, "blinded": not leaked}


def gold_by_case_index(src_csv: str) -> List[Dict[str, Any]]:
    """The gold answers, read by position — the verifier's side of the wall.

    Position is the only join key available: GoS identifies a case by its
    row index (`--cases 30-39` are indices), and the blinded file preserves row
    order, so index i in the blinded file is index i here.
    """
    import pandas as pd

    data = pd.read_csv(src_csv)
    out = []
    for i, row in data.iterrows():
        out.append({
            "index": int(i),
            "case_id": row.get("id"),
            "final_diagnosis": row.get("Final Diagnosis", ""),
            "right_option": row.get("Right Option", ""),
        })
    return out
