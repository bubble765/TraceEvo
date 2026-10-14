"""S5B's tests.

Three kinds, and the first kind is the one that matters:

1. **Guards** — checks that fail loudly if an invariant this stage depends on
   slips: the freeze holds real thresholds, no module reads a development
   case, the injection specification matches the frozen one, the pipeline
   cannot open a reference before verifying seals.

2. **Recurrence guards** — three failures have now happened twice in this
   project (S3D's empty-digest freeze, S5A's version of the same, and a
   comparison across things that are not comparable). Where a test here looks
   like it is re-testing something already covered, it is there because the
   earlier fix did not generalise.

3. **Arithmetic** — Wilson intervals, the case-cluster bootstrap's sampling
   unit, and the measured-zero rule. These are the quantities a reviewer
   cannot check by reading a table.

Run:
    python3 -m pytest S5B_final_heldout/tests/test_s5b.py -q
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "S5B_final_heldout")
sys.path.insert(0, ROOT)

FREEZE = os.path.join(OUT, "Final_Core_v1_freeze_manifest.json")
S5B_MODULES = ["traceevo/s5b_freeze.py", "traceevo/s5b_run.py",
               "traceevo/s5b_pipeline.py", "traceevo/s5b_diagnostic.py",
               "traceevo/s5b_anomaly.py", "traceevo/s5b_gate.py",
               "traceevo/s5b_ablation.py", "traceevo/s5b_overhead.py",
               "traceevo/s5b_tables.py", "traceevo/s5b_figures.py",
               "traceevo/s5b_finalize.py"]


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _freeze():
    with open(FREEZE, encoding="utf-8") as f:
        return json.load(f)


def _code_only(src: str) -> str:
    """Strip comments and docstrings, keeping code and inline strings.

    The case-range guard scans for `case-N` literals, and a guard that cannot
    tell a comment from code fails on correct code — S5A FAILURE 4, whose
    boundary check rejected the line declaring the boundary. Prose that names
    a development case (a comment explaining why a default was removed, a
    docstring citing S3C) is not a read.
    """
    import io
    import tokenize
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE):
                continue
            if tok.type == tokenize.STRING:
                # Docstrings are prose. Strings shorter than a line are kept,
                # because a lookup key is a string too.
                if "\n" in tok.string or tok.string.startswith(('"""', "'''")):
                    continue
            out.append(tok.string)
    except tokenize.TokenError:                           # pragma: no cover
        return src
    return "\n".join(out)


# ------------------------------------------------------------------- guards
def test_freeze_records_the_real_p0_p3_thresholds():
    """The S3D/S5A failure, guarded by name.

    `44136fa355b3678a...` is sha256("{}"). Both earlier freezes wrote it by
    looking for thresholds in a module that stores them lower-case in a dict,
    and both times the manifest looked complete.
    """
    man = _freeze()
    rules = man["rule_hashes"]["p0_p3_rules"]
    assert rules["default_cfg"], (
        "P0-P3 thresholds are empty: the rule hash is the digest of nothing "
        "(44136fa355b3678a is sha256('{}'))")
    h = man["rule_hashes"]["p0_p3_rule_hash"]
    # 44136fa355b3678a... is the first 16 hex digits of sha256("{}").
    assert not h.startswith("44136fa355b3678a"), (
        "the p0_p3_rule_hash is the sha256 of an empty dict")
    for k in ("direct_min_coverage", "soft_min_coverage",
              "soft_min_char_similarity", "expert_synthesis_min_coverage",
              "input_inference_min_coverage", "min_content_tokens"):
        assert k in rules["default_cfg"], f"{k} missing from the frozen rules"


def test_the_freeze_pins_every_driver_script():
    """§4's evaluation and table hashes must cover scripts that exist."""
    man = _freeze()
    assert man["driver_script_hashes"]
    missing = [p for p, h in man["driver_script_hashes"].items() if not h]
    assert not missing, f"frozen scripts absent at freeze time: {missing}"
    for key in ("controlled_injection_spec_hash", "evaluation_script_hash",
                "table_generator_hash"):
        assert man[key], f"{key} is empty"


