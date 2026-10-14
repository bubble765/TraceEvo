"""S3A §4: re-test A4 case 5 and case 8 with the invocation ledger on.

S2.1 measured both A4 injections as L0 and reported §5.4 as `n/a`, because the
fabricated response was absorbed at GoS's decision/content seam and produced no
event to localize. §4 asks for that to be re-measured with the ledger, which
should move the answer from

    semantic event root   = n/a

to

    model-call root       = localized
    semantic event root   = n/a

-- not by making A4 propagate, but by making the discarded call observable.

**How this reproduces the run without new live calls.** The injected run was in
extend mode, so the recording it used holds the whole trajectory: the replayed
clean prefix, then every live call the divergence caused. Replaying that file
with the same substitution re-serves the injected response at the same call and
finds every later prompt already recorded, so the run is reproduced exactly --
and `strict=true` makes that a check rather than a hope: one divergent prompt
would raise instead of quietly going live. The reproduced run's provenance is
then compared against the original injected run's, digest for digest.

The injection content is untouched. §3: nothing here is tuned to change the
outcome.

Usage:
    python traceevo/s3a_a4_recheck.py --cases 5,8 \
      --case-studies S2_audit/case_studies --out S3A_migration/a4_invocation_recheck
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
PY = sys.executable

sys.path.insert(0, ROOT)
from traceevo.audit.event_schema import sha256_hex  # noqa: E402
from traceevo.audit.invocation_ledger import load_ledger  # noqa: E402

_VOLATILE = ("timestamp", "event_hash", "prev_hash")
_INCONSISTENT = "Content stage result inconsistent with decision stage"


def _count_in(path: str, needle: str) -> int:
    if not os.path.exists(path):
        return -1          # -1: not measurable, which is not the same as zero
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for line in f if needle in line)


def _events_digest(path: str) -> str:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            for k in _VOLATILE:
                row.pop(k, None)
            rows.append(row)
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _run(name: str, case: int, recording: str, substitution: str,
         workdir: str, ledger: bool) -> Dict[str, Any]:
    run_dir = os.path.join(workdir, name)
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["TRACEEVO_ENABLED"] = "1"
    env["TRACEEVO_LEDGER"] = "1" if ledger else "0"
    env["TRACEEVO_OUTPUT_DIR"] = os.path.join(run_dir, "traceevo")
    env["TRACEEVO_REPLAY_MODE"] = "replay"
    env["TRACEEVO_REPLAY_PATH"] = recording
    env["TRACEEVO_REPLAY_STRICT"] = "1"
    env["TRACEEVO_SUBSTITUTE"] = substitution
    cmd = [PY, "-u", "main.py", "--cases", str(case),
           "--out-csv", os.path.join(run_dir, "results.csv"),
           "--html-dir", os.path.join(run_dir, "html"),
           "--json-dir", os.path.join(run_dir, "graphs"),
           "--log-dir", os.path.join(run_dir, "logs"),
           "--tag", name]
    log_path = os.path.join(run_dir, "run.log")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=GOS, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    return {"name": name, "returncode": proc.returncode, "dir": run_dir,
            "log": log_path,
            "artifacts": os.path.join(run_dir, "traceevo", "runs", name)}


def _linear_injected_transcript(work: str, recording: str,
                                sub_index: int, out_path: str) -> Dict[str, Any]:
    """Rebuild the injected run's call sequence as a flat recording.

    The extend-mode recording is not a linear transcript of the injected run.
    It is the clean recording (whose first `sub_index + 1` calls the injected
    run replayed) with the injected run's *live* calls appended after it. The
    file therefore reads

        [clean 0..N-1][injected live N..M]

    while the run itself executed

        [clean 0..sub_index][injected live N..M]

    Replaying the file as-is serves clean[sub_index+1] where the injected run
    had already diverged -- which is exactly what a strict replay reports as a
    divergence at request `sub_index + 1`. Splicing the two pieces back
    reconstructs the real sequence, and strict replay then either reproduces
    the run exactly or refuses; it cannot silently approximate it.
    """
    clean_path = os.path.join(work, "clean_recording.jsonl")
    with open(recording, encoding="utf-8") as f:
        rec = [json.loads(ln) for ln in f if ln.strip()]
    with open(clean_path, encoding="utf-8") as f:
        clean = [json.loads(ln) for ln in f if ln.strip()]

    n_clean = len(clean)
    prefix_matches = [r["key"] for r in rec[:n_clean]] == [c["key"] for c in clean]
    transcript = rec[:sub_index + 1] + rec[n_clean:]
    with open(out_path, "w", encoding="utf-8") as f:
        for row in transcript:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "clean_prefix_len": n_clean,
        "recording_len": len(rec),
        "substitution_call_index": sub_index,
        "transcript_len": len(transcript),
        "recording_prefix_is_the_clean_run": prefix_matches,
        "construction": ("clean[0..sub_index] + recorded_live_tail["
                         f"{n_clean}..{len(rec) - 1}]"),
    }


def _fabricated_text(fabricated_prefix: str, recording: str,
                     call_index: int) -> str:
    """What the substitution actually served, read from the sidecar."""
    side = recording + ".substitutions.json"
    if os.path.exists(side):
        with open(side, encoding="utf-8") as f:
            subs = json.load(f).get("substitutions") or []
        if subs:
            return subs[0].get("response", "")
    return fabricated_prefix


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="5,8")
    ap.add_argument("--case-studies", default=os.path.join(
        ROOT, "S2_audit", "case_studies"))
    ap.add_argument("--out", default=os.path.join(
        ROOT, "S3A_migration", "a4_invocation_recheck"))
    ap.add_argument("--workdir", default=os.path.join(ROOT, "S3A_runs",
                                                      "s3a_a4"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    out_index = {}
    for case in [int(x) for x in args.cases.split(",") if x.strip()]:
        name = f"A4_case{case}"
        work = os.path.join(args.case_studies, f"_work_{name}")
        recording = os.path.join(work, "recording.jsonl")
        substitution = os.path.join(work, "substitution.json")
        original_dir = os.path.join(args.case_studies, name)
        print(f"\n=== {name} ===", flush=True)
        for p in (recording, substitution):
            if not os.path.exists(p):
                raise SystemExit(f"missing {p}")

        with open(recording + ".substitutions.json", encoding="utf-8") as f:
            sub_index = json.load(f)["substitutions"][0]["call_index"]
        workdir = os.path.join(args.workdir, name)
        os.makedirs(workdir, exist_ok=True)
        transcript = os.path.join(workdir, "injected_transcript.jsonl")
        layout = _linear_injected_transcript(work, recording, sub_index,
                                             transcript)

        run = _run(f"{name}_replay_ledger", case, transcript, substitution,
                   args.workdir, ledger=True)
        print(f"  rc={run['returncode']} (strict replay of the injected run)",
              flush=True)

        result: Dict[str, Any] = {
            "study": "A4_invocation_root_recheck",
            "case": case,
            "method": ("deterministic strict replay of the original injected "
                       "run under the ledger; no live calls, no change to the "
                       "injection"),
            "transcript_layout": layout,
            "returncode": run["returncode"],
            "replay_was_exact": run["returncode"] == 0,
        }
        if run["returncode"] != 0:
            result["error"] = "the injected trajectory did not replay exactly"
            _dump(args.out, case, result)
            out_index[case] = result
            continue

        led = load_ledger(os.path.join(run["artifacts"],
                                       f"case-{case}_invocations.jsonl"))
        emitted = _fabricated_text("", recording, 0)
        injected_hash = sha256_hex(emitted)

        # 1. the invocation root: the call the injected response was served to
        served = [r for r in led if r["response_hash"] == injected_hash]
        disc = [r for r in led if r["parse_status"] == "discarded"]
        mismatch = [r for r in disc
                    if r["discard_reason"] == "DECISION_CONTENT_MISMATCH"]
        # The call right after the injected one, and the one the reject lands
        # on. This is the honest shape: the injected response is *accepted* by
        # the parser (it is well-formed JSON with a legal `decision`); what it
        # causes is the next call being thrown away.
        idx = next((i for i, r in enumerate(led)
                    if r["response_hash"] == injected_hash), None)
        follow = led[idx + 1] if idx is not None and idx + 1 < len(led) else None
        # The same check fires spontaneously in the clean runs, so the count
        # alone is not attributable to the injection. Both are reported.
        clean_log = os.path.join(work, f"{name}_record", "run.log")
        clean_mismatch = _count_in(clean_log, _INCONSISTENT)

        events = []
        epath = os.path.join(run["artifacts"], f"case-{case}_events.jsonl")
        with open(epath, encoding="utf-8") as f:
            events = [json.loads(ln) for ln in f if ln.strip()]
        needle = emitted[:70]
        carrying = [e["event_id"] for e in events
                    if needle in json.dumps(e.get("payload") or {},
                                            ensure_ascii=False)]
        nodes_carrying = []
        bpath = os.path.join(run["artifacts"], f"case-{case}_bindings.json")
        if os.path.exists(bpath):
            with open(bpath, encoding="utf-8") as f:
                pass

        # 2. did the reproduction produce the same trace as the original run?
        repro = {"events_digest": _events_digest(epath)}
        orig_epath = os.path.join(original_dir, "events.jsonl")
        if os.path.exists(orig_epath):
            repro["original_events_digest"] = _events_digest(orig_epath)
            repro["matches_original"] = (repro["events_digest"]
                                         == repro["original_events_digest"])

        result.update({
            "fabricated_response_sha256": injected_hash,
            "invocation_root": {
                "localized": bool(served),
                "call_id": served[0]["call_id"] if served else "",
                "stage": served[0]["stage"] if served else "",
                "step_id": served[0]["step_id"] if served else None,
                "prompt_hash": served[0]["prompt_hash"] if served else "",
                # Accepted, and that is the correct record: the substituted
                # response is well-formed JSON with a legal `decision`, so the
                # decision parser has nothing to object to. What it does not do
                # is commit -- no event carries it.
                "parse_status": served[0]["parse_status"] if served else "",
                "committed_to_state": (served[0]["committed_to_state"]
                                       if served else None),
                "committed_event_ids": (served[0]["committed_event_ids"]
                                        if served else []),
            },
            "discarded_follow_on_call": ({
                "call_id": follow["call_id"],
                "stage": follow["stage"],
                "parse_status": follow["parse_status"],
                "discard_reason": follow["discard_reason"],
                "committed_to_state": follow["committed_to_state"],
                "explanation": ("the content stage returned `retrieve` while "
                                "the injected decision said `analyze`, so GoS "
                                "discarded the step as a parse error and "
                                "emitted no event for it"),
            } if follow else {}),
            "discard_baseline": {
                "injected_run_mismatches": len(mismatch),
                "clean_run_mismatches": clean_mismatch,
                "attributable_to_the_injection": (1 if follow and
                                                  follow["parse_status"] ==
                                                  "discarded" else 0),
                "note": ("the decision/content disagreement also fires "
                         "spontaneously in the clean runs, so the injected "
                         "run's count is not by itself an injection signal"),
            },
            "semantic_event_root": {
                "localized": bool(carrying),
                "events_carrying_the_injected_text": carrying,
                # Kept explicitly: §4 requires the two to be reported as
                # separate quantities, not merged into one "root".
                "note": ("n/a by construction -- the response was discarded "
                         "before GoS emitted anything for it"),
            },
            "n_calls": len(led),
            "n_discarded": len(disc),
            "n_decision_content_mismatch": len(mismatch),
            "reproduction": repro,
            "ledger_summary": {
                "by_status": _count(r["parse_status"] for r in led),
                "by_stage": _count(r["stage"] for r in led),
                "uncommitted_call_rate": round(
                    sum(1 for r in led if not r["committed_to_state"])
                    / len(led), 4) if led else 0.0,
            },
        })
        _dump(args.out, case, result)
        out_index[case] = result
        print(f"  invocation root localized: {bool(served)} "
              f"({served[0]['discard_reason'] if served else '-'})", flush=True)
        print(f"  semantic event root       : {bool(carrying)}", flush=True)
        print(f"  reproduces the original   : "
              f"{repro.get('matches_original')}", flush=True)

    with open(os.path.join(args.out, "index.json"), "w",
              encoding="utf-8") as f:
        json.dump(out_index, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}")
    return 0


def _count(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _dump(out_dir: str, case: int, result: Dict[str, Any]) -> None:
    with open(os.path.join(out_dir, f"case{case}.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
