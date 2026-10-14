"""S5B-A §2/§4/§15: freeze the final claim, the method, and the access log.

Three artifacts are produced here and all three must exist before case 80 is
opened:

    FINAL_CLAIM_MANIFEST.md              §2, the claims and what would settle
                                         each one
    Final_Core_v1_freeze_manifest.json   §4, the frozen method and its hashes
    FINAL_DATA_ACCESS_LOG.json           §4, whose first entry is this
                                         manifest's own timestamp

The order is the point. §2 says the research claim is frozen *before* the first
case is read, so that a claim cannot be written to fit the block. §15 says the
case-study selection rules are pre-registered, so that the final write-up
cannot pick the best-looking example. Both are statements about time, and the
only way to make them checkable is for the freeze to record the time and for
the run to record its own time separately.

§4 also names three hashes whose inputs are scripts that are part of the stage
itself: the controlled injection specification, the evaluation scripts, and
the table generators. They are hashed here rather than after the run, which
requires the driver scripts to be written before the freeze. That is the
intended order -- a script written afterwards could not have been frozen.

§8 names `eta`, which does not exist in this implementation; it is recorded as
absent with the reason, as S3D and S5A did, rather than given a plausible
value.

Usage:
    python traceevo/s5b_freeze.py --out S5B_final_heldout
    python traceevo/s5b_freeze.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOS = os.path.join(ROOT, "gos")
DATASET_CSV = os.path.join(GOS, "datasets", "DiagnosisArena", "test_set.csv")
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "S5B_final_heldout")
HELDOUT = list(range(80, 138))
MUST_NOT_READ = list(range(0, 80))
N_HELDOUT = len(HELDOUT)
SEED = 20260920
BOOTSTRAP = 10000

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

# ---------------------------------------------------------------- §7.2 frozen
# The injection specification, frozen before any case 80 content is read.
#
# §7.2 says 原则上每个适用 case 每类 1 次 and then gives per-class caps of
# "up to 58" for A1/A2/A3 while A5 is "按冻结的 tamper variants". The caps
# equal the number of cases, which fixes the reading: one injection per case
# per class for A1/A2/A3, and every frozen tamper variant for A5.
#
# A2 has two frozen variants and one slot per case, so the choice has to be
# made by a rule rather than by preference. The rule below alternates by case
# index in the frozen variant order, which is fixed here and cannot be
# adjusted once the results are visible.
INJECTION_SPEC: Dict[str, Any] = {
    "frozen_at": "before first read of case 80",
    "headline_classes": ["A1", "A2", "A3", "A5"],
    "case_study_classes": ["A4"],
    "excluded_classes": ["A0"],
    "A1": {"variants": ["add_node"], "per_case": 1,
           "n_attempted_max": N_HELDOUT},
    "A2": {"variants": ["wrong_record_binding", "record_tampering"],
           "per_case": 1, "n_attempted_max": N_HELDOUT,
           "variant_selection": (
               "deterministic alternation in the frozen variant order: "
               "case c takes A2.variants[(c - 80) % 2]")},
    "A3": {"variants": ["unresolvable_expert"], "per_case": 1,
           "n_attempted_max": N_HELDOUT},
    "A5": {"variants": ["modify", "delete", "reorder", "truncate"],
           "per_case": 4, "n_attempted_max": 4 * N_HELDOUT,
           "note": ("§7.2's A5 line defers to 冻结的 tamper variants, and all "
                    "four are frozen; each is a different integrity failure "
                    "mode rather than a repeat of one")},
    "A4": {"variants": ["corrupt_analysis"], "per_case": 1,
           "n_attempted_max": N_HELDOUT,
           "headline": False,
           "note": ("§7.1: A4 is an invocation/parser boundary case study and "
                    "does not enter the headline semantic propagation F1")},
    "A0": {"status": "NOT_INJECTED", "reason": (
        "§7.1 keeps A0 out of the headline. S4A established that no event in "
        "this pipeline carries status=degraded or failed, and §11 forbids "
        "fabricating a status to populate a category, so `status_marked` is "
        "refused. The `silent` variant fabricates no status; it is reported "
        "separately and is not in the headline either.")},
    "not_applicable": ("recorded as NOT_APPLICABLE with the injector's own "
                       "rejection message rather than forced into existence"),
    "statistics": {
        "headline": "macro average by anomaly class (§8.1)",
        "also_reported": "micro average (§8.1)",
        "ci": ("case-cluster bootstrap, case_id as the sampling unit, "
               f"{BOOTSTRAP} resamples, 95%, seed {SEED} (§8.2)"),
        "secondary": ("§8.3: secondary findings are reported as their own "
                      "count and type list, and are not counted as primary "
                      "false positives"),
    },
    "root": {
        "headline": "observable-root top-1 accuracy (§9)",
        "latent": ("latent-root exact accuracy, only for classes where the "
                   "latent root still exists in the trace; delete and "
                   "truncate are excluded because the object to localize is "
                   "gone"),
    },
}

# --------------------------------------------------------------- §15 frozen
# §15 asks for the case-study selection rules to be fixed before the run so
# that the final write-up cannot choose the best-looking case.
CASE_STUDY_RULES: Dict[str, Any] = {
    "dual_graph_normal_example": {
        "rule": "case 80, whatever it shows",
        "fallback": ("case 81, and only if the graph is unreadable, with the "
                     "reason recorded as a technical drawing failure"),
    },
    "natural_p3_example": {
        "rule": ("the smallest case_id carrying a P3 node that was used in "
                 "reasoning"),
        "if_none": "no natural P3 case study exists on this block",
        "forbidden": "choosing by diagnostic result",
    },
    "controlled_anomaly_path": {
        "rule": "case 80's A1 injection",
        "fallback": ("if A1 is not applicable to case 80, the first "
                     "applicable case in ascending case_id order"),
    },
    "experience_gate_case": {
        "rule": "the smallest case_id with a heldout verified_success",
        "requires": "clean vs poisoned Gate comparison",
        "if_none": ("no final positive case study; the paper says so "
                    "explicitly"),
    },
}

# --------------------------------------------------------------- §12-§13
# The ablation is an offline operation on artifact copies. Which modules are
# disabled, and which capability each is expected to carry, is fixed here.
ABLATION_SPEC: Dict[str, Any] = {
    "mode": "offline, on artifact copies; no LLM is re-run",
    "arms": {
        "Full": "every module enabled",
        "w/o ProvenanceTier": "P0-P3 source-aware evidence audit disabled",
        "w/o CrossGraph": ("evidence/hypothesis to execution-event binding "
                           "disabled"),
        "w/o Integrity": "hash-chain integrity audit disabled",
        "w/o Ledger": ("model invocation ledger disabled; scored only on the "
                       "parser-boundary and uncommitted-call capabilities"),
    },
    "capabilities": ["A1", "A2", "A5", "EvidenceTrace", "RootLoc",
                     "ParserBoundary"],
    "claim_tested": ("a module is necessary for the capability it was "
                     "designed for; §13 explicitly does NOT require every "
                     "module to lower one overall macro-F1"),
    "ledger_exclusion": ("§12: the ledger arm is not put into the same total "
                         "as A1/A2/A5, because averaging over capabilities it "
                         "was never meant to serve would be a meaningless "
                         "number either way"),
}

# ------------------------------------------------------------- Method-1/2
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
        "Experience Eligibility Contract":
            "traceevo/evolution/experience_gate.py",
        "Validated Success / Failure / Review / Reject":
            "traceevo/evolution/experience_gate.py",
        "OutcomeOnly / AuditOnly / TraceEvo Gate":
            "traceevo/evolution/experience_gate.py",
        "Success / Failure Memory Partition": "traceevo/evolution/memory_store.py",
        "Gold Leakage Guard": "traceevo/evolution/leakage_guard.py",
    },
}

DEMOTED = {
    "Role-specific Capability Profile": {
        "module": "traceevo/s4a_profile.py",
        "finding": "S4A NO_ROLE_SPECIFIC_ADAPTATION",
    },
    "ATGG-v1 adaptive guard": {
        "module": "traceevo/evolution/grounding_guard.py",
        "finding": "S4B LOW_TRIGGER_DENSITY",
    },
    "Cross-case Success Memory Injection": {
        "module": "traceevo/hooks.py::on_plan_prompt",
        "finding": "S3B probe: M0 (no memory) led on Match and Relevant",
    },
    "Failure Memory online activation": {
        "module": "traceevo/evolution/memory_store.py",
        "finding": ("no experiment has shown a diagnostic benefit; the "
                    "partition is produced, not activated"),
    },
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

# §4's three script hashes. The evaluation scripts are the frozen metric code
# (no S5B module reimplements a metric); the driver scripts are S5B's own and
# are written before this manifest is generated.
EVALUATION_SCRIPTS = [
    "traceevo/s2_matrix.py",
    "traceevo/s3b_gate.py",
    "gos/evaluate_Medical_scoring.py",
    "traceevo/evolution/outcome_verifier.py",
]
DRIVER_SCRIPTS = [
    "traceevo/s5b_run.py",
    "traceevo/s5b_pipeline.py",
    "traceevo/s5b_diagnostic.py",
    "traceevo/s5b_anomaly.py",
    "traceevo/s5b_gate.py",
    "traceevo/s5b_ablation.py",
    "traceevo/s5b_overhead.py",
    "traceevo/s5b_tables.py",
    "traceevo/s5b_figures.py",
    "traceevo/s5b_finalize.py",
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
    except Exception:                                     # pragma: no cover
        return ""


def _yaml(path: str) -> Dict[str, Any]:
    import yaml
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def environment() -> Dict[str, Any]:
    """§4's environment hash, over what the run actually depends on."""
    from importlib import metadata
    pkgs = {}
    for name in ("scipy", "numpy", "pandas", "pyyaml", "requests",
                 "matplotlib", "openai"):
        try:
            pkgs[name] = metadata.version(name)
        except Exception:                                 # pragma: no cover
            pkgs[name] = "ABSENT"
    env = {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": pkgs,
    }
    env["environment_hash"] = _sha_text(json.dumps(env, sort_keys=True))
    return env


