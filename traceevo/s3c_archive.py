"""S3C-A §3.1: establish what the archived case 10-29 artifacts are.

§3.1 asks for a specific proof:

    S3A sealed artifact hash == S3C OutcomeVerifier input artifact hash

**That proof cannot be produced, and this module says so rather than
approximating it.** S3A never wrote a seal for the case 10-29 holdout. It
recorded prompt and response *sequence* hashes for ledger determinism
(`S3A_report.md`) and per-case `_integrity.json` files whose `expected_tip_hash`
is `null` — a chain that verifies against itself, never against a recorded
value. There is no `sealed_artifacts.json` for this corpus, the way S3B has one
for case 30-39.

What this module produces instead is the strongest available substitute, and it
is labelled as a substitute:

1. **Chain re-verification.** Every trace's hash chain is recomputed now. A
   chain that verifies says the events are internally consistent; it does not
   say they are unchanged since S3A, because the value it verifies against was
   never published.
2. **A dated input manifest.** sha256 of every artifact, computed at S3C time
   and written to `corpus_manifest.json`. This attests to *what S3C verified*,
   so the verification is reproducible. It does not attest to immutability
   between S3A and S3C.
3. **Cross-check against S3A's recorded aggregates.** `S3A_migration/
   migration_summary.json` recorded per-corpus numbers — 20 cases, 150 evidence
   nodes, externally_grounded_rate 0.58, and others. Those are recomputed from
   the artifacts on disk. If they match, the artifacts are substantively the
   ones S3A measured; if they do not, S3C is verifying something else.

Point 3 is the one that does real work, and it is the reason this is not just
a formality: it is the only available test that the corpus being verified is
the corpus S3A produced.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
ARCHIVE = os.path.join(ROOT, "S3A_runs", "s3a_migration", "traceevo", "runs",
                       "migration_holdout")
S3A_SUMMARY = os.path.join(ROOT, "S3A_migration", "migration_summary.json")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)
sys.path.insert(0, GOS)

CASES = list(range(10, 30))
SUFFIXES = ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
            "_integrity.json", "_invocations.jsonl")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def chain_check(case: int) -> Dict[str, Any]:
    """Recompute the trace's hash chain from the events on disk."""
    from traceevo.audit.integrity import verify_chain
    path = os.path.join(ARCHIVE, f"case-{case}_events.jsonl")
    if not os.path.exists(path):
        return {"case": case, "present": False, "ok": False,
                "reason": "no events file"}
    events = _load_jsonl(path)
    rec = verify_chain(events)
    d = rec.to_dict() if hasattr(rec, "to_dict") else dict(rec)
    recorded = {}
    ip = os.path.join(ARCHIVE, f"case-{case}_integrity.json")
    if os.path.exists(ip):
        with open(ip, encoding="utf-8") as f:
            recorded = json.load(f)
    return {
        "case": case, "present": True,
        "n_events": len(events),
        "chain_ok_now": bool(d.get("ok")),
        "n_issues": len(d.get("issues") or []),
        "tip_hash_now": d.get("tip_hash", ""),
        "tip_hash_recorded_at_S3A": recorded.get("tip_hash", ""),
        "expected_tip_hash_at_S3A": recorded.get("expected_tip_hash"),
        "tip_matches_s3a_record": d.get("tip_hash") == recorded.get("tip_hash"),
        # the field S3A left empty, and why the seal cannot be proven
        "s3a_expected_tip_hash_was_null": recorded.get("expected_tip_hash") is None,
    }


