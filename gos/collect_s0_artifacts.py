"""TraceEvo S0: collect per-case artifacts into the S0_baseline deliverable.

Produces:
  raw_outputs/case<N>_trace.txt        full GoS reasoning log for the case
  raw_outputs/case<N>_graphs.json      causal-graph snapshots (from the HTML blob)
  raw_outputs/case<N>_graph.html       interactive causal-graph snapshot
  raw_outputs/case<N>_summary.json     per-case call/token/time accounting
"""
import argparse
import json
import os
import re
import shutil

import pandas as pd

HTML_DATA_RE = re.compile(r"const\s+graphData\s*=\s*(.*?);\s*\n", re.S)


def extract_graphs(html_path):
    if not os.path.exists(html_path):
        return None
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    m = HTML_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-log-root", default="logs/S0_dev10")
    ap.add_argument("--html-dir", default="outputs/html_s0")
    ap.add_argument("--results", default="outputs/s0_dev10.csv")
    ap.add_argument("--run-log", default="")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # Map case index -> ground truth from the results CSV (row order == case order).
    df = pd.read_csv(args.results, encoding="utf-8")

    case_ids = sorted(
        int(d.split("_")[1]) for d in os.listdir(args.case_log_root)
        if d.startswith("Case_")
    )
    print(f"collecting {len(case_ids)} cases: {case_ids}")

    for idx in case_ids:
        case_dir = os.path.join(args.case_log_root, f"Case_{idx}")
        # Multiple timestamped logs can exist if a case was retried; take the newest.
        logs = sorted(f for f in os.listdir(case_dir) if f.endswith(".txt"))
        if not logs:
            print(f"  case {idx}: no trace log, skipped")
            continue
        src_log = os.path.join(case_dir, logs[-1])
        shutil.copyfile(src_log, os.path.join(args.out_dir, f"case{idx}_trace.txt"))

        html_src = os.path.join(args.html_dir, f"case{idx}.html")
        graphs = extract_graphs(html_src)
        if graphs is not None:
            with open(os.path.join(args.out_dir, f"case{idx}_graphs.json"),
                      "w", encoding="utf-8") as f:
                json.dump(graphs, f, ensure_ascii=False, indent=2)
            if os.path.exists(html_src):
                shutil.copyfile(html_src, os.path.join(args.out_dir, f"case{idx}_graph.html"))

        trace = open(src_log, encoding="utf-8", errors="replace").read()
        summary = {
            "case_index": idx,
            "trace_log": logs[-1],
            "trace_bytes": os.path.getsize(src_log),
            "n_graph_snapshots": len(graphs) if graphs else 0,
            "final_graph_nodes": len(graphs[-1]["nodes"]) if graphs else None,
            "final_graph_edges": len(graphs[-1]["edges"]) if graphs else None,
            "n_llm_calls_in_trace": trace.count("#### Step"),
            "n_backtrackings": trace.count("backtracking invoked"),
            "n_levels_deleted": trace.count("deteled"),
            "n_retrieval_observations": trace.count("Observations:"),
            "n_tool_observations": trace.count("Tool observations:"),
        }
        if idx < len(df):
            summary["prediction"] = df.iloc[idx]["prediction"]
            summary["answer"] = df.iloc[idx]["answer"]
        with open(os.path.join(args.out_dir, f"case{idx}_summary.json"),
                  "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  case {idx}: {summary['n_graph_snapshots']} graph snapshots, "
              f"{summary['final_graph_nodes']} nodes / {summary['final_graph_edges']} edges, "
              f"{summary['n_backtrackings']} backtracks")

    if args.run_log and os.path.exists(args.run_log):
        shutil.copyfile(args.run_log, os.path.join(args.out_dir, "..", "run.log"))
    print(f"artifacts -> {args.out_dir}")


if __name__ == "__main__":
    main()