def requirements_lock(out_dir: str) -> Dict[str, Any]:
    """§21's `requirements_lock.txt`, and §4's requirements hash."""
    from importlib import metadata
    dists = sorted(
        f"{d.metadata['Name']}=={d.version}"
        for d in metadata.distributions()
        if d.metadata and d.metadata.get("Name"))
    body = ("# TraceEvo S5B final held-out — environment lock\n"
            "# generated by traceevo/s5b_freeze.py before case 80 was read\n"
            "# Python " + sys.version.split()[0] + " on " + platform.platform()
            + "\n\n" + "\n".join(dists) + "\n")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "requirements_lock.txt"), "w",
              encoding="utf-8") as f:
        f.write(body)
    return {"n_distributions": len(dists),
            "requirements_hash": _sha_text(body),
            "gos_requirements_hash": _sha("gos/requirements.txt")}


def case_index_hash() -> Dict[str, Any]:
    """§4's dataset hash / case index hash.

    The held-out case *index* is recorded by its position in the frozen split,
    not by reading the rows. Hashing the file is not reading it, and the
    content hash of the dataset is exactly what a freeze is for.
    """
    return {
        "dataset": os.path.relpath(DATASET_CSV, ROOT),
        "dataset_sha256": _sha(os.path.relpath(DATASET_CSV, ROOT)),
        "case_index": {
            "heldout": [HELDOUT[0], HELDOUT[-1]],
            "n_cases": N_HELDOUT,
            "index_hash": _sha_text(json.dumps(HELDOUT)),
        },
        "must_not_read": {
            "ranges": "case 0-79",
            "reason": ("§3: historical data may be used only for the frozen "
                       "rules themselves, the development-vs-heldout "
                       "contrast, and re-checking earlier paper results; no "
                       "rule may be adjusted against it during S5B"),
        },
    }


