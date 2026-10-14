"""S3A §3: the Model Invocation Ledger records calls that leave no event.

The load-bearing test is `test_discarded_call_is_the_a4_shape`: it pins the
exact record S2.1 could not produce -- a model response that really arrived,
was really discarded, and has to still be visible after the fact.
"""
from __future__ import annotations

import json
import os

import pytest

from traceevo.audit.invocation_ledger import (InvocationLedger, load_ledger)


@pytest.fixture()
def ledger(tmp_path):
    return InvocationLedger("case-1", str(tmp_path))


def _rows(ledger):
    return load_ledger(ledger.path)


# --------------------------------------------------------------- §3.2 shape
def test_entry_exists_before_the_parser_runs(ledger):
    call_id = ledger.open_call("EXPERT_CONTENT", actor_id="Lab",
                               actor_role="expert", step_id=1,
                               prompt_hash="p", response_hash="r")
    on_disk = _rows(ledger)
    assert len(on_disk) == 1
    row = on_disk[0]
    assert row["call_id"] == call_id
    # Written before any parse outcome is known -- that is the whole point of
    # the channel: a call that dies in the parser is still on the record.
    assert row["parse_status"] == "pending"
    assert row["committed_to_state"] is False


def test_discarded_call_is_the_a4_shape(ledger):
    """The substituted decision response GoS parsed and then threw away."""
    c = ledger.open_call("EXPERT_CONTENT", prompt_hash="p", response_hash="r")
    ledger.mark_parsed(c, "discarded", "DECISION_CONTENT_MISMATCH")
    row = _rows(ledger)[0]
    assert row["parse_status"] == "discarded"
    assert row["discard_reason"] == "DECISION_CONTENT_MISMATCH"
    assert row["committed_to_state"] is False
    assert row["committed_event_ids"] == []


def test_accepted_call_that_produced_no_event_is_distinguishable(ledger):
    """An empty retrieval query: parsed fine, and GoS discarded it silently."""
    a = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(a, "accepted", "NONE", "")
    unused = ledger.open_call("EXPERT_DECISION")
    ledger.mark_parsed(unused, "accepted", "NONE")
    rows = {r["call_id"]: r for r in _rows(ledger)}
    assert rows[a]["parse_status"] == "accepted"
    assert rows[a]["committed_to_state"] is False
    assert rows[unused]["parse_status"] == "accepted"


# ------------------------------------------------------------------ binding
def test_commit_binds_by_the_committed_text(ledger):
    c = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(c, "accepted", "NONE", "Need the biopsy report")
    assert ledger.commit("evt-1", "Need the biopsy report",
                         "EXPERT_CONTENT") == c
    row = _rows(ledger)[0]
    assert row["committed_to_state"] is True
    assert row["committed_event_ids"] == ["evt-1"]
    assert row["binding"] == "text"


def test_commit_refuses_to_guess_when_no_call_carries_the_text(ledger):
    c = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(c, "accepted", "NONE", "something the model said")
    # Upstream's degraded-analysis fallback: no model ever emitted this.
    assert ledger.commit("evt-1", "Insufficient information to complete "
                                  "the analysis", "EXPERT_CONTENT") is None
    assert _rows(ledger)[0]["committed_to_state"] is False
    assert ledger.summary()["n_unbound_commits"] == 1


def test_commit_falls_back_to_positional_only_without_text(ledger):
    c = ledger.open_call("EVIDENCE_RETRIEVAL")
    ledger.mark_parsed(c, "accepted", "NONE", "")
    assert ledger.commit("evt-9", "", "EVIDENCE_RETRIEVAL") == c
    assert _rows(ledger)[0]["binding"] == "positional"


def test_a_committed_call_is_not_reused_for_a_second_event(ledger):
    a = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(a, "accepted", "NONE", "first")
    b = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(b, "accepted", "NONE", "second")
    assert ledger.commit("evt-1", "first", "EXPERT_CONTENT") == a
    assert ledger.commit("evt-2", "second", "EXPERT_CONTENT") == b


def test_state_commit_is_not_an_event_commit(ledger):
    """The belief text becomes GoS state without becoming any event."""
    c = ledger.open_call("OTHER")
    ledger.mark_parsed(c, "accepted", "NONE", "the belief")
    assert ledger.commit_state("GRAPH_BELIEF") == c
    row = _rows(ledger)[0]
    assert row["committed_to_state"] is True
    assert row["commit_target"] == "GRAPH_BELIEF"
    assert row["committed_event_ids"] == []


