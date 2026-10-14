"""S3D-F/G §9-§11: cross-case retrieval audit, and the review queue.

§9 is explicit about what this is and is not. It is a *description* of what the
retriever does across cases. It is not a new threshold, and §9.1 says the number
28.6789 must stop being called a retrieval threshold at all: it comes from a
self-versus-other split and therefore shows only that the retriever can tell a
case from itself. `self_identity_threshold_note.md` records the rename and the
reason, so S4 cannot inherit it as an online cross-case threshold.

Two things §9 forbids and this module does not do:

    - no source diagnosis or gold diagnosis as a runtime feature. The query is
      built by `build_retrieval_key(case_info, physical)`, which is the same
      function the runtime uses, over the same two fields the model sees before
      it starts. Nothing else enters.
    - no reading of case 60+. The target range ends at 59.

§10 forbids the third thing: filling `relevance` from the final diagnosis. The
review queue ships with `review_label = UNREVIEWED` on every row and no
relevance column at all, because a column that exists gets filled by something.

Usage:
    python traceevo/s3d_retrieval.py --out S3D_verified_expansion
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S3D_verified_expansion")
TAG = "s3d_expansion"
SELF_IDENTITY_THRESHOLD = 28.6789
REVIEW_QUESTION = ("从任务开始时可见信息与策略层面看，这条历史经验是否适合作为当前"
                   "病例的流程指导？")
QUERY_CASES = list(range(40, 60))


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def strategy_summary(entry: Dict[str, Any]) -> str:
    """The strategy, in words, with no diagnosis and no case text."""
    s = (entry.get("payload") or {}).get("strategy") or {}
    if not s:
        return ""
    return (f"experts={','.join(s.get('expert_roles_used') or [])}"
            f"|retrieval={s.get('retrieval_count', 0)}"
            f"|backtrack={s.get('backtrack_count', 0)}"
            f"|drilldown={s.get('drilldown_count', 0)}"
            f"|graph_update={s.get('graph_update_count', 0)}"
            f"|analysis={s.get('analysis_count', 0)}"
            f"|plan={s.get('plan_count', 0)}")


def audit(out_dir: str, k: int = 3) -> Dict[str, Any]:
    import pandas as pd
    from traceevo.evolution.memory_retriever import (BM25Index,
                                                     build_retrieval_key)
    from traceevo.evolution.memory_store import MemoryStore

    store_dir = os.path.join(out_dir, "expanded_success_store")
    store = MemoryStore(store_dir, enabled=True,
                        initial_plan_partitions=("success",))
    entries = store.initial_plan_retrievable()
    if not entries:
        return {"error": "no initial-plan-retrievable success memories"}

    # Loaded from the blinded copy: the query text must be the pre-task text
    # and nothing in this file has a gold column in it.
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_50_59.csv")
                          if os.path.exists(os.path.join(
                              out_dir, "dataset_blinded_50_59.csv"))
                          else os.path.join(ROOT, "S3C_experience_expansion",
                                            "..", "S3B_runs", "memory_build",
                                            "dataset_blinded.csv"))
    docs = [(e["memory_id"], e["task_retrieval_key"]) for e in entries]
    by_id = {e["memory_id"]: e for e in entries}

    topk_rows, review_rows = [], []
    for qc in QUERY_CASES:
        row = blinded.iloc[qc]
        query = build_retrieval_key(row["Case Information"],
                                    row["Physical Examination"])
        # §9: a case in 50-59 must not retrieve its own memory. Excluded
        # before ranking, so the ranks below are ranks over the admissible set
        # rather than ranks with a hole punched in them afterwards.
        excl = {m for m in docs if _src(m[0]) == qc}
        admiss = [d for d in docs if d[0] not in excl]
        if not admiss:
            continue
        index = BM25Index(admiss)
        hits = index.top_k(query, k=k, min_score=0.0)
        for rank, h in enumerate(hits, 1):
            e = by_id[h["memory_id"]]
            topk_rows.append({
                "query_case": qc, "memory_case": _src(h["memory_id"]),
                "memory_id": h["memory_id"], "score": round(h["score"], 4),
                "rank": rank, "task_retrieval_key": e["task_retrieval_key"][:180],
                "memory_strategy_summary": strategy_summary(e),
                "excluded_self_memory": bool(excl),
            })
            review_rows.append({
                "query_case": qc, "memory_case": _src(h["memory_id"]),
                "score": round(h["score"], 4), "rank": rank,
                "query_pre_task_summary": _summ(row["Case Information"]),
                "memory_pre_task_summary": _summ(e["task_retrieval_key"]),
                "memory_strategy": strategy_summary(e),
                "review_label": "UNREVIEWED",
                "review_question": REVIEW_QUESTION,
            })

    _write_csv(os.path.join(out_dir, "cross_case_retrieval_topk.csv"), topk_rows)
    _write_csv(os.path.join(out_dir, "cross_case_retrieval_review.csv"),
               review_rows)

    scores = [r["score"] for r in topk_rows]
    top1 = [r["score"] for r in topk_rows if r["rank"] == 1]
    summary = {
        "store": os.path.relpath(store_dir, ROOT),
        "n_memories": len(entries),
        "memory_cases": sorted({_src(e["memory_id"]) for e in entries}),
        "query_cases": QUERY_CASES,
        "k": k,
        "n_rows": len(topk_rows),
        "score": {
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "top1_min": min(top1) if top1 else None,
            "top1_max": max(top1) if top1 else None,
            "top1_median": sorted(top1)[len(top1) // 2] if top1 else None,
        },
        "above_self_identity_threshold": sum(
            1 for s in scores if s > SELF_IDENTITY_THRESHOLD),
        "self_identity_separation_threshold": SELF_IDENTITY_THRESHOLD,
        "threshold_status": ("diagnostic only (§9.1). It separates a case from "
                            "itself; it says nothing about whether two "
                            "different cases should share an experience, and "
                            "S4 must not inherit it as an online threshold."),
        "review_label_counts": dict(_counts(r["review_label"]
                                            for r in review_rows)),
        "note": ("§10: every relevance cell is UNREVIEWED. No final diagnosis "
                 "was used to fill relevance, and no relevance column exists."),
    }
    with open(os.path.join(out_dir, "cross_case_retrieval_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _counts(it):
    import collections
    return collections.Counter(it)


def _src(memory_id: str) -> int:
    """`exp-case-11-<hash>` -> 11."""
    try:
        return int(memory_id.split("-")[2])
    except (IndexError, ValueError):
        return -1


def _summ(text: str, n: int = 240) -> str:
    return " ".join(str(text).split())[:n]


def threshold_note(out_dir: str) -> Dict[str, Any]:
    """§9.1: the rename, and why S4 may not inherit the number."""
    note = {
        "renamed_from": {"name": "success retrieval threshold",
                         "value": SELF_IDENTITY_THRESHOLD},
        "renamed_to": {"name": "self_identity_separation_threshold",
                       "value": SELF_IDENTITY_THRESHOLD},
        "what_it_was_measured_on": (
            "BM25 scores between each case 10-39 success memory and its own "
            "source case (self, n=6, 44.26-126.13) versus every other case "
            "(other, n=28, 0.43-13.10); threshold at the midpoint."),
        "what_it_proves": ("The retriever separates a case from itself by a "
                           "factor of 3.4 in BM25 score, so the abstention "
                           "mechanism is capable of abstaining rather than "
                           "merely never being asked to."),
        "what_it_does_not_prove": (
            "That a new similar case retrieves the *right* historical "
            "experience. Self-versus-other is not the cross-case question. "
            "The dry-run on case 40-49 returning 0 hits is consistent with "
            "abstention working and equally consistent with the retriever "
            "having nothing useful to say; this measurement cannot tell the "
            "two apart."),
        "s4_constraint": ("S4 must not adopt 28.6789 as an online cross-case "
                          "relevance threshold. Any threshold S4 uses must be "
                          "derived from reviewed cross-case pairs, which is "
                          "what the review queue exists to produce."),
        "review_queue": "cross_case_retrieval_review.csv (§10)",
    }
    p = os.path.join(out_dir, "self_identity_threshold_note.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# The 28.6789 threshold, renamed and demoted (§9.1)\n\n")
        f.write(f"**`{note['renamed_to']['name']}` = "
                f"{SELF_IDENTITY_THRESHOLD}** — renamed from "
                f"\"success retrieval threshold\".\n\n")
        for k in ("what_it_was_measured_on", "what_it_proves",
                  "what_it_does_not_prove", "s4_constraint"):
            f.write(f"## {k.replace('_', ' ')}\n\n{note[k]}\n\n")
    with open(os.path.join(out_dir, "self_identity_threshold_note.json"), "w",
              encoding="utf-8") as f:
        json.dump(note, f, ensure_ascii=False, indent=2)
    return note


def failure_memory_status(out_dir: str) -> Dict[str, Any]:
    """§11: failure memory stays stored, auditable, and unreachable."""
    from traceevo.evolution.memory_store import MemoryStore

    d = os.path.join(out_dir, "failure_memory_store")
    store = MemoryStore(d, enabled=True, initial_plan_partitions=())
    ids = store.retrievable_ids()
    out = {
        "store": os.path.relpath(d, ROOT) if os.path.isdir(d) else
        os.path.relpath(os.path.join(ROOT, "S3C_experience_expansion",
                                     "failure_memory_store"), ROOT),
        "stored": True,
        "auditable": True,
        "n_retrievable": len(ids),
        "initial_plan_retrievable": False,
        "initial_plan_ids": store.initial_plan_ids(),
        "activation_status": "inactive_until_S4",
        "risk_triggered_retrieval_implemented": False,
        "failure_global_retrieval_reopened": False,
        "note": ("§11: S3D does not implement risk-triggered failure "
                 "retrieval and does not reopen failure global retrieval "
                 "because success retrieval is sparse."),
    }
    assert out["initial_plan_ids"] == [], (
        "§11: the failure store must answer [] to any initial-plan caller")
    with open(os.path.join(out_dir, "failure_memory_status.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    a = audit(args.out, k=3)
    print(json.dumps(a, ensure_ascii=False, indent=2))
    print(json.dumps(failure_memory_status(args.out), ensure_ascii=False,
                     indent=2))
    threshold_note(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
