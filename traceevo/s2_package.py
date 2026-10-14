"""Assemble the §11 deliverable bundle into S2_audit/.

    python3 traceevo/s2_package.py --out S2_audit --runs <clean_run_dir> \
        --injected S2A_inject --cases 0-9

Generates the machine-derived artifacts (schema, per-trace result CSVs, an
example audit report, the rendered propagation path, code provenance) and
copies the test suite. The narrative documents are written separately.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "gos"))

from traceevo.audit import dependency_graph as dep  # noqa: E402
from traceevo.audit import evidence_auditor as ea  # noqa: E402
from traceevo.audit import graph_delta as gd  # noqa: E402
from traceevo.audit import injector as inj  # noqa: E402
from traceevo.audit import integrity_auditor as ia  # noqa: E402
from traceevo.audit.anomaly_detector import detect_task  # noqa: E402
from traceevo.audit.event_schema import ProvenanceEvent  # noqa: E402
from traceevo.audit.path_tracer import PathTracer  # noqa: E402
from traceevo.audit.policy import CapabilityPolicy  # noqa: E402
from traceevo.audit.cross_graph_binding import NodeBinding, RetrievalRecord  # noqa: E402


def parse_cases(spec: str) -> List[int]:
    out: List[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


# --------------------------------------------------------------------- schema
def _fields(cls) -> Dict[str, Any]:
    import dataclasses
    props = {}
    for f in dataclasses.fields(cls):
        typ = str(f.type)
        props[f.name] = {"type": typ}
        if f.default is not dataclasses.MISSING:
            props[f.name]["default"] = (f.default
                                        if isinstance(f.default, (int, float,
                                                                  str, bool,
                                                                  type(None)))
                                        else str(f.default))
    return props


def build_schema() -> Dict[str, Any]:
    """JSON Schema for every persisted artifact, generated from the dataclasses.

    Generated rather than hand-written so it cannot drift from the code -- the
    same property S1's schema.json relied on.
    """
    from traceevo.audit.execution_auditor import ExecutionFinding
    from traceevo.audit.integrity_auditor import IntegrityFinding
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "TraceEvo S2 provenance schema v2",
        "description": ("Adds to v1: status/reason_code/model_id/"
                        "model_version/version_observable on events, "
                        "parent_event_ids on the execution dependency DAG, "
                        "record_hash on retrieval records, typed edge deltas, "
                        "and the unified anomaly object."),
        "definitions": {
            "ProvenanceEvent": {
                "type": "object", "properties": _fields(ProvenanceEvent),
                "required": ["event_id", "task_id", "step_id", "event_type",
                             "actor_id", "parent_event_ids", "status",
                             "reason_code", "prev_hash", "event_hash"],
            },
            "GraphDelta": {"type": "object", "properties": _fields(gd.GraphDelta),
                           "required": ["delta_id", "event_id"]},
            "NodeBinding": {"type": "object", "properties": _fields(NodeBinding)},
            "RetrievalRecord": {"type": "object",
                                "properties": _fields(RetrievalRecord),
                                "required": ["record_id", "record_hash"]},
            "DagIssue": {"type": "object", "properties": _fields(dep.Issue)},
            "ExecutionFinding": {"type": "object",
                                 "properties": _fields(ExecutionFinding)},
            "IntegrityFinding": {"type": "object",
                                 "properties": _fields(IntegrityFinding)},
            "TierDecision": {"type": "object",
                             "properties": _fields(ea.TierDecision)},
            "Injection": {"type": "object", "properties": _fields(inj.Injection)},
            "Anomaly": {
                "type": "object",
                "description": "Unified detector output (amendment §6).",
                "properties": {
                    "anomaly_id": {"type": "string"},
                    "task_id": {"type": "string"},
                    "type": {"enum": ["A0", "A1", "A2", "A3", "A4", "A5"]},
                    "root_event_id": {"type": "string"},
                    "root_node_id": {"type": "string"},
                    "severity": {"enum": ["low", "medium", "high", "critical"]},
                    "evidence": {"type": "array"},
                    "affected_events": {"type": "array",
                                        "items": {"type": "string"}},
                    "affected_nodes": {"type": "array",
                                       "items": {"type": "string"}},
                    "affected_hypotheses": {"type": "array",
                                            "items": {"type": "string"}},
                    "path_to_conclusion": {"type": "array",
                                           "items": {"type": "string"}},
                    "reaches_report": {"type": "boolean"},
                    "detector_rule": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["anomaly_id", "task_id", "type", "root_event_id",
                             "root_node_id", "severity", "detector_rule",
                             "confidence"],
            },
        },
    }


# ------------------------------------------------------------------ per-trace
def per_trace_results(runs: str, cases: List[int]) -> Dict[str, List[Dict[str, Any]]]:
    integ, dagrows = [], []
    for c in cases:
        task = f"case-{c}"
        path = os.path.join(runs, f"{task}_events.jsonl")
        if not os.path.exists(path):
            continue
        art = inj.TaskArtifacts.load(runs, task)
        findings = ia.audit_integrity(art.events, art.deltas, art.bindings)
        integ.append({
            "task_id": task, "n_events": len(art.events),
            "chain_ok": not any(f.rule == "hash_chain_broken" for f in findings),
            "completeness_ok": not any(
                f.rule in ("missing_final_report", "missing_graph_delta",
                           "evidence_record_missing") for f in findings),
            "n_findings": len(findings),
            "rules": ";".join(sorted({f.rule for f in findings})) or "none",
            "n_deltas": len(art.deltas),
        })
        dag, rep = dep.check(art.events, task)
        dagrows.append({
            "task_id": task, "n_events": rep.n_events, "n_edges": rep.n_edges,
            "ok": rep.ok, "n_issues": len(rep.issues),
            "issues_by_kind": json.dumps(rep.by_kind(), ensure_ascii=False),
            "topo_ok": rep.topo_order is not None,
            "infos": json.dumps({k: v for k, v in rep.by_kind().items()},
                                ensure_ascii=False),
        })
    return {"integrity": integ, "dag": dagrows}


def write_csv(path: str, rows: List[Dict[str, Any]], cols: List[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# --------------------------------------------------------------- example trace
def example_audit_report(runs: str, task: str, out_dir: str) -> Dict[str, Any]:
    """A full anomaly report for one task, with traced paths (§6 + §7)."""
    art = inj.TaskArtifacts.load(runs, task)
    anomalies = detect_task(runs, task)
    edges = [[e["src"], e["dst"], e.get("type", "")]
             for e in art.final_state().get("edges", [])]
    tracer = PathTracer(art.events, art.nodes, edges)
    report = {
        "task_id": task,
        "n_events": len(art.events),
        "n_nodes": len(art.nodes),
        "n_anomalies": len(anomalies),
        "anomalies": anomalies,
        "traces": [tracer.trace(a.get("root_event_id", ""),
                                a.get("root_node_id", ""), task).to_dict()
                   for a in anomalies],
        "capability_policy": {
            "roster": CapabilityPolicy.from_events(art.events).roster,
            "source": "derived from successful AGENT_CALL events",
        },
        "note": ("Execution dependency means 'this event's inputs included "
                 "that event', not model-internal causation (amendment §7)."),
    }
    with open(os.path.join(out_dir, "example_audit_report.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def render_path_png(anomalies: List[Dict[str, Any]], out_path: str,
                    labels: Optional[Dict[str, str]] = None,
                    events: Optional[Dict[str, str]] = None) -> bool:
    """Draw one anomaly's root -> conclusion path on both graphs.

    The longest traced case is drawn rather than the first, so the figure shows
    what a real propagation path looks like instead of a two-hop stub.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    if not anomalies:
        return False
    labels = labels or {}
    events = events or {}
    a = max(anomalies, key=lambda x: (len(x.get("path_to_conclusion") or [])
                                      + len(x.get("graph_path") or [])))
    chain = a.get("path_to_conclusion") or []
    graph_path = a.get("graph_path") or []
    if not chain and not graph_path:
        return False

    fig, axes = plt.subplots(1, 2, figsize=(15, 8))
    for ax, seq, title, colour, kind in (
            (axes[0], chain, "Execution dependency  (FINAL_REPORT \u2192 root)",
             "#2563eb", "event"),
            (axes[1], graph_path,
             "Causal graph  (anomalous node \u2192 node cited by the report)",
             "#dc2626", "node")):
        ax.set_title(title, fontsize=11)
        ax.axis("off")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        if not seq:
            ax.text(0.5, 0.5, "(no path)", ha="center", va="center",
                    color="#6b7280")
            continue
        n = len(seq)
        step = 0.86 / max(n, 1)
        for i, item in enumerate(seq):
            y = 0.95 - (i + 0.5) * step
            head = "root" if i == len(seq) - 1 else ("report" if i == 0
                                                     else "")
            if kind == "event":
                ev = events.get(item, {})
                text = f"{item}\n{ev.get('event_type', '')}  " \
                       f"step {ev.get('step_id', '')} {head}".strip()
            else:
                text = f"{item}  [{labels.get(item, '')[:52]}] {head}".strip()
            ax.text(0.03, y, text, fontsize=8, family="monospace",
                    va="center", wrap=True,
                    bbox=dict(boxstyle="round,pad=0.35",
                              fc="#eff6ff" if kind == "event" else "#fef2f2",
                              ec=colour))
            if i:
                ax.annotate("", xy=(0.02, y + step * 0.55),
                            xytext=(0.02, y + step * 1.45),
                            arrowprops=dict(arrowstyle="-|>", color=colour,
                                            lw=1.4))
    fig.suptitle(f"{a['task_id']}  |  {a['type']}  |  {a['detector_rule']}  |  "
                 f"severity={a['severity']}  confidence={a['confidence']}",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return True


def render_execution_path_png(events: List[Dict[str, Any]], out_path: str,
                              task_id: str = "", note: str = "") -> bool:
    """Figure for a run the audit found nothing in.

    §6 asks for a path figure for every case study. When the detector reports no
    anomaly there is no root to draw, and inventing one would misreport an
    unlocalized injection as a detection. What is real is the execution path
    itself, drawn with the statement that nothing was flagged.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    if not events:
        return False

    fig, ax = plt.subplots(figsize=(11, 9))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    n = len(events)
    step = 0.90 / max(n, 1)
    for i, e in enumerate(events):
        y = 0.97 - (i + 0.5) * step
        ax.text(0.04, y,
                f"{e.get('event_id', '')}  {e.get('event_type', ''):<14} "
                f"{str(e.get('actor_id', ''))[:22]}  step {e.get('step_id', '')}",
                fontsize=7.5, family="monospace", va="center")
    ax.set_title(f"{task_id}  |  execution path", fontsize=12)
    if note:
        fig.text(0.02, 0.012, note, fontsize=8, color="#6b7280", wrap=True)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return True


def render_aborted_path(events: List[Dict[str, Any]], out_path: str,
                        task_id: str = "") -> bool:
    """Figure for a task that died before any report existed.

    There is no causal graph and no root->conclusion path to draw, so drawing
    an empty one would be misleading. What is real is the execution sequence
    up to the failure, with the dispatch that raised marked as such.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return False
    if not events:
        return False

    fig, ax = plt.subplots(figsize=(11, 7))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    n = len(events)
    step = 0.84 / max(n, 1)
    for i, e in enumerate(events):
        y = 0.95 - (i + 0.5) * step
        failed = e.get("status") == "failed"
        colour = "#dc2626" if failed else "#2563eb"
        fc = "#fef2f2" if failed else "#eff6ff"
        detail = (f"  status={e.get('status')}  "
                  f"reason={e.get('reason_code')}" if failed else "")
        ax.text(0.04, y,
                f"{e['event_id']}\n{e['event_type']}  step "
                f"{e.get('step_id', '')}{detail}",
                fontsize=9, family="monospace", va="center",
                bbox=dict(boxstyle="round,pad=0.4", fc=fc, ec=colour))
        if i:
            ax.annotate("", xy=(0.03, y + step * 0.55),
                        xytext=(0.03, y + step * 1.45),
                        arrowprops=dict(arrowstyle="-|>", color="#6b7280",
                                        lw=1.4))
    ax.text(0.62, 0.5,
            "no FINAL_REPORT\n(process aborted)\n\n"
            "the dispatch raised,\nso no graph update,\n"
            "no causal graph and\nno report exist",
            fontsize=10, color="#dc2626", va="center",
            bbox=dict(boxstyle="round,pad=0.6", fc="#fef2f2", ec="#dc2626"))
    fig.suptitle(f"{task_id}  |  A3  |  invalid expert dispatch  |  "
                 f"task aborted", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return True


def _short(node_id: str, limit: int = 60) -> str:
    return node_id if len(node_id) <= limit else node_id[:limit - 3] + "..."


# ---------------------------------------------------------------- provenance
def code_provenance(out_dir: str) -> None:
    gos = os.path.join(ROOT, "gos")
    lines = ["# TraceEvo S2 — code provenance",
             f"# generated {_now()}", ""]
    try:
        commit = subprocess.run(["git", "-C", gos, "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "-C", gos, "rev-parse",
                                 "--abbrev-ref", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        log = subprocess.run(["git", "-C", gos, "log", "--oneline", "-5"],
                             capture_output=True, text=True).stdout.strip()
        lines += ["## Instrumented GoS working copy",
                  f"path:    {gos}", f"branch:  {branch}",
                  f"HEAD:    {commit}", "", "recent commits:", log, ""]
        diff = subprocess.run(["git", "-C", gos, "diff", "HEAD"],
                              capture_output=True, text=True).stdout
        with open(os.path.join(out_dir, "git_diff.patch"), "w",
                  encoding="utf-8") as f:
            f.write(diff)
        stat = subprocess.run(["git", "-C", gos, "diff", "HEAD", "--stat"],
                              capture_output=True, text=True).stdout
        lines += ["S2 changes vs the S1 commit (uncommitted working tree):",
                  stat, ""]
    except Exception as exc:                       # pragma: no cover
        lines.append(f"(git unavailable: {exc})")
    lines += ["## Upstream GoS",
              "repo:   https://github.com/gaorch85/Graph-of-States",
              "commit: 0f28c2e11b55f3dccf8e249b63286bd83b8edd35",
              f"path:   {os.path.join(ROOT, 'gos_official')}", ""]
    with open(os.path.join(out_dir, "git_commit.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _all_trace_dirs(clean: str, injected: str, cases: List[int]):
    """(dir, task) for every trace worth considering as the example."""
    for c in cases:
        yield clean, f"case-{c}"
    if not os.path.isdir(injected):
        return
    for atype in sorted(os.listdir(injected)):
        tdir = os.path.join(injected, atype)
        if not os.path.isdir(tdir):
            continue
        for variant in sorted(os.listdir(tdir)):
            vdir = os.path.join(tdir, variant)
            if not os.path.isdir(vdir):
                continue
            for task in sorted(os.listdir(vdir)):
                rundir = os.path.join(vdir, task)
                if os.path.exists(os.path.join(
                        rundir, f"{task}_bindings.json")):
                    yield rundir, task


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")


# ---------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "S2_audit"))
    ap.add_argument("--runs", required=True)
    ap.add_argument("--injected", default=os.path.join(ROOT, "S2A_inject"))
    ap.add_argument("--cases", default="0-9")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    cases = parse_cases(args.cases)

    with open(os.path.join(out, "schema_v2.json"), "w", encoding="utf-8") as f:
        json.dump(build_schema(), f, ensure_ascii=False, indent=2)

    res = per_trace_results(args.runs, cases)
    write_csv(os.path.join(out, "integrity_results.csv"), res["integrity"],
              ["task_id", "n_events", "n_deltas", "chain_ok", "completeness_ok",
               "n_findings", "rules"])
    write_csv(os.path.join(out, "dependency_dag_results.csv"), res["dag"],
              ["task_id", "n_events", "n_edges", "ok", "n_issues",
               "issues_by_kind", "topo_ok"])

    gt = os.path.join(args.injected, "injection_ground_truth.jsonl")
    if os.path.exists(gt):
        shutil.copy(gt, os.path.join(out, "injection_ground_truth.jsonl"))

    # The example is the *longest* traced case across clean and injected
    # traces, so the figure shows a real multi-step propagation path rather
    # than a two-hop stub (the only clean trace with a finding, case 4, was
    # flagged at its final step and produced a one-edge path).
    candidates: List[tuple] = []
    for rundir, task in _all_trace_dirs(args.runs, args.injected, cases):
        try:
            fs = detect_task(rundir, task)
        except Exception:
            continue
        if not fs:
            continue
        # Prefer a case that exercises *both* panels of the figure; among
        # those, the longest combined path.
        span = max(len(f.get("path_to_conclusion") or []) for f in fs)
        has_graph_path = any(f.get("graph_path") for f in fs)
        candidates.append(((1 if has_graph_path else 0, span), rundir, task, fs))
    best = None
    if candidates:
        key, rundir, task, fs = max(candidates, key=lambda x: x[0])
        best = (task, fs, key[1], rundir)
    if best:
        example_audit_report(best[3], best[0], out)
        art = inj.TaskArtifacts.load(best[3], best[0])
        labels = {n["node_id"]: n.get("label", "") for n in art.nodes}
        events = {e["event_id"]: {"event_type": e["event_type"],
                                  "step_id": e["step_id"]}
                  for e in art.events}
        ok = render_path_png(best[1], os.path.join(out, "example_path.png"),
                             labels, events)
        print(f"example audit report: {best[0]} from {best[3]} "
              f"({len(best[1])} anomalies, longest path {best[2]}), "
              f"png={'yes' if ok else 'no'}")

    tests_src = os.path.join(ROOT, "traceevo", "tests")
    tests_dst = os.path.join(out, "tests")
    if os.path.isdir(tests_dst):
        shutil.rmtree(tests_dst)
    shutil.copytree(tests_src, tests_dst,
                    ignore=shutil.ignore_patterns("__pycache__"))

    code_provenance(out)
    print(f"bundle assembled in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
