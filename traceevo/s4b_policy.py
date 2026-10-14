"""S4B-A §6: freeze the ATGG-v1 policy before case 60 is read.

§6 asks for the historical audit findings to be turned into an executable policy
*first*, with source artifact hashes, so that the policy is a commitment rather
than a description of what the guard turned out to do.

Three guards appear in the manifest and only one is active. The two inactive
ones are the load-bearing part: §1 closes the role-weighting branch on S4A's
negative result, and §3 keeps P1 out of the hard guard on case-51's evidence
that a P1 node can sit on a correct path. Writing them down as *decisions not
to act* is what makes them reviewable later; a manifest that listed only the
active guard would look the same whether or not anyone had considered the other
two.

Everything here is computed from case 10-59, none of which S4B runs. The
manifest hashes its inputs so a reviewer can confirm the policy was derived
from the corpus it claims.

Usage:
    python traceevo/s4b_policy.py --out S4B_adaptive_guard
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S4B_adaptive_guard")
HOLDOUT = list(range(60, 70))
MUST_NOT_READ = list(range(70, 138))
POLICY_VERSION = "ATGG-v1"

# The corpus the policy is derived from. case 40-49 is omitted deliberately:
# it received a memory behaviour treatment (S3B probe) and S3D §4 excludes it
# from profile work for the same reason.
SOURCE_BLOCKS = [
    {"block": "10-29", "role": "S3A migration holdout / historical feedback",
     "archive": os.path.join(ROOT, "S3A_runs", "s3a_migration", "traceevo",
                             "runs", "migration_holdout")},
    {"block": "30-39", "role": "S3B memory build",
     "archive": os.path.join(ROOT, "S3B_runs", "memory_build", "gos",
                             "traceevo", "runs", "memory_build")},
    {"block": "50-59", "role": "S3D expansion build",
     "archive": os.path.join(ROOT, "S3D_verified_expansion", "runs_50_59",
                             "traceevo", "runs", "s3d_expansion")},
]


def _sha_file(p: str) -> str:
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def _sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _jsonl(p: str) -> List[Dict[str, Any]]:
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def survey() -> Dict[str, Any]:
    """Count the historical evidence for each guard decision.

    This is the empirical basis §6 asks for. It reads audit summaries, not
    traces, and it is deliberately the same measurement S4A made so the two
    stages cannot disagree about how often P3 is used.
    """
    from traceevo.s3b_gate import audit_bundle

    per_block, p3_events, p1_events = [], [], []
    for blk in SOURCE_BLOCKS:
        lo, hi = blk["block"].split("-")
        n_cases = n_p3_used = n_p1_used = 0
        n_nodes = 0
        for c in range(int(lo), int(hi) + 1):
            ar = blk["archive"]
            if not os.path.exists(os.path.join(ar, f"case-{c}_events.jsonl")):
                continue
            n_cases += 1
            b = audit_bundle(ar, f"case-{c}")
            for d in b["decisions"]:
                n_nodes += 1
                if not d.get("used_in_reasoning"):
                    continue
                if d.get("provenance_tier") == "P3_UNSUPPORTED_ASSERTION":
                    n_p3_used += 1
                    p3_events.append({"case": c, "node_id": d["node_id"],
                                      "label": (d.get("label") or "")[:80],
                                      "derivation": d.get("derivation", "")})
                if d.get("provenance_tier") == "P1_SOFT_GROUNDED":
                    n_p1_used += 1
                    p1_events.append({"case": c, "node_id": d["node_id"],
                                      "label": (d.get("label") or "")[:80]})
        per_block.append({
            "block": blk["block"], "role": blk["role"],
            "archive": os.path.relpath(blk["archive"], ROOT),
            "n_cases": n_cases, "n_decisions": n_nodes,
            "n_p3_used_in_reasoning": n_p3_used,
            "n_p1_used_in_reasoning": n_p1_used,
            "has_events_file": os.path.relpath(
                os.path.join(blk["archive"], "case-"
                             f"{int(lo)}_events.jsonl"), ROOT)})
    return {"per_block": per_block, "p3_events": p3_events,
            "p1_events": p1_events,
            "n_p3_used_total": sum(b["n_p3_used_in_reasoning"]
                                   for b in per_block),
            "n_p1_used_total": sum(b["n_p1_used_in_reasoning"]
                                   for b in per_block),
            "n_cases_total": sum(b["n_cases"] for b in per_block)}


def build(out_dir: str) -> Dict[str, Any]:
    from traceevo.audit.event_schema import utc_now
    from traceevo.evolution.grounding_guard import (G_P1_SOFT, G_P3_SOURCE,
                                                    G_ROLE_RISK, RETRY_PROMPT,
                                                    TRIGGER_RULE)

    s = survey()
    os.makedirs(out_dir, exist_ok=True)

    source_hashes = []
    for blk in SOURCE_BLOCKS:
        lo, hi = blk["block"].split("-")
        files = {}
        for c in range(int(lo), int(hi) + 1):
            p = os.path.join(blk["archive"], f"case-{c}_events.jsonl")
            if os.path.exists(p):
                files[f"case-{c}_events.jsonl"] = _sha_file(p)
        source_hashes.append({
            "block": blk["block"],
            "archive": os.path.relpath(blk["archive"], ROOT),
            "n_files_hashed": len(files),
            "combined_sha256": _sha_text(
                json.dumps(sorted(files.items()))),
            "files": files})

    manifest = {
        "policy_version": POLICY_VERSION,
        "generated_at": utc_now(),
        "purpose": ("§6: the historical audit findings turned into an "
                    "executable policy, frozen before case 60 is read."),
        "source_artifacts": [b["archive"] for b in SOURCE_BLOCKS],
        "source_artifact_hashes": source_hashes,
        "source_blocks": SOURCE_BLOCKS,
        "holdout": {"cases": f"{HOLDOUT[0]}-{HOLDOUT[-1]}",
                    "n": len(HOLDOUT),
                    "note": "S4B runs only these; nothing here was read by "
                            "the policy"},
        "must_not_read": {"cases": f"{MUST_NOT_READ[0]}-{MUST_NOT_READ[-1]}",
                          "n": len(MUST_NOT_READ)},
        "historical_evidence": {
            "per_block": s["per_block"],
            "n_p3_used_total": s["n_p3_used_total"],
            "n_p1_used_total": s["n_p1_used_total"],
            "n_cases_total": s["n_cases_total"],
            "p3_events": s["p3_events"],
            "p1_events": s["p1_events"],
        },
        "active_guards": [{
            "guard_id": G_P3_SOURCE,
            "trigger": TRIGGER_RULE,
            "action": "one source-verification retry before commit",
            "max_retry": 1,
            "rationale": (
                f"{s['n_p3_used_total']} P3 nodes used in reasoning across "
                f"{s['n_cases_total']} cases of case 10-59. §3 selects this "
                f"as the one risk with empirical support: an unsupported "
                f"assertion entering the causal graph as SUPPORT/REFUTE is "
                f"the failure the audit layer exists to catch, and the "
                f"counterexample work in S3B/S3C already showed the Gate "
                f"refuses such traces."),
            "on_retry_still_p3": "remove the offending Evidence node and its "
                                 "support/refute edge; preserve the raw "
                                 "candidate in the provenance record; commit "
                                 "the rest; guard_result = BLOCKED",
            "on_retry_parse_failure": "commit the original delta minus the "
                                      "offending elements; guard_result = "
                                      "RETRY_FAILED",
            "forbidden": ["rewriting the content into a medical fact",
                          "inventing a source",
                          "modifying the invocation ledger"],
            "retry_prompt_sha256": _sha_text(RETRY_PROMPT),
            "retry_prompt": RETRY_PROMPT,
        }],
        "inactive_guards": [
            {"guard_id": G_P1_SOFT,
             "reason": ("case-51 (S3D) was a verified success whose decision "
                        "path carried 2 P1 soft-grounded nodes; P1 is observed "
                        "on correct paths and there is no evidence that "
                        "blocking it improves anything. §3: S4B records P1 and "
                        "does not intervene."),
             "evidence": {"n_p1_used_total": s["n_p1_used_total"],
                          "p1_events": s["p1_events"]}},
            {"guard_id": G_ROLE_RISK,
             "reason": ("S4A `NO_ROLE_SPECIFIC_ADAPTATION`: the three expert "
                        "roles' P3-use intervals overlap and no role-specific "
                        "predictor beat the global baseline. §1 closes the "
                        "role-weighting branch; §19 forbids reopening it."),
             "evidence": {"source": "S4A_capability_profile/"
                                    "profile_actionability.json",
                          "n_guard_candidates": 0}},
        ],
        "policy_is_not_a_model": (
            "§6: this is not a trained model. It is a fixed rule over the "
            "frozen P0-P3 classifier, and no threshold in it was fitted to "
            "any outcome."),
        "frozen_rules_not_modified": [
            "traceevo/audit/evidence_auditor.py (P0-P3)",
            "traceevo/audit/cross_graph_binding.py",
            "traceevo/audit/anomaly_detector.py",
            "traceevo/evolution/experience_gate.py",
            "traceevo/evolution/outcome_verifier.py",
        ],
    }
    with open(os.path.join(out_dir, "guard_policy_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def freeze(out_dir: str) -> Dict[str, Any]:
    """The §17 freeze manifest: policy hash plus every file S4B may not change."""
    from traceevo.audit.event_schema import utc_now
    from traceevo.s3d_freeze import CONFIG_FILES, RULE_FILES

    pol = os.path.join(out_dir, "guard_policy_manifest.json")
    files = {p: _sha_file(os.path.join(ROOT, p))
             for p in RULE_FILES + CONFIG_FILES}
    # the S4B additions are not frozen rules but must be pinned too, so a
    # mid-run edit to the guard itself is visible after the fact
    for p in ("traceevo/evolution/grounding_guard.py",
              "traceevo/hooks.py",
              "gos/agents/central_agent.py"):
        files[p] = _sha_file(os.path.join(ROOT, p))
    man = {
        "generated_at": utc_now(),
        "policy_version": POLICY_VERSION,
        "policy_manifest": os.path.relpath(pol, ROOT),
        "policy_manifest_sha256": _sha_file(pol),
        "audit_rule_hash": _sha_text(json.dumps(
            {p: files[p] for p in RULE_FILES}, sort_keys=True)),
        "file_hashes": files,
        "checked": len(files),
        "ntp_note": ("no network time is used; timestamps come from the local "
                     "clock via event_schema.utc_now()"),
    }
    with open(os.path.join(out_dir, "freeze_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return man


def verify(out_dir: str) -> Dict[str, Any]:
    p = os.path.join(out_dir, "freeze_manifest.json")
    with open(p, encoding="utf-8") as f:
        man = json.load(f)
    drift = [{"file": k, "frozen": v, "now": _sha_file(os.path.join(ROOT, k))}
             for k, v in man["file_hashes"].items()
             if _sha_file(os.path.join(ROOT, k)) != v]
    pol_now = _sha_file(os.path.join(ROOT, man["policy_manifest"]))
    return {"checked": len(man["file_hashes"]), "drift": drift,
            "unchanged": not drift,
            "policy_meta_changed": pol_now != man["policy_manifest_sha256"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        print(json.dumps(verify(args.out), ensure_ascii=False, indent=2))
        return 0
    m = build(args.out)
    f = freeze(args.out)
    print(json.dumps({
        "policy_version": m["policy_version"],
        "n_active_guards": len(m["active_guards"]),
        "n_inactive_guards": len(m["inactive_guards"]),
        "historical": {b["block"]: {
            "cases": b["n_cases"],
            "p3_used": b["n_p3_used_in_reasoning"],
            "p1_used": b["n_p1_used_in_reasoning"]}
            for b in m["historical_evidence"]["per_block"]},
        "n_p3_used_total": m["historical_evidence"]["n_p3_used_total"],
        "n_p1_used_total": m["historical_evidence"]["n_p1_used_total"],
        "freeze": {"checked": f["checked"] if isinstance(f, dict) else None,
                   "policy_sha256": f["policy_manifest_sha256"][:16]},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