def test_the_injection_spec_matches_the_frozen_one():
    import hashlib
    man = _freeze()
    got = hashlib.sha256(json.dumps(man["controlled_injection_spec"],
                                    sort_keys=True).encode()).hexdigest()
    assert got == man["controlled_injection_spec_hash"]


def test_the_switches_are_all_off_and_reasoned():
    man = _freeze()
    sw = man["switches"]
    for k in ("memory_injection", "failure_memory_activation",
              "role_adaptation", "atgg"):
        assert sw[k] == "OFF", f"{k} is not OFF"
    assert sw["how_asserted"], "the switches carry no assertion method"


def test_the_backbone_is_the_frozen_one():
    assert _freeze()["run_config"]["backbone"] == "deepseek-v4-flash"


ARTIFACT_SUFFIXES = ("_events.jsonl", "_bindings.json", "_deltas.jsonl",
                     "_integrity.json", "_invocations.jsonl",
                     "_replay.json", "_summary.json", "_injections.jsonl",
                     "_provenance_graph.json")


def _case_reads(src: str):
    """Numbers of development cases this source can *open*.

    Two instruments were tried before this one and both failed on correct
    code, which is the failure mode S5A recorded: a check whose rule does not
    match its purpose fails for a reason unrelated to what it protects, and
    its failure is indistinguishable from a real violation.

    1. Every `case-N` literal anywhere. Failed on a comment explaining a fix.
    2. The same, with comments and docstrings stripped. Failed on an f-string
       that writes a sentence into `final_limitations.md` citing case-51 —
       prose, and permitted by §3 (historical data may be cited for the
       frozen rules and the development-vs-heldout contrast).

    What actually reads a case is a path. So this flags a `case-<number>`
    literal only when an artifact suffix follows it, plus any `range(a, b)`
    that reaches below 80 — a loop bound is the other way a module could walk
    into the development blocks.
    """
    bad = []
    for m in re.finditer(r"case-(\d+)(_\w+\.(?:json|jsonl|png))?", src):
        n = int(m.group(1))
        if n >= 80:
            continue
        if m.group(2) in ARTIFACT_SUFFIXES:
            bad.append(f"case-{n}{m.group(2)}")
    for m in re.finditer(r"range\((\d+),\s*(\d+)\)", src):
        a, b = int(m.group(1)), int(m.group(2))
        if a < 80 <= b or (a < 80 and b <= 80):
            bad.append(f"range({a}, {b})")
    return bad


def test_no_s5b_module_can_open_a_case_outside_the_heldout_block():
    """§3, checked against the operation that would violate it."""
    for rel in S5B_MODULES:
        bad = _case_reads(_code_only(_src(rel)))
        assert not bad, (
            f"{rel} can reach a case outside 80-137 via {bad}")
        for m in re.finditer(r"range\((\d+),\s*(\d+)\)", _code_only(_src(rel))):
            a, b = int(m.group(1)), int(m.group(2))
            if (a, b) in {(80, 138), (0, 80), (0, 138)}:
                continue
            assert b <= 138, f"{rel} ranges over case {a}-{b - 1}, past 137"


def test_the_block_constant_is_the_heldout_block_everywhere():
    """One definition of the block, so no module can drift to another."""
    for rel in S5B_MODULES:
        src = _code_only(_src(rel))
        for m in re.finditer(r"CASES\s*=\s*list\(range\((\d+),\s*(\d+)\)\)",
                             src):
            assert (int(m.group(1)), int(m.group(2))) == (80, 138), (
                f"{rel} defines CASES as range{m.groups()}, not 80-137")


def test_the_runner_refuses_to_open_a_development_case():
    for spec in ("0-9", "79", "138"):
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "traceevo", "s5b_run.py"),
             "--out", OUT, "--cases", spec, "--run"],
            capture_output=True, text=True)
        assert p.returncode != 0, f"the runner accepted --cases {spec}"
        assert "80-137" in (p.stdout + p.stderr)


def test_the_pipeline_verifies_seals_before_opening_any_reference():
    """§5.1 steps 6-8, checked on the call order inside `run`.

    The first version indexed the whole file, and failed on correct code
    because `outcome_and_gate` (which imports `gold_by_case_index` at the top
    of its body) is *defined* above `run` while being *called* from inside it.
    Definition order is not execution order, so the instrument did not match
    the property. This checks the sequence the interpreter actually follows.
    """
    src = _src("traceevo/s5b_pipeline.py")
    # `main()` calls `run` by name, so the first "run(" in the file is `def`.
    body = src[src.index("def run("):]
    seals_call = body.index("check_seals(out_dir)")
    gold_call = body.index("outcome_and_gate(")
    assert seals_call < gold_call, (
        "the reference is reachable before seal verification")


def test_the_reference_is_imported_inside_the_function_that_opens_it():
    """A module-level gold import would make the order above unenforceable."""
    src = _src("traceevo/s5b_pipeline.py")
    head = src[:src.index("def audit_case(")]
    assert "gold_by_case_index" not in head, (
        "the reference is imported at module scope, so any function in this "
        "module could open it without passing the seal gate")


def test_the_run_never_names_a_gold_column_on_its_way_in():
    src = _src("traceevo/s5b_run.py")
    assert "Final Diagnosis" not in src
    assert "Right Option" not in src
    assert 'blinded' in src and "verify_blinded" in src


def test_attempt_history_is_flushed_inside_the_case_loop():
    """§5.2: a crash at case 40 must not lose cases 1-39's record."""
    src = _src("traceevo/s5b_run.py")
    loop = src.index("for c in cases:")
    flush = src.index("_attempt_log(out_dir, records)")
    assert flush > loop, "the attempt log is flushed outside the case loop"
    assert src.index("continue", flush) > flush


def test_case_study_rules_are_preregistered():
    man = _freeze()
    rules = man["case_study_rules_preregistered"]
    for k in ("dual_graph_normal_example", "natural_p3_example",
              "controlled_anomaly_path", "experience_gate_case"):
        assert k in rules and rules[k]["rule"], f"{k} has no pre-registered rule"
    assert "case 80" in rules["dual_graph_normal_example"]["rule"]


# --------------------------------------------------------------- arithmetic
def test_wilson_of_zero_is_not_zero():
    from traceevo.s5b_gate import wilson
    lo, hi = wilson(0, 10)
    assert lo == 0.0
    assert 0.27 < hi < 0.29, "the Wilson upper bound at 0/10 is ~0.278"


def test_wilson_of_all_is_not_one():
    from traceevo.s5b_gate import wilson
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and 0.71 < lo < 0.73


def test_wilson_on_an_empty_denominator_is_not_a_number():
    from traceevo.s5b_gate import wilson
    assert wilson(0, 0) == [None, None]


def test_no_rate_is_reported_without_a_denominator():
    """§11. A 0.0 with no denominator is indistinguishable from unmeasured."""
    from traceevo.s5b_gate import _ratio
    r = _ratio(0, 0, "nothing to measure")
    assert r["rate"] is None and r["ci95"] == [None, None]
    assert r["numerator"] == 0 and r["denominator"] == 0


def test_the_cluster_bootstrap_resamples_cases_not_mutations():
    """§8.2. Duplicating a case's mutations must not narrow the interval.

    Two datasets with the same case-level structure and different numbers of
    mutations per case. Under case-clustering the two intervals coincide,
    because the resample unit is the case; under event-level bootstrapping the
    denser one would be narrower, which is the error §8.2 names.
    """
    from traceevo.s5b_anomaly import cluster_bootstrap

    def rows(mutations_per_case):
        out = []
        for case in (80, 81):
            for i in range(mutations_per_case):
                out.append({"case": case, "injected_type": "A1",
                            "detected": case == 80,
                            "detected_types": ["A1"] if case == 80 else [],
                            "n_anomalies": 1 if case == 80 else 0})
        return out

    a = cluster_bootstrap(rows(1), ["A1"], n=400)
    b = cluster_bootstrap(rows(6), ["A1"], n=400)
    assert a["n_cases"] == b["n_cases"] == 2
    assert a["macro_f1"]["ci95"] == b["macro_f1"]["ci95"], (
        "the interval moved when only the mutations-per-case changed, so the "
        "resample unit is not the case")


def test_a_measured_zero_is_not_reported_as_missing():
    """A class that was injected and never detected has F1 0.0, not None.

    `per_class_metrics` returns None because precision is 0/0. Left as None it
    renders as an em dash and reads as "not measured" -- the more flattering
    and the wrong claim.
    """
    from traceevo.s5b_anomaly import _normalise
    m = _normalise({"type": "A1", "n_injected": 10, "tp": 0, "fn": 10,
                    "f1": None, "precision": None, "recall": 0.0})
    assert m["f1"] == 0.0
    assert m["f1_zero_by_no_detection"] is True
    untouched = _normalise({"type": "A2", "n_injected": 0, "f1": None})
    assert untouched["f1"] is None, "a class with no injections is not a zero"


def test_the_macro_average_covers_every_injected_class():
    """Four injected classes must average over four, including a zero."""
    from traceevo.s5b_anomaly import _mean
    assert _mean([1.0, 0.0, 1.0, 0.0]) == 0.5


def test_not_scored_is_a_value_not_a_zero():
    """§12/§13: the ledger arm is not averaged over capabilities it never had."""
    from traceevo.s5b_ablation import NOT_SCORED
    assert NOT_SCORED == "NOT_SCORED"
    assert NOT_SCORED != 0 and NOT_SCORED != "0.0"


def test_the_token_parser_reads_the_transports_own_lines(tmp_path):
    from traceevo.s5b_overhead import TOKEN_RE, ATTEMPT_RE
    line = ("[LLM-API] Generation Completed | Model：deepseek-v4-flash | "
            "Input token nums：1329 | Output token nums：901 | "
            "price(CNY)：0.00000000")
    m = TOKEN_RE.search(line)
    assert m and m.group(1) == "1329" and m.group(2) == "901"
    assert ATTEMPT_RE.search("=== case-84 attempt 1/3 2026-09-20T05:00:00Z ===")


def test_every_required_table_has_a_generator():
    from traceevo.s5b_tables import TABLES
    assert len(TABLES) >= 5, "§17 asks for at least five main tables"
    for name in TABLES:
        assert any(k in _src("traceevo/s5b_tables.py") for k in (name,)), (
            f"{name} has no generator")


def test_figure4_caption_distinguishes_the_sources():
    """§18: a development half must be labelled as development.

    Checked on the caption the figure carries, not on the file text — the
    clause is split across source lines, so a grep for the phrase fails on
    correct code and would pass on a caption that had been quietly reworded.
    """
    from traceevo.s5b_figures import figure4_caption

    absent = figure4_caption({
        "branch": "HELDOUT_POSITIVE_CLASS_ABSENT", "n_verified_success": 0,
        "incorrect_clean": {"n_incorrect_clean": 12}})
    assert "development controlled experiment" in absent
    assert "NOT plotted" in absent
    assert "held-out case 80-137 only" in absent
    assert "12 incorrect-clean candidates" in absent

    present = figure4_caption({
        "branch": "HELDOUT_POSITIVE_CLASS_ADEQUATE", "n_gold_safe": 7,
        "twins_built": 28, "incorrect_clean": {"n_incorrect_clean": 12}})
    assert "development data appears" in present and "no development" in present

    # §11's rule applies to captions too: a count with no denominator is not
    # a measurement, so the caption must carry both halves.
    assert "n = 12 incorrect-clean candidates" in absent


def test_the_finalizer_separates_protocol_drift_from_reporting_drift():
    """§20 c2/c7 must not fail on a reporting change (S5A FAILURE 3's shape)."""
    src = _src("traceevo/s5b_finalize.py")
    assert "rule_file_drift" in src
    assert 'freeze.get("unchanged")' not in src, (
        "a protocol condition is still keyed on the combined hash")


def test_heldout_original_and_mutations_are_separate_directories():
    """§7: injections go on copies; the sealed originals are never written."""
    src = _src("traceevo/s5b_anomaly.py")
    assert "controlled_mutations" in src
    assert "heldout_original" in src
    assert "originals_unchanged" in src
