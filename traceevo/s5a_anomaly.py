"""S5A-D/E §11-§12: the controlled anomaly protocol, on copies.

§11's first sentence carries the whole constraint:

    复制 artifact，绝不修改原始 sealed trace

so every injection here goes through `TaskArtifacts.load` -> inject -> `save`
to a new directory. The sealed originals are opened read-only and the module
verifies afterwards that their hashes are unchanged — a copy discipline that is
checked rather than asserted, because "we only read it" is exactly the claim
that silently stops being true.

§11 also carries two instructions that amount to *don't manufacture a class*:

* A4 becomes an invocation/parser boundary case study, "不再包装成已验证
  semantic propagation anomaly". It is run and reported as a case study.
* A0 is kept "只有存在可靠 degraded/failed instrumentation 时才保留". S4A
  established that the artifact vocabulary has no degraded or failed status at
  all — all events are `status=success` — so A0 cannot be triggered honestly
  here. It is recorded as `NOT_INJECTABLE` with that reason rather than given a
  synthetic status to fill the row.

§12's headline redefinition is largely already implemented in `s2_matrix`:
secondary findings are excluded from the primary precision denominator, and
the latent root is kept separate from the observable root. S5A runs that
machinery on the validation block and groups the output as §12 asks.

Usage:
    python traceevo/s5a_anomaly.py --out S5A_protocol_validation
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
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5A_protocol_validation")
CASES = list(range(70, 80))
TAG = "core_v1"
SEED = 20260919

# §11's list. A4 is present as a case study; A0 is attempted and will be
# recorded as not injectable, which is the honest outcome.
PROTOCOL = ("A1", "A2", "A3", "A5")
CASE_STUDY = ("A4",)
CONDITIONAL = ("A0",)


def _sha_file(p: str) -> str:
    import hashlib
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        open(path, "w").close()
        return
    keys: List[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def inject(out_dir: str, artifacts: str, cases: List[int]) -> Dict[str, Any]:
    """§11's protocol on copies, plus a hash check that the originals are
    untouched."""
    from traceevo.audit.injector import (DEFAULT_VARIANTS, INJECTORS,
                                         TaskArtifacts)
    import random

    before = {c: _sha_file(os.path.join(artifacts, f"case-{c}_events.jsonl"))
              for c in cases}
    inj_dir = os.path.join(out_dir, "injected")
    os.makedirs(inj_dir, exist_ok=True)
    rng = random.Random(SEED)
    records, not_injectable = [], []

    for c in cases:
        task = f"case-{c}"

        # A5 first, on an unmodified load: it is an integrity failure and
        # re-sealing would erase what it injects.
        for variant in DEFAULT_VARIANTS["A5"]:
            art = TaskArtifacts.load(artifacts, task)
            try:
                rec = inject_a5_variant(art, task, variant)
            except ValueError as exc:
                not_injectable.append({"case": c, "anomaly_type": "A5",
                                       "variant": variant, "reason": str(exc)})
                continue
            dest = os.path.join(inj_dir, "A5", variant, task)
            art.save(dest)
            rec["dir"] = dest
            rec["case"] = c
            records.append(rec)

        for atype in ("A1", "A2", "A3", "A4", "A0"):
            for variant in DEFAULT_VARIANTS[atype]:
                # §11: "若无可确定触发，不为凑类别模拟一个虚假 status". The
                # `status_marked` A0 variant writes status="degraded" into the
                # artifact -- a value this pipeline never produces (S4A: all
                # 855 events are status=success). Running it would manufacture
                # the instrumentation the clause requires to already exist.
                if atype == "A0" and variant == "status_marked":
                    not_injectable.append({
                        "case": c, "anomaly_type": "A0", "variant": variant,
                        "reason": ("REFUSED: this variant writes "
                                   "status=degraded, a value the pipeline "
                                   "never produces; §11 forbids fabricating a "
                                   "status to populate the category")})
                    continue
                art = TaskArtifacts.load(artifacts, task)
                kw: Dict[str, Any] = {"variant": variant}
                if atype in ("A1", "A4"):
                    kw["rng"] = rng
                if atype == "A2":
                    kw["rng"] = random.Random(SEED + c)
                try:
                    rec = INJECTORS[atype](art, task, **kw).to_dict()
                except ValueError as exc:
                    not_injectable.append({"case": c, "anomaly_type": atype,
                                           "variant": variant,
                                           "reason": str(exc)})
                    continue
                dest = os.path.join(inj_dir, atype, variant, task)
                art.save(dest)
                rec["dir"] = dest
                rec["case"] = c
                records.append(rec)

    after = {c: _sha_file(os.path.join(artifacts, f"case-{c}_events.jsonl"))
             for c in cases}
    changed = [c for c in cases if before[c] != after[c]]

    with open(os.path.join(out_dir, "anomaly_injection_ground_truth.jsonl"),
              "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "anomaly_not_injectable.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "records": not_injectable,
            "a0_note": (
                "§11: A0 is kept only if reliable degraded/failed "
                "instrumentation exists. It does not: S4A established that "
                "every event in this pipeline carries status=success and "
                "reason_code=NONE, so no A0 injection can be produced from a "
                "real status transition. It is recorded here as not injectable "
                "rather than simulated with a fabricated status, which is what "
                "§11's '不为凑类别模拟一个虚假 status' forbids."),
        }, f, ensure_ascii=False, indent=2)

    return {"n_injections": len(records),
            "n_not_injectable": len(not_injectable),
            "by_type": dict(collections.Counter(r["anomaly_type"]
                                                for r in records)),
            "originals_unchanged": not changed,
            "changed_cases": changed,
            "protocol_types": list(PROTOCOL),
            "case_study_types": list(CASE_STUDY),
            "conditional_types": list(CONDITIONAL)}


def inject_a5_variant(art, task: str, variant: str):
    from traceevo.audit.injector import inject_a5
    return inject_a5(art, task, variant=variant).to_dict()


def evaluate(out_dir: str) -> Dict[str, Any]:
    """§12's headline groups, over the injected copies.

    The primary/secondary split is `s2_matrix.per_class_metrics`, imported
    rather than reimplemented: it already encodes §12's rule that a secondary
    finding on an injected trace is not a false positive of the injected class,
    and the strict variant is reported alongside for continuity. Writing a
    second copy of that arithmetic here would let the two disagree about what
    precision means.
    """
    from traceevo.s2_matrix import evaluate_trace, per_class_metrics, TYPES

    gt = {}
    p = os.path.join(out_dir, "anomaly_injection_ground_truth.jsonl")
    with open(p, encoding="utf-8") as f:
        for ln in f:
            if ln.strip():
                r = json.loads(ln)
                gt[(r["case_id"], r["anomaly_type"], r["variant"])] = r

    artifacts = os.path.join(out_dir, "runs_70_79", "traceevo", "runs", TAG)
    rows = []

    # ---- clean pass: §12's Clean group, measured on this block
    for c in CASES:
        task = f"case-{c}"
        _ev, summary = evaluate_trace(artifacts, task, injected_type="clean")
        rows.append({"group": "clean", "case": c, "variant": "", **summary})

    # ---- injected pass
    for (case_id, atype, variant), rec in sorted(gt.items()):
        d = rec.get("dir")
        if not d or not os.path.isdir(d):
            continue
        _ev, summary = evaluate_trace(d, case_id, injected_type=atype, inj=rec)
        rows.append({"group": ("case_study" if atype in CASE_STUDY
                               else "conditional" if atype in CONDITIONAL
                               else "primary"),
                     "case": rec.get("case"), "variant": variant, **summary})
    _write_csv(os.path.join(out_dir, "anomaly_detection_results.csv"), rows)

    # ---- §12's Root group, latent and observable kept apart
    root_rows = []
    for r in rows:
        if r.get("injected_type") == "clean":
            continue
        root_rows.append({
            "case": r.get("case"), "task_id": r.get("task_id"),
            "injected_type": r.get("injected_type"), "variant": r.get("variant"),
            "latent_root_event_id": r.get("latent_root_event_id", ""),
            "observable_root_event_id": r.get("observable_root_event_id", ""),
            "latent_root_is_observable": r.get("latent_root_is_observable"),
            "root_event_match": r.get("root_event_match"),
            "root_node_match": r.get("root_node_match"),
            "observable_root_top1": r.get("root_top1"),
            "affected_event_recall": r.get("affected_event_recall"),
            "reaches_report": r.get("reaches_report"),
            "integrity_flagged": r.get("integrity_flagged"),
        })
    _write_csv(os.path.join(out_dir, "root_localization_results.csv"),
               root_rows)

    clean = [r for r in rows if r["group"] == "clean"]
    primary = [r for r in rows if r["group"] == "primary"]
    case_study = [r for r in rows if r["group"] == "case_study"]
    conditional = [r for r in rows if r["group"] == "conditional"]

    # §12's headline numbers come from the shared metric function.
    prim_types = [t for t in TYPES if any(r["injected_type"] == t
                                          for r in primary)]
    per_class = per_class_metrics(rows) if rows else []
    kept = [m for m in per_class if m["type"] in prim_types]
    tp = sum(m["tp"] for m in kept)
    fn = sum(m["fn"] for m in kept)
    fp_clean = sum(m["fp_on_clean"] for m in kept)
    secondary = sum(m.get("secondary_consequence_detections", 0)
                    for m in kept)
    precision = tp / (tp + fp_clean) if (tp + fp_clean) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)

    alert_clean = [r for r in clean if r.get("n_anomalies", 0) > 0]
    evidence_flagged = [r for r in clean
                        if r.get("integrity_flagged") is not None
                        and any(t in ("A1", "A3")
                                for t in (r.get("detected_types") or []))]
    roots = [r for r in primary if r.get("root_top1") is not None]

    out = {
        "clean": {
            "n_traces": len(clean),
            "n_traces_with_alerts": len(alert_clean),
            "clean_trace_alert_rate": (len(alert_clean) / len(clean)
                                       if clean else None),
            "clean_evidence_alert_rate": (len(evidence_flagged) / len(clean)
                                          if clean else None),
            "detected_type_counts": _type_counts(clean),
            "note": ("§12's Clean group, measured on this block rather than "
                     "inherited from an earlier stage. Clean Evidence Alert "
                     "Rate counts clean traces carrying an evidence-tier "
                     "finding (A1/A3), which is a statement about nodes rather "
                     "than about the trace."),
        },
        "primary_injected": {
            "n": len(primary), "detected": tp, "missed": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "false_positives_from_clean": fp_clean,
            "per_type": kept,
            "note": ("§12: secondary findings are not counted as false "
                     "positives of the injected class; the strict variant is "
                     "in `per_type` for continuity and is not the headline."),
        },
        "secondary_findings": {
            "count": secondary,
            "note": ("§12's Secondary group. These are detections of type T on "
                     "a trace injected with T' != T — a real downstream "
                     "consequence, not an error."),
        },
        "root": {
            "n": len(roots),
            "observable_root_top1_accuracy": (
                sum(1 for r in roots if r.get("root_top1")) / len(roots)
                if roots else None),
            "latent_root_equals_observable": sum(
                1 for r in roots if r.get("latent_root_is_observable")),
            "note": ("§12: for delete/truncate the latent root is an event "
                     "that no longer exists and no detector could name, so "
                     "scoring uses the observable root and the metric is never "
                     "called 'exact injected root localization'."),
        },
        "conditional_not_in_headline": {
            "n": len(conditional),
            "types": sorted({r.get("injected_type") for r in conditional}),
            "n_detected": sum(1 for r in conditional if r.get("detected")),
            "note": ("§11 keeps A0 only 只有存在可靠 degraded/failed "
                     "instrumentation 时才保留. That instrumentation does not "
                     "exist here (S4A: no event carries status=degraded or "
                     "failed), so A0 rows are reported in this group and are "
                     "NOT in the primary precision/recall/F1 above. The "
                     "`status_marked` variant was refused outright because it "
                     "writes a status the pipeline never produces."),
        },
        "case_study": {
            "n": len(case_study),
            "types": sorted({r.get("injected_type") for r in case_study}),
            "note": ("§11: A4 is a parser/invocation boundary case study, "
                     "reported here and not merged into the primary numbers."),
        },
    }
    with open(os.path.join(out_dir, "anomaly_headline.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _type_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    c: Dict[str, int] = collections.Counter()
    for r in rows:
        for t in (r.get("detected_types") or []):
            c[t] += 1
    return dict(c)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip-inject", action="store_true")
    args = ap.parse_args()
    artifacts = os.path.join(args.out, "runs_70_79", "traceevo", "runs", TAG)
    if not os.path.isdir(artifacts):
        raise SystemExit("run s5a_run.py --run first")
    if not args.skip_inject:
        s = inject(args.out, artifacts, CASES)
        print(json.dumps(s, ensure_ascii=False, indent=2))
    h = evaluate(args.out)
    print(json.dumps(h, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
