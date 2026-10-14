"""S5B §5.2: what actually happened to each attempt, read back from the log.

`attempt_history.jsonl` carries a `rerun_reason` on every record. That field
is written by the runner *while the attempt is still running*, from
`attempt > 1` — the retry number, not the failure. So a retry that succeeded
is labelled `infrastructure: process exited 0 without integrity.json`, which
is a description of a successful run wearing a failure's name. This module
exists because that field cannot be trusted and the log can.

It reads, and changes nothing:

    heldout_original/run.log        the console of every attempt, in order
    attempt_history.jsonl           §5.2's record: case, attempt, rc, timing

and writes `run_audit.json`, which:

* names the exception each failed attempt actually died of;
* classifies it against §5.2's list, which permits a re-run only for
  network interruption, API 5xx, unexpected process exit, or filesystem
  failure;
* states plainly whether each re-run falls inside that list.

The classification is the point. §5.2's list is about infrastructure, and a
GoS process that dies because the planner named an expert that does not exist
is not infrastructure — it is the model's own output ending the case. That
distinction does not disappear because the runner happened to write the word
"infrastructure" next to it, and the reviewer should get the real one.

Usage:
    python traceevo/s5b_run_audit.py --out S5B_final_heldout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
CASES = list(range(80, 138))

ATTEMPT_RE = re.compile(r"=== (case-\d+) attempt (\d+)/(\d+) ([^=]+) ===")
# The last `SomethingError: message` line of a traceback.
EXC_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|"
                    r"Interrupt|Fault|Warning)):\s*(.*)$")
# The marker the bridge prints when a case finishes: `[TraceEvo] case 104: 46
# events, chain=OK, replay=OK`. Note `case 104` with a space and no hyphen --
# the first version of this regex wrote `case-\d+`, matched nothing, and
# reported all 47 attempts as failed with no exception. An audit module that
# says every run failed is worse than no audit module.
SUCCESS_RE = re.compile(r"\[TraceEvo\] case (\d+): (\d+) events, chain=(\w+), "
                        r"replay=(\w+)")

INFRASTRUCTURE_PATTERNS = [
    (re.compile(r"Connection(Error|Reset|Refused|Aborted)"), "network"),
    (re.compile(r"(Read|Connect|Socket)?Timeout(Error)?"), "network"),
    (re.compile(r"requests\.exceptions"), "network"),
    (re.compile(r"\b(502|503|504)\b"), "api 5xx"),
    (re.compile(r"BrokenPipe|RemoteDisconnected|IncompleteRead"), "network"),
    (re.compile(r"OSError.*(No space|Input/output error)"), "filesystem"),
]
# The planner naming an expert that is not in the list, and the other ways a
# model's own output ends a case rather than a machine failing.
MODEL_OUTPUT_PATTERNS = [
    (re.compile(r"Not founded"), "planner named an expert absent from the "
                                 "expert list"),
    (re.compile(r"JSONDecodeError|json\.decoder"), "model returned "
                                                   "unparseable JSON"),
    (re.compile(r"KeyError"), "model output missing an expected field"),
    (re.compile(r"ValueError.*(parse|format|expect)"), "model output in an "
                                                      "unexpected format"),
]
PROTOCOL_PATTERNS = [
    (re.compile(r"traceevo/"), "raised inside the TraceEvo package"),
    (re.compile(r"AssertionError"), "an assertion failed"),
]


def classify(exc_line: str, block: str) -> Dict[str, str]:
    """§5.2's question: is this one of the four listed failures?"""
    text = f"{exc_line}\n{block}"
    for pat, why in INFRASTRUCTURE_PATTERNS:
        if pat.search(text):
            return {"kind": "INFRASTRUCTURE", "why": why,
                    "in_section_5_2_list": "yes"}
    for pat, why in PROTOCOL_PATTERNS:
        if pat.search(text):
            return {"kind": "PROTOCOL_FAILURE", "why": why,
                    "in_section_5_2_list": "no"}
    for pat, why in MODEL_OUTPUT_PATTERNS:
        if pat.search(exc_line) or pat.search(block):
            return {"kind": "MODEL_OUTPUT_FAILURE", "why": why,
                    "in_section_5_2_list": "no"}
    if exc_line:
        return {"kind": "UNCLASSIFIED_FAILURE", "why": exc_line[:160],
                "in_section_5_2_list": "no"}
    return {"kind": "NO_EXCEPTION", "why": "the attempt left no traceback",
            "in_section_5_2_list": "no"}


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def split_attempts(log_path: str) -> List[Dict[str, Any]]:
    """One record per `=== case-N attempt M/K ===` block."""
    if not os.path.exists(log_path):
        return []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    marks = list(ATTEMPT_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        block = text[m.end():end]
        exc = ""
        for line in block.splitlines():
            e = EXC_RE.match(line.strip())
            if e and not line.startswith(" "):
                exc = f"{e.group(1)}: {e.group(2)}"
        ok = SUCCESS_RE.search(block)
        out.append({
            "task_id": m.group(1), "attempt": int(m.group(2)),
            "of": int(m.group(3)), "started_at": m.group(4).strip(),
            "exception": exc,
            "console_marker_completed": bool(ok),
            "events": int(ok.group(2)) if ok else None,
            "chain_ok": (ok.group(3) == "OK") if ok else None,
            "replay_ok": (ok.group(4) == "OK") if ok else None,
            "block_chars": len(block),
        })
    return out


def run(out_dir: str) -> Dict[str, Any]:
    log = os.path.join(out_dir, "heldout_original", "run.log")
    attempts = split_attempts(log)
    history = _jsonl(os.path.join(out_dir, "attempt_history.jsonl"))
    hist = {(h["task_id"], h["attempt"]): h for h in history}

    # Three states, not two. An attempt whose case has not yet been flushed to
    # `attempt_history.jsonl` is *still running* — the runner writes a case's
    # records only once its attempts are over. Calling that "failed" is what
    # the first version did, and it reported the case currently in flight as a
    # crash: a log with no completion marker yet and a log with a crash look
    # identical until you know whether anything is still writing to it.
    #
    # `attempt_history.jsonl` is the authority for finished/unfinished; the
    # console marker is an independent second signal for the same question,
    # kept apart so a disagreement is visible rather than averaged away.
    disagreements = []
    for a in attempts:
        h = hist.get((a["task_id"], a["attempt"]))
        a["runner_returncode"] = (h or {}).get("returncode")
        a["runner_rerun_reason"] = (h or {}).get("rerun_reason")
        a["runner_said_stalled"] = (h or {}).get("stalled")
        if h is None:
            a["status"] = "IN_PROGRESS"
        else:
            a["status"] = "COMPLETED" if h.get("complete") else "FAILED"
        a["console_marker_agrees"] = (
            None if h is None
            else bool(h.get("complete")) == bool(a["console_marker_completed"]))
        if a["console_marker_agrees"] is False:
            disagreements.append({
                "task_id": a["task_id"], "attempt": a["attempt"],
                "console_says_completed": a["console_marker_completed"],
                "runner_says_complete": h.get("complete")})
        a["classification"] = classify(a["exception"], "")

    retries = [a for a in attempts if a["attempt"] > 1]
    in_progress = [a for a in attempts if a["status"] == "IN_PROGRESS"]
    failed = [a for a in attempts if a["status"] == "FAILED"]
    failed_first = [a for a in failed if a["attempt"] == 1]

    # The runner's own field, audited. A retry that succeeded carries a
    # description of a failure; that is a mislabel and it is named as one.
    mislabelled = []
    for a in attempts:
        if a["status"] == "COMPLETED" and str(
                a.get("runner_rerun_reason", "")).startswith("infrastructure"):
            mislabelled.append({
                "task_id": a["task_id"], "attempt": a["attempt"],
                "runner_wrote": a["runner_rerun_reason"],
                "actually": ("this attempt succeeded; the field describes a "
                             "failure that did not happen"),
                "the_failure_that_caused_the_retry": next(
                    (f["exception"] for f in attempts
                     if f["task_id"] == a["task_id"]
                     and f["attempt"] == a["attempt"] - 1), ""),
            })

    causes: Dict[str, int] = {}
    for a in failed_first:
        k = a["classification"]["kind"]
        causes[k] = causes.get(k, 0) + 1

    # §5.2 governs *re-runs*, so the denominator is the retries, and each
    # retry is judged by the failure that caused it. The first version
    # counted failed attempts over retries and printed "4 of 3" — a ratio
    # whose two sides were different populations, which is the error this
    # project has now recorded three times.
    by_key = {(a["task_id"], a["attempt"]): a for a in attempts}
    retry_causes = []
    for a in retries:
        prior = by_key.get((a["task_id"], a["attempt"] - 1))
        retry_causes.append({
            "task_id": a["task_id"], "retry_attempt": a["attempt"],
            "the_retry_completed": a["status"] == "COMPLETED",
            "cause": (prior or {}).get("classification", {}).get("kind"),
            "cause_detail": (prior or {}).get("exception", ""),
            "in_section_5_2_list":
                (prior or {}).get("classification", {}).get(
                    "in_section_5_2_list"),
        })
    non_infra = [r for r in retry_causes
                 if r["in_section_5_2_list"] != "yes"]

    # A failed attempt with no attempt after it is a different object: it is
    # not a re-run, and folding it into the retry count is how "4 of 3"
    # happened. "Has a successor" means an attempt for the same case with the
    # next number — the first version tested membership of the wrong key and
    # listed every retried failure as untried.
    keys = {(a["task_id"], a["attempt"]) for a in attempts}
    failed_without_retry = [
        {"task_id": a["task_id"], "attempt": a["attempt"],
         "exception": a["exception"],
         "classification": a["classification"]}
        for a in failed
        if (a["task_id"], a["attempt"] + 1) not in keys]
    out = {
        "check": "§5.2 — which re-runs the section actually permits",
        "n_attempts_logged": len(attempts),
        "n_cases_attempted": len({a["task_id"] for a in attempts}),
        "n_attempts_that_completed": sum(1 for a in attempts
                                         if a["status"] == "COMPLETED"),
        "n_attempts_that_failed": len(failed),
        "n_failed_attempts": len(failed),
        "n_attempts_in_progress": len(in_progress),
        "in_progress": [{"task_id": a["task_id"], "attempt": a["attempt"]}
                        for a in in_progress],
        "in_progress_note": (
            "an attempt whose case has no record in attempt_history.jsonl yet "
            "is still running. It is excluded from every failure count: a log "
            "with no completion marker and a log with a crash are the same "
            "string until you know whether anything is still writing."),
        "console_vs_runner_disagreements": disagreements,
        "n_retries": len(retries),
        "retry_cases": sorted({a["task_id"] for a in retries}),
        "failure_causes": causes,
        "retry_causes": retry_causes,
        "retries_not_permitted_by_section_5_2": non_infra,
        "failed_attempts_without_a_retry": failed_without_retry,
        "section_5_2_verdict": (
            f"{len(non_infra)} of {len(retries)} re-run(s) fall outside "
            f"§5.2's list and are disclosed rather than blessed"
            if non_infra else
            f"all {len(retries)} re-run(s) were infrastructure failures"),
        "section_5_2_denominator": (
            "re-runs, because §5.2 governs re-runs. A failed attempt that was "
            "never retried is reported separately under "
            "`failed_attempts_without_a_retry` rather than being added to "
            "this denominator."),
        "runner_field_audit": {
            "mislabelled_records": mislabelled,
            "the_bug": ("`rerun_reason` is written from `attempt > 1`, the "
                        "retry number, not from whether the attempt failed. A "
                        "retry that succeeded therefore carries a "
                        "failure's description. The field on the failing "
                        "attempt is the informative one; on the succeeding "
                        "attempt it should say that the retry worked."),
            "correction": ("`run_audit.json` supersedes the runner's field. "
                           "The runner was not edited: it was mid-pass, and "
                           "editing it would have invalidated the block for "
                           "the second time."),
        },
        "what_this_does_not_do": (
            "It does not re-run, re-seal or exclude anything. Every attempt "
            "below stayed in the block; the question this file answers is "
            "what the block should be said to have done."),
        "attempts": attempts,
    }
    with open(os.path.join(out_dir, "run_audit.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    r = run(args.out)
    print(json.dumps({k: v for k, v in r.items() if k != "attempts"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
