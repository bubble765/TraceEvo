"""S3D-A §3.2: freeze everything before case 50 is read.

§3.2 asks for this to happen "在首次读取 case 50 之前" — before the first read
of case 50 — and the ordering is the whole point. A freeze written after the run
records what the config happened to be, not what it was committed to. This
module reads only configuration files, rule sources and git state; it never
opens a case.

Follows `S3A_migration/rule_freeze_manifest.json`'s convention, and adds the
items §3.2 names that S3A did not record: the OutcomeVerifier's prompt hash and
model, and the Experience Gate's hash.

Two fields in §3.2's list have no counterpart in this implementation and are
recorded as absent rather than given a plausible value:

    eta     no such parameter exists in the belief or agent configuration
    delta   present as `gap_delta` in the FSM thresholds; recorded under both
            names so the correspondence is explicit

Usage:
    python traceevo/s3d_freeze.py --out S3D_verified_expansion
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
sys.path.insert(0, ROOT)

# §D2's long-term split, frozen before any case 50-59 content is read.
DATA_SPLIT = {
    "0-9": {"role": "legacy_dev", "headline_eligible": False,
            "source": "S0/S1/S2 Dev-Small"},
    "10-29": {"role": "migration_headline_historical", "headline_eligible": True,
              "source": "S3A migration holdout"},
    "30-39": {"role": "original_memory_build", "headline_eligible": True,
              "source": "S3B build"},
    "40-49": {"role": "historical_behavior_probe", "headline_eligible": False,
              "source": "S3B probe; retrieval dry-run only from here"},
    "50-59": {"role": "s3d_expansion_build", "headline_eligible": True,
              "source": "S3D run (no memory)"},
    "60-69": {"role": "reserve_s4_development_probe",
              "headline_eligible": False, "source": "untouched"},
    "70-79": {"role": "reserve_s4_parameter_validation",
              "headline_eligible": False, "source": "untouched"},
    "80-137": {"role": "final_heldout_test", "headline_eligible": False,
               "source": "untouched"},
}

RULE_FILES = [
    # `cross_graph_binding.py` holds the absence / inference / recommend /
    # knowledge regexes. The first version of this list omitted it and read the
    # patterns from `policy.py`, which has none -- so `absence_regex_hash` was
    # the digest of an empty dict and the freeze recorded "no absence rules"
    # about the one file that defines them.
    "traceevo/audit/cross_graph_binding.py",
    "traceevo/audit/evidence_auditor.py",
    "traceevo/audit/anomaly_detector.py",
    "traceevo/audit/policy.py",
    "traceevo/audit/dependency_graph.py",
    "traceevo/audit/integrity_auditor.py",
    "traceevo/audit/invocation_ledger.py",
    "traceevo/audit/event_schema.py",
    "traceevo/audit/graph_delta.py",
    "traceevo/evolution/experience_gate.py",
    "traceevo/evolution/outcome_verifier.py",
    "traceevo/evolution/leakage_guard.py",
    "traceevo/evolution/memory_store.py",
    "traceevo/evolution/memory_retriever.py",
    "traceevo/evolution/experience.py",
    "traceevo/config.py",
    "gos/agents/central_agent.py",
    "gos/agents/expert_agent.py",
    "gos/belief/graph.py",
    "gos/Run/DiagnosisArena.py",
    "gos/utils/public_function.py",
    "gos/utils/traceevo_bridge.py",
    "gos/main.py",
]

CONFIG_FILES = [
    "gos/configs/Agent_Configs.yaml",
    "gos/configs/Belief_Configs.yaml",
    "gos/configs/LLM_Configs.yaml",
    "gos/configs/TraceEvo_Configs.yaml",
]


def _sha(path: str) -> str:
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _git(cwd: str, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def _yaml(path: str) -> Dict[str, Any]:
    import yaml
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build(out_dir: str) -> Dict[str, Any]:
    from traceevo.audit.event_schema import utc_now

    agent = _yaml("gos/configs/Agent_Configs.yaml").get("llm_args", {})
    belief = _yaml("gos/configs/Belief_Configs.yaml").get("fsm", {}).get(
        "thresholds", {})

    # hashes of the rule modules, gathered so a single digest can be compared
    file_hashes = {p: _sha(p) for p in RULE_FILES + CONFIG_FILES}
    audit_rule_hash = _sha_text(json.dumps(
        {p: file_hashes[p] for p in RULE_FILES}, sort_keys=True))

    # the absence regexes and the P0-P3 tier rules live in policy.py /
    # evidence_auditor.py; their hashes come from the same file digests, and the
    # individual patterns are copied here because a digest cannot be read
    from traceevo.audit import cross_graph_binding as cgb
    absence = {}
    for name in ("_ABSENCE_RE", "_INFERENCE_RE", "_RECOMMEND_RE",
                 "_KNOWLEDGE_RE"):
        rx = getattr(cgb, name, None)
        if rx is not None:
            absence[name.lstrip("_")] = {
                "pattern": rx.pattern[:90], "sha256": _sha_text(rx.pattern)}
    from traceevo.audit import evidence_auditor as ea
    tier_rules = {
        "tiers": [n for n in ("P0", "P1", "P2", "P3")
                  if hasattr(ea, n)],
        "thresholds": {k: getattr(ea, k, None)
                       for k in ("DIRECT_MIN_COVERAGE", "SOFT_MIN_COVERAGE",
                                 "SOFT_MIN_CHAR_SIMILARITY",
                                 "EXPERT_SYNTHESIS_MIN_COVERAGE",
                                 "INPUT_INFERENCE_MIN_COVERAGE",
                                 "MIN_CONTENT_TOKENS")
                       if hasattr(ea, k)},
    }

    from traceevo.evolution.outcome_verifier import JUDGE_SYSTEM_PROMPT
    from traceevo.evolution.experience_gate import (ELIGIBLE, FAILURE_EXPERIENCE,
                                                    MANUAL_REVIEW, REJECT)

    man: Dict[str, Any] = {
        "generated_at": utc_now(),
        "purpose": ("S3D §3.2 freeze. Written before any case 50-59 content was "
                    "read; nothing in this manifest may change during S3D."),
        "data_split": DATA_SPLIT,
        "s3d_reads": "case 50-59 only",
        "s3d_must_not_read": "case 60-137",
        "git": {
            "traceevo_commit": _git(ROOT, "rev-parse", "HEAD"),
            "traceevo_branch": _git(ROOT, "rev-parse", "--abbrev-ref", "HEAD"),
            "traceevo_status": _git(ROOT, "status", "--porcelain"),
            "gos_commit": _git(GOS, "rev-parse", "HEAD"),
            "gos_branch": _git(GOS, "rev-parse", "--abbrev-ref", "HEAD"),
            "gos_status": _git(GOS, "status", "--porcelain"),
        },
        "run_config": {
            "backbone": agent.get("model_path"),
            "temperature": agent.get("temperature"),
            "max_tokens": agent.get("max_tokens"),
            "max_steps": belief.get("max_steps"),
            "session_max_steps": agent.get("session_max_steps"),
            "max_retrieval_steps": agent.get("max_retrieval_steps"),
            "retrieval_budget": agent.get("max_retrieval_steps"),
            "delta": belief.get("gap_delta"),
            "gap_delta": belief.get("gap_delta"),
            "min_support": belief.get("min_support"),
            "eta": None,
            "eta_note": ("§3.2 names `eta`; no such parameter exists in the "
                         "belief or agent configuration of this "
                         "implementation. Recorded as absent rather than "
                         "assigned a plausible value."),
            "memory": "OFF",
            "capability_profile": "OFF",
        },
        "rule_hashes": {
            "audit_rule_hash": audit_rule_hash,
            "p0_p3_rule_hash": _sha_text(json.dumps(tier_rules,
                                                    sort_keys=True)),
            "p0_p3_rules": tier_rules,
            "absence_regex_hash": _sha_text(json.dumps(absence, sort_keys=True)),
            "absence_regexes": absence,
            "experience_gate_hash": file_hashes.get(
                "traceevo/evolution/experience_gate.py"),
            "gate_outcomes": [ELIGIBLE, FAILURE_EXPERIENCE, MANUAL_REVIEW,
                              REJECT],
        },
        "outcome_verifier": {
            "prompt_sha256": _sha_text(JUDGE_SYSTEM_PROMPT),
            "model": agent.get("model_path"),
            "temperature": 0.0,
            "repeats": 3,
            "rubric": "2 exact, 1 broad category containing, 0 otherwise",
            "verdict_rule": ("3/3 score 2 -> verified_success; 3/3 score 0 -> "
                             "verified_incorrect; else uncertain"),
        },
        "file_hashes": file_hashes,
        "config_hashes": {p: file_hashes.get(p) for p in CONFIG_FILES},
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "S3D_freeze_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return man


def verify(out_dir: str) -> Dict[str, Any]:
    """Re-check every hash after the run. §12 criterion 2 depends on this."""
    path = os.path.join(out_dir, "S3D_freeze_manifest.json")
    with open(path, encoding="utf-8") as f:
        man = json.load(f)
    drift = []
    for p, want in man["file_hashes"].items():
        got = _sha(p)
        if got != want:
            drift.append({"file": p, "frozen": want, "now": got})
    return {"checked": len(man["file_hashes"]), "drift": drift,
            "unchanged": not drift}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        ROOT, "S3D_verified_expansion"))
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        print(json.dumps(verify(args.out), ensure_ascii=False, indent=2))
        return 0
    m = build(args.out)
    print(json.dumps({
        "generated_at": m["generated_at"],
        "backbone": m["run_config"]["backbone"],
        "temperature": m["run_config"]["temperature"],
        "audit_rule_hash": m["rule_hashes"]["audit_rule_hash"][:16],
        "absence_regex_hash": m["rule_hashes"]["absence_regex_hash"][:16],
        "experience_gate_hash": m["rule_hashes"]["experience_gate_hash"][:16],
        "outcome_verifier_prompt": m["outcome_verifier"]["prompt_sha256"][:16],
        "eta": m["run_config"]["eta"],
    }, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}/S3D_freeze_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