def build(out_dir: str) -> Dict[str, Any]:
    from traceevo.audit.event_schema import utc_now
    from traceevo.evolution.outcome_verifier import JUDGE_SYSTEM_PROMPT
    from traceevo.audit import evidence_auditor as ea
    from traceevo.audit import cross_graph_binding as cgb

    agent = _yaml("gos/configs/Agent_Configs.yaml").get("llm_args", {})
    belief = _yaml("gos/configs/Belief_Configs.yaml").get("fsm", {}).get(
        "thresholds", {})

    files = {p: _sha(p) for p in RULE_FILES + CONFIG_FILES}

    absence = {n.lstrip("_"): {"pattern": getattr(cgb, n).pattern[:90],
                               "sha256": _sha_text(getattr(cgb, n).pattern)}
               for n in ("_ABSENCE_RE", "_INFERENCE_RE", "_RECOMMEND_RE",
                         "_KNOWLEDGE_RE") if hasattr(cgb, n)}

    # The thresholds live in `DEFAULT_CFG`, lowercase. S3D's freeze and then
    # S5A's wrote the digest of an empty dict by looking for uppercase module
    # constants that never existed. The guard is carried forward unchanged:
    # an empty ruleset is unrepresentable.
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

    env = environment()
    req = requirements_lock(out_dir)
    idx = case_index_hash()

    inj_hash = _sha_text(json.dumps(INJECTION_SPEC, sort_keys=True))
    eval_hash = _sha_text(json.dumps(
        {p: _sha(p) for p in EVALUATION_SCRIPTS}, sort_keys=True))
    table_hash = _sha_text(json.dumps(
        {p: _sha(p) for p in ("traceevo/s5b_tables.py",
                              "traceevo/s5b_finalize.py")}, sort_keys=True))
    driver_hashes = {p: _sha(p) for p in DRIVER_SCRIPTS}

    missing = [p for p, h in driver_hashes.items() if not h]
    if missing:
        raise SystemExit(
            f"refusing to freeze: driver script(s) absent {missing}. §4's "
            f"evaluation/table hashes must cover scripts that exist before "
            f"the run, otherwise they cannot have been frozen by it.")

    man: Dict[str, Any] = {
        "generated_at": utc_now(),
        "stage": "S5B final held-out",
        "method_version": "Core-v1",
        "purpose": ("§4 final freeze. Written before the first read of case "
                    "80. §23 forbids modifying the algorithm, the audit "
                    "rules, the OutcomeVerifier or the Gate after this "
                    "point."),
        "data_split": DATA_SPLIT,
        "heldout": {"cases": f"{HELDOUT[0]}-{HELDOUT[-1]}",
                    "n_cases": N_HELDOUT,
                    "one_pass": ("§5: one pass, strict case order, no case's "
                                 "result may affect a later case's strategy")},
        "must_not_read": {"cases": "0-79", "reason": "§3"},
        "git": {
            "traceevo_commit": _git(ROOT, "rev-parse", "HEAD"),
            "traceevo_branch": _git(ROOT, "rev-parse", "--abbrev-ref", "HEAD"),
            "traceevo_status": _git(ROOT, "status", "--porcelain"),
            "gos_commit": _git(GOS, "rev-parse", "HEAD"),
            "gos_branch": _git(GOS, "rev-parse", "--abbrev-ref", "HEAD"),
            "gos_status": _git(GOS, "status", "--porcelain"),
        },
        "environment": env,
        "requirements": req,
        "dataset": idx,
        "run_config": {
            "backbone": agent.get("model_path"),
            "backbone_frozen": ("the backbone is frozen at deepseek-v4-flash "
                                "for every stage; §23 forbids changing it"),
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
                         "assigned a plausible value, as S3D and S5A did."),
        },
        "switches": {
            "memory_injection": "OFF",
            "failure_memory_activation": "OFF",
            "role_adaptation": "OFF",
            "atgg": "OFF",
            "how_asserted": (
                "TRACEEVO_MEMORY=0, TRACEEVO_GUARD=0, no routing call, and "
                "the run is then checked for the artifact each would have "
                "produced (injection records, guard actions, role profile "
                "reads) rather than trusting the flag"),
        },
        "core_v1_modules": CORE_V1,
        "demoted_to_exploratory": DEMOTED,
        "rule_hashes": {
            "audit_rule_hash": _sha_text(json.dumps(
                {p: files[p] for p in RULE_FILES}, sort_keys=True)),
            "p0_p3_rule_hash": _sha_text(json.dumps(tier_rules,
                                                    sort_keys=True)),
            "p0_p3_rules": tier_rules,
            "absence_rule_hash": _sha_text(json.dumps(absence,
                                                      sort_keys=True)),
            "absence_rules": absence,
            "audit_policy_hash": files.get("traceevo/audit/policy.py"),
            "integrity_policy_hash":
                files.get("traceevo/audit/integrity_auditor.py"),
            "anomaly_detector_hash":
                files.get("traceevo/audit/anomaly_detector.py"),
            "outcome_verifier_hash":
                files.get("traceevo/evolution/outcome_verifier.py"),
            "outcome_verifier_prompt_sha256": _sha_text(JUDGE_SYSTEM_PROMPT),
            "experience_gate_hash":
                files.get("traceevo/evolution/experience_gate.py"),
        },
        "outcome_verifier": {
            "model": agent.get("model_path"),
            "temperature": 0.0,
            "repeats": 3,
            "rubric": "2 exact, 1 broad category containing, 0 otherwise",
            "verdict_rule": ("3/3 score 2 -> verified_success; 3/3 score 0 -> "
                             "verified_incorrect; else uncertain"),
            "prompt_sha256": _sha_text(JUDGE_SYSTEM_PROMPT),
        },
        "controlled_injection_spec": INJECTION_SPEC,
        "controlled_injection_spec_hash": inj_hash,
        "evaluation_script_hash": eval_hash,
        "evaluation_scripts": {p: _sha(p) for p in EVALUATION_SCRIPTS},
        "table_generator_hash": table_hash,
        "driver_script_hashes": driver_hashes,
        "statistics": {
            "seed": SEED,
            "diagnostic_ci": ("case-level bootstrap, 10,000 resamples, 95% "
                              "(§16)"),
            "clean_alert_ci": "Wilson 95% (§16)",
            "anomaly_ci": ("case-cluster bootstrap, case_id as the sampling "
                           "unit, 10,000 resamples, 95% (§8.2, §16)"),
            "gate_ci": "Wilson / exact binomial (§16)",
            "no_p_hunting": ("§16: no unregistered p-value hunting, no "
                             "subgroup added because a result looked poor, no "
                             "searching the 58 cases for a significant "
                             "subset"),
        },
        "case_study_rules_preregistered": CASE_STUDY_RULES,
        "ablation_spec": ABLATION_SPEC,
        "failure_protocol": {
            "infrastructure_rerun": {
                "allowed": ("network interruption, API 5xx, process exit, "
                            "filesystem failure — §5.2"),
                "requirements": ("same frozen code, same frozen config, "
                                 "rerun_reason recorded, attempt history "
                                 "preserved; the first failed record is never "
                                 "deleted"),
            },
            "algorithm_bug": {
                "verdict": "FINAL_HELDOUT_INVALIDATED",
                "action": ("§5.3: stop immediately. Do not fix the code and "
                           "re-run case 80-137 as if still unseen. Return to "
                           "review: either new data, or this block is "
                           "demoted to additional development evidence."),
            },
        },
        "file_hashes": files,
        "config_hashes": {p: files.get(p) for p in CONFIG_FILES},
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "Final_Core_v1_freeze_manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)

    _claim_manifest(os.path.join(out_dir, "FINAL_CLAIM_MANIFEST.md"), man)
    _access_log(os.path.join(out_dir, "FINAL_DATA_ACCESS_LOG.json"), man)
    return man


