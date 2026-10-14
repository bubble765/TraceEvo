"""TraceEvo S0: merge predictions, judge scores and per-case LLM accounting
into the final S0_baseline/results.csv."""
import argparse
import json
import re

import pandas as pd

CASE_RE = re.compile(r"\[RUN\] Case (\d+)")
CALL_RE = re.compile(r"Input token nums：(\d+) \| Output token nums：(\d+)")
RETRY_RE = re.compile(r"\[LLM-API\] attempt (\d)/")


def parse_run_log(path):
    """Per-case LLM call / token / retry accounting, keyed by case index."""
    stats = {}
    current = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = CASE_RE.search(line)
            if m:
                current = int(m.group(1))
                stats.setdefault(current, {"llm_calls": 0, "prompt_tokens": 0,
                                           "completion_tokens": 0, "retries": 0})
                continue
            if current is None:
                continue
            if RETRY_RE.search(line):
                stats[current]["retries"] += 1
                continue
            # Skip the cumulative "[LLM-API] Total |" summary line: it has the
            # same field names but reports process-wide totals.
            if "Generation Completed" not in line:
                continue
            m = CALL_RE.search(line)
            if m:
                stats[current]["llm_calls"] += 1
                stats[current]["prompt_tokens"] += int(m.group(1))
                stats[current]["completion_tokens"] += int(m.group(2))
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--run-log", required=True)
    ap.add_argument("--judge-scores", required=True)
    ap.add_argument("--summary-glob", required=True,
                    help="raw_outputs dir holding case<N>_summary.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.results, encoding="utf-8")
    df = df.reset_index().rename(columns={"index": "case_index"})
    log_stats = parse_run_log(args.run_log)

    judge = pd.read_csv(args.judge_scores, encoding="utf-8")
    judge = judge[["case_index", "judge_score"]]
    df = df.merge(judge, on="case_index", how="left")

    import glob
    import os
    graph_stats = {}
    for p in glob.glob(os.path.join(args.summary_glob, "case*_summary.json")):
        with open(p, encoding="utf-8") as f:
            s = json.load(f)
        graph_stats[s["case_index"]] = s

    rows = []
    for _, r in df.iterrows():
        idx = int(r["case_index"])
        ls = log_stats.get(idx, {})
        gs = graph_stats.get(idx, {})
        rows.append({
            "case_index": idx,
            "prediction": r["prediction"],
            "answer": r["answer"],
            "judge_score": r["judge_score"],
            "llm_calls": ls.get("llm_calls"),
            "prompt_tokens": ls.get("prompt_tokens"),
            "completion_tokens": ls.get("completion_tokens"),
            "retries": ls.get("retries"),
            "graph_snapshots": gs.get("n_graph_snapshots"),
            "final_graph_nodes": gs.get("final_graph_nodes"),
            "final_graph_edges": gs.get("final_graph_edges"),
            "backtrackings": gs.get("n_backtrackings"),
            "levels_deleted": gs.get("n_levels_deleted"),
            "report_chars": len(str(r["report"])) if pd.notna(r["report"]) else None,
        })

    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False, encoding="utf-8")
    print(out.to_string(max_colwidth=48))
    print(f"\n-> {args.out}")

    scored = out.dropna(subset=["judge_score"])
    n = len(scored)
    print(f"\ncases={len(out)} scored={n} unparsed={len(out)-n}")
    if n:
        print(f"Match    = {(scored['judge_score']==2).sum()}/{n} = "
              f"{(scored['judge_score']==2).mean()*100:.2f}%")
        print(f"Relevant = {(scored['judge_score']>=1).sum()}/{n} = "
              f"{(scored['judge_score']>=1).mean()*100:.2f}%")
    print(f"total LLM calls = {out['llm_calls'].sum()}, "
          f"prompt_tokens = {out['prompt_tokens'].sum()}, "
          f"completion_tokens = {out['completion_tokens'].sum()}")


if __name__ == "__main__":
    main()
