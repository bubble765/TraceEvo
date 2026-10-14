"""S3A §9: assemble the deliverable directory.

Most files are written by the step that produces them (`s3a_freeze.py`,
`s3a_regression.py`, `s3a_a4_recheck.py`, `s3a_migration.py`). This module
writes the two that describe the code itself, and checks that §9's list is
actually complete rather than assuming it is.

Usage:
    python traceevo/s3a_package.py --out S3A_migration --git
    python traceevo/s3a_package.py --out S3A_migration --check
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
sys.path.insert(0, ROOT)

# §9's tree, verbatim. `None` means "written by another step"; a string is a
# file that must be non-empty.
DELIVERABLES = [
    "S3A_report.md", "REVIEW_REQUEST.md", "model_invocation_schema.json",
    "experience_eligibility_contract.md", "migration_config.yaml",
    "rule_freeze_manifest.json", "migration_results.csv",
    "migration_evidence_analysis.csv", "migration_call_analysis.csv",
    "migration_review_queue.csv", "a4_invocation_recheck/case5.json",
    "a4_invocation_recheck/case8.json", "regression_results.csv",
    "failure_cases.md", "tests/", "git_diff.patch", "git_commit.txt",
]


def write_git(out_dir: str) -> None:
    from traceevo.audit.event_schema import utc_now
    lines = ["# TraceEvo S3A — code provenance", f"# generated {utc_now()}", ""]
    try:
        branch = subprocess.run(["git", "-C", GOS, "rev-parse", "--abbrev-ref",
                                 "HEAD"], capture_output=True,
                                text=True).stdout.strip()
        commit = subprocess.run(["git", "-C", GOS, "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
        log = subprocess.run(["git", "-C", GOS, "log", "--oneline", "-5"],
                             capture_output=True, text=True).stdout.strip()
        lines += ["## Instrumented GoS working copy",
                  f"path:    {GOS}", f"branch:  {branch}",
                  f"HEAD:    {commit}", "", "recent commits:", log, ""]
        diff = subprocess.run(["git", "-C", GOS, "diff", "HEAD"],
                              capture_output=True, text=True).stdout
        with open(os.path.join(out_dir, "git_diff.patch"), "w",
                  encoding="utf-8") as f:
            f.write(diff)
        stat = subprocess.run(["git", "-C", GOS, "diff", "HEAD", "--stat"],
                              capture_output=True, text=True).stdout
        lines += ["S3A changes vs the S1 commit (uncommitted working tree):",
                  stat, ""]
    except Exception as exc:                       # pragma: no cover
        lines.append(f"(git unavailable: {exc})")
    lines += [
        "## TraceEvo package",
        "path:   " + ROOT,
        "",
        "## Upstream GoS",
        "repo:   https://github.com/gaorch85/Graph-of-States",
        "commit: 0f28c2e11b55f3dccf8e249b63286bd83b8edd35",
        f"path:   {os.path.join(ROOT, 'gos_official')}", "",
    ]
    with open(os.path.join(out_dir, "git_commit.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote {out_dir}/git_diff.patch and git_commit.txt")


def check(out_dir: str) -> int:
    missing, empty = [], []
    for rel in DELIVERABLES:
        p = os.path.join(out_dir, rel)
        if not os.path.exists(p):
            missing.append(rel)
        elif os.path.isfile(p) and os.path.getsize(p) == 0:
            empty.append(rel)
    status = {"present": len(DELIVERABLES) - len(missing),
              "total": len(DELIVERABLES), "missing": missing, "empty": empty}
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not missing and not empty:
        print("§9 deliverables: complete")
        return 0
    print("§9 deliverables: INCOMPLETE")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "S3A_migration"))
    ap.add_argument("--git", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    rc = 0
    if args.git:
        write_git(args.out)
    if args.check or not args.git:
        rc = check(args.out)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
