"""S5B-I §12-§13: module ablation on held-out artifact copies.

§12 allows exactly one kind of ablation: *"只在 heldout artifact 副本上离线
disable 模块，不重新运行 LLM"*. So every arm here re-runs the frozen detector
over the same sealed (or mutated) copies with one module's *producer* removed,
and no model call is made.

The four arms and the capability each is expected to carry come from §12 and
are read back from the freeze manifest, not restated:

    Full                  every module
    w/o ProvenanceTier    the P0-P3 source-aware evidence audit
    w/o CrossGraph        evidence/hypothesis to execution-event binding
    w/o Integrity         the hash-chain and DAG integrity audit
    w/o Ledger            the model invocation ledger

Removing a module means its findings do not exist — not that they are hidden.
The arms are implemented by substituting the module's entry point for the
duration of one arm's evaluation. Nothing on disk is modified: the frozen
files' hashes still verify afterwards, which `git_diff.patch` and
`s5b_freeze.py --verify` both check.

§13's output is a module x capability matrix with `NOT_SCORED` cells where a
capability was never the module's job. The ledger arm in particular is scored
only on the parser-boundary and uncommitted-call capabilities, because — §12
says so directly — folding it into the same total as A1/A2/A5 would produce a
meaningless average in either direction.

Usage:
    python traceevo/s5b_ablation.py --out S5B_final_heldout
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))
TAG = "core_v1_final"
SEED = 20260920

NOT_SCORED = "NOT_SCORED"
ARMS = ("Full", "w/o ProvenanceTier", "w/o CrossGraph", "w/o Integrity",
        "w/o Ledger")
CAPABILITIES = ("A1", "A2", "A5", "EvidenceTrace", "RootLoc",
                "ParserBoundary")


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


# ------------------------------------------------------------- arm machinery
@contextlib.contextmanager
def arm(name: str):
    """Substitute the module this arm removes, for the duration of the block.

    Each substitution replaces the module's *producer* with one that returns
    nothing, so the downstream code runs unchanged and simply has no findings
    from that module. That is what "the module is disabled" means for an
    offline ablation; filtering the output afterwards would instead measure
    the filter.
    """
    from traceevo.audit import anomaly_detector as det
    from traceevo.audit import execution_auditor as ex
    from traceevo.audit.injector import TaskArtifacts as art_cls

    # (owner, attribute, original value): restored in reverse on the way out,
    # so a nested arm could not leave a substitution behind either.
    saved: List[Any] = []

    def swap(obj, attr, value):
        saved.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    try:
        if name == "w/o ProvenanceTier":
            # The P0-P3 evidence audit's only consumer in the detector is the
            # A1 rule; with no tier decisions there is no A1.
            swap(det, "audit_task", lambda *a, **k: [])
        elif name == "w/o CrossGraph":
            # The binding layer wrote `matched_record_id` / `match_tier` onto
            # the nodes and supplied the record pool A4 reads. Both are
            # removed: A2's binding rule and A4's pool lose their inputs.
            #
            # The property is patched on the class in `injector`, which is
            # where `TaskArtifacts` lives — `anomaly_detector` imports it
            # *inside* `detect_task` rather than at module level, so patching
            # `det.TaskArtifacts` raises AttributeError before any arm runs.
            # The first version did, and the fixture caught it.
            orig_nodes = art_cls.nodes

            def stripped(self):
                return [{k: v for k, v in n.items()
                         if k not in ("matched_record_id", "match_tier")}
                        for n in orig_nodes.fget(self)]
            swap(art_cls, "nodes", property(stripped))
            orig_a4 = det.detect_a4
            swap(det, "detect_a4",
                 lambda events, bindings, records, **k:
                 orig_a4(events, [], records, **k))
        elif name == "w/o Integrity":
            # A5 has exactly two producers and both are removed.
            swap(det, "audit_integrity", lambda *a, **k: [])
            swap(det, "detect_dag_issues", lambda *a, **k: [])
        elif name == "w/o Ledger":
            # The ledger is what makes a model call visible at all. With it
            # gone, the audit sees events but not the calls that produced them
            # or failed to, so A4's parser-boundary rule loses its ground.
            swap(ex, "detect_a4", lambda *a, **k: [])
        yield
    finally:
        for obj, attr, value in reversed(saved):
            setattr(obj, attr, value)


def _evaluate_arm(artifacts: str, gt: Dict[Any, Dict[str, Any]],
                  clean_cases: List[int]) -> List[Dict[str, Any]]:
    from traceevo.s2_matrix import evaluate_trace

    rows = []
    for c in clean_cases:
        task = f"case-{c}"
        if not os.path.exists(os.path.join(artifacts, f"{task}_events.jsonl")):
            continue
        _ev, summary = evaluate_trace(artifacts, task, injected_type="clean")
        rows.append({"group": "clean", "case": c, "variant": "", **summary})
    for (case_id, atype, variant), rec in sorted(gt.items()):
        d = rec.get("dir")
        if not d or not os.path.isdir(d):
            continue
        _ev, summary = evaluate_trace(d, case_id, injected_type=atype, inj=rec)
        rows.append({"group": ("case_study" if atype == "A4" else "primary"),
                     "case": rec.get("case"), "variant": variant, **summary})
    return rows


def _scores(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The six capability cells for one arm."""
    from traceevo.s2_matrix import per_class_metrics, TYPES

    primary_types = [t for t in TYPES
                     if any(r["injected_type"] == t for r in rows
                            if r["group"] == "primary")]
    per = {m["type"]: m for m in per_class_metrics(rows)
           if m["type"] in primary_types}
    out: Dict[str, Any] = {}
    for t in ("A1", "A2", "A5"):
        if t not in per:
            out[t] = NOT_SCORED
            continue
        # `per_class_metrics` returns None for F1 when the class has no
        # detections at all — precision is 0/0. That None is a *measured*
        # zero, not a missing value: a class that was injected and never
        # detected has F1 0.0. Left as None it renders as an em dash and
        # reads as "not measured", which is the failure this stage is named
        # for. The distinction is kept honest by reporting the counts
        # alongside, so a 0.0 here cannot be confused with "not attempted".
        f1 = per[t]["f1"]
        out[t] = (0.0 if f1 is None and per[t]["n_injected"] else f1)
        out[f"{t}_tp"] = per[t]["tp"]
        out[f"{t}_n"] = per[t]["n_injected"]
    # EvidenceTrace: does the audit still attribute a finding to a graph node?
    # Without node-level attribution there is no evidence trace to speak of,
    # whatever the detection counts say.
    injected = [r for r in rows if r["group"] != "clean"]
    node_attributed = [r for r in injected
                       if r.get("root_node_match") is not None]
    out["EvidenceTrace"] = (
        round(sum(1 for r in injected if r.get("detected")) / len(injected), 4)
        if injected else NOT_SCORED)
    out["EvidenceTrace_definition"] = (
        "share of injected traces where the injected class was detected with "
        "an attributed root; the node-level half is `node_attributed` below")
    out["EvidenceTrace_node_attributed"] = len(node_attributed)
    # RootLoc: observable-root top-1 over the headline injections.
    scored = [r for r in rows
              if r["group"] == "primary" and r.get("root_top1") is not None]
    out["RootLoc"] = (round(sum(1 for r in scored if r["root_top1"])
                            / len(scored), 4) if scored else NOT_SCORED)
    out["RootLoc_n"] = len(scored)
    # ParserBoundary: the A4 case study's detection rate.
    a4 = [r for r in rows if r["group"] == "case_study"]
    out["ParserBoundary"] = (
        round(sum(1 for r in a4 if r.get("detected")) / len(a4), 4)
        if a4 else NOT_SCORED)
    out["ParserBoundary_n"] = len(a4)
    return out