# ---------------------------------------------------------------- §2 claims
CLAIMS: List[Dict[str, Any]] = [
    {
        "id": "C1", "kind": "final claim",
        "text": ("TraceEvo can build fine-grained, replayable execution "
                 "provenance during GoS medical multi-agent reasoning, and "
                 "bind execution provenance to the causal reasoning graph."),
        "heldout_evidence": ["replay", "cross-graph binding", "source tier",
                             "integrity", "natural trace"],
        "established_if": ("seals verify, delta replay passes, bindings "
                           "resolve, and the natural trace is complete on all "
                           "58 held-out cases"),
        "falsified_if": ("a systematic seal, replay or binding failure on the "
                         "held-out block"),
    },
    {
        "id": "C2", "kind": "final claim",
        "text": ("TraceEvo can detect controlled collaboration anomalies from "
                 "provenance, evidence-source and integrity information, and "
                 "localize the observable failure seam."),
        "heldout_evidence": ["A1/A2/A3/A5 controlled injection",
                             "clean alert rate",
                             "observable-root localization"],
        "established_if": ("per-class detection is reported with its real "
                           "denominator and CIs, and observable-root top-1 is "
                           "reported on held-out traces"),
        "falsified_if": ("detection fails on the held-out block, or the "
                         "numbers only hold on the injection procedure that "
                         "produced them"),
        "caveat": ("precision and recall are measured against injections this "
                   "codebase makes; that is consistency with its own fault "
                   "model and not detection in the wild"),
    },
    {
        "id": "C3", "kind": "conditional claim",
        "text": ("A Gate that joins Outcome Verification with Audit avoids "
                 "unsafe experience admission that either signal alone would "
                 "allow."),
        "heldout_evidence": ["verified_success", "incorrect-clean",
                             "correct-but-poisoned twins"],
        "established_if": ("the held-out block yields enough verified "
                           "successes to build poisoned twins and the Gate's "
                           "positive branch is exercised independently"),
        "if_positive_class_sparse": ("C3-positive = "
                                     "development-supported only, stated as "
                                     "such (§2)"),
        "if_positive_class_absent": ("HELDOUT_POSITIVE_CLASS_ABSENT: the "
                                     "positive branch is supported by the "
                                     "development controlled experiment and "
                                     "the final held-out block is reported as "
                                     "not having produced an independent "
                                     "replication"),
    },
    {
        "id": "C4", "kind": "claimable",
        "text": ("TraceEvo can turn audit results into traceable Success / "
                 "Failure / Manual Review / Reject experience partitions."),
        "frame": ("Trustworthy Experience Consolidation — NOT 'verified "
                  "continuous improvement of diagnostic performance'"),
        "heldout_evidence": ["gate candidates", "gate verdict distribution",
                             "partition assignment on held-out traces"],
    },
]

