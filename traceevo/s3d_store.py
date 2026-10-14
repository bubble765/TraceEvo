"""S3D-D/E §7-§8: extend the headline success store, and poison the new entries.

§7 admits only new gold-safe successes, and lists seven things each entry must
carry. Six are checked as presence; the fourth is the one that costs something:

    gold diagnosis absent
    prediction absent
    reference answer absent

"Absent" is checked against the *rendered* memory, not against the payload
dict, because the payload is not what the model sees — `render()` is. A payload
free of gold that renders to a string containing gold would pass a
payload-only check and leak in production, which is the check that would have
mattered least while looking like the one that mattered most.

The scan is run twice for the same reason. The self-scan is S3C's: each memory
against its own source case. On its own it is close to vacuous, since the
payload is built from audit summaries rather than trace text. The cross-scan is
the real one: every memory against every *other* case's gold, which is the leak
a retrieval could actually cause. §9's query set (40-49, 50-59) supplies the
targets; 50-59 memories are excluded from their own case, per §9.

Usage:
    python traceevo/s3d_store.py --out S3D_verified_expansion
"""
from __future__ import annotations

import argparse
import collections
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
S3C_STORE = os.path.join(ROOT, "S3C_experience_expansion",
                         "success_memory_store")
S3C_OUT = os.path.join(ROOT, "S3C_experience_expansion")

# §7's required fields, as they appear on a stored record.
REQUIRED_FIELDS = ("source_trace_hashes", "audit_summary_hash",
                   "outcome_verdict", "extractor_version")


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def new_gold_safe(out_dir: str) -> List[int]:
    """The cases §7 may admit: gold-safe, from block 50-59 only."""
    p = os.path.join(out_dir, "gold_safe_triage_50_59.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return sorted(int(r["case"]) for r in csv.DictReader(f)
                      if r["gold_safe"] == "True")


def build_store(out_dir: str, trace_hash_fn) -> Dict[str, Any]:
    """§7: extend the success store with the new gold-safe successes."""
    from traceevo.evolution import experience_gate as gate
    from traceevo.evolution.experience import (build_candidate,
                                               forbidden_content)
    from traceevo.evolution.memory_retriever import build_retrieval_key
    from traceevo.evolution.blinding import gold_by_case_index
    from traceevo.s3c_expand import gold_safe_conditions
    import pandas as pd

    cases = new_gold_safe(out_dir)
    artifacts = os.path.join(out_dir, "runs_50_59", "traceevo", "runs", TAG)
    blinded = pd.read_csv(os.path.join(out_dir, "dataset_blinded_50_59.csv"))
    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    with open(os.path.join(out_dir, "outcome_results_50_59.csv"),
              encoding="utf-8") as f:
        verdicts = {int(r["case"]): r for r in csv.DictReader(f)}

    store_dir = os.path.join(out_dir, "expanded_success_store")
    # §8's separation is structural and carries over: only "success" is
    # initial-plan reachable in this store.
    from traceevo.evolution.memory_store import MemoryStore
    store = MemoryStore(store_dir, enabled=True,
                        initial_plan_partitions=("success",))

    rows, admitted = [], []
    for c in cases:
        task = f"case-{c}"
        g = gold_safe_conditions(artifacts, task)
        b = g["bundle"]
        row = blinded.iloc[c]
        key = build_retrieval_key(row["Case Information"],
                                  row["Physical Examination"])
        cand = build_candidate(task, trace_hash_fn(artifacts, task), key,
                               verdicts[c]["outcome_verdict"], b["events"],
                               b["ledger"], b["summary"],
                               [a["type"] for a in b["anomalies"]])
        cand.payload["corpus_source"] = "s3d_expansion"
        cand.payload["headline_eligible"] = True
        verdict = gate.evaluate(cand, {"decisions": b["decisions"]}, b["ledger"],
                                b["anomalies"],
                                g["conditions"]["integrity_ok"],
                                g["conditions"]["semantic_trace_complete"],
                                forbidden_hits=forbidden_content(cand, {}))
        self_gold = gold.get(c) or {}
        # The presence checks are the frozen leakage guard's, run over the
        # *rendered* memory -- not an ad-hoc substring test over the payload.
        # The first version wrote `right_option.lower() not in payload_text`,
        # which for a single-letter option is a test for the letter "d" in any
        # English text. It reported `reference_answer_absent = False` for a
        # payload containing no answer at all, and would have reported `True`
        # for a payload that did contain one, since the guard's answer-cue
        # requirement was bypassed. This is S3B FAILURE 4 (`exact_right_option`
        # dead code, found by spike-in control) recurring in new code, which is
        # the argument for calling the guard rather than paraphrasing it.
        from traceevo.evolution.leakage_guard import scan_prompt
        from traceevo.evolution.memory_formatter import render
        block = render([cand.to_dict()])
        scan = scan_prompt(block, self_gold,
                           source_predictions=[cand.outcome_verdict],
                           source_case_text="")
        kinds = {l["kind"] for l in scan["leaks"]}
        checks = {
            "gold_diagnosis_absent":
                "exact_final_diagnosis" not in kinds
                and "reference_ngram_overlap" not in kinds,
            "prediction_absent": "exact_prediction" not in kinds,
            "reference_answer_absent": "exact_right_option" not in kinds,
            "source_trace_hash_present": bool(cand.source_trace_hashes),
            "audit_summary_hash_present": bool(cand.audit_summary_hash),
            "outcome_verdict_present": bool(cand.outcome_verdict),
            "extractor_version_present": bool(cand.extractor_version),
        }
        checks["guard_kinds"] = "|".join(sorted(kinds)) if kinds else ""
        # Stored into whatever partition the frozen Gate names -- including
        # `manual_review`, which §6.3 says is kept for a human and never
        # retrievable. Discarding it here instead would lose the audit trail
        # for the one case S3D most needs a trail for.
        rec = store.add(cand, verdict["eligibility"])
        # §7's "prediction absent" means this case's own GoS answer. The guard
        # call above passes the verdict as `source_predictions` (S3C's usage,
        # kept for continuity); that checks the verdict string, not the
        # prediction text, so the prediction is checked here as well. It should
        # be absent by construction -- `build_candidate` takes a strategy
        # summary, never trace text -- and "should be" is what this measures.
        pred = str(verdicts[c].get("prediction") or "").strip()
        checks["prediction_absent"] = not (len(pred) >= 8 and pred.lower() in
                                           block.lower())
        # `admitted` means the frozen Gate returned `eligible`, which is the
        # only thing that puts an entry in the retrievable success partition.
        # The first version counted `rec["stored"]`, which after the fix above
        # is true for a `manual_review` entry too -- it would have reported
        # case-51 as admitted to the headline store while the store held it in
        # a partition nothing can retrieve. That is S3C FAILURE 4's shape
        # again: a count that reads as an achievement because it was never
        # asked what it was counting.
        is_admitted = verdict["eligibility"] == gate.ELIGIBLE
        required = [v for k, v in checks.items() if k != "guard_kinds"]
        rows.append({"case": c, "memory_id": cand.memory_id,
                     "gate_eligibility": verdict["eligibility"],
                     "stored": rec["stored"], "partition": rec["partition"],
                     "headline_admitted": is_admitted,
                     "gate_reasons": "|".join(verdict["reasons"]),
                     **checks,
                     "all_required": all(required)})
        if is_admitted:
            admitted.append(cand.memory_id)
        print(f"  {task}: {verdict['eligibility']:14} "
              f"partition={rec['partition']:14} admitted={is_admitted} "
              f"required={all(checks.values())}", flush=True)

    seeded = _seed_from_s3c(store, store_dir)
    _write_csv(os.path.join(out_dir, "expanded_store_admission.csv"), rows)
    return {"n_new_gold_safe": len(cases), "gold_safe_cases": cases,
            "n_new_admitted": len(admitted), "new_admitted_memory_ids": admitted,
            "n_seeded_from_s3c": len(seeded),
            "headline_store_entries": len(store.initial_plan_ids()),
            "store_dir": os.path.relpath(store_dir, ROOT),
            "all_required_fields_present": all(r["all_required"] for r in rows)
            if rows else None}


