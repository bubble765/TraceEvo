"""Assemble the S1_provenance/ deliverable package (amendment §7).

Copies representative artifacts, the full per-case provenance run, the LLM
record/replay recording and the acceptance CSVs into S1_provenance/.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def copy(src: str, dst: str, label: str, copied: List[str]) -> bool:
    if not os.path.exists(src):
        print(f"  MISSING {label}: {src}")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    copied.append(os.path.relpath(dst, ROOT))
    return True


def copy_tree(src_dir: str, dst_dir: str, pattern: str, copied: List[str]) -> int:
    n = 0
    for path in sorted(glob.glob(os.path.join(src_dir, "**", pattern), recursive=True)):
        rel = os.path.relpath(path, src_dir)
        dst = os.path.join(dst_dir, rel.replace(os.sep, "__"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(path, dst)
        copied.append(os.path.relpath(dst, ROOT))
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t1-dir", default=os.path.join(ROOT, "S1_runs", "t1_dev10"))
    ap.add_argument("--out", default=os.path.join(ROOT, "S1_provenance"))
    args = ap.parse_args()

    out = args.out
    os.makedirs(out, exist_ok=True)
    copied: List[str] = []

    prov_run = os.path.join(args.t1_dir, "C_prov_on_replay")
    prov_dir = os.path.join(prov_run, "traceevo", "runs", "C_prov_on_replay")
    log_dir = os.path.join(prov_run, "logs", "C_prov_on_replay")

    print("== top-level ==")
    copy(os.path.join(args.t1_dir, "t1_noninvasiveness.json"),
         os.path.join(out, "t1_noninvasiveness.json"), "T1 report", copied)

    # A/B/C outputs side by side for the non-invasiveness comparison
    rows = []
    for name in ("A_prov_off_record", "B_prov_off_replay", "C_prov_on_replay"):
        rp = os.path.join(args.t1_dir, name, "results.csv")
        if os.path.exists(rp):
            rows.append((name, rp))
    if rows:
        import csv
        with open(os.path.join(out, "before_after_outputs.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["configuration", "case_index", "prediction", "answer",
                        "report_chars"])
            for name, rp in rows:
                with open(rp, encoding="utf-8") as g:
                    for i, row in enumerate(csv.DictReader(g)):
                        w.writerow([name, i, row.get("prediction", ""),
                                    row.get("answer", ""),
                                    len(row.get("report", "") or "")])
        copied.append(os.path.relpath(os.path.join(out, "before_after_outputs.csv"), ROOT))

    print("== acceptance CSVs ==")
    for name in ("coverage_results.csv", "grounding_results.csv",
                 "grounding_sensitivity.csv", "replay_results.csv",
                 "integrity_test_results.csv", "source_taxonomy.md"):
        copy(os.path.join(out, "_analysis", name), os.path.join(out, name),
             name, copied)

    print("== sample artifacts (case 0) ==")
    sample = os.path.join(prov_dir, "case-0_events.jsonl")
    copy(sample, os.path.join(out, "sample_trace.jsonl"), "events", copied)
    copy(os.path.join(prov_dir, "case-0_provenance_graph.json"),
         os.path.join(out, "sample_provenance_graph.json"), "P_t", copied)
    copy(os.path.join(prov_dir, "case-0_provenance_graph.png"),
         os.path.join(out, "sample_provenance_graph.png"), "P_t png", copied)
    copy(os.path.join(prov_dir, "case-0_deltas.jsonl"),
         os.path.join(out, "sample_graph_deltas.jsonl"), "deltas", copied)
    copy(os.path.join(prov_dir, "case-0_bindings.json"),
         os.path.join(out, "cross_graph_mapping.json"), "bindings", copied)

    print("== full provenance run (all cases) ==")
    n = copy_tree(prov_dir, os.path.join(out, "provenance_run"), "case-*",
                  copied)
    print(f"  copied {n} per-case artifacts")

    print("== LLM recording ==")
    rec = os.path.join(args.t1_dir, "llm_responses.jsonl")
    if os.path.exists(rec):
        copy(rec, os.path.join(out, "llm_recording_sample.jsonl"), "recording",
             copied)

    print("== tests ==")
    for path in sorted(glob.glob(os.path.join(ROOT, "traceevo", "tests", "*.py"))):
        copy(path, os.path.join(out, "tests", os.path.basename(path)),
             os.path.basename(path), copied)

    print("== code patch ==")
    import subprocess
    diff = subprocess.run(["git", "-C", os.path.join(ROOT, "gos"), "diff", "HEAD"],
                          capture_output=True, text=True)
    with open(os.path.join(out, "git_diff.patch"), "w", encoding="utf-8") as f:
        f.write(diff.stdout)
    copied.append("S1_provenance/git_diff.patch")

    with open(os.path.join(out, "package_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"n_files": len(copied), "files": sorted(copied)}, f,
                  ensure_ascii=False, indent=2)
    print(f"\n{len(copied)} files -> {out}")


if __name__ == "__main__":
    main()