NOT_CLAIMED = [
    {"text": "Success Memory improves diagnostic accuracy",
     "why": "no experiment has shown it; the S3B probe had M0 (no memory) "
            "highest on both Match and Relevant"},
    {"text": "role-specific routing is effective",
     "why": "S4A NO_ROLE_SPECIFIC_ADAPTATION"},
    {"text": "ATGG improves natural task performance",
     "why": "S4B LOW_TRIGGER_DENSITY — never evaluated for efficacy"},
    {"text": "TraceEvo continuously improves medical diagnostic performance "
             "through online self-evolution",
     "why": "no behaviour experiment supports it and S5B runs none"},
]


def _claim_manifest(path: str, man: Dict[str, Any]) -> None:
    L = [
        "# FINAL CLAIM MANIFEST",
        "",
        "Per `TraceEvo_S5A_Review_S5B_Final_Heldout_v1.md` §2. **Frozen at "
        + man["generated_at"] + ", before the first read of case 80.**",
        "",
        "The purpose of writing this now is that a claim fixed after seeing "
        "the block is a description of the block, not a claim. Each entry "
        "below has a stated held-out evidence source and a stated falsifier, "
        "so the final matrix (§19) can mark it supported or not against "
        "criteria that predate the data.",
        "",
        "## Frozen method",
        "",
        "* Core-v1 = Method-1 Dual-Graph Auditable Reasoning + Method-2 "
        "Audit-Gated Trustworthy Experience Consolidation.",
        "* Memory injection OFF, Failure Memory activation OFF, role "
        "adaptation OFF, ATGG OFF.",
        "* Backbone frozen at `" + str(man["run_config"]["backbone"]) + "`.",
        "* Held-out = case 80-137, " + str(N_HELDOUT) + " cases, one pass.",
        "",
    ]
    for c in CLAIMS:
        L += [f"## {c['id']} — {c['kind']}", "", c["text"], ""]
        if c.get("frame"):
            L += [f"> Framed as: {c['frame']}", ""]
        L += ["| | |", "|---|---|"]
        L.append("| held-out evidence | "
                 + "; ".join(c["heldout_evidence"]) + " |")
        for k, label in (("established_if", "established if"),
                         ("falsified_if", "falsified if"),
                         ("caveat", "caveat"),
                         ("if_positive_class_sparse", "if positive class sparse"),
                         ("if_positive_class_absent", "if positive class absent")):
            if c.get(k):
                L.append(f"| {label} | {c[k]} |")
        L.append("")
    L += ["## Explicitly NOT claimed", "",
          "§2: none of these may be claimed in S5B regardless of the result.",
          "", "| not claimed | why |", "|---|---|"]
    for n in NOT_CLAIMED:
        L.append(f"| {n['text']} | {n['why']} |")
    L += [
        "",
        "## Pre-registered case-study rules (§15)",
        "",
        "§15 fixes these before the run so the final write-up cannot select "
        "the best-looking case:",
        "",
        "| slot | rule |",
        "|---|---|",
    ]
    for k, v in CASE_STUDY_RULES.items():
        rule = v["rule"] + (f" (fallback: {v['fallback']})"
                            if v.get("fallback") else "")
        rule += (f"; if none: {v['if_none']}" if v.get("if_none") else "")
        L.append(f"| {k} | {rule} |")
    L += [
        "",
        "## What would make the whole block invalid (§5.3)",
        "",
        "A P0-P3 rule implementation error, a Gate logic error, a wrong audit "
        "statistic, premature gold access, or final output invalidated by a "
        "Core code defect gives `FINAL_HELDOUT_INVALIDATED` and an immediate "
        "stop. The code is not fixed and case 80-137 is not re-run as if "
        "still unseen.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def _access_log(path: str, man: Dict[str, Any]) -> None:
    log = {
        "stage": "S5B final held-out",
        "requirement": ("§4: the first access to case 80 must be later than "
                        "the freeze manifest. This file is written by the "
                        "freeze and appended to by the run, so the ordering "
                        "is a property of two timestamps rather than of a "
                        "comment."),
        "freeze_manifest_written_at": man["generated_at"],
        "first_case_80_access": None,
        "ordering_check": "PENDING",
        "entries": [
            {"at": man["generated_at"], "actor": "traceevo/s5b_freeze.py",
             "action": "wrote Final_Core_v1_freeze_manifest.json",
             "read_cases": [], "note": "no case content read"},
            {"at": man["generated_at"], "actor": "traceevo/s5b_freeze.py",
             "action": "wrote FINAL_CLAIM_MANIFEST.md",
             "read_cases": [], "note": "§2 claim frozen before case 80"},
        ],
        "must_not_read": {"cases": "0-79", "reason": "§3"},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def log_access(out_dir: str, action: str, actor: str, cases: List[int],
               note: str = "") -> Dict[str, Any]:
    """Append to §4's access log. Called by the run and pipeline modules."""
    from traceevo.audit.event_schema import utc_now
    p = os.path.join(out_dir, "FINAL_DATA_ACCESS_LOG.json")
    if not os.path.exists(p):
        raise SystemExit(f"no access log at {p}; the freeze must run first")
    with open(p, encoding="utf-8") as f:
        log = json.load(f)
    now = utc_now()
    log["entries"].append({"at": now, "actor": actor, "action": action,
                           "read_cases": cases, "note": note})
    if log.get("first_case_80_access") is None and 80 in cases:
        log["first_case_80_access"] = now
        log["ordering_check"] = ("OK: first case 80 access is after the "
                                 "freeze manifest"
                                 if now > log["freeze_manifest_written_at"]
                                 else "VIOLATION: case 80 was accessed at or "
                                      "before the freeze manifest")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return log


def verify(out_dir: str) -> Dict[str, Any]:
    p = os.path.join(out_dir, "Final_Core_v1_freeze_manifest.json")
    with open(p, encoding="utf-8") as f:
        man = json.load(f)
    drift = []
    for path, want in man["file_hashes"].items():
        got = _sha(path)
        if got != want:
            drift.append({"file": path, "frozen": want[:16], "now": got[:16]})
    sdrift = []
    for path, want in man["driver_script_hashes"].items():
        got = _sha(path)
        if got != want:
            sdrift.append({"file": path, "frozen": want[:16], "now": got[:16]})
    return {"rule_files_checked": len(man["file_hashes"]),
            "rule_file_drift": drift,
            "driver_scripts_checked": len(man["driver_script_hashes"]),
            "driver_script_drift": sdrift,
            "unchanged": not drift and not sdrift}


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
        "generated_at": m["generated_at"],
        "heldout": m["heldout"],
        "backbone": m["run_config"]["backbone"],
        "temperature": m["run_config"]["temperature"],
        "max_steps": m["run_config"]["max_steps"],
        "delta": m["run_config"]["delta"],
        "eta": m["run_config"]["eta"],
        "retrieval_budget": m["run_config"]["retrieval_budget"],
        "switches": {k: v for k, v in m["switches"].items()
                     if k != "how_asserted"},
        "environment_hash": m["environment"]["environment_hash"][:16],
        "requirements_hash": m["requirements"]["requirements_hash"][:16],
        "dataset_sha256": m["dataset"]["dataset_sha256"][:16],
        "hashes": {k: (v[:16] if isinstance(v, str) else v)
                   for k, v in m["rule_hashes"].items()
                   if k.endswith("_hash") or k.endswith("sha256")},
        "controlled_injection_spec_hash":
            m["controlled_injection_spec_hash"][:16],
        "evaluation_script_hash": m["evaluation_script_hash"][:16],
        "table_generator_hash": m["table_generator_hash"][:16],
    }, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}/Final_Core_v1_freeze_manifest.json, "
          f"FINAL_CLAIM_MANIFEST.md, FINAL_DATA_ACCESS_LOG.json, "
          f"requirements_lock.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