# ------------------------------------------------------------------ liveness
def test_an_open_call_is_closed_as_failed_not_left_pending(ledger):
    c = ledger.open_call("EXPERT_CONTENT")
    assert ledger.close_open_calls("failed", "OTHER") == 1
    row = _rows(ledger)[0]
    assert row["call_id"] == c
    assert row["parse_status"] == "failed"
    assert row["discard_reason"] == "OTHER"


def test_rows_do_not_share_their_event_list(ledger):
    """Two rows must not alias one list through the row template.

    Committing to one call appended its event ids to every row in the ledger,
    which then made `committed_to_state` look true for calls that produced
    nothing.
    """
    a = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(a, "accepted", "NONE", "alpha")
    b = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(b, "accepted", "NONE", "beta")
    ledger.commit("evt-a", "alpha", "EXPERT_CONTENT")
    rows = {r["call_id"]: r for r in _rows(ledger)}
    assert rows[a]["committed_event_ids"] == ["evt-a"]
    assert rows[b]["committed_event_ids"] == []


def test_ledger_survives_a_process_that_dies_mid_parse(ledger):
    """The A3 shape: the run aborts and `finish_task` never runs.

    Everything written so far has to be readable from the file alone, with the
    in-flight call still `pending` -- which is the truthful record of how far
    the run got, and the opposite of losing it.
    """
    a = ledger.open_call("CENTRAL_PLAN")
    ledger.mark_parsed(a, "accepted", "NONE", "plan")
    ledger.commit("evt-1", "plan", "CENTRAL_PLAN")
    ledger.open_call("EXPERT_DECISION")          # in flight when it died
    reloaded = load_ledger(ledger.path)
    assert [r["parse_status"] for r in reloaded] == ["accepted", "pending"]
    assert reloaded[0]["committed_event_ids"] == ["evt-1"]


def test_rejection_applies_to_a_call_the_parser_already_accepted(ledger):
    """The A4 seam: parsed fine, then discarded by the caller's next stage.

    `pending_call_id` cannot see this call -- nothing is pending by then -- so
    the rejection resolved to nothing and the discard vanished. Both A4
    rechecks reported zero discards for runs whose own logs say the step was
    thrown away.
    """
    c = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(c, "accepted", "NONE", "content")
    assert ledger.pending_call_id() == ""
    assert ledger.rejectable_call_id() == c
    ledger.mark_parsed(ledger.rejectable_call_id(), "discarded",
                       "DECISION_CONTENT_MISMATCH")
    row = _rows(ledger)[0]
    assert row["parse_status"] == "discarded"
    assert row["discard_reason"] == "DECISION_CONTENT_MISMATCH"
    assert row["committed_to_state"] is False


def test_pending_call_id_resolves_the_most_recent(ledger):
    a = ledger.open_call("EXPERT_DECISION")
    b = ledger.open_call("EXPERT_CONTENT")
    assert ledger.pending_call_id() == b
    ledger.mark_parsed(b, "discarded", "PARSER_ERROR")
    assert ledger.pending_call_id() == a


# ------------------------------------------------------------------ summary
def test_summary_reports_the_rates_q4_and_q5_ask_for(ledger):
    a = ledger.open_call("EXPERT_DECISION")
    ledger.mark_parsed(a, "accepted", "NONE")
    b = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(b, "accepted", "NONE", "text")
    ledger.commit("evt-1", "text", "EXPERT_CONTENT")
    c = ledger.open_call("EXPERT_CONTENT")
    ledger.mark_parsed(c, "discarded", "DECISION_CONTENT_MISMATCH")
    s = ledger.summary()
    assert s["n_calls"] == 3
    assert s["by_status"] == {"accepted": 2, "discarded": 1}
    assert s["decision_content_discards"] == 1
    assert s["uncommitted_call_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_ledger_file_is_jsonl_and_stable(ledger):
    for i in range(3):
        c = ledger.open_call("EXPERT_CONTENT", step_id=i)
        ledger.mark_parsed(c, "accepted", "NONE", f"t{i}")
    with open(ledger.path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    assert len(lines) == 3
    for ln in lines:
        assert json.loads(ln)["task_id"] == "case-1"
