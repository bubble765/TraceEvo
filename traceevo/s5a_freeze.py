"""S5A-A §8: freeze Core-v1 before case 70 is read.

§8 asks for the final method version pinned before the validation block is
touched, and for three switches to be recorded as OFF rather than merely set:
Memory injection, role adaptation, ATGG.

The three OFF switches are the substantive part. Core-v1 is what survives §5's
demotion list, and the manifest says so by naming what is *excluded*:

    ATGG           S4B found no natural efficacy evidence (LOW_TRIGGER_DENSITY)
    role profile   S4A found no role-specific information gain
    memory         never showed a diagnostic benefit (S3B probe: M0 led)

Recording them as OFF in the frozen artifact means a later reader can see the
final method was chosen on the evidence rather than by omission.

Follows `s3d_freeze.py`'s convention so the two manifests can be diffed. §8
names `eta`, which does not exist in this implementation; it is recorded as
absent with the reason, as in S3D, rather than given a plausible value.

Usage:
    python traceevo/s5a_freeze.py --out S5A_protocol_validation
    python traceevo/s5a_freeze.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from typing import Any, Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5A_protocol_validation")
VALIDATION = list(range(70, 80))
MUST_NOT_READ = list(range(80, 138))

# The frozen data split, per §7. Recorded here as well as in the S3D manifest
# because a split that only exists in an earlier stage's file is a split a
# later stage can drift from without noticing.
DATA_SPLIT = {
    "0-9": "legacy_development",
    "10-29": "migration_historical_outcome",
    "30-39": "experience_build",
    "40-49": "historical_memory_behavior_probe",
    "50-59": "expansion_profile_evaluation",
    "60-69": "gold_blind_natural_trigger_density_cohort",
    "70-79": "s5a_protocol_validation",
    "80-137": "final_held_out_test",
}

# Method-1 and Method-2 as §4 defines them, mapped to the modules that
# implement each item, so "Core-v1 contains X" is checkable rather than
# asserted.
CORE_V1 = {
    "Method-1 Dual-Graph Auditable Reasoning": {
        "ProvenanceEvent": "traceevo/audit/event_schema.py",
        "Model Invocation Ledger": "traceevo/audit/invocation_ledger.py",
        "GraphDelta": "traceevo/audit/graph_delta.py",
        "Cross-Graph Binding": "traceevo/audit/cross_graph_binding.py",
        "Evidence Provenance Tier": "traceevo/audit/evidence_auditor.py",
        "Execution Dependency DAG": "traceevo/audit/dependency_graph.py",
        "Integrity Audit": "traceevo/audit/integrity_auditor.py",
        "Evidence Audit": "traceevo/audit/evidence_auditor.py",
        "Observable-root Localization": "traceevo/audit/anomaly_detector.py",
        "Propagation Tracing": "traceevo/audit/dependency_graph.py",
    },
    "Method-2 Audit-Gated Trustworthy Experience Consolidation": {
        "Post-hoc Outcome Verification": "traceevo/evolution/outcome_verifier.py",
        "Experience Eligibility Contract": "traceevo/evolution/experience_gate.py",
        "Validated Success / Failure / Review / Reject":
            "traceevo/evolution/experience_gate.py",
        "OutcomeOnly / AuditOnly / TraceEvo Gate":
            "traceevo/evolution/experience_gate.py",
        "Success / Failure Memory Partition": "traceevo/evolution/memory_store.py",
        "Gold Leakage Guard": "traceevo/evolution/leakage_guard.py",
    },
}

DEMOTED = {   # §5: kept in the codebase, not in the first paper's core
    "Role-specific Capability Profile": "traceevo/s4a_profile.py",
    "ATGG-v1 adaptive guard": "traceevo/evolution/grounding_guard.py",
    "Cross-case Success Memory Injection": "traceevo/hooks.py::on_plan_prompt",
    "Failure Memory online activation": "traceevo/evolution/memory_store.py",
}

RULE_FILES = [
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
        for ch in iter(lambda: f.read(65536), b""):
            h.update(ch)
    return h.hexdigest()


def _sha_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _git(cwd: str, *a: str) -> str:
    try:
        return subprocess.run(["git", "-C", cwd, *a], capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:                                 # pragma: no cover
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
    from traceevo.evolution.outcome_verifier import JUDGE_SYSTEM_PROMPT

    agent = _yaml("gos/configs/Agent_Configs.yaml").get("llm_args", {})
    belief = _yaml("gos/configs/Belief_Configs.yaml").get("fsm", {}).get(
        "thresholds", {})

    files = {p: _sha(p) for p in RULE_FILES + CONFIG_FILES}
    from traceevo.audit import cross_graph_binding as cgb
    absence = {n.lstrip("_"): {"pattern": getattr(cgb, n).pattern[:90],
                               "sha256": _sha_text(getattr(cgb, n).pattern)}
               for n in ("_ABSENCE_RE", "_INFERENCE_RE", "_RECOMMEND_RE",
                         "_KNOWLEDGE_RE") if hasattr(cgb, n)}
    # The thresholds live in `DEFAULT_CFG`, lowercase, not as module-level
    # constants. S3D's freeze made the same mistake one level up (reading the
    # absence regexes from a file that had none) and wrote the digest of an
    # empty dict -- "no P0-P3 thresholds" about the module that defines them.
    # This is that failure recurring: the first version of this freeze looked
    # for `DIRECT_MIN_COVERAGE` and friends, found none, and produced
    # `p0_p3_rule_hash = 44136fa355b3678a`, which is the sha256 of `{}`.
    #
    # A hash of nothing is not a missing field; it is a field claiming the
    # ruleset is empty. The guard below makes that unrepresentable.
    from traceevo.audit import evidence_auditor as ea
    tier_rules = {
        "tiers": list(getattr(ea, "TIERS", ())),
        "default_cfg": dict(getattr(ea, "DEFAULT_CFG", {})),
        "content_lineage": sorted(getattr(ea, "CONTENT_LINEAGE", ())),
        "self_declared": sorted(getattr(ea, "SELF_DECLARED", ())),
    }
    if not tier_rules["default_cfg"]:
        raise SystemExit(
            "P0-P3 thresholds not found; refusing to freeze a manifest whose "
            "rule hash would be the digest of an empty dict")

    man: Dict[str, Any] = {
        "generated_at": utc_now(),
        "method_version": "Core-v1",
        "purpose": ("§8 freeze. Written before any case 70-79 content was "
                    "read; nothing in this manifest may change during S5A."),
        "data_split": DATA_SPLIT,
        "s5a_reads": f"case {VALIDATION[0]}-{VALIDATION[-1]}",
        "s5a_must_not_read": f"case {MUST_NOT_READ[0]}-{MUST_NOT_READ[-1]}",
        "core_v1_modules": CORE_V1,
        "demoted_to_exploratory": DEMOTED,
        "switches": {
            "memory_injection": "OFF",
            "role_adaptation": "OFF",
            "atgg": "OFF",
            "switches_reason": {
                "memory_injection": (
                    "S3B probe: M0 (no memory) led on Match and Relevant; no "
                    "experiment has shown a diagnostic benefit."),
                "role_adaptation": (
                    "S4A NO_ROLE_SPECIFIC_ADAPTATION: role intervals overlap "
                    "and no role profile beat a global baseline."),
                "atgg": (
                    "S4B LOW_TRIGGER_DENSITY: no natural efficacy evidence; "
                    "Core-v1 does not depend on it."),
            },
            "how_asserted": (
                "each switch is set via TRACEEVO_MEMORY=0, no routing call, "
                "TRACEEVO_GUARD=0, and the run is checked afterwards for the "
                "artifact each would have produced (injections, guard "
                "actions) rather than trusting the flag"),
        },
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
            "retrieval_budget": agent.get("max_retrieval_steps"),
            "delta": belief.get("gap_delta"),
            "gap_delta": belief.get("gap_delta"),
            "min_support": belief.get("min_support"),
            "eta": None,
            "eta_note": ("§8 names `eta`; no such parameter exists in the "
                         "belief or agent configuration of this "
                         "implementation. Recorded as absent rather than "
                         "assigned a plausible value. S3D recorded the same."),
        },
        "rule_hashes": {
            "audit_rule_hash": _sha_text(json.dumps(
                {p: files[p] for p in RULE_FILES}, sort_keys=True)),
            "p0_p3_rule_hash": _sha_text(json.dumps(tier_rules,
                                                    sort_keys=True)),
            "p0_p3_rules": tier_rules,
            "absence_regex_hash": _sha_text(json.dumps(absence, sort_keys=True)),
            "absence_regexes": absence,
            "outcome_verifier_hash":
                files.get("traceevo/evolution/outcome_verifier.py"),
            "outcome_verifier_prompt_sha256": _sha_text(JUDGE_SYSTEM_PROMPT),
            "experience_gate_hash": files.get(
                "traceevo/evolution/experience_gate.py"),
        },
        "outcome_verifier": {
            "model": agent.get("model_path"),
            "temperature": 0.0, "repeats": 3,
            "rubric": "2 exact, 1 broad category containing, 0 otherwise",
            "verdict_rule": ("3/3 score 2 -> verified_success; 3/3 score 0 -> "
                             "verified_incorrect; else uncertain"),
        },
        "file_hashes": files,
        "config_hashes": {p: files.get(p) for p in CONFIG_FILES},
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "Core-v1_freeze_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    return man


def verify(out_dir: str) -> Dict[str, Any]:
    p = os.path.join(out_dir, "Core-v1_freeze_manifest.json")
    with open(p, encoding="utf-8") as f:
        man = json.load(f)
    drift = []
    for path, want in man["file_hashes"].items():
        got = _sha(path)
        if got != want:
            drift.append({"file": path, "frozen": want, "now": got})
    return {"checked": len(man["file_hashes"]), "drift": drift,
            "unchanged": not drift}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        print(json.dumps(verify(args.out), ensure_ascii=False, indent=2))
        return 0
    m = build(args.out)
    print(json.dumps({
        "method_version": m["method_version"],
        "generated_at": m["generated_at"],
        "backbone": m["run_config"]["backbone"],
        "temperature": m["run_config"]["temperature"],
        "max_steps": m["run_config"]["max_steps"],
        "delta": m["run_config"]["delta"],
        "retrieval_budget": m["run_config"]["retrieval_budget"],
        "eta": m["run_config"]["eta"],
        "switches": {k: v for k, v in m["switches"].items()
                     if not k.endswith("_reason") and k != "how_asserted"},
        "hashes": {k: (v[:16] if isinstance(v, str) else v)
                   for k, v in m["rule_hashes"].items()
                   if k.endswith("_hash") or k.endswith("sha256")},
    }, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}/Core-v1_freeze_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