def _seed_from_s3c(store, store_dir: str) -> List[str]:
    """§9 needs the *expanded* headline store, not a store holding only S3D's.

    The first version built `expanded_success_store` from case 50-59 alone, so
    it held zero retrievable entries and the §9 audit would have run over an
    empty index and reported a score distribution of `None`. The store §9
    describes is the union: the four S3C headline successes plus whatever S3D
    adds.
    """
    from traceevo.evolution.experience import Experience

    fields = set(Experience.__dataclass_fields__)
    out = []
    for e in _s3c_success_entries():
        c = int(e["source_trace_id"].split("-")[1])
        if c not in (11, 20, 21, 27):
            continue          # the headline four; legacy is seeded separately
        dst = os.path.join(store_dir, "success", f"{e['memory_id']}.json")
        if os.path.exists(dst):
            continue
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(e, f, ensure_ascii=False, indent=2)
        out.append(e["memory_id"])
    return out


def leakage_rescan(out_dir: str) -> Dict[str, Any]:
    """§7: `gold_leakage_count = 0`, over the rendered form, twice.

    Self-scan (each memory vs its own source gold) is S3C's check, kept for
    continuity. Cross-scan (each memory vs every other case's gold) is the one
    that would catch a retrieval-borne leak.
    """
    from traceevo.evolution.leakage_guard import scan_prompt
    from traceevo.evolution.memory_formatter import render
    from traceevo.evolution.memory_store import MemoryStore
    from traceevo.evolution.blinding import gold_by_case_index

    gold = {g["index"]: g for g in gold_by_case_index(DATASET_CSV)}
    store = MemoryStore(os.path.join(out_dir, "expanded_success_store"),
                        enabled=True, initial_plan_partitions=("success",))
    retrievable_ids = set(store.initial_plan_ids())

    # Every stored entry, in every partition, not only the retrievable ones.
    # Scanning only `initial_plan_retrievable()` would have scanned zero
    # memories here -- case-51 is a `manual_review` -- and reported
    # `gold_leakage_count = 0, passed = true` from an empty loop, which is S3C
    # FAILURE 3 exactly (a check that reports success when it has nothing to
    # check). The partition is recorded per row instead, and the retrievable
    # subset is counted separately so a vacuous result cannot read as a clean
    # one.
    entries = []
    for part in ("success", "failure", "manual_review", "rejected"):
        for e in store.entries(part):
            entries.append(dict(e, partition=part))

    self_rows, cross_rows = [], []
    self_total = 0
    for e in entries:
        src = int(e["source_trace_id"].split("-")[1])
        block = render([e])
        r = scan_prompt(block, gold.get(src, {}),
                        source_predictions=[e.get("outcome_verdict", "")],
                        source_case_text="")
        self_total += r["n_leaks"]
        self_rows.append({"memory_id": e["memory_id"], "source_case": src,
                          "partition": e["partition"],
                          "injectable": e["memory_id"] in retrievable_ids,
                          "n_leaks": r["n_leaks"],
                          "kinds": "|".join(sorted({l["kind"]
                                                    for l in r["leaks"]})),
                          "block_chars": len(block)})

    # §9's target set, minus each memory's own case. Only injectable memories
    # are cross-scanned, because only they can reach a prompt.
    targets = list(range(40, 60))
    cross_total, n_pairs = 0, 0
    for e in entries:
        if e["memory_id"] not in retrievable_ids:
            continue
        src = int(e["source_trace_id"].split("-")[1])
        block = render([e])
        for t in targets:
            if t == src:
                continue
            n_pairs += 1
            r = scan_prompt(block, gold.get(t, {}))
            cross_total += r["n_leaks"]
            if r["n_leaks"]:
                cross_rows.append({
                    "memory_id": e["memory_id"], "source_case": src,
                    "target_case": t, "n_leaks": r["n_leaks"],
                    "kinds": "|".join(sorted({l["kind"]
                                              for l in r["leaks"]}))})

    _write_csv(os.path.join(out_dir, "leakage_scan_results.csv"), self_rows)
    _write_csv(os.path.join(out_dir, "leakage_scan_cross_case.csv"), cross_rows)
    out = {
        "n_entries_scanned": len(entries),
        "n_injectable_scanned": len(retrievable_ids),
        "n_cross_pairs": n_pairs,
        "self_gold_leakage_count": self_total,
        "cross_gold_leakage_count": cross_total,
        "targets": f"{targets[0]}-{targets[-1]}",
        "gold_leakage_count": self_total + cross_total,
        "passed": (self_total + cross_total) == 0,
        "vacuous": len(entries) == 0,
        "note": ("scanned over `render([memory])`, the string the model would "
                 "actually receive, not over the payload dict. `injectable` "
                 "marks the entries a retrieval could actually surface."),
    }
    with open(os.path.join(out_dir, "leakage_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def headline_vs_legacy(out_dir: str) -> Dict[str, Any]:
    """§7: legacy entries stay in a separate corpus and out of the headline."""
    from traceevo.evolution.memory_store import MemoryStore

    hi = MemoryStore(os.path.join(out_dir, "expanded_success_store"),
                     enabled=True, initial_plan_partitions=("success",))
    lo_dir = os.path.join(out_dir, "legacy_success_store")
    lo = MemoryStore(lo_dir, enabled=True, initial_plan_partitions=("success",))
    for e in _s3c_success_entries():
        if e["source_trace_id"] in {f"case-{c}" for c in (5, 9)}:
            from traceevo.evolution.experience import Experience
            try:
                lo.add(Experience(**{k: v for k, v in e.items()
                                     if k in Experience.__dataclass_fields__}),
                       "eligible")
            except Exception:
                pass
    hi_ids = hi.initial_plan_ids()
    lo_ids = lo.initial_plan_ids()
    out = {
        "headline_store": os.path.relpath(hi.root, ROOT),
        "headline_entries": len(hi_ids), "headline_memory_ids": hi_ids,
        "legacy_store": os.path.relpath(lo.root, ROOT),
        "legacy_entries": len(lo_ids), "legacy_memory_ids": lo_ids,
        "headline_excludes_legacy": not (set(hi_ids) & set(lo_ids)),
        "note": ("§7: legacy successes are kept in a separate corpus and are "
                 "not counted in the headline statistics"),
    }
    with open(os.path.join(out_dir, "store_split.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _s3c_success_entries() -> List[Dict[str, Any]]:
    d = os.path.join(S3C_STORE, "success")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    from traceevo.s3c_expand import _trace_hashes

    s = build_store(args.out, _trace_hashes)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    # Run regardless of how many were admitted: the store now holds the seeded
    # S3C entries too, so the leakage scan has content either way and a zero
    # from it is a measurement rather than an empty loop.
    print(json.dumps(leakage_rescan(args.out), ensure_ascii=False, indent=2))
    print(json.dumps(headline_vs_legacy(args.out), ensure_ascii=False,
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
