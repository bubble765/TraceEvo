"""S5B-E/F §7-§9: the controlled anomaly protocol on held-out copies.

§7's first sentence carries the whole constraint — 复制 artifact, 原始 seal 永不
修改 — so every injection goes through `TaskArtifacts.load` -> inject -> `save`
to a new directory under `controlled_mutations/`. The sealed originals are
opened read-only and this module verifies afterwards that their hashes are
unchanged, because "we only read it" is exactly the claim that quietly stops
being true.

The headline classes are A1/A2/A3/A5 (§7.1). A4 is reported as an
invocation/parser boundary case study. A0 is not injected and is not in the
headline: S4A established that no event in this pipeline carries
status=degraded or failed, and §11 forbids fabricating a status to populate a
category.

The injection specification — which variant, how many per case, which seed —
was frozen in `s5b_freeze.py` before case 80 was read, and is read back from
the manifest here rather than restated. A specification that lives in two
places is a specification that can disagree with itself.

§8.2's interval is a **case-cluster bootstrap**: mutations from one case are
not independent samples, so the resampling unit is the case id. Treating
every mutation as an independent draw would understate the interval, which is
the direction that makes a result look better than it is.

Usage:
    python traceevo/s5b_anomaly.py --out S5B_final_heldout
    python traceevo/s5b_anomaly.py --skip-inject
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
import random
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"
SEED = 20260920
BOOTSTRAP = 10000

PROTOCOL = ("A1", "A2", "A3", "A5")
CASE_STUDY = ("A4",)
EXCLUDED = ("A0",)


def _sha_file(p: str) -> str:
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


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def frozen_spec(out_dir: str) -> Dict[str, Any]:
    """Read the injection specification back from the freeze manifest."""
    p = os.path.join(out_dir, "Final_Core_v1_freeze_manifest.json")
    if not os.path.exists(p):
        raise SystemExit("§4: no freeze manifest; refusing to inject against "
                         "an unfrozen specification")
    with open(p, encoding="utf-8") as f:
        man = json.load(f)
    spec = man["controlled_injection_spec"]
    got = hashlib.sha256(json.dumps(spec, sort_keys=True).encode(
        "utf-8")).hexdigest()
    if got != man["controlled_injection_spec_hash"]:
        raise SystemExit(
            "the injection specification does not hash to the frozen value; "
            "the stage would be running against a specification nobody "
            "froze")
    return spec


def _a2_variant(spec: Dict[str, Any], case: int) -> str:
    vs = spec["A2"]["variants"]
    return vs[(case - CASES[0]) % len(vs)]


def inject(out_dir: str, artifacts: str, cases: List[int]) -> Dict[str, Any]:
    """§7 on copies, with a hash check that the originals are untouched."""
    from traceevo.audit.injector import TaskArtifacts, INJECTORS
    from traceevo.audit.injector import inject_a5 as _a5

    spec = frozen_spec(out_dir)
    before = {c: _sha_file(os.path.join(artifacts, f"case-{c}_events.jsonl"))
              for c in cases}
    inj_dir = os.path.join(out_dir, "controlled_mutations")
    os.makedirs(inj_dir, exist_ok=True)
    rng = random.Random(SEED)
    records, not_applicable = [], []

    for c in cases:
        task = f"case-{c}"

        # A5 first, on an unmodified load: it is an integrity failure and
        # re-sealing would erase what it injects.
        for variant in spec["A5"]["variants"]:
            art = TaskArtifacts.load(artifacts, task)
            try:
                rec = _a5(art, task, variant=variant).to_dict()
            except ValueError as exc:
                not_applicable.append({"case": c, "anomaly_type": "A5",
                                       "variant": variant,
                                       "reason": str(exc)})
                continue
            dest = os.path.join(inj_dir, "A5", variant, task)
            art.save(dest)
            rec["dir"] = dest
            rec["case"] = c
            records.append(rec)

        # A1/A2/A3 as the protocol, A4 as the case study.
        plan = [("A1", spec["A1"]["variants"][0]),
                ("A2", _a2_variant(spec, c)),
                ("A3", spec["A3"]["variants"][0]),
                ("A4", spec["A4"]["variants"][0])]
        for atype, variant in plan:
            art = TaskArtifacts.load(artifacts, task)
            kw: Dict[str, Any] = {"variant": variant}
            if atype in ("A1", "A4"):
                kw["rng"] = rng
            if atype == "A2":
                # Seeded per case so that A2's draw does not depend on how
                # many cases ran before it — the same case gets the same
                # mutation whether it is injected alone or last.
                kw["rng"] = random.Random(SEED + c)
            try:
                rec = INJECTORS[atype](art, task, **kw).to_dict()
            except ValueError as exc:
                not_applicable.append({"case": c, "anomaly_type": atype,
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
    with open(os.path.join(out_dir, "anomaly_not_applicable.json"), "w",
              encoding="utf-8") as f:
        json.dump({
            "records": not_applicable,
            "n_not_applicable": len(not_applicable),
            "policy": ("§7.2: an injection that the injector refuses for a "
                       "case is recorded as NOT_APPLICABLE with the "
                       "injector's own message, and is not forced into "
                       "existence"),
            "a0": spec["A0"],
            "denominators": ("the real denominator per class is the number of "
                             "records produced, reported per class below and "
                             "in anomaly_class_metrics.csv"),
        }, f, ensure_ascii=False, indent=2)

    by_type = collections.Counter(r["anomaly_type"] for r in records)
    return {"n_injections": len(records),
            "n_not_applicable": len(not_applicable),
            "by_type": dict(by_type),
            "denominator_per_class": {t: by_type.get(t, 0)
                                      for t in PROTOCOL + CASE_STUDY},
            "originals_unchanged": not changed,
            "changed_cases": changed,
            "protocol_classes": list(PROTOCOL),
            "case_study_classes": list(CASE_STUDY),
            "excluded_classes": list(EXCLUDED),
            "a2_variant_by_case": {str(c): _a2_variant(spec, c)
                                   for c in cases},
            "spec_hash": _sha_file(os.path.join(
                out_dir, "Final_Core_v1_freeze_manifest.json"))}


def evaluate(out_dir: str) -> Dict[str, Any]:
    """§8's metrics and §9's localization, over the injected copies.

    The per-class arithmetic is `s2_matrix.per_class_metrics`, imported rather
    than reimplemented: it already encodes §8.3's rule that a secondary
    finding on an injected trace is not a false positive of the injected
    class. A second copy here would be free to disagree about what precision
    means.
    """
    from traceevo.s2_matrix import evaluate_trace, per_class_metrics, TYPES

    gt = {}
    for r in _jsonl(os.path.join(out_dir,
                                 "anomaly_injection_ground_truth.jsonl")):
        gt[(r["case_id"], r["anomaly_type"], r["variant"])] = r

    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
    rows: List[Dict[str, Any]] = []

    # ---- clean pass. §6.3: a genuine finding here still counts as an alert.
    for c in CASES:
        task = f"case-{c}"
        if not os.path.exists(os.path.join(artifacts, f"{task}_events.jsonl")):
            continue
        _ev, summary = evaluate_trace(artifacts, task, injected_type="clean")
        rows.append({"group": "clean", "case": c, "variant": "", **summary})

    # ---- injected pass
    for (case_id, atype, variant), rec in sorted(gt.items()):
        d = rec.get("dir")
        if not d or not os.path.isdir(d):
            continue
        _ev, summary = evaluate_trace(d, case_id, injected_type=atype, inj=rec)
        rows.append({"group": ("case_study" if atype in CASE_STUDY
                               else "primary"),
                     "case": rec.get("case"), "variant": variant, **summary})
    _write_csv(os.path.join(out_dir, "anomaly_detection_results.csv"), rows)

    # ---- §9's Root group, latent and observable kept in separate columns
    root_rows = []
    for r in rows:
        if r.get("injected_type") == "clean":
            continue
        root_rows.append({
            "case": r.get("case"), "task_id": r.get("task_id"),
            "injected_type": r.get("injected_type"),
            "variant": r.get("variant"),
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

    prim_types = [t for t in TYPES if any(r["injected_type"] == t
                                          for r in primary)]
    per_class = per_class_metrics(rows) if rows else []
    kept = [_normalise(m) for m in per_class if m["type"] in prim_types]
    _write_csv(os.path.join(out_dir, "anomaly_class_metrics.csv"), kept)

    tp = sum(m["tp"] for m in kept)
    fn = sum(m["fn"] for m in kept)
    fp_clean = sum(m["fp_on_clean"] for m in kept)
    precision = tp / (tp + fp_clean) if (tp + fp_clean) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)
    # The macro is taken over every class that was actually injected, using
    # the normalised values. Averaging only the non-None ones would silently
    # turn a four-class macro into a three-class one exactly when a class
    # failed completely — the case where the reader most needs to see a zero.
    macro = {
        "precision": _mean([m["precision"] for m in kept]),
        "recall": _mean([m["recall"] for m in kept]),
        "f1": _mean([m["f1"] for m in kept]),
        "classes_averaged": [m["type"] for m in kept],
        "n_classes": len(kept),
    }

    # §8.2: the interval resamples cases, not mutations.
    cluster = cluster_bootstrap(rows, prim_types)
    secondary = sum(m.get("secondary_consequence_detections", 0) for m in kept)
    sec_types: Dict[str, int] = {}
    for r in primary + case_study:
        for t in (r.get("detected_types") or []):
            if t != r.get("injected_type"):
                sec_types[t] = sec_types.get(t, 0) + 1

    scored = [r for r in primary if r.get("root_top1") is not None]
    applicable = [r for r in scored if r.get("latent_root_is_observable")]
    observable_hits = sum(1 for r in scored if r.get("root_top1"))
    latent_hits = sum(1 for r in applicable if r.get("root_top1"))

    out = {
        "clean": {
            "n_traces": len(clean),
            "n_traces_with_alerts": sum(1 for r in clean
                                        if r.get("n_anomalies", 0) > 0),
            "detected_type_counts": _type_counts(clean),
            "note": ("§6.3/§12: the Clean group, measured on the held-out "
                     "block. A genuine audit finding still counts as an "
                     "alert and nothing was removed from the denominator "
                     "for looking like a real problem."),
        },
        "primary_injected": {
            "denominator_per_class": {t: sum(1 for r in primary
                                             if r["injected_type"] == t)
                                      for t in prim_types},
            "n": len(primary), "detected": tp, "missed": fn,
            "precision": _r(precision), "recall": _r(recall), "f1": _r(f1),
            "precision_denominator": {"tp": tp, "fp_on_clean": fp_clean},
            "recall_denominator": {"tp": tp, "fn": fn},
            "macro_by_class": macro,
            "headline": "macro_by_class (§8.1)",
            "per_class": kept,
            "case_cluster_bootstrap_ci95": cluster,
            "note": ("§8.1: the headline is the macro average by class, so "
                     "that a class with more injections cannot dominate. §12: "
                     "secondary findings are not counted as false positives "
                     "of the injected class; `precision_strict`, which does "
                     "count them, is in `per_class` for continuity and is not "
                     "the headline."),
        },
        "secondary_findings": {
            "count": secondary,
            "types": sec_types,
            "note": ("§8.3: a detection of type T on a trace injected with "
                     "T' != T. These are real downstream consequences, "
                     "reported as their own count and type list and not "
                     "counted as primary-class false positives."),
        },
        "root": {
            "n_scored": len(scored),
            "observable_root_top1_accuracy": (
                _r(observable_hits / len(scored)) if scored else None),
            "observable_root_top1_denominator": {
                "hits": observable_hits, "n": len(scored)},
            "latent_root_exact_accuracy": (
                _r(latent_hits / len(applicable)) if applicable else None),
            "latent_root_denominator": {
                "hits": latent_hits, "n": len(applicable),
                "excluded": ("delete and truncate variants, where the latent "
                             "root is an event that no longer exists"), },
            "latent_root_equals_observable": sum(
                1 for r in scored if r.get("latent_root_is_observable")),
            "note": ("§9: the headline is observable-root top-1. Latent-root "
                     "exact accuracy is reported only over the injections "
                     "whose latent root still exists in the trace — delete "
                     "and truncate are excluded, because requiring a system "
                     "to localize an event that was removed measures the "
                     "ground truth's choice rather than the detector."),
        },
        "case_study": {
            "n": len(case_study),
            "types": sorted({r.get("injected_type") for r in case_study}),
            "denominator": {t: sum(1 for r in case_study
                                   if r["injected_type"] == t)
                            for t in CASE_STUDY},
            "note": ("§7.1: A4 is reported as an invocation/parser boundary "
                     "case study and does not enter the headline semantic "
                     "propagation F1."),
        },
        "excluded": {
            "classes": list(EXCLUDED),
            "note": SPEC_NOTE,
        },
    }
    with open(os.path.join(out_dir, "anomaly_headline.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


SPEC_NOTE = (
    "§7.1 keeps A0 out of the headline. S4A established that no event in this "
    "pipeline carries status=degraded or failed, so the `status_marked` "
    "variant would manufacture the instrumentation its own precondition "
    "requires; it is refused. `silent` fabricates no status, and is not in "
    "the headline either. Nothing in this stage simulates a status the "
    "pipeline does not produce.")


def cluster_bootstrap(rows: List[Dict[str, Any]], prim_types: List[str],
                      seed: int = SEED, n: int = BOOTSTRAP) -> Dict[str, Any]:
    """§8.2's case-cluster bootstrap over the injected rows.

    Mutation rows from one case share a trace and a graph, so they are not
    independent draws. The resample unit is the case id; a case with four A5
    variants contributes all four together or not at all. Resampling mutations
    instead would shrink the interval by treating correlated rows as
    independent evidence.
    """
    from traceevo.s2_matrix import per_class_metrics
    by_case: Dict[int, List[Dict[str, Any]]] = collections.defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    case_ids = sorted(by_case)
    if not case_ids:
        return {"n_cases": 0, "note": "no rows to resample"}
    rng = random.Random(seed)
    samples = {"macro_f1": [], "micro_f1": [], "macro_precision": [],
               "macro_recall": []}
    for _ in range(n):
        drawn = [case_ids[rng.randrange(len(case_ids))] for _ in case_ids]
        pool: List[Dict[str, Any]] = []
        for c in drawn:
            pool.extend(by_case[c])
        per = [m for m in per_class_metrics(pool) if m["type"] in prim_types]
        mf = _mean([m["f1"] for m in per])
        mp = _mean([m["precision"] for m in per])
        mr = _mean([m["recall"] for m in per])
        tp = sum(m["tp"] for m in per)
        fn = sum(m["fn"] for m in per)
        fp = sum(m["fp_on_clean"] for m in per)
        p = tp / (tp + fp) if (tp + fp) else None
        rc = tp / (tp + fn) if (tp + fn) else None
        uf = (2 * p * rc / (p + rc)) if (p and rc) else None
        for k, v in (("macro_f1", mf), ("micro_f1", uf),
                     ("macro_precision", mp), ("macro_recall", mr)):
            if v is not None:
                samples[k].append(v)
    out = {"n_cases": len(case_ids), "resamples": n, "seed": seed,
           "unit": "case_id (§8.2)"}
    for k, vs in samples.items():
        if not vs:
            out[k] = {"point": None, "ci95": [None, None], "n_effective": 0}
            continue
        vs.sort()
        out[k] = {"point": round(sum(vs) / len(vs), 4),
                  "ci95": [round(vs[int(0.025 * len(vs))], 4),
                           round(vs[min(len(vs) - 1, int(0.975 * len(vs)))], 4)],
                  "n_effective": len(vs)}
    out["note"] = ("§8.2: the sampling unit is the case, not the mutation. "
                   "Mutations of one case share a trace, so event-level "
                   "independence would understate the interval.")
    return out


def _normalise(m: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a measured-zero into 0.0 and say so.

    `s2_matrix.per_class_metrics` returns `f1 = None` when a class was
    injected and never detected, because precision is 0/0. That is a *measured
    zero*: the class was tested and nothing was found. Left as `None` it
    renders as an em dash in every table and reads as "not measured", which is
    the wrong claim and the more flattering one. The counts are carried
    alongside so a 0.0 here can never be confused with "not attempted".
    """
    if m.get("f1") is None and m.get("n_injected"):
        m = dict(m, f1=0.0, f1_zero_by_no_detection=True,
                 f1_note=("injected and never detected: precision is 0/0, so "
                          "F1 is a measured 0.0 rather than an absent value"))
    return m


def _mean(xs: List[Any]) -> Any:
    vals = [x for x in xs if isinstance(x, float)]
    return round(sum(vals) / len(vals), 4) if vals else None


def _r(x: Any) -> Any:
    return round(x, 6) if isinstance(x, float) else None


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
    artifacts = os.path.join(args.out, "heldout_original", "traceevo", "runs",
                             TAG)
    if not os.path.isdir(artifacts):
        raise SystemExit("run s5b_run.py --run first")
    if not args.skip_inject:
        s = inject(args.out, artifacts, CASES)
        print(json.dumps({k: v for k, v in s.items()
                          if k != "a2_variant_by_case"}, ensure_ascii=False,
                         indent=2))
    h = evaluate(args.out)
    print(json.dumps({k: v for k, v in h.items()
                      if k != "clean"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