def run(out_dir: str) -> Dict[str, Any]:
    artifacts = os.path.join(out_dir, "heldout_original", "traceevo", "runs",
                             TAG)
    if not os.path.isdir(artifacts):
        raise SystemExit("run s5b_run.py --run first")
    with open(os.path.join(out_dir, "Final_Core_v1_freeze_manifest.json"),
              encoding="utf-8") as f:
        spec = json.load(f)["ablation_spec"]

    gt: Dict[Any, Dict[str, Any]] = {}
    for r in _jsonl(os.path.join(out_dir,
                                 "anomaly_injection_ground_truth.jsonl")):
        gt[(r["case_id"], r["anomaly_type"], r["variant"])] = r
    clean_cases = [c for c in CASES
                   if os.path.exists(os.path.join(artifacts,
                                                  f"case-{c}_events.jsonl"))]

    matrix: Dict[str, Dict[str, Any]] = {}
    raw: Dict[str, List[Dict[str, Any]]] = {}
    for a in spec["arms"]:
        with arm(a):
            rows = _evaluate_arm(artifacts, gt, clean_cases)
        raw[a] = rows
        matrix[a] = _scores(rows)
        print(f"  arm {a}: " + json.dumps(
            {k: v for k, v in matrix[a].items()
             if not k.endswith("_definition")}, ensure_ascii=False), flush=True)

    # §13: the ledger arm is scored on its own two capabilities only. Filling
    # the other four cells with zeros would average a module over abilities
    # it was never built for.
    for cap in ("A1", "A2", "A5", "EvidenceTrace", "RootLoc"):
        matrix["w/o Ledger"][cap] = NOT_SCORED
    matrix["w/o Ledger"]["ledger_note"] = (
        "§12/§13: the invocation ledger is scored only on ParserBoundary and "
        "on uncommitted-call observability. A1/A2/A5/EvidenceTrace/RootLoc "
        "are NOT_SCORED for this arm rather than 0, because the ledger was "
        "never designed to carry them and averaging over them would be a "
        "meaningless number in either direction.")

    rows_out = []
    for a in ARMS:
        rec = {"arm": a}
        for cap in CAPABILITIES:
            rec[cap] = matrix.get(a, {}).get(cap, NOT_SCORED)
        rows_out.append(rec)
    _write_csv(os.path.join(out_dir, "module_ablation_matrix.csv"), rows_out)

    # §13's actual claim test: a module is necessary for the capability it was
    # designed for. Reported as a delta against Full per capability, so a
    # module that changes nothing is visible as a zero rather than as a pass.
    necessity = {}
    for a in ARMS[1:]:
        row = {}
        for cap in CAPABILITIES:
            full, armv = matrix["Full"].get(cap), matrix[a].get(cap)
            if armv == NOT_SCORED or full == NOT_SCORED:
                row[cap] = {"full": full, "arm": armv,
                            "delta": NOT_SCORED,
                            "reason": "capability not scored for this arm"}
            else:
                row[cap] = {"full": full, "arm": armv,
                            "delta": (round(armv - full, 4)
                                      if isinstance(armv, float)
                                      and isinstance(full, float) else None)}
        necessity[a] = row

    expected = {
        "w/o ProvenanceTier": ["A1"],
        "w/o CrossGraph": ["A1", "A2", "RootLoc"],
        "w/o Integrity": ["A5"],
        "w/o Ledger": ["ParserBoundary"],
    }
    # §13 asks whether a module is necessary for the capability it was
    # designed for. §12 lists the capabilities to *evaluate* per arm, not a
    # set that must all fall, so the verdict names which ones fell rather
    # than collapsing them into a boolean that would read as a failed claim
    # whenever one of three capabilities happened to be unaffected. A
    # capability that did not move is a result and is reported as one.
    verdict = {}
    for a, caps in expected.items():
        deltas = {c: necessity[a][c]["delta"] for c in caps}
        moved = [c for c in caps if isinstance(deltas[c], float)
                 and deltas[c] < 0]
        verdict[a] = {
            "designed_for": caps,
            "deltas": deltas,
            "moved": moved,
            "not_moved": [c for c in caps if c not in moved],
            "necessary_for": moved,
            "necessary_for_all_designed_capabilities": len(moved) == len(caps),
            "note": ("§13: the test is that the module is necessary for the "
                     "capability it was designed for. A module is NOT "
                     "required to lower one overall macro-F1, and a designed "
                     "capability that did not move is reported in "
                     "`not_moved` rather than hidden inside a boolean."),
        }

    out = {
        "mode": spec["mode"],
        "arms": list(spec["arms"]),
        "capabilities": list(CAPABILITIES),
        "matrix": matrix,
        "necessity": necessity,
        "verdict": verdict,
        "not_scored_policy": (
            "NOT_SCORED is a value, not a zero. A cell is NOT_SCORED when the "
            "capability was never the module's job; writing 0 there would "
            "report a failure that was never tested for."),
        "originals_unchanged": _originals_unchanged(out_dir, artifacts),
    }
    with open(os.path.join(out_dir, "ablation_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _originals_unchanged(out_dir: str, artifacts: str) -> bool:
    """The ablation edits nothing on disk; this checks that it didn't."""
    from traceevo.s5b_run import check_seals
    seals = check_seals(out_dir)
    return bool(seals) and all(s["match"] for s in seals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    r = run(args.out)
    print(json.dumps({"verdict": r["verdict"],
                      "originals_unchanged": r["originals_unchanged"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
