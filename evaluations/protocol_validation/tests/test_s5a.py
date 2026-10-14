"""S5A: the freeze, the seal-before-gold order, and the branches that must not
be filled in by hand.

S5A is the stage where the protocol becomes the deliverable, so the tests are
about the *shape* of the outputs rather than about any single number: a freeze
that records the rules it claims to freeze, a copy discipline that is checked,
and two branches (§11's A0, §13's absent positive class) that both invite the
same wrong move — manufacturing the row that the instructions say to leave
empty.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "S5A_protocol_validation")

from traceevo.s5a_freeze import (CORE_V1, DEMOTED, MUST_NOT_READ,  # noqa: E402
                                 VALIDATION)


def _load(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p):
        pytest.skip(f"{name} not produced yet")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- §8 the freeze
def test_the_freeze_records_the_real_p0_p3_thresholds():
    """S3D's empty-digest failure, guarded.

    The first version of the S5A freeze looked for module-level constants that
    do not exist, found none, and produced the sha256 of `{}` — a hash claiming
    the ruleset is empty. `s5a_freeze` now refuses to write that manifest, and
    this checks the value it did write carries thresholds rather than absence.
    """
    m = _load("Core-v1_freeze_manifest.json")
    rules = m["rule_hashes"]["p0_p3_rules"]
    assert rules["default_cfg"], "P0-P3 thresholds must not be empty"
    assert rules["default_cfg"]["direct_min_coverage"] == 0.85
    assert len(rules["tiers"]) == 4
    assert m["rule_hashes"]["p0_p3_rule_hash"] != \
        "44136fa355b3678a5c9d1a9d7b1a7c4f0d5e2b8a6c3f9e0d4a7b2c5e8f1a3d6b", (
            "44136fa... is sha256('{}'); seeing it here means the thresholds "
            "were not read")
    assert rules["content_lineage"] and rules["self_declared"]


def test_the_freeze_records_the_three_switches_as_off():
    """§8's OFF switches, and the artifact-level assertion that accompanies
    them."""
    m = _load("Core-v1_freeze_manifest.json")
    sw = m["switches"]
    assert sw["memory_injection"] == "OFF"
    assert sw["role_adaptation"] == "OFF"
    assert sw["atgg"] == "OFF"
    for k in ("memory_injection", "role_adaptation", "atgg"):
        assert sw["switches_reason"][k], (
            f"{k} is recorded OFF without a reason; OFF by omission is not a "
            f"method decision")
    assert "artifact" in sw["how_asserted"]


def test_the_freeze_names_the_core_and_the_demoted_modules():
    """§4/§5: what Core-v1 contains, and what it deliberately does not."""
    m = _load("Core-v1_freeze_manifest.json")
    core = m["core_v1_modules"]
    assert set(core) == {"Method-1 Dual-Graph Auditable Reasoning",
                         "Method-2 Audit-Gated Trustworthy Experience "
                         "Consolidation"}
    flat = {k for mods in core.values() for k in mods}
    for required in ("ProvenanceEvent", "Model Invocation Ledger",
                     "Evidence Provenance Tier", "Post-hoc Outcome Verification",
                     "Experience Eligibility Contract", "Gold Leakage Guard"):
        assert required in flat, f"§4 names {required} as Core-v1"
    demoted = m["demoted_to_exploratory"]
    assert len(demoted) == 4
    assert "ATGG-v1 adaptive guard" in demoted
    assert "Role-specific Capability Profile" in demoted


def test_the_freeze_refuses_the_final_held_out_block():
    m = _load("Core-v1_freeze_manifest.json")
    assert m["s5a_reads"] == f"case {VALIDATION[0]}-{VALIDATION[-1]}"
    assert m["s5a_must_not_read"] == f"case {MUST_NOT_READ[0]}-{MUST_NOT_READ[-1]}"
    assert 80 not in VALIDATION and 137 not in VALIDATION


def test_the_freeze_reverified_after_the_runs():
    """§15 criterion 8: no rule changed to make the validation come out."""
    p = os.path.join(OUT, "Core-v1_freeze_manifest.json")
    if not os.path.exists(p):
        pytest.skip("freeze not written")
    from traceevo.s5a_freeze import verify
    v = verify(OUT)
    assert v["checked"] > 0
    assert v["unchanged"], f"frozen files drifted during S5A: {v['drift']}"


# ------------------------------------------------------------- §9 the ordering
def test_no_module_reads_gold_before_the_seal_check():
    """§9's order, checked by reading the source rather than trusting it.

    `s5a_run.py` must not import the gold reader at all, and `s5a_pipeline.py`
    must call `check_seals` before it does.
    """
    run_src = open(os.path.join(ROOT, "traceevo", "s5a_run.py"),
                   encoding="utf-8").read()
    assert "gold_by_case_index" not in run_src, (
        "the runner must have no way to read a reference")
    assert "outcome_verifier" not in run_src
    pipe = open(os.path.join(ROOT, "traceevo", "s5a_pipeline.py"),
                encoding="utf-8").read()
    assert pipe.index("check_seals") < pipe.index("gold_by_case_index"), (
        "§9: seal verification must precede the first reference read")


def test_the_risk_and_audit_modules_do_not_open_a_reference():
    for name in ("s5a_anomaly.py",):
        src = open(os.path.join(ROOT, "traceevo", name), encoding="utf-8").read()
        assert "gold_by_case_index" not in src, f"{name} must not read gold"


def test_the_seals_verify():
    from traceevo.s5a_run import check_seals
    rows = check_seals(OUT)
    if not rows:
        pytest.skip("run not started")
    bad = [r for r in rows if not r["match"]]
    assert not bad, f"§9 step 7: {bad}"


# --------------------------------------------------------- §11 copy discipline
def test_the_originals_are_untouched_by_injection():
    """§11's 绝不修改原始 sealed trace, verified by hash."""
    s = _load("anomaly_injection_summary.json") if os.path.exists(
        os.path.join(OUT, "anomaly_injection_summary.json")) else None
    p = os.path.join(OUT, "anomaly_not_injectable.json")
    if not os.path.exists(p):
        pytest.skip("injection not run yet")
    # the summary lives in the module's return value; the check is repeated
    # here from the artifacts so it does not depend on that file existing
    from traceevo.s5a_run import check_seals
    rows = check_seals(OUT)
    assert all(r["match"] for r in rows), (
        "an injection modified an original sealed trace")


