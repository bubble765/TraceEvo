"""S5B-K §17: the five final tables, generated from sealed artifacts.

§17 asks for at least five tables and §15 condition 9 (carried into S5B's §20)
asks that every one of them be script-generatable. Both are the same
requirement seen from two sides: a number that exists only in the report is a
number nobody can re-derive, and a table assembled by hand is a transcript.

So every table here is written by this script, from files that earlier scripts
wrote from sealed artifacts. No table cell is typed in, and `TABLES` below is
the roster the finalizer checks against the files on disk.

Usage:
    python traceevo/s5b_tables.py --out S5B_final_heldout
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")

TABLES = ("table1_heldout_natural.md", "table2_controlled_anomaly.md",
          "table3_root_traceability.md", "table4_experience_gate.md",
          "table5a_ablation.md", "table5b_overhead.md")


def _json(p: str, default: Any = None) -> Any:
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _csv(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fmt(x: Any, nd: int = 4) -> str:
    if x is None or x == "":
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _ci(pair: Any) -> str:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return "—"
    if pair[0] is None:
        return "—"
    return f"[{pair[0]:.3f}, {pair[1]:.3f}]"


def _write(out_dir: str, name: str, lines: List[str]) -> None:
    d = os.path.join(out_dir, "tables")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  wrote tables/{name}")


# ---------------------------------------------------------------- Table 1
def table1(out_dir: str) -> List[str]:
    p = _json(os.path.join(out_dir, "pipeline_summary.json"), {})
    d = _json(os.path.join(out_dir, "diagnostic_summary.json"), {})
    c = _json(os.path.join(out_dir, "clean_alert_summary.json"), {})
    a = p.get("audit", {})
    rows = [
        "# Table 1 — Held-out natural performance and audit",
        "",
        f"Block: case 80-137, N = {p.get('n_seal_verified', '—')} "
        f"seal-verified of {p.get('n_sealed', '—')} sealed "
        f"({p.get('n_seal_rejected', '—')} rejected). "
        "Natural runs only; no controlled injection is included in this table.",
        "",
        "| quantity | value | 95% CI | denominator |",
        "|---|---|---|---|",
    ]
    m, r = d.get("Match", {}), d.get("Relevant", {})
    rows.append(f"| GoS Match | {_fmt(m.get('mean'))} | {_ci(m.get('ci95'))} "
                f"| {m.get('n', 0)} scored cases |")
    rows.append(f"| GoS Relevant | {_fmt(r.get('mean'))} "
                f"| {_ci(r.get('ci95'))} | {r.get('n', 0)} scored cases |")
    dist = d.get("score_distribution", {})
    rows.append("| judge score distribution | "
                f"2: {dist.get('2', 0)} · 1: {dist.get('1', 0)} · "
                f"0: {dist.get('0', 0)} · unparsed: {dist.get('unparsed', 0)} "
                "| — | same cases |")
    # The fallback here used to be `sum(...) or 1`, which renders an absent
    # outcome file as "1 outcome" — a denominator invented to avoid a zero.
    # §11's rule applies to this table too: no denominator, no rate, and no
    # denominator either if the file is missing.
    o = d.get("experience_outcomes", {})
    n_out = sum(o.values())
    den = f"{n_out} outcomes" if n_out else "no outcome file"
    for key in ("verified_success", "verified_incorrect", "uncertain"):
        rows.append(f"| {key} | {o.get(key, 0)} | — | {den} |")
    rows.append("")
    rows += [
        "Evidence tiers over the natural traces (nodes / used in reasoning):",
        "",
        "| | P0 | P1 | P2 | P3 |",
        "|---|---|---|---|---|",
        f"| nodes | {a.get('p0', '—')} | {a.get('p1', '—')} | "
        f"{a.get('p2', '—')} | {a.get('p3', '—')} |",
        f"| used | — | {a.get('p1_used', '—')} | {a.get('p2_used', '—')} | "
        f"{a.get('p3_used', '—')} |",
        "",
        "| audit quantity | value | 95% CI | denominator |",
        "|---|---|---|---|",
    ]
    ct = c.get("clean_trace_alert", {})
    ce = c.get("clean_evidence_alert", {})
    rows.append(f"| Clean trace alert rate | {_fmt(ct.get('rate'))} "
                f"| {_ci(ct.get('wilson95'))} | "
                f"{ct.get('numerator', 0)}/{ct.get('denominator', 0)} |")
    rows.append(f"| Clean evidence alert rate | {_fmt(ce.get('rate'))} "
                f"| {_ci(ce.get('wilson95'))} | "
                f"{ce.get('numerator', 0)}/{ce.get('denominator', 0)} |")
    rows.append("")
    rows += [
        "| integrity | value |",
        "|---|---|",
        f"| sealed / verified | {p.get('n_sealed', '—')} / "
        f"{p.get('n_seal_verified', '—')} |",
        f"| all traces complete | {a.get('all_traces_complete')} |",
        f"| all hash chains verify | {a.get('all_chains_ok')} |",
        f"| all graph replays ok | {a.get('all_replays_ok')} |",
        f"| ledger binding failures (discarded calls) | "
        f"{a.get('total_discarded', '—')} |",
        f"| accepted but uncommitted calls | "
        f"{a.get('total_accepted_but_uncommitted', '—')} |",
        f"| critical anomalies | {a.get('critical_alerts', '—')} |",
        f"| events | {a.get('total_events', '—')} |",
        f"| model calls | {a.get('total_llm_calls', '—')} |",
        "",
        "Match/Relevant and the experience outcome categories are different "
        "instruments: §6.1 forbids reading `verified_incorrect` as "
        "`Match = 0`. The rows above are reported next to each other and are "
        "not derived from each other.",
        "",
        f"Clean alert interval: Wilson 95%. Match/Relevant interval: "
        f"{m.get('method', 'case-level bootstrap')}.",
        "",
        "Generated by `traceevo/s5b_tables.py` from `pipeline_summary.json`, "
        "`diagnostic_summary.json` and `clean_alert_summary.json`.",
    ]
    return rows


# ---------------------------------------------------------------- Table 2
def table2(out_dir: str) -> List[str]:
    h = _json(os.path.join(out_dir, "anomaly_headline.json"), {})
    prim = h.get("primary_injected", {})
    boot = prim.get("case_cluster_bootstrap_ci95", {})
    rows = [
        "# Table 2 — Controlled anomaly detection",
        "",
        "§7.1's headline classes. Injections are made on copies; the sealed "
        "originals are hash-verified unchanged afterwards.",
        "",
        "| class | N | precision | recall | F1 | 95% CI (macro F1) |",
        "|---|---|---|---|---|---|",
    ]
    for m in prim.get("per_class", []):
        t = m["type"]
        cib = boot.get("macro_f1", {}) if t else {}
        rows.append(
            f"| {t} | {m.get('n_injected', '—')} | {_fmt(m.get('precision'))} "
            f"| {_fmt(m.get('recall'))} | {_fmt(m.get('f1'))} | — |")
    macro = prim.get("macro_by_class", {})
    rows.append(f"| **Macro by class** | {prim.get('n', '—')} | "
                f"{_fmt(macro.get('precision'))} | {_fmt(macro.get('recall'))}"
                f" | {_fmt(macro.get('f1'))} | {_ci(boot.get('macro_f1', {}).get('ci95'))} |")
    pd_ = prim.get("precision_denominator", {})
    rd = prim.get("recall_denominator", {})
    rows.append(f"| **Micro** | {prim.get('n', '—')} | "
                f"{_fmt(prim.get('precision'))} | {_fmt(prim.get('recall'))} | "
                f"{_fmt(prim.get('f1'))} | "
                f"{_ci(boot.get('micro_f1', {}).get('ci95'))} |")
    rows += [
        "",
        f"Denominators: precision {pd_.get('tp', '—')} tp / "
        f"{pd_.get('fp_on_clean', '—')} clean false positive; recall "
        f"{rd.get('tp', '—')} tp / {rd.get('fn', '—')} missed. Real "
        "per-class denominators are the N column, not 58 — §7.2 records "
        "`NOT_APPLICABLE` rather than forcing an injection into existence.",
        "",
        f"Not-applicable injections are listed in `anomaly_not_applicable.json`.",
        "",
        "## Secondary findings (§8.3)",
        "",
        f"Count: {h.get('secondary_findings', {}).get('count', '—')}. Types: "
        f"{h.get('secondary_findings', {}).get('types', {})}.",
        "",
        "A secondary finding is a detection of type T on a trace injected "
        "with T' != T — a real downstream consequence, reported as its own "
        "count and **not** counted as a primary-class false positive.",
        "",
        "## Case study (§7.1)",
        "",
        f"A4 (invocation / parser boundary): n = "
        f"{h.get('case_study', {}).get('n', '—')}, denominator "
        f"{h.get('case_study', {}).get('denominator', {})}. A4 does not enter "
        "the headline F1.",
        "",
        "## Excluded",
        "",
        f"{h.get('excluded', {}).get('note', '')}",
        "",
        "Generated by `traceevo/s5b_tables.py` from `anomaly_headline.json`.",
    ]
    return rows


# ---------------------------------------------------------------- Table 3
def table3(out_dir: str) -> List[str]:
    h = _json(os.path.join(out_dir, "anomaly_headline.json"), {})
    root = h.get("root", {})
    od = root.get("observable_root_top1_denominator", {})
    ld = root.get("latent_root_denominator", {})
    p = _json(os.path.join(out_dir, "pipeline_summary.json"), {})
    a = p.get("audit", {})
    rows = [
        "# Table 3 — Root and traceability",
        "",
        "| quantity | value | numerator / denominator |",
        "|---|---|---|",
        f"| Observable-root Top-1 | "
        f"{_fmt(root.get('observable_root_top1_accuracy'))} | "
        f"{od.get('hits', '—')}/{od.get('n', '—')} |",
        f"| Latent-root exact (applicable classes only) | "
        f"{_fmt(root.get('latent_root_exact_accuracy'))} | "
        f"{ld.get('hits', '—')}/{ld.get('n', '—')} |",
        f"| latent root equals observable | "
        f"{root.get('latent_root_equals_observable', '—')} | "
        f"of {root.get('n_scored', '—')} scored |",
        f"| Evidence-trace success (all traces complete) | "
        f"{a.get('all_traces_complete')} | {p.get('n_seal_verified', '—')} "
        f"cases |",
        f"| Integrity detection (all chains verify) | "
        f"{a.get('all_chains_ok')} | {p.get('n_seal_verified', '—')} cases |",
        "",
        "§9: the headline is the observable-root Top-1 — the earliest "
        "anomalous seam visible in the remaining trace. Latent-root exact "
        "accuracy is reported only over the injections whose latent root "
        "still exists in the trace; delete and truncate variants are "
        "excluded, because requiring a system to localize an event that was "
        "removed measures the ground truth's choice rather than the detector.",
        "",
        "Per-injection detail, with the latent and observable roots in "
        "separate columns, is in `root_localization_results.csv`.",
        "",
        "Generated by `traceevo/s5b_tables.py` from `anomaly_headline.json` "
        "and `pipeline_summary.json`.",
    ]
    return rows


# ---------------------------------------------------------------- Table 4
def table4(out_dir: str) -> List[str]:
    g = _json(os.path.join(out_dir, "gate_final.json"), {})
    nat = (g.get("natural") or {}).get("rows", [])
    base = _csv(os.path.join(out_dir, "gate_baseline_metrics.csv"))
    rows = [
        "# Table 4 — Experience Gate",
        "",
        f"Block: case 80-137, N = {g.get('n_cases', '—')}. "
        f"Positive-class branch: **{g.get('branch', '—')}**.",
        "",
        "## §10.1 natural candidates, by rule",
        "",
        f"n = {(g.get('natural') or {}).get('n_natural_candidates', '—')} "
        "natural candidates, every rule run on every one.",
        "",
        "| rule | eligible | failure_experience | manual_review | reject | "
        "admission rate | 95% CI |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in nat:
        rows.append(
            f"| {r.get('rule', '—')} | {r.get('eligible', '—')} | "
            f"{r.get('failure_experience', '—')} | "
            f"{r.get('manual_review', '—')} | {r.get('reject', '—')} | "
            f"{_fmt(r.get('admission_rate'))} | "
            f"{_ci(r.get('admission_rate_ci95'))} |")
    ic = g.get("incorrect_clean") or {}
    rows += [
        "",
        "## §10.2 Incorrect-clean",
        "",
        f"Definition: {ic.get('definition', '—')}. "
        f"n = {ic.get('n_incorrect_clean', '—')}.",
        "",
        "| rule | admitted | denominator | rate | 95% CI |",
        "|---|---|---|---|---|",
    ]
    for rule, v in (ic.get("by_rule") or {}).items():
        rows.append(f"| {rule} | {v.get('numerator')} | {v.get('denominator')}"
                    f" | {_fmt(v.get('rate'))} | {_ci(v.get('ci95'))} |")
    rows += [
        "",
        "## §11 rates, each as numerator / denominator",
        "",
        "| rule | metric | numerator | denominator | rate | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    # `gate_baseline_metrics.csv` is flat — one column per rate component.
    # The nested `metric_block` form lives in `gate_final.json`; reading the
    # CSV expecting the nested form produced an empty table, which renders as
    # "no rates to report" rather than as a bug.
    for r in base:
        for key, label in (
                ("validated_success_retention", "Validated Success Retention"),
                ("admission_precision", "Admission Precision"),
                ("admission_recall", "Admission Recall"),
                ("incorrect_clean_admission_rate", "Incorrect-Clean Admission"),
                ("poison_admission_rate", "Poison Admission"),
                ("correct_but_poisoned_admission_rate",
                 "Correct-but-Poisoned Admission"),
                ("manual_review_rate", "Manual Review Rate"),
                ("failure_capture_rate", "Failure Capture Rate")):
            num = r.get(f"{key}_numerator")
            if num in (None, ""):
                continue
            rows.append(
                f"| {r.get('rule', '—')} | {label} | {num} | "
                f"{r.get(f'{key}_denominator')} | "
                f"{_rate(r.get(f'{key}_rate'))} | "
                f"{_int(r.get(f'{key}_ci95_lo'), r.get(f'{key}_ci95_hi'))} |")
    icp = g.get("positive_class_metrics")
    rows += [
        "",
        "## Positive class (§10.4)",
        "",
        f"verified_success = {g.get('n_verified_success', '—')}; "
        f"clean + protocol-eligible = {g.get('n_poison_eligible', '—')}; "
        f"gold-safe = {g.get('n_gold_safe', '—')}; twins built = "
        f"{g.get('twins_built', '—')}.",
        "",
    ]
    if g.get("branch") == "HELDOUT_POSITIVE_CLASS_ABSENT":
        rows += [
            "> **HELDOUT_POSITIVE_CLASS_ABSENT.** The block produced no "
            "verified success usable as a positive class, so the "
            "correct-but-poisoned positive branch could not be tested here "
            "and is not reported as if it had been. The paper attributes that "
            "conclusion to the development controlled experiment and states "
            "that the final held-out block did not replicate it "
            "independently.",
            "",
        ]
    elif icp:
        rows += [
            f"Twins: {icp.get('n_twins_applicable', '—')} applicable.",
            "",
            "| rule | poisoned twins admitted | denominator | rate | 95% CI |",
            "|---|---|---|---|---|",
        ]
        for rule, v in (icp.get("correct_but_poisoned_admission") or {}).items():
            rows.append(f"| {rule} | {v.get('numerator')} | "
                        f"{v.get('denominator')} | {_fmt(v.get('rate'))} | "
                        f"{_ci(v.get('ci95'))} |")
        rows.append("")
    rows += [
        "Every proportion above carries its numerator and denominator; §11 "
        "forbids writing 0.0 or 1.0 without the counts behind them, and the "
        "Wilson interval is what makes a 0/58 distinguishable from a 0/2.",
        "",
        "Generated by `traceevo/s5b_tables.py` from `gate_final.json` and "
        "`gate_baseline_metrics.csv`.",
    ]
    return rows


def _rate(v: Any) -> str:
    """A rate cell, where an absent denominator stays absent.

    `float(None)` becomes 0.0 through any default, and 0.0 is a *rate* — so a
    format helper with a numeric default silently converts "no denominator"
    into "zero", which is S5A FAILURE 5 in a different instrument. An empty
    cell renders as an em dash.
    """
    if v in (None, ""):
        return "—"
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return "—"


def _int(lo: Any, hi: Any) -> str:
    if lo in (None, "") or hi in (None, ""):
        return "—"
    try:
        return f"[{float(lo):.3f}, {float(hi):.3f}]"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------- Table 5
def table5a(out_dir: str) -> List[str]:
    s = _json(os.path.join(out_dir, "ablation_summary.json"), {})
    caps = s.get("capabilities", [])
    rows = [
        "# Table 5a — Module ablation",
        "",
        "§12: offline module disable on held-out artifact copies; no LLM is "
        "re-run. `NOT_SCORED` marks a capability that was never the module's "
        "job — it is a value, not a zero.",
        "",
        "| arm | " + " | ".join(caps) + " |",
        "|---" * (len(caps) + 1) + "|",
    ]
    for a, m in (s.get("matrix") or {}).items():
        cells = [_fmt(m.get(c)) if m.get(c) != "NOT_SCORED" else "NOT_SCORED"
                 for c in caps]
        rows.append(f"| {a} | " + " | ".join(cells) + " |")
    rows += [
        "",
        "## Necessity, per §13",
        "",
        "| arm | designed for | delta vs Full | moved | not moved |",
        "|---|---|---|---|---|",
    ]
    for a, v in (s.get("verdict") or {}).items():
        deltas = ", ".join(f"{k}: {_fmt(val)}"
                           for k, val in (v.get("deltas") or {}).items())
        rows.append(f"| {a} | {', '.join(v.get('designed_for', []))} | "
                    f"{deltas} | {', '.join(v.get('moved', [])) or '—'} | "
                    f"{', '.join(v.get('not_moved', [])) or '—'} |")
    rows += [
        "",
        "§13 does not require every module to lower one overall macro-F1. "
        "The claim under test is narrow: a module is necessary for the "
        "capability it was designed for.",
        "",
        "Generated by `traceevo/s5b_tables.py` from `ablation_summary.json`.",
    ]
    return rows


def table5b(out_dir: str) -> List[str]:
    o = _json(os.path.join(out_dir, "overhead_summary.json"), {})
    lp = o.get("local_processing", {})
    net = o.get("llm_network", {})

    def row(label, key, unit="ms"):
        s = lp.get(key) or {}
        if not s or s.get("median") is None:
            return f"| {label} | — | — | — | — |"
        return (f"| {label} | {_fmt(s.get('median'))} | "
                f"{s.get('iqr', ['—', '—'])[0]}–{s.get('iqr', ['—', '—'])[1]} "
                f"| {_fmt(s.get('p95'))} | {s.get('unit', unit)} |")

    rows = [
        "# Table 5b — Overhead",
        "",
        "§14's three classes, reported apart. Local TraceEvo processing is a "
        "property of the implementation; network time is engineering data and "
        "is not used for any claim; token usage is not estimated.",
        "",
        "## Local TraceEvo, per case",
        "",
        f"n = {o.get('n_cases', '—')} cases.",
        "",
        "| quantity | median | IQR | p95 | unit |",
        "|---|---|---|---|---|",
        row("Event logging", "event_logging"),
        row("Ledger", "ledger"),
        row("Graph delta", "graph_delta"),
        row("Audit", "audit"),
        row("Gate", "gate"),
        row("TraceEvo local total", "traceevo_local_total"),
        row("Storage", "storage", "MiB"),
        "",
        "The gate is additionally reported in microseconds: it is "
        "sub-millisecond, and at a coarser precision a real measurement and a "
        "rounding artefact are the same string.",
        "",
        "## LLM / API",
        "",
        "| quantity | value |",
        "|---|---|",
        f"| model calls | {net.get('n_model_calls', '—')} |",
        f"| wall-clock, total | {net.get('wall_clock_seconds_total', '—')} s |",
        f"| wall-clock median per case | "
        f"{(net.get('wall_clock_seconds') or {}).get('median', '—')} s |",
        "",
        "Network and queue time is reported separately from the local "
        "figures above and is not part of any claim.",
        "",
        "## Token",
        "",
        "| quantity | value |",
        "|---|---|",
    ]
    tk = o.get("tokens", {})
    if tk.get("status") == "REPORTED_BY_API":
        rows += [
            f"| status | `{tk['status']}` |",
            f"| input tokens | {tk.get('input_tokens_total', '—')} |",
            f"| output tokens | {tk.get('output_tokens_total', '—')} |",
            f"| total tokens | {tk.get('tokens_total', '—')} |",
            f"| calls with usage | {tk.get('n_calls_with_usage', '—')} |",
            f"| source | {tk.get('source', '—')} |",
        ]
    else:
        rows.append(f"| status | `{tk.get('status', 'TOKEN_UNAVAILABLE')}` |")
    rows += [
        "",
        f"{tk.get('note', '')}",
        "",
        "Generated by `traceevo/s5b_tables.py` from `overhead_summary.json`.",
    ]
    return rows


def generate(out_dir: str) -> Dict[str, Any]:
    made = {}
    made["table1_heldout_natural.md"] = table1(out_dir)
    made["table2_controlled_anomaly.md"] = table2(out_dir)
    made["table3_root_traceability.md"] = table3(out_dir)
    made["table4_experience_gate.md"] = table4(out_dir)
    made["table5a_ablation.md"] = table5a(out_dir)
    made["table5b_overhead.md"] = table5b(out_dir)
    for name, lines in made.items():
        _write(out_dir, name, lines)
    out = {"generated": sorted(made), "n": len(made),
           "generator": "traceevo/s5b_tables.py",
           "policy": ("every table is produced by this script from files "
                      "earlier scripts wrote out of sealed artifacts; no "
                      "table cell is entered by hand")}
    with open(os.path.join(out_dir, "tables_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    print(json.dumps(generate(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
