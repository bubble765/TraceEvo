"""S3A §6.1: freeze the S2 audit rules before the migration audit runs.

§6.1 asks for a record of what the rules *were* at the moment the holdout was
first touched:

    audit_rule_commit
    evidence_thresholds
    absence_regex_hash
    capability_policy_hash

The point is not bookkeeping. §7's whole verdict rests on the claim that the
rules were not adapted to case 10-29, and there are no gold labels on those
cases to appeal to. The only defence available is that the rules are pinned by
hash before the first case runs and verified again after the last one, so
"frozen" is a checkable fact rather than a promise.

`verify()` is the same manifest read back and compared. It is run by the
migration audit at the end and its result is reported either way: a mismatch
means a rule changed mid-run and the migration numbers describe two different
auditors.

Usage:
    python traceevo/s3a_freeze.py --write       # before the holdout run
    python traceevo/s3a_freeze.py --verify      # after it
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")

# The files whose contents *are* the audit rules. Hashing the source rather
# than restating the rules in a config is deliberate: a config can drift from
# the code, a hash cannot.
RULE_FILES = [
    "traceevo/audit/cross_graph_binding.py",   # absence / inference / recommendation / knowledge regexes
    "traceevo/audit/evidence_auditor.py",      # P0-P3 cascade + thresholds
    "traceevo/audit/anomaly_detector.py",      # A0-A5 detectors, severity/confidence
    "traceevo/audit/policy.py",                # capability policy
    "traceevo/audit/dependency_graph.py",      # the nine parent rules
    "traceevo/audit/integrity_auditor.py",     # completeness / hash-chain checks
    "traceevo/audit/invocation_ledger.py",     # S3A channel (must also be frozen)
    "traceevo/audit/event_schema.py",
    "traceevo/config.py",
]

GOS_FILES = [
    "gos/agents/central_agent.py",
    "gos/agents/expert_agent.py",
    "gos/belief/graph.py",
    "gos/utils/public_function.py",
    "gos/configs/LLM_Configs.yaml",
    "gos/configs/Agent_Configs.yaml",
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(args: List[str], cwd: str) -> str:
    try:
        out = subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                             text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def collect() -> Dict[str, Any]:
    sys.path.insert(0, ROOT)
    from traceevo.audit import cross_graph_binding as cgb
    from traceevo.audit import evidence_auditor as ea
    from traceevo.audit import anomaly_detector as ad
    from traceevo.audit import policy as pol

    file_hashes = {}
    for rel in RULE_FILES + GOS_FILES:
        p = os.path.join(ROOT, rel)
        file_hashes[rel] = sha256_file(p) if os.path.exists(p) else "MISSING"

    # The absence regex is quoted in full, not just hashed, so a reviewer can
    # see what was frozen without having to resolve a hash to a revision.
    absence_source = {
        "ABSENCE": cgb._ABSENCE_RE.pattern,
        "INFERENCE": cgb._INFERENCE_RE.pattern,
        "RECOMMEND": cgb._RECOMMEND_RE.pattern,
        "KNOWLEDGE": cgb._KNOWLEDGE_RE.pattern,
    }
    return {
        "generated_at": _now(),
        "audit_rule_commit": {
            "traceevo_git": _git(["rev-parse", "HEAD"], ROOT),
            "gos_git": _git(["rev-parse", "HEAD"], GOS),
            "gos_git_status": _git(["status", "--porcelain"], GOS),
            "traceevo_git_status": _git(["status", "--porcelain"], ROOT),
        },
        "file_hashes": file_hashes,
        "evidence_thresholds": dict(ea.DEFAULT_CFG),
        "absence_regex": {k: {"pattern": v, "sha256": sha256_text(v)}
                          for k, v in absence_source.items()},
        "absence_regex_hash": sha256_text(json.dumps(absence_source,
                                                     sort_keys=True)),
        "capability_policy": {
            "central_tools": sorted(pol.CENTRAL_TOOLS),
            "expert_tools": sorted(pol.EXPERT_TOOLS),
            "sha256": sha256_text(json.dumps(
                {"central": sorted(pol.CENTRAL_TOOLS),
                 "expert": sorted(pol.EXPERT_TOOLS)}, sort_keys=True)),
        },
        "severity": dict(ad.SEVERITY),
        "confidence": {str(k): v for k, v in ad.CONFIDENCE.items()},
        "ledger": {"stages": list(_ledger_stages()),
                   "parse_statuses": list(_ledger_statuses())},
    }


def _ledger_stages():
    from traceevo.audit.invocation_ledger import STAGES
    return STAGES


def _ledger_statuses():
    from traceevo.audit.invocation_ledger import PARSE_STATUSES
    return PARSE_STATUSES


def _now() -> str:
    from traceevo.audit.event_schema import utc_now
    return utc_now()


def verify(manifest_path: str) -> Dict[str, Any]:
    with open(manifest_path, encoding="utf-8") as f:
        frozen = json.load(f)
    current = collect()
    diffs = []
    for rel, h in frozen["file_hashes"].items():
        if current["file_hashes"].get(rel) != h:
            diffs.append({"file": rel, "frozen": h[:16],
                          "now": str(current["file_hashes"].get(rel))[:16]})
    for key in ("evidence_thresholds", "absence_regex_hash",
                "capability_policy", "severity"):
        if frozen.get(key) != current.get(key):
            diffs.append({"rule": key, "frozen": "differs", "now": "differs"})
    return {"frozen": True, "unchanged": not diffs, "differences": diffs,
            "verified_at": current["generated_at"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", metavar="PATH")
    g.add_argument("--verify", metavar="PATH")
    args = ap.parse_args()
    if args.write:
        m = collect()
        with open(args.write, "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        print(f"froze {len(m['file_hashes'])} files -> {args.write}")
        print(f"  absence_regex_hash : {m['absence_regex_hash'][:16]}")
        print(f"  evidence_thresholds: {m['evidence_thresholds']}")
        print(f"  gos commit         : {m['audit_rule_commit']['gos_git'][:12]}")
        return 0
    v = verify(args.verify)
    print(json.dumps(v, ensure_ascii=False, indent=2))
    return 0 if v["unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