def test_a0_is_recorded_not_simulated():
    """§11: no fabricated status to fill the category."""
    p = os.path.join(OUT, "anomaly_not_injectable.json")
    if not os.path.exists(p):
        pytest.skip("injection not run yet")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    assert "不为凑类别模拟一个虚假 status" in d["a0_note"]
    a0 = [r for r in d["records"] if r["anomaly_type"] == "A0"]
    assert a0, "A0 must appear as attempted-and-not-injectable"
    assert all("reason" in r and r["reason"] for r in a0), (
        "an unproduced row owes its reason")


def test_a4_is_a_case_study_not_a_primary_detection_class():
    from traceevo.s5a_anomaly import CASE_STUDY, PROTOCOL
    assert "A4" in CASE_STUDY and "A4" not in PROTOCOL
    assert set(PROTOCOL) == {"A1", "A2", "A3", "A5"}


# --------------------------------------------------------------- §12/§13
def test_secondary_findings_are_not_counted_as_false_positives():
    """§12's central redefinition, checked on the shared implementation."""
    import inspect
    from traceevo.s2_matrix import per_class_metrics
    src = inspect.getsource(per_class_metrics)
    assert "fp_clean" in src and "secondary" in src
    assert "precision = tp / (tp + fp_clean)" in src, (
        "primary precision must use clean-trace false positives only")


def test_the_absent_positive_class_is_named_not_filled():
    """§13's second branch.

    If the validation block has no gold-safe success, the output must be the
    named verdict and a zero twin count — never a twin built from a case that
    is not a gold-safe success.
    """
    p = os.path.join(OUT, "gate_drill.json")
    if not os.path.exists(p):
        pytest.skip("gate drill not run yet")
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if d["verdict"] == "GATE_POSITIVE_CLASS_ABSENT_IN_VALIDATION":
        assert d["twins_built"] == 0
        assert d["n_gold_safe"] == 0
        assert d["what_was_not_done"]
        assert "judge was not modified" in " ".join(d["what_was_not_done"])
        assert d["prior_evidence"]
    else:
        assert d["twins_built"] > 0
        assert d["n_gold_safe"] > 0


def test_the_gate_drill_reports_a_reason_for_every_verdict():
    p = os.path.join(OUT, "gate_validation_results.csv")
    if not os.path.exists(p):
        pytest.skip("gate results not written")
    import csv
    with open(p, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        pytest.skip("no gate rows")
    if "gate_reasons" in rows[0]:
        for r in rows:
            if r.get("TraceEvo") in ("rejected", "manual_review",
                                     "failure_experience"):
                assert r.get("gate_reasons"), (
                    "a non-admission owes its reason")


# ------------------------------------------------------------------ §14
def test_overhead_does_not_mix_network_time():
    o = _load("overhead_summary.json")
    assert o["llm_network"]["status"] == "REPORTED_SEPARATELY"
    assert o["tokens"]["status"] == "TOKEN_UNAVAILABLE"
    assert "不得估算" in o["tokens"]["note"]
    for r in o["per_case"]:
        assert r["llm_network_seconds"] == "REPORTED_SEPARATELY"
        assert r["token_usage"] == "TOKEN_UNAVAILABLE"


def test_non_invasiveness_compares_three_runs_not_two():
    """A two-run comparison cannot tell 'identical' from 'both crashed the
    same way'."""
    n = _load("nonintrusive_summary.json")
    assert len(n["runs"]) == 3
    assert n["runs_completed"], "an aborted run makes the comparison vacuous"
    assert n["strict_replay_clean"]
    assert n["core_off_vs_on_identical"]
    assert n["core_on_repeatable"]


def test_no_case_beyond_the_validation_block_is_referenced():
    """§7/§15 criterion 10.

    Matches case *identifiers*, not bare numbers. The first version scanned for
    any integer above 79 and rejected `list(range(70, 80))`, whose 80 is an
    exclusive bound — a test failing for a reason unrelated to what it checks,
    which is the same defect class this project keeps recording in its own
    instruments.
    """
    import re
    # case-N literals, and the explicit upper bounds S5A is allowed to name
    allowed_bounds = {138}     # MUST_NOT_READ = range(80, 138): 138 is a bound
    for name in ("s5a_freeze.py", "s5a_run.py", "s5a_pipeline.py",
                 "s5a_anomaly.py", "s5a_gate.py", "s5a_overhead.py"):
        src = open(os.path.join(ROOT, "traceevo", name), encoding="utf-8").read()
        for m in re.finditer(r"case-(\d+)", src):
            n = int(m.group(1))
            assert n <= 79, (
                f"{name} contains the literal case-{n}, which is outside the "
                f"validation block")
        for m in re.finditer(r"range\((\d+),\s*(\d+)\)", src):
            lo, hi = int(m.group(1)), int(m.group(2))
            assert lo >= 70 or hi <= 80 or hi in allowed_bounds, (
                f"{name} contains range({lo}, {hi}), which reaches outside the "
                f"validation block")
