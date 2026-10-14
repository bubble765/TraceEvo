"""Model Invocation Ledger — the second observability channel (S3A Goal A).

S2's audit answers questions about *semantic events*: things that entered
GoS's explainable state. That boundary has a hole, and S2.1 measured it. The
A4 response-substitution studies injected a fabricated analysis at the expert's
decision stage; GoS parsed only `{type, decision}` out of it, the content stage
disagreed, the step was discarded as a parse error, and **no event was emitted
for it**. The audit could not localize the injection because there was nothing
in the event log to localize — the anomaly happened and left no trace.

This module records the other half:

    Provenance Event     -- what entered GoS's explainable state
    Invocation Ledger    -- what the model was actually asked and answered,
                            whether or not any of it was kept

The two are separate on purpose. A call that was discarded is still a real call
that really happened, and S3B's Experience Gate must not treat its response as
experience; conversely a call that was accepted but produced no event is not
evidence that nothing happened.

Design constraints, all from S3A §3.2:

* the entry is created **before** the parser runs, so a discarded call exists
  in the ledger even though it produces no event;
* the response is stored as a hash plus a pointer into the raw recording, not
  as text, so the ledger stays small;
* the ledger **only observes**. It is not consulted by any GoS decision.

Non-invasiveness (§8) is not asserted here, it is tested: with the ledger off
and on, the same recording must replay to identical prompt hashes, responses,
predictions, reports and provenance events.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .event_schema import canonical_json, sha256_hex, utc_now

# §3.2's stage enum. CENTRAL_PLAN / EXPERT_DECISION / EXPERT_CONTENT /
# GRAPH_UPDATE / REPORT are the five stages the GoS pipeline actually has;
# baseline scripts) is OTHER rather than a new invented category.
#
# EVIDENCE_RETRIEVAL is an addition, and a deliberate one: the §3.2 list is
# explicitly illustrative, and `evidence_retriever` is the model call whose
# output is now stored on the completed retrieval TOOL_CALL event. Filing it
# under OTHER would leave the ledger blind to the most audit-relevant call.
STAGES = ("CENTRAL_PLAN", "EXPERT_DECISION", "EXPERT_CONTENT",
          "GRAPH_UPDATE", "REPORT", "EVIDENCE_RETRIEVAL", "OTHER")

# `pending` is not in §3.2's example enum and is added deliberately: §3.2 also
# requires the entry to exist *before* the parser runs, and a call whose parser
# has not returned yet is genuinely pending, not accepted. A run that dies
# mid-parse (the A3 case) leaves its last call `pending`, which is the truthful
# record of how far it got.
PARSE_STATUSES = ("pending", "accepted", "discarded", "failed")

DISCARD_REASONS = ("NONE", "DECISION_CONTENT_MISMATCH", "PARSER_ERROR",
                   "INVALID_SCHEMA", "UNSUPPORTED_RESPONSE_FORMAT", "OTHER")


# The shape `open_call` writes. Kept as a literal so `invocation_schema()` can
# be generated from it rather than maintained in parallel with it.
_TEMPLATE: Dict[str, Any] = {
    "call_id": "", "task_id": "", "step_id": 0, "actor_id": "", "actor_role": "",
    "stage": "", "model_id": "", "prompt_hash": "", "response_hash": "",
    "parent_event_id": "", "source": "", "parse_status": "", "discard_reason": "",
    "committed_to_state": False, "commit_target": "", "committed_event_ids": [],
    "emitted_text_hash": "", "binding": "", "timestamp": "",
}


class InvocationLedger:
    """Append-only-in-spirit record of LLM calls for one task.

    The file is rewritten on every state change rather than appended to,
    because §3.2 wants one record per call with its final status, and a run
    that dies must leave a readable ledger. At the ~50-120 calls a case makes
    that costs nothing; `test_ledger_survives_a_dead_process` pins the
    behaviour that matters.
    """

    def __init__(self, task_id: str, out_dir: str,
                 path: Optional[str] = None):
        self.task_id = task_id
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.path = path or os.path.join(out_dir, f"{task_id}_invocations.jsonl")
        self._rows: List[Dict[str, Any]] = []
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._seq = 0
        self._last_event_id = ""
        # Most recent accepted-but-uncommitted call per stage, and globally.
        self._last_accepted: str = ""
        self._warnings: List[str] = []
        # Events whose committed text matches no model response. Expected for
        # upstream's degraded-analysis fallback; see commit().
        self._unbound_commits: List[str] = []

    # ------------------------------------------------------------- opening
    def open_call(self, stage: str, actor_id: str = "", actor_role: str = "",
                  step_id: int = 0, model_id: str = "", prompt_hash: str = "",
                  response_hash: str = "", source: str = "live",
                  parent_event_id: str = "") -> str:
        """Create the entry. Called before the caller parses the response."""
        self._seq += 1
        call_id = f"call-{self.task_id}-{self._seq:05d}"
        row = dict(_TEMPLATE)
        # `dict()` is shallow, so the template's list would be shared by every
        # row: committing one event appended it to all of them. Caught by
        # `test_rows_do_not_share_their_event_list`.
        row["committed_event_ids"] = []
        row.update({
            "call_id": call_id,
            "task_id": self.task_id,
            "step_id": step_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "stage": stage if stage in STAGES else "OTHER",
            "model_id": model_id,
            "prompt_hash": prompt_hash,
            "response_hash": response_hash,
            "parent_event_id": parent_event_id or self._last_event_id,
            "source": source,
            "parse_status": "pending",
            "commit_target": "",   # EVENT | GRAPH_BELIEF | ""
            "timestamp": utc_now(),
        })
        self._rows.append(row)
        self._by_id[call_id] = row
        self.flush()
        return call_id

    # -------------------------------------------------------------- closing
    def mark_parsed(self, call_id: str, status: str, reason: str = "NONE",
                    emitted_text: str = "") -> None:
        """Record what the caller's parser decided about the response."""
        row = self._by_id.get(call_id)
        if row is None:
            self._warnings.append(f"mark_parsed on unknown call {call_id}")
            return
        row["parse_status"] = status if status in PARSE_STATUSES else "failed"
        row["discard_reason"] = reason if reason in DISCARD_REASONS else "OTHER"
        if emitted_text:
            row["emitted_text_hash"] = sha256_hex(emitted_text)
        if status == "accepted":
            self._last_accepted = call_id
        self.flush()

    def commit(self, event_id: str, text: str = "",
               stage: Optional[str] = None) -> Optional[str]:
        """Bind a provenance event to the call whose response produced it.

        Binding is by the hash of the text being committed, because that is
        checkable: the text the parser extracted is the text the event carries.
        Two refusals are deliberate.

        * **Text given, no call carries it** -> return `None` and count it in
          `unbound_commits`. Either some other call produced this event, or --
          the expected case -- the text is upstream's own degraded-analysis
          fallback, which no model ever emitted. Guessing a binding would make
          an unverified link look verified.
        * **No text given** -> fall back to the most recent accepted call of
          the stage and mark `binding="positional"`, which is a weaker claim
          and is visibly weaker in the record.
        """
        if not self._rows:
            return None
        candidates = [r for r in self._rows
                      if r["parse_status"] == "accepted"
                      and not r["committed_to_state"]
                      and (stage is None or r["stage"] == stage)]
        chosen = None
        binding = ""
        if text:
            want = sha256_hex(text)
            for r in reversed(candidates):
                if r["emitted_text_hash"] == want:
                    chosen, binding = r, "text"
                    break
            if chosen is None:
                # Not a warning. The degraded-analysis fallback is upstream's
                # own fixed string, not any model's response, so an ANALYSIS
                # carrying it has no call behind it. Counting these separately
                # keeps `warnings` for things that are actually wrong.
                self._unbound_commits.append(event_id)
                return None
        else:
            if not candidates:
                return None
            chosen, binding = candidates[-1], "positional"
        chosen["committed_to_state"] = True
        chosen["commit_target"] = "EVENT"
        chosen["committed_event_ids"].append(event_id)
        chosen["binding"] = binding
        self._last_event_id = event_id
        self.flush()
        return chosen["call_id"]

    def note_event(self, event_id: str) -> None:
        """Track the latest event so later calls can name their parent."""
        self._last_event_id = event_id

    def commit_state(self, target: str) -> Optional[str]:
        """Mark the pending-or-last accepted call as having become state that
        is not an event. Keeps `uncommitted_call_rate` from counting a response
        that GoS actually used."""
        chosen = None
        for row in reversed(self._rows):
            if row["parse_status"] == "accepted" and not row["committed_to_state"]:
                chosen = row
                break
        if chosen is None:
            return None
        chosen["committed_to_state"] = True
        chosen["commit_target"] = target
        self.flush()
        return chosen["call_id"]

    def pending_call_id(self) -> str:
        """The most recent call whose parser has not reported back yet.

        Unique in practice: this pipeline parses each response before it issues
        the next request, so the pending set has at most one member. If that
        ever stops being true the caller gets the newest, and the extra
        `pending` rows stay visible in the ledger rather than being silently
        attributed.
        """
        for row in reversed(self._rows):
            if row["parse_status"] == "pending":
                return row["call_id"]
        return ""

    def rejectable_call_id(self) -> str:
        """Where a rejection applies: the pending call, or the one just parsed.

        The A4 seam needs the second case. GoS parses a response successfully
        -- the substituted decision is well-formed JSON with a legal `decision`
        -- and *then* throws the step away because the content stage disagreed.
        At that moment nothing is pending, and resolving to "the pending call"
        would drop the rejection on the floor, which is what the first version
        of this did: both A4 rechecks reported zero discards for a run whose own
        log says `Failed Parsing: Content stage result inconsistent`.
        """
        for row in reversed(self._rows):
            if row["parse_status"] in ("pending", "accepted"):
                return row["call_id"]
        return ""

    def close_open_calls(self, status: str = "failed",
                         reason: str = "OTHER") -> int:
        """Close anything still `pending` when the task ends.

        A call that never reached its parser is not a call that was accepted.
        Recording it as `failed` with a reason is the truthful record; leaving
        it `pending` forever would let it be read as "still running".
        """
        n = 0
        for row in self._rows:
            if row["parse_status"] == "pending":
                row["parse_status"] = status
                row["discard_reason"] = reason
                n += 1
        if n:
            self.flush()
        return n

    # --------------------------------------------------------------- output
    def rows(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._rows]

    def warnings(self) -> List[str]:
        return list(self._warnings)

    def flush(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                for row in self._rows:
                    f.write(canonical_json(row) + "\n")
        except OSError:
            pass

    # -------------------------------------------------------------- summary
    def summary(self) -> Dict[str, Any]:
        n = len(self._rows)
        by_status: Dict[str, int] = {}
        by_stage: Dict[str, int] = {}
        for r in self._rows:
            by_status[r["parse_status"]] = by_status.get(r["parse_status"], 0) + 1
            by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
        discarded = [r for r in self._rows if r["parse_status"] == "discarded"]
        return {
            "task_id": self.task_id,
            "n_calls": n,
            "by_status": by_status,
            "by_stage": by_stage,
            "n_uncommitted": sum(1 for r in self._rows
                                 if not r["committed_to_state"]),
            "uncommitted_call_rate": (round(
                sum(1 for r in self._rows if not r["committed_to_state"]) / n, 4)
                if n else 0.0),
            "decision_content_discards": sum(
                1 for r in discarded
                if r["discard_reason"] == "DECISION_CONTENT_MISMATCH"),
            "binding_modes": _count([r["binding"] for r in self._rows if r["binding"]]),
            "n_unbound_commits": len(self._unbound_commits),
            "unbound_commit_ids": list(self._unbound_commits),
            "warnings": self._warnings,
        }


def _count(values: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def invocation_schema() -> Dict[str, Any]:
    """The ledger record, described. §9 asks for it as a deliverable.

    Generated from the writer rather than maintained beside it, so the two
    cannot drift: `_TEMPLATE` is the row `open_call` builds.
    """
    props: Dict[str, Any] = {
        "call_id": {"type": "string",
                    "description": "unique per task; assigned at open_call"},
        "task_id": {"type": "string"},
        "step_id": {"type": "integer"},
        "actor_id": {"type": "string"},
        "actor_role": {"type": "string", "enum": ["central", "expert", ""]},
        "stage": {"type": "string", "enum": list(STAGES)},
        "model_id": {"type": "string"},
        "prompt_hash": {"type": "string",
                        "description": "sha256 of the request key (model + "
                                       "messages + temperature); identical to "
                                       "the recording key, so a ledger row and "
                                       "a recorded call can be joined"},
        "response_hash": {"type": "string", "description": "sha256 of the response text"},
        "parent_event_id": {"type": "string",
                            "description": "latest provenance event at open time"},
        "source": {"type": "string", "enum": ["live", "replay"]},
        "parse_status": {"type": "string", "enum": list(PARSE_STATUSES),
                         "description": "`pending` between open_call and the "
                                        "parser reporting back; a run that dies "
                                        "in between leaves it `pending`"},
        "discard_reason": {"type": "string", "enum": list(DISCARD_REASONS)},
        "committed_to_state": {"type": "boolean"},
        "commit_target": {"type": "string", "enum": ["EVENT", "GRAPH_BELIEF", ""]},
        "committed_event_ids": {"type": "array", "items": {"type": "string"}},
        "emitted_text_hash": {"type": "string",
                              "description": "sha256 of the text the parser "
                                             "extracted, used to bind an event "
                                             "to its call exactly"},
        "binding": {"type": "string", "enum": ["text", "positional", ""],
                    "description": "`text` = the committed text hashes to this "
                                   "call's emitted text; `positional` = no "
                                   "text was available and the most recent "
                                   "accepted call was used instead, which is "
                                   "a weaker claim"},
        "timestamp": {"type": "string"},
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "TraceEvo Model Invocation",
        "description": (
            "One LLM call that actually happened, whether or not its response "
            "became a provenance event. The second observability channel: "
            "S2.1's A4 injections were absorbed at GoS's decision/content seam "
            "and produced no event, so a trace-only audit could not see them."),
        "type": "object",
        "required": list(_TEMPLATE.keys()),
        "properties": props,
        "x-file": "<task_id>_invocations.jsonl, one record per line",
    }


def write_schema(path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(invocation_schema(), f, ensure_ascii=False, indent=2)
    return path


def load_ledger(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows
