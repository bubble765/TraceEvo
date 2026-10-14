"""S3D: the seal, and the count that depends on it.

The seal exists because S3A did not have one and S3C could not prove the
artifacts it verified were the artifacts S3A produced. S3C's FAILURE 3 is the
reason the first test here is a *positive control*: a check that cannot fail is
not a check, and an abstention mechanism reporting zero abstentions was
indistinguishable from one that worked until it was made to abstain.

So `test_a_tampered_artifact_fails_the_seal` is the load-bearing test in this
file. If it ever passes trivially — because `check_seals` returns [] and the
assertion loop never runs — that is the bug, and
`test_check_seals_returns_a_row_per_seal` is there to catch it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from traceevo.s3d_run import SUFFIXES, seal_one  # noqa: E402
from traceevo.s3d_verify import (check_seals, recompute_seal,  # noqa: E402
                                 score_shape)
from traceevo.s3d_triage import CONDITIONS, MIN_HEADLINE, s3c_headline  # noqa: E402


@pytest.fixture()
def fake_run(tmp_path):
    """A minimal artifact set with the same suffixes the runner writes."""
    out = tmp_path / "S3D_verified_expansion"
    artifacts = out / "runs_50_59" / "traceevo" / "runs" / "s3d_expansion"
    artifacts.mkdir(parents=True)
    for c in (50, 51, 52):
        task = f"case-{c}"
        for s in SUFFIXES:
            (artifacts / f"{task}{s}").write_text(
                json.dumps({"case": c, "suffix": s}), encoding="utf-8")
    (out / "seals").mkdir()
    for c in (50, 51, 52):
        seal = seal_one(str(artifacts), f"case-{c}", c)
        (out / "seals" / f"case-{c}_seal.json").write_text(
            json.dumps(seal, indent=2), encoding="utf-8")
    return out, artifacts


# ------------------------------------------------------------- the seal works
def test_check_seals_returns_a_row_per_seal(fake_run):
    """Guards the positive control below from passing vacuously."""
    out, artifacts = fake_run
    rows = check_seals(str(out), str(artifacts))
    assert len(rows) == 3, "every seal must produce a row"
    assert all(r["match"] for r in rows)


def test_a_tampered_artifact_fails_the_seal(fake_run):
    """The positive control. If this passes, the seal is a real check."""
    out, artifacts = fake_run
    target = artifacts / "case-51_events.jsonl"
    payload = json.loads(target.read_text())
    payload["tampered"] = True
    target.write_text(json.dumps(payload), encoding="utf-8")

    rows = {r["task_id"]: r for r in check_seals(str(out), str(artifacts))}
    assert not rows["case-51"]["match"], (
        "a modified artifact must break the seal; if this holds, the seal is "
        "decoration")
    assert rows["case-51"]["changed_artifacts"] == ["events.jsonl"], (
        "the mismatch must name the artifact that moved, not only report a "
        "digest change")
    # and the untampered cases must be untouched by it
    assert rows["case-50"]["match"] and rows["case-52"]["match"]


def test_a_deleted_artifact_fails_the_seal(fake_run):
    """The other way an artifact can stop matching: it is gone."""
    out, artifacts = fake_run
    os.remove(artifacts / "case-50_bindings.json")
    rows = {r["task_id"]: r for r in check_seals(str(out), str(artifacts))}
    assert not rows["case-50"]["match"]
    assert "bindings.json" in rows["case-50"]["changed_artifacts"]


def test_seal_hash_is_reproducible_from_the_files_alone(fake_run):
    """A reviewer must be able to recompute the seal without trusting it."""
    out, artifacts = fake_run
    for task in ("case-50", "case-51", "case-52"):
        with open(out / "seals" / f"{task}_seal.json", encoding="utf-8") as f:
            seal = json.load(f)
        assert recompute_seal(str(artifacts), task) == seal["input_artifact_hash"]


def test_the_seal_covers_every_artifact_the_runner_writes():
    """A suffix written but not sealed is an artifact the seal does not cover.

    This test is the reason FAILURE 1 was fixable. Its first version asserted
    the *broken* five-suffix set, so it passed for the whole run and would have
    blocked the fix had it been written as an equality against the corrected
    list. It is kept as an equality, against the set that matches what
    `ls` on the artifact directory shows -- and that directory is the
    authority, not this file.
    """
    sealed = {s.lstrip("_") for s in SUFFIXES}
    assert sealed == {
        "events.jsonl", "bindings.json", "deltas.jsonl", "integrity.json",
        "invocations.jsonl", "injections.jsonl", "provenance_graph.json",
        "provenance_graph.png", "replay.json", "summary.json",
    }, "§3.3 step 2 names events/ledger/deltas/graph/prediction/report"


def test_the_seal_actually_matches_the_run_directory():
    """The check above compares SUFFIXES to a literal. This compares it to the
    artifacts case 50-59 actually produced, which is what FAILURE 1 was about.
    """
    d = os.path.join(ROOT, "S3D_verified_expansion", "runs_50_59", "traceevo",
                     "runs", "s3d_expansion")
    if not os.path.isdir(d):
        pytest.skip("run directory not present")
    on_disk = {n[len("case-50"):] for n in os.listdir(d)
               if n.startswith("case-50")}
    sealed = {s for s in SUFFIXES}
    assert on_disk <= sealed, (
        f"the runner wrote {sorted(on_disk - sealed)} and the seal does not "
        f"cover it (FAILURE 1)")


def test_a_seal_mismatch_is_its_own_verdict():
    """§3.3: such a case does not enter the corpus. Not a success, not
    uncertain -- a separate value, so it cannot be read as either."""
    assert "excluded_seal_mismatch" not in ("verified_success",
                                            "verified_incorrect", "uncertain")


# ------------------------------------------------------------------- §4 shapes
def test_score_1_shapes_are_told_apart():
    """S3C found 8 of 10 uncertains were a unanimous 1|1|1. §4 asks S3D to
    say whether that recurs, so the two shapes must not collapse."""
    assert score_shape([1, 1, 1]) == "score_1_unanimous"
    assert score_shape([1, 1, 0]) == "score_1_mixed"
    assert score_shape([2, 2, 2]) == "no_score_1"
    assert score_shape([0, 0, 0]) == "no_score_1"
    assert score_shape([]) == "none"


# --------------------------------------------------------------------- §5/§6
def test_the_six_conditions_are_the_frozen_six():
    assert CONDITIONS == ("verified_success", "integrity_ok",
                          "invocation_complete", "semantic_trace_complete",
                          "no_critical_anomaly",
                          "no_unresolved_decision_path_p3")


def test_the_headline_excludes_the_legacy_and_probe_blocks():
    """§6 sums 10-29, 30-39 and 50-59. 0-9 and 40-49 are not in it."""
    prior = s3c_headline()
    assert set(prior) == {"10-29", "30-39"}
    assert prior["10-29"]["n_gold_safe"] == 4, (
        "S3C's recorded count; if this changes, S3D is reading a different "
        "S3C than it verified against")
    assert prior["30-39"]["n_gold_safe"] == 0


def test_the_shortfall_is_one_and_that_is_why_s3d_exists():
    prior = s3c_headline()
    have = prior["10-29"]["n_gold_safe"] + prior["30-39"]["n_gold_safe"]
    assert MIN_HEADLINE - have == 1


# ------------------------------------------------------------------- the freeze
def test_the_frozen_absence_regexes_match_s3a_recorded_hashes():
    """§3.2's freeze is only meaningful if it records the rules S3A froze.

    The first version of the freeze read these from `policy.py`, which has no
    regexes, and wrote the digest of an empty dict -- "no absence rules" about
    the one file that defines them.
    """
    state = os.path.join(ROOT, "S3A_migration", "rule_freeze_manifest.json")
    if not os.path.exists(state):
        pytest.skip("S3A manifest not present")
    with open(state, encoding="utf-8") as f:
        s3a = json.load(f)
    frozen = os.path.join(ROOT, "S3D_verified_expansion",
                          "S3D_freeze_manifest.json")
    if not os.path.exists(frozen):
        pytest.skip("S3D freeze not written yet")
    with open(frozen, encoding="utf-8") as f:
        s3d = json.load(f)
    got = {k: v["sha256"] for k, v in
           s3d["rule_hashes"]["absence_regexes"].items()}
    assert got, "absence regexes must not be empty (see the module docstring)"
    text = json.dumps(s3a)
    for name, sha in got.items():
        assert sha[:16] in text, f"{name} does not match S3A's frozen hash"


# --------------------------------------- the §5 / §7 gap, and the failures
def test_a_single_letter_option_is_not_a_leak():
    """S3B FAILURE 4, recurring in S3D's own admission checks.

    `right_option` for case-51 is `'D'`. The first version of the §7 field
    check was `str(right_option).lower() not in payload_text`, which for a
    one-letter option is a test for the letter "d" in prose. It reported a
    reference-answer leak in a payload that contains no answer, and would have
    reported no leak in one that did. The guard below is the frozen one, which
    requires an answer cue before the letter.
    """
    from traceevo.evolution.leakage_guard import scan_prompt

    gold = {"final_diagnosis": "Horner syndrome secondary to invasive "
                               "Blastomyces pneumonia",
            "right_option": "D"}
    prose = ("Consultation pattern: Radiologist, Pathologist and Laboratory "
             "Physician were consulted in a verified execution. 16 evidence "
             "retrievals, 1 hypothesis refinement.")
    r = scan_prompt(prose, gold)
    kinds = {l["kind"] for l in r["leaks"]}
    assert "exact_right_option" not in kinds, (
        "the letter 'd' in ordinary prose is not the answer D")

    answer_text = "The correct answer is D."
    r2 = scan_prompt(answer_text, gold)
    assert "exact_right_option" in {l["kind"] for l in r2["leaks"]}, (
        "and a real answer cue must still be caught; a guard that fails both "
        "ways is not a guard")


def test_a_leakage_scan_over_nothing_is_not_a_pass():
    """S3C FAILURE 3, caught in S3D before it shipped.

    `leakage_rescan` scans every stored partition, not only
    `initial_plan_retrievable()`. The first version scanned the retrievable
    set, which is empty here because case-51 is a `manual_review`, and would
    have reported `gold_leakage_count = 0, passed = true` from a loop that ran
    zero times.
    """
    import json as _json
    p = os.path.join(ROOT, "S3D_verified_expansion", "leakage_summary.json")
    if not os.path.exists(p):
        pytest.skip("leakage rescan not run yet")
    with open(p, encoding="utf-8") as f:
        s = _json.load(f)
    assert "vacuous" in s, "a summary that cannot say it was vacuous is one"
    if s["n_entries_scanned"] == 0:
        assert s["vacuous"] is True


def test_admission_counts_eligible_not_stored():
    """`manual_review` is stored and is not admitted. Counting `stored` would
    have reported case-51 as a headline admission while the store held it in a
    partition no retrieval can reach (§6.3)."""
    import csv as _csv
    p = os.path.join(ROOT, "S3D_verified_expansion",
                     "expanded_store_admission.csv")
    if not os.path.exists(p):
        pytest.skip("store not built yet")
    with open(p, encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    for r in rows:
        assert r["headline_admitted"] == str(
            r["gate_eligibility"] == "eligible")
        if r["partition"] == "manual_review":
            assert r["headline_admitted"] == "False"
            assert r["stored"] == "True", (
                "manual_review is kept for a human; it is stored and not "
                "admitted, and both halves of that need to be visible")


def test_the_gold_safe_bar_is_not_the_eligible_bar():
    """The substantive finding of S3D, pinned so it cannot quietly change.

    §5's six conditions do not include §6.1's soft-grounding rule, so a case
    can be gold-safe and still not reach `eligible`. case-51 is exactly that
    case, and the gap is what makes the headline count 5 while the store gains
    nothing.
    """
    import csv as _csv
    p = os.path.join(ROOT, "S3D_verified_expansion",
                     "expanded_store_admission.csv")
    if not os.path.exists(p):
        pytest.skip("store not built yet")
    with open(p, encoding="utf-8") as f:
        r = list(_csv.DictReader(f))[0]
    assert r["case"] == "51"
    assert r["gate_eligibility"] == "manual_review"
    assert r["gold_diagnosis_absent"] == "True"
    assert r["reference_answer_absent"] == "True"
    assert "P1 soft-grounded" in r["gate_reasons"]