def cross_check_aggregates() -> Dict[str, Any]:
    """Recompute S3A's recorded corpus statistics from the artifacts on disk."""
    from traceevo.audit.evidence_auditor import audit_task, summarize
    from traceevo.audit.invocation_ledger import load_ledger

    if not os.path.exists(S3A_SUMMARY):
        return {"available": False}
    with open(S3A_SUMMARY, encoding="utf-8") as f:
        agg = json.load(f).get("aggregate", {})

    n_evidence, n_ext, n_lineage, n_calls, n_uncommitted, n_discarded = 0, 0, 0, 0, 0, 0
    per_case = []
    for c in CASES:
        task = f"case-{c}"
        try:
            decisions = audit_task(ARCHIVE, task)
        except Exception as exc:
            per_case.append({"case": c, "error": f"{type(exc).__name__}: {exc}"})
            continue
        s = summarize(decisions)
        # `n_evidence_nodes` is Evidence-typed nodes only. The first version of
        # this check counted every audited node and got 285 against S3A's 150,
        # which read as "the corpus is not the one S3A measured". It was the
        # denominator that was wrong: `audit_task` also returns Signal and
        # Hypothesis nodes (20 and 115 here), and S3A's summary counts neither.
        # Restricting to Evidence reproduces 150 / 87 / 126 exactly.
        evidence = [d for d in decisions if d.node_type == "Evidence"]
        n_evidence += len(evidence)
        n_ext += sum(1 for d in evidence if d.externally_grounded)
        n_lineage += sum(1 for d in evidence if d.lineage_complete)
        try:
            rows = load_ledger(os.path.join(ARCHIVE, f"{task}_invocations.jsonl"))
        except Exception:
            rows = []
        n_calls += len(rows)
        n_uncommitted += sum(1 for r in rows if not r["committed_to_state"])
        n_discarded += sum(1 for r in rows if r["parse_status"] == "discarded")
        per_case.append({"case": c, "n_evidence": len(evidence),
                         "by_tier": s.get("by_tier", {})})

    def _rate(num, den):
        return round(num / den, 4) if den else None

    computed = {
        "n_cases": len(CASES),
        "n_evidence_nodes": n_evidence,
        "externally_grounded_rate": _rate(n_ext, n_evidence),
        "lineage_complete_rate": _rate(n_lineage, n_evidence),
        "uncommitted_call_rate": _rate(n_uncommitted, n_calls),
        "discarded_call_rate": _rate(n_discarded, n_calls),
    }
    compared = []
    for k, v in computed.items():
        want = agg.get(k)
        compared.append({"metric": k, "s3a_recorded": want, "s3c_recomputed": v,
                         "matches": (want == v) if want is not None else None})
    return {"available": True, "computed": computed, "s3a_recorded": agg,
            "comparison": compared, "per_case": per_case,
            "all_match": all(c["matches"] for c in compared
                             if c["matches"] is not None)}


def run(out_dir: str) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "archive_judge_raw"), exist_ok=True)
    from traceevo.audit.event_schema import utc_now

    manifest: Dict[str, Any] = {
        "generated_at": utc_now(),
        "archive": ARCHIVE,
        "cases": CASES,
        "purpose": ("§3.1 input seal substitute. S3A published no seal for "
                    "case 10-29, so this manifest attests to what S3C read, "
                    "not to immutability since S3A."),
        "artifacts": {},
    }
    checks = []
    for c in CASES:
        task = f"case-{c}"
        entry = {}
        for suf in SUFFIXES:
            p = os.path.join(ARCHIVE, f"{task}{suf}")
            entry[suf.lstrip("_")] = sha256_file(p) if os.path.exists(p) else ""
        manifest["artifacts"][task] = entry
        chk = chain_check(c)
        chk["complete"] = bool(entry["events.jsonl"] and entry["bindings.json"])
        checks.append(chk)

    xc = cross_check_aggregates()
    manifest["chain_summary"] = {
        "n_traces": len([c for c in checks if c["present"]]),
        "n_chain_ok_now": sum(1 for c in checks if c.get("chain_ok_now")),
        "n_tip_matches_s3a_record": sum(1 for c in checks
                                        if c.get("tip_matches_s3a_record")),
        "n_with_null_expected_tip": sum(
            1 for c in checks if c.get("s3a_expected_tip_hash_was_null")),
    }
    manifest["seal_gap"] = {
        "found": False,
        "what_is_missing": ("a recorded per-artifact hash from S3A. "
                            "`_integrity.json` carries `expected_tip_hash: "
                            "null`, so the chain verifies against nothing "
                            "published."),
        "consequence": ("§3.1's literal requirement — S3A sealed hash == S3C "
                        "input hash — cannot be satisfied. The corpus is "
                        "attested by chain re-verification, a dated S3C "
                        "manifest, and agreement with S3A's recorded corpus "
                        "aggregates; none of those is the proof §3.1 asked "
                        "for."),
    }
    manifest["s3a_aggregate_cross_check"] = {
        k: v for k, v in xc.items() if k != "per_case"}
    with open(os.path.join(out_dir, "corpus_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "sealed_artifact_check.csv"), "w",
              newline="", encoding="utf-8") as f:
        cols = ["case", "complete", "n_events", "chain_ok_now", "n_issues",
                "tip_matches_s3a_record", "expected_tip_hash_at_S3A",
                "events_sha256"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for chk in checks:
            ev = manifest["artifacts"].get(f"case-{chk['case']}", {})
            w.writerow({**{k: chk.get(k) for k in cols if k in chk},
                        "events_sha256": ev.get("events.jsonl", "")})
    return manifest


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        ROOT, "S3C_experience_expansion"))
    args = ap.parse_args()
    m = run(args.out)
    print(json.dumps({
        "chain_summary": m["chain_summary"],
        "seal_gap": m["seal_gap"]["found"],
        "s3a_aggregate_cross_check": m["s3a_aggregate_cross_check"].get(
            "comparison"),
        "all_match": m["s3a_aggregate_cross_check"].get("all_match"),
    }, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}/corpus_manifest.json, sealed_artifact_check.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
