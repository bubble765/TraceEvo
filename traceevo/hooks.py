"""Instrumentation API called from the GoS working copy (amendment §4).

Every function here is a no-op when `traceevo.enabled` is false, so the S0
baseline behaviour is preserved (amendment §5, §6 T1).

Hook sites and how they map onto the fixed event_type enum (§3.1):

    site in GoS                          -> event_type
    ---------------------------------------------------------------------
    Central_Agent.ingest()               -> GRAPH_UPDATE  (x2, INPUT_OBSERVATION)
    Central_Agent.extract_frontier()     -> (no event of its own; the focus is
                                             attached to the next PLAN event)
    Central_Agent.plan()                 -> PLAN
    Central_Agent.act()                  -> AGENT_CALL
    Expert_Agent.run() evidence retrieval -> TOOL_CALL (query + returned output)
    Expert_Agent.run() analyse           -> ANALYSIS
    Central_Agent UpdateGraph block      -> GRAPH_UPDATE  (+ GraphDelta)
    backtrack_frontier_chain()           -> BACKTRACK     (+ seal)
    refine branch                        -> DRILL_DOWN    (+ seal)
    Report                               -> FINAL_REPORT  (+ seal)

Graph sealing: every point that mutates the BeliefGraph seals a snapshot, so
the delta chain covers the whole run. Missing a mutation point would break
`Replay(G_t, delta_t) == G_(t+1)` and is caught by T4.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, List, Optional

from . import config as tx_config
from .audit import graph_delta as gd
from .audit.cross_graph_binding import BindingRegistry, record_hash
from .audit.event_logger import EventLogger
from .audit.event_schema import ProvenanceEvent, utc_now
from .audit.integrity import verify_chain

_state = threading.local()


# --------------------------------------------------------------------- context
class TaskContext:
    """Per-case provenance state."""

    def __init__(self, task_id: str, out_dir: str,
                 seed: Optional[Dict[str, Any]] = None,
                 recovery_feedback: str = ""):
        seed = seed or {}
        self.task_id = task_id
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.logger = EventLogger(os.path.join(out_dir, f"{task_id}_events.jsonl"),
                                  task_id=task_id,
                                  initial_events=seed.get("events") or [])
        self.bindings = copy.deepcopy(seed.get("bindings") or BindingRegistry(task_id))
        self.step_id = int(seed.get("step_id", 0))
        self.focus: Dict[str, Any] = copy.deepcopy(seed.get("focus") or {})
        self.current_expert: str = ""
        self.graph_states: List[Dict[str, Any]] = copy.deepcopy(
            seed.get("graph_states") or [])
        self.deltas: List[gd.GraphDelta] = [
            copy.deepcopy(delta) if isinstance(delta, gd.GraphDelta)
            else gd.GraphDelta.from_dict(delta)
            for delta in (seed.get("deltas") or [])]
        self.events: List[Dict[str, Any]] = copy.deepcopy(seed.get("events") or [])
        self._last_event_by_type: Dict[str, str] = {}
        for row in self.events:
            self._last_event_by_type[row.get("event_type", "")] = row.get("event_id", "")
        self._open_agent_calls: Dict[str, str] = {}
        self._pending_tool_calls: Dict[str, Dict[str, str]] = {}
        self._expert_retrievals: List[str] = []
        self._expert_retrieval_events: List[str] = []
        self.warnings: List[str] = copy.deepcopy(seed.get("warnings") or [])
        # S2 §3.2 execution dependency bookkeeping
        self._step_analyses: List[str] = []      # ANALYSIS event ids this step
        self._last_graph_update: str = str(seed.get("last_graph_update", ""))
        self._last_state_change: str = str(seed.get("last_state_change", ""))
        self._last_plan: str = str(seed.get("last_plan", ""))
        self._last_ingest: str = str(seed.get("last_ingest", ""))
        self._pending_seal_anchor: str = ""      # state change awaiting its seal
        # S3B: the case text visible before the run, used as the retrieval
        # query (§11) and never injected (§11 again).
        self.retrieval_query: str = str(seed.get("retrieval_query", ""))
        # Every injection this task made, for the §13 leakage guard.
        self.injections: List[Dict[str, Any]] = copy.deepcopy(
            seed.get("injections") or [])
        self.recovery_feedback = recovery_feedback or str(seed.get("recovery_feedback", ""))
        self.recovery_checkpoints: List[Dict[str, Any]] = copy.deepcopy(
            seed.get("recovery_checkpoints") or [])
        # S3A §3: the second observability channel. None when disabled, which
        # is the ledger-off arm of §8's regression.
        from .audit.invocation_ledger import InvocationLedger
        self.ledger = (InvocationLedger(task_id, out_dir)
                       if tx_config.ledger_enabled() else None)

    def model_fields(self) -> Dict[str, Any]:
        """S2 §1 D1: report what is actually knowable about the model."""
        cfg = tx_config.load()
        model_id = (cfg.get("model_id")
                    or tx_config.gos_default_model())
        version = cfg.get("model_version")
        return {"model_id": model_id,
                "model_version": version,
                "version_observable": bool(cfg.get("version_observable", False))}

    # ------------------------------------------------------------- emission
    def emit(self, event_type: str, actor_id: str, actor_role: str,
             source_class: str = "SYSTEM_GENERATED", tool_or_model: str = "",
             input_refs: Optional[List[str]] = None,
             output_refs: Optional[List[str]] = None,
             causal_node_refs: Optional[List[str]] = None,
             parent_event_ids: Optional[List[str]] = None,
             status: str = "success", reason_code: str = "NONE",
             payload: Optional[Dict[str, Any]] = None) -> ProvenanceEvent:
        model = self.model_fields()
        ev = ProvenanceEvent(
            event_id=self.logger.next_event_id(),
            task_id=self.task_id,
            step_id=self.step_id,
            timestamp=utc_now(),
            event_type=event_type,
            actor_id=actor_id,
            actor_role=actor_role,
            tool_or_model=tool_or_model,
            input_refs=list(input_refs or []),
            output_refs=list(output_refs or []),
            causal_node_refs=list(causal_node_refs or []),
            parent_event_ids=list(parent_event_ids or []),
            source_class=source_class,
            model_id=model["model_id"],
            model_version=model["model_version"],
            version_observable=model["version_observable"],
            status=status,
            reason_code=reason_code,
            payload=payload or {},
        )
        self.logger.append(ev)
        row = ev.to_dict()
        self.events.append(row)
        self._last_event_by_type[event_type] = ev.event_id
        if self.ledger is not None:
            self.ledger.note_event(ev.event_id)
        return ev

    def last(self, event_type: str) -> Optional[str]:
        return self._last_event_by_type.get(event_type)

    # ------------------------------------------------------- graph sealing
    # Sealing is two-phase because the event is hashed at emission time but the
    # nodes it produced are only known after the graph has been diffed:
    #   preview_graph_change() -> which nodes are new
    #   emit(event, causal_node_refs=new_ids)
    #   commit_graph_change()  -> store the delta and bind the nodes
    # Emitting first and patching the event afterwards would break its hash
    # (caught by test_mutation_after_seal_is_detected).
    def preview_graph_change(self, graph):
        after = gd.snapshot(graph)
        before = (self.graph_states[-1] if self.graph_states
                  else {"nodes": {}, "edges": [], "start_signal_id": None})
        before_nodes = before.get("nodes") or {}
        new_ids = [nid for nid in (after.get("nodes") or {}) if nid not in before_nodes]
        return before, after, new_ids

    def commit_graph_change(self, before: Dict[str, Any], after: Dict[str, Any],
                            new_ids: List[str], event_id: str,
                            phase: str) -> gd.GraphDelta:
        delta = gd.diff_states(
            before, after,
            delta_id=f"delta-{self.task_id}-{len(self.deltas) + 1:04d}",
            event_id=event_id)
        self.deltas.append(delta)
        self.graph_states.append(after)
        self._bind_new_nodes(before, after, event_id, phase)
        # S2 §3.2: remember this state-changing event so the next PLAN and the
        # FINAL_REPORT can point at it as their parent.
        self._last_state_change = event_id
        if phase in ("ingest", "update_graph"):
            self._last_graph_update = event_id
        return delta

    def seal_graph(self, graph, event_id: str, phase: str) -> gd.GraphDelta:
        """Convenience wrapper for callers that already emitted the event."""
        before, after, new_ids = self.preview_graph_change(graph)
        return self.commit_graph_change(before, after, new_ids, event_id, phase)

    def _bind_new_nodes(self, before: Dict[str, Any], after: Dict[str, Any],
                        event_id: str, phase: str) -> None:
        cfg = tx_config.load().get("source_classification", {})
        before_nodes = before.get("nodes") or {}
        after_nodes = after.get("nodes") or {}
        for nid, node in after_nodes.items():
            if nid in before_nodes:
                continue
            self.bindings.bind(node_id=nid,
                               node_type=node.get("type", "Evidence"),
                               label=node.get("label", ""),
                               created_by_event=event_id,
                               step_id=self.step_id,
                               phase=phase, cfg=cfg)


def _ctx() -> Optional[TaskContext]:
    return getattr(_state, "ctx", None)


def current() -> Optional[TaskContext]:
    return _ctx()


def start_task(task_id: str, out_dir: Optional[str] = None,
               task_label: Optional[str] = None,
               retrieval_query: str = "") -> Optional[TaskContext]:
    """Begin a provenance task. `task_label` is used only for the output folder
    name, so a replayed run of the same case does not overwrite the original."""
    if not tx_config.enabled():
        _state.ctx = None
        _ensure_recorder()
        return None
    out = out_dir or os.path.join(tx_config.output_dir(), "runs")
    if task_label:
        out = os.path.join(out, task_label)
    ctx = TaskContext(task_id, out)
    ctx.retrieval_query = retrieval_query or ""
    _state.ctx = ctx
    _ensure_recorder()
    return ctx


def start_recovery_task(parent_ctx: TaskContext,
                        checkpoint: Dict[str, Any],
                        attempt_number: int,
                        feedback: str) -> Optional[TaskContext]:
    """Start an isolated retry artifact set from a verified event prefix.

    The original task directory and event log remain unchanged. The retry uses
    the same task/event IDs for the copied prefix, then continues the hash chain
    in a separate `recovery-N/` directory.
    """
    if not tx_config.enabled() or parent_ctx is None:
        _state.ctx = None
        return None
    event_count = int(checkpoint.get("event_count", 0))
    seed = {
        "events": copy.deepcopy(parent_ctx.events[:event_count]),
        "bindings": copy.deepcopy(checkpoint.get("bindings") or BindingRegistry(parent_ctx.task_id)),
        "graph_states": copy.deepcopy(checkpoint.get("graph_states") or []),
        "deltas": copy.deepcopy(checkpoint.get("deltas") or []),
        "step_id": int(checkpoint.get("step_id", 0)),
        "focus": copy.deepcopy(checkpoint.get("focus") or {}),
        "last_state_change": checkpoint.get("last_state_change", ""),
        "last_graph_update": checkpoint.get("last_graph_update", ""),
        "last_plan": checkpoint.get("last_plan", ""),
        "last_ingest": checkpoint.get("last_ingest", ""),
        "retrieval_query": parent_ctx.retrieval_query,
        "injections": copy.deepcopy(checkpoint.get("injections") or []),
        "warnings": copy.deepcopy(checkpoint.get("warnings") or []),
        "recovery_checkpoints": [copy.deepcopy(row)
                                 for row in parent_ctx.recovery_checkpoints
                                 if int(row.get("event_count", 0)) <= event_count],
    }
    out_dir = os.path.join(parent_ctx.out_dir, f"recovery-{attempt_number}")
    ctx = TaskContext(parent_ctx.task_id, out_dir, seed=seed,
                      recovery_feedback=feedback)
    _state.ctx = ctx
    _ensure_recorder()
    return ctx


def end_task() -> Optional[TaskContext]:
    ctx = _ctx()
    _state.ctx = None
    return ctx


def _ensure_recorder() -> None:
    """Install the configured LLM response cache once per process."""
    global _recorder
    if _recorder is not None:
        return
    cfg = (tx_config.load().get("replay") or {})
    mode = cfg.get("mode") or "off"
    path = cfg.get("recording_path") or ""
    if mode == "off":
        return
    if not path:
        path = os.path.join(tx_config.output_dir(), "llm_responses.jsonl")
    init_recorder(mode, path, strict=bool(cfg.get("strict", True)))


# ------------------------------------------------------------------ recording
_recorder = None


def recorder():
    return _recorder


def init_recorder(mode: str, path: str, strict: bool = True):
    """Install the process-wide LLM response cache.

    Intentionally independent of `enabled`: T1 must be able to replay with
    instrumentation off as well as on.
    """
    global _recorder
    from .recording import ResponseCache, MODE_OFF
    if not mode or mode == MODE_OFF:
        _recorder = None
        return None
    _recorder = ResponseCache(mode=mode, path=path, strict=strict)
    return _recorder


# ---------------------------------------------------------------- hook bodies
def report_recorder() -> Optional[Dict[str, Any]]:
    """Print the LLM record/replay accounting. Called once at the end of a run.

    The exhaustion check matters: a replay that stops early (or consumes fewer
    entries than were recorded) must be visible, otherwise "identical outputs"
    could mean "the run quietly did less work".
    """
    if _recorder is None:
        return None
    stats = _recorder.stats()
    stats["exhausted"] = None
    if getattr(_recorder, "mode", "") == "replay":
        check = _recorder.verify_exhausted()
        stats["exhausted"] = check["ok"]
        stats["unconsumed_entries"] = check["unconsumed"]
    print(f"[TraceEvo] llm backend: {json.dumps(stats, ensure_ascii=False)}")
    return stats


# ------------------------------------------------- Model Invocation Ledger
# S3A §3. The ledger is a second, independent observation of the same run: it
# records every LLM call, including the ones whose response never became an
# event. Nothing here is read by GoS.
def on_llm_open(stage: str, prompt_hash: str, model_id: str = "",
                actor_id: str = "") -> str:
    """Create the ledger entry. Called before the caller parses anything."""
    ctx = _ctx()
    if ctx is None or ctx.ledger is None:
        return ""
    role = "central" if stage in ("CENTRAL_PLAN", "GRAPH_UPDATE", "REPORT") \
        else ("expert" if stage.startswith("EXPERT_") else "")
    return ctx.ledger.open_call(
        stage=stage,
        actor_id=actor_id or (ctx.current_expert if role == "expert"
                              else "Central_Agent"),
        actor_role=role,
        step_id=ctx.step_id,
        model_id=model_id,
        prompt_hash=prompt_hash)


def on_llm_response(call_id: str, response_text: str, source: str = "live") -> None:
    """Attach the response to the entry once it exists (cached or live)."""
    ctx = _ctx()
    if ctx is None or ctx.ledger is None or not call_id:
        return
    row = ctx.ledger._by_id.get(call_id)
    if row is None:
        return
    from .audit.event_schema import sha256_hex
    row["response_hash"] = sha256_hex(response_text or "")
    row["source"] = source
    ctx.ledger.flush()


def on_llm_parsed(call_id: str, status: str, reason: str = "NONE",
                  emitted_text: str = "") -> None:
    """What the caller's parser decided about the response.

    An empty `call_id` resolves to the most recent still-pending call. That is
    exact here rather than a guess: this pipeline parses a response before it
    issues the next request, so at most one call is ever pending. Resolving it
    inside the ledger keeps the call id out of GoS's own variables, which is
    what keeps the instrumentation from being able to affect a decision.
    """
    ctx = _ctx()
    if ctx is None or ctx.ledger is None:
        return
    if not call_id:
        # Accepting resolves to the pending call; rejecting may also re-classify
        # the call that was just accepted, which is the A4 seam (see
        # `rejectable_call_id`).
        call_id = (ctx.ledger.pending_call_id() if status == "accepted"
                   else ctx.ledger.rejectable_call_id())
        if not call_id:
            ctx.ledger._warnings.append(
                f"parse outcome {status!r} reported with no call to apply it to")
            return
    ctx.ledger.mark_parsed(call_id, status, reason, emitted_text)


def on_llm_state(target: str) -> None:
    """The pending call's response became GoS state that is not an event.

    The belief text is the only such case: it is computed by an LLM call and
    written straight into `graph.belief`, which later prompts read, without any
    provenance event being emitted for it.
    """
    ctx = _ctx()
    if ctx is None or ctx.ledger is None:
        return
    ctx.ledger.commit_state(target)


def on_llm_commit(event_id: str, text: str = "", stage: str = "") -> None:
    """Bind a provenance event to the call whose response produced it."""
    ctx = _ctx()
    if ctx is None or ctx.ledger is None:
        return
    ctx.ledger.commit(event_id, text=text, stage=stage or None)


def on_plan_prompt(user_prompt: str) -> str:
    """S3B §12: append the trusted-experience block to a plan prompt.

    Returns the prompt unchanged when memory is off, when nothing clears the
    similarity floor, or when no store is configured -- so with memory disabled
    the string handed to the model is byte-identical to GoS's own, and that is
    testable rather than asserted.

    The block is appended, never merged: no existing prompt text is touched.
    """
    ctx = _ctx()
    if ctx is None:
        return user_prompt
    if ctx.recovery_feedback:
        user_prompt = user_prompt.rstrip() + "\n\n" + ctx.recovery_feedback
    if not tx_config.memory_enabled():
        return user_prompt
    from .evolution.memory_retriever import retrieve
    from .evolution.memory_formatter import inject_into_plan_prompt
    store = tx_config.memory_store()
    if store is None:
        return user_prompt
    hits = retrieve(store, ctx.retrieval_query,
                    k=int(tx_config.memory_top_k()),
                    min_score=float(tx_config.memory_min_score()),
                    initial_plan=True)
    if not hits:
        return user_prompt
    ctx.injections.append({
        "task_id": ctx.task_id,
        "source_memory_ids": [h["memory_id"] for h in hits],
        "scores": [h["retrieval_score"] for h in hits],
        "block": _render_block(hits),
    })
    return inject_into_plan_prompt(user_prompt, hits)


def _render_block(hits):
    from .evolution.memory_formatter import render
    return render(hits)


def on_step(step: int) -> None:
    ctx = _ctx()
    if ctx:
        ctx.step_id = step


def on_recovery_checkpoint(graph, fsm, experts, loop_step: int,
                           phase: str = "before_step") -> Optional[Dict[str, Any]]:
    """Capture a complete pre-operation state when report-level recovery is on."""
    ctx = _ctx()
    if ctx is None or not tx_config.post_report_audit_enabled():
        return None
    fsm_state = (fsm.to_dict() if hasattr(fsm, "to_dict") else {
        "state": getattr(fsm, "state", 1),
        "history": list(getattr(fsm, "history", [])),
        "stage_steps": dict(getattr(fsm, "_stage_steps", {})),
        "thresholds": dict(getattr(fsm, "thresholds", {})),
    })
    checkpoint = {
        "checkpoint_id": f"ckpt-{len(ctx.recovery_checkpoints) + 1:04d}",
        "phase": str(phase),
        "loop_step": int(loop_step),
        "step_id": ctx.step_id,
        "event_count": len(ctx.events),
        "graph_state": gd.snapshot(graph),
        "next_node_index": int(getattr(graph, "idx", 1)),
        "graph_belief": str(getattr(graph, "belief", "")),
        "fsm_state": copy.deepcopy(fsm_state),
        "expert_histories": {
            agent.name: copy.deepcopy(getattr(agent, "retrieval_history", []))
            for agent in (experts or [])
        },
        "bindings": copy.deepcopy(ctx.bindings),
        "graph_states": copy.deepcopy(ctx.graph_states),
        "deltas": copy.deepcopy(ctx.deltas),
        "focus": copy.deepcopy(ctx.focus),
        "last_state_change": ctx._last_state_change,
        "last_graph_update": ctx._last_graph_update,
        "last_plan": ctx._last_plan,
        "last_ingest": ctx._last_ingest,
        "injections": copy.deepcopy(ctx.injections),
        "warnings": copy.deepcopy(ctx.warnings),
    }
    ctx.recovery_checkpoints.append(checkpoint)
    return checkpoint


def on_recovery_prompt(user_prompt: str) -> str:
    """Append validated audit feedback to a recovery attempt's prompt."""
    ctx = _ctx()
    if ctx is None or not ctx.recovery_feedback:
        return user_prompt
    return (user_prompt.rstrip() + "\n\n" + ctx.recovery_feedback)


def on_focus(frontier: Dict[str, Any]) -> None:
    """extract_frontier(): stash the reasoning focus for the next PLAN event."""
    ctx = _ctx()
    if ctx:
        ctx.focus = dict(frontier or {})


def on_plan(actor: str, response_text: str) -> None:
    ctx = _ctx()
    if not ctx:
        return
    focus_id = ctx.focus.get("node_id")
    # S2 §3.2: the first PLAN of a task has no predecessor; later ones depend on
    # the previous state change (GRAPH_UPDATE / BACKTRACK / DRILL_DOWN).
    parent = ctx._last_state_change or None
    ev = ctx.emit("PLAN", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED",
                  input_refs=[focus_id] if focus_id else [],
                  causal_node_refs=[focus_id] if focus_id else [],
                  parent_event_ids=[parent] if parent else [],
                  payload={"response": response_text,
                           "focus": dict(ctx.focus)})
    ctx._last_plan = ev.event_id
    ctx._step_analyses = []
    on_llm_commit(ev.event_id, response_text, "CENTRAL_PLAN")


def on_agent_call(actor: str, expert: str) -> str:
    ctx = _ctx()
    if not ctx:
        return ""
    ctx.current_expert = expert
    focus_id = ctx.focus.get("node_id")
    parent = ctx._last_plan or None
    ev = ctx.emit("AGENT_CALL", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED", tool_or_model=expert,
                  causal_node_refs=[focus_id] if focus_id else [],
                  parent_event_ids=[parent] if parent else [],
                  payload={"expert": expert})
    ctx._open_agent_calls[expert] = ev.event_id
    return ev.event_id


def on_dispatch_failed(actor: str, requested: str, available: List[str],
                       message: str) -> str:
    """S2 §3.1 / A3: the planner named an agent that does not exist.

    Recorded as a failed AGENT_CALL and returned to the caller, which re-raises
    the original exception. Nothing here repairs or swallows the error.
    """
    ctx = _ctx()
    if not ctx:
        return ""
    focus_id = ctx.focus.get("node_id")
    parent = ctx._last_plan or None
    ev = ctx.emit("AGENT_CALL", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED", tool_or_model=requested,
                  causal_node_refs=[focus_id] if focus_id else [],
                  parent_event_ids=[parent] if parent else [],
                  status="failed", reason_code="INVALID_EXPERT_DISPATCH",
                  payload={"expert": requested, "available": list(available),
                           "error": message, "dispatch": "failed"})
    return ev.event_id


def on_tool_call(query: str, tool_name: str = "Evidence_Text",
                 source_store: Optional[str] = None) -> str:
    """Stage a tool request until its output can be logged on one event.

    A completed TOOL_CALL event carries both this request and its result. This
    function records only transient context; it does not emit a second event.
    """
    ctx = _ctx()
    if not ctx:
        return ""
    expert = ctx.current_expert or "unknown"
    ctx._pending_tool_calls[expert] = {
        "query": query,
        "tool_name": tool_name,
        "source_store": source_store or "",
        "started_at": utc_now(),
    }
    return ""


def on_retrieval_result(query: str, retrieved_text: str,
                        source_store: Optional[str] = None,
                        source_refs: Optional[List[Dict[str, Any]]] = None,
                        tool_name: str = "Evidence_Text",
                        ledger_stage: Optional[str] = "EVIDENCE_RETRIEVAL",
                        retrieval_output: Optional[str] = None,
                        hit: Optional[bool] = None) -> str:
    """Emit one completed TOOL_CALL event with the query and raw output.

    `retrieved_text` is the source-bearing text used by evidence binding.
    `retrieval_output` is the full text returned to the expert, including a
    no-hit explanation when there is no source passage. Both are covered by
    the event hash; only the source-bearing text is used for record matching.
    """
    ctx = _ctx()
    if not ctx:
        return ""
    expert = ctx.current_expert or "unknown"
    pending = ctx._pending_tool_calls.pop(expert, {})
    query = query or pending.get("query", "")
    tool_name = tool_name or pending.get("tool_name", "Evidence_Text")
    source_store = source_store or pending.get("source_store") or None
    output_text = retrieved_text if retrieval_output is None else retrieval_output
    has_hit = bool(retrieved_text) if hit is None else bool(hit)

    # The record ID and content hash must be ready before the event is sealed.
    record_id = ctx.bindings.new_record_id()
    rec_hash = record_hash(ctx.task_id, query, retrieved_text, expert)
    parent = ctx._open_agent_calls.get(expert)
    payload = {
        "query": query,
        "hit": has_hit,
        "retrieval_output": output_text,
        "record_hash": rec_hash,
        "started_at": pending.get("started_at", ""),
    }
    if source_store is not None:
        payload["source_store"] = source_store
    if source_refs is not None:
        payload["source_refs"] = source_refs
    ev = ctx.emit("TOOL_CALL", actor_id=expert, actor_role="expert",
                  source_class="RETRIEVED_RECORD" if has_hit else "UNKNOWN",
                  tool_or_model=tool_name,
                  output_refs=[record_id],
                  parent_event_ids=[parent] if parent else [],
                  payload=payload)
    ctx.bindings.add_record(query=query, text=retrieved_text,
                            record_id=record_id,
                            retrieval_event_id=ev.event_id,
                            step_id=ctx.step_id, expert=expert,
                            record_hash=rec_hash,
                            source_store=source_store,
                            source_refs=source_refs)
    ctx._expert_retrievals.append(record_id)
    ctx._expert_retrieval_events.append(ev.event_id)
    # One completed tool event can bind both the expert's query-generation call
    # and the evidence-retriever call in the independent invocation ledger.
    on_llm_commit(ev.event_id, query, "EXPERT_CONTENT")
    if ledger_stage:
        on_llm_commit(ev.event_id, retrieved_text, ledger_stage)
    return record_id


def on_evidence_read(query: str, retrieved_text: str,
                     source_store: Optional[str] = None,
                     source_refs: Optional[List[Dict[str, Any]]] = None,
                     tool_name: str = "Evidence_Text",
                     ledger_stage: Optional[str] = "EVIDENCE_RETRIEVAL",
                     retrieval_output: Optional[str] = None,
                     hit: Optional[bool] = None) -> str:
    """Backward-compatible hook name; new runs emit one completed TOOL_CALL."""
    return on_retrieval_result(
        query, retrieved_text, source_store=source_store,
        source_refs=source_refs, tool_name=tool_name,
        ledger_stage=ledger_stage, retrieval_output=retrieval_output, hit=hit)


def on_evidence_read_failed(query: str, tool_name: str,
                            source_store: str, error: str) -> str:
    """Record a failed retrieval as one completed (failed) TOOL_CALL event."""
    ctx = _ctx()
    if not ctx:
        return ""
    expert = ctx.current_expert or "unknown"
    pending = ctx._pending_tool_calls.pop(expert, {})
    query = query or pending.get("query", "")
    tool_name = tool_name or pending.get("tool_name", "Evidence_Text")
    source_store = source_store or pending.get("source_store", "unknown")
    parent = ctx._open_agent_calls.get(expert)
    ev = ctx.emit(
        "TOOL_CALL",
        actor_id=expert,
        actor_role="expert",
        source_class="UNKNOWN",
        tool_or_model=tool_name,
        parent_event_ids=[parent] if parent else [],
        status="failed",
        reason_code="TOOL_ERROR",
        payload={
            "query": query,
            "hit": False,
            "retrieval_output": "",
            "source_store": source_store,
            "error": str(error)[:500],
            "started_at": pending.get("started_at", ""),
        },
    )
    ctx._expert_retrieval_events.append(ev.event_id)
    on_llm_commit(ev.event_id, query, "EXPERT_CONTENT")
    return ev.event_id


def on_analysis(analysis: str, status: str = "success",
                reason_code: str = "NONE", exit_cause: str = "") -> None:
    """One expert finished (or fell back).

    `status`/`reason_code` describe the outcome; the analysis text itself is
    whatever upstream produced and is never altered here.
    """
    ctx = _ctx()
    if not ctx:
        return
    expert = ctx.current_expert or "unknown"
    # S2 §3.2: ANALYSIS depends on the AGENT_CALL plus each completed retrieval
    # TOOL_CALL this expert performed for the current focus.
    parents: List[str] = []
    agent_call = ctx._open_agent_calls.get(expert)
    if agent_call:
        parents.append(agent_call)
    parents.extend(ctx._expert_retrieval_events)
    ev = ctx.emit("ANALYSIS", actor_id=expert, actor_role="expert",
                  source_class="AGENT_GENERATED",
                  input_refs=list(ctx._expert_retrievals),
                  parent_event_ids=parents,
                  status=status, reason_code=reason_code,
                  payload={"analysis": analysis,
                           "exit_cause": exit_cause})
    ctx._step_analyses.append(ev.event_id)
    ctx._expert_retrievals = []
    ctx._expert_retrieval_events = []
    on_llm_commit(ev.event_id, analysis, "EXPERT_CONTENT")


def on_ingest(graph, actor: str, response_text: str, phase: str) -> str:
    """ingest() produced nodes. phase in {basic_node, l1_hypo}."""
    ctx = _ctx()
    if not ctx:
        return ""
    before, after, new_ids = ctx.preview_graph_change(graph)
    # Rule 10 (extension, S2 §3.2 + 1): the L1 hypotheses are proposed *from*
    # the symptom/evidence nodes, so the second ingest genuinely depends on the
    # first. Without this edge the two halves of ingest are separate roots and
    # a report cannot be traced back to the case input by parent pointers.
    parents: List[str] = []
    if phase == "l1_hypo" and ctx._last_ingest:
        parents.append(ctx._last_ingest)
    ev = ctx.emit("GRAPH_UPDATE", actor_id=actor, actor_role="central",
                  source_class="INPUT_OBSERVATION",
                  causal_node_refs=new_ids,
                  parent_event_ids=parents,
                  payload={"phase": f"ingest:{phase}", "response": response_text})
    ctx._last_ingest = ev.event_id
    ctx.commit_graph_change(before, after, new_ids, ev.event_id, phase="ingest")
    on_llm_commit(ev.event_id, response_text, "GRAPH_UPDATE")
    return ev.event_id


def on_graph_update(graph, actor: str, response_text: str) -> str:
    """UpdateGraph finished applying the {edit, nodes, edges} proposal."""
    ctx = _ctx()
    if not ctx:
        return ""
    before, after, new_ids = ctx.preview_graph_change(graph)
    # S2 §3.2: GRAPH_UPDATE depends on this step's ANALYSIS events.
    parents = list(ctx._step_analyses)
    ev = ctx.emit("GRAPH_UPDATE", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED",
                  causal_node_refs=new_ids,
                  parent_event_ids=parents,
                  payload={"phase": "update_graph", "response": response_text})
    ctx.commit_graph_change(before, after, new_ids, ev.event_id, phase="update_graph")
    on_llm_commit(ev.event_id, response_text, "GRAPH_UPDATE")
    return ev.event_id


def on_seal_graph(graph, phase: str) -> None:
    """Record a graph state produced by a non-UpdateGraph mutation.

    Two things this must NOT do, both learned from case 8:

    * anchor on the last event of a type *ever emitted*. `ctx.last("DRILL_DOWN")`
      keeps returning a step-1 drill-down forever, which made every later PLAN
      point at a state change that had long been superseded and mis-attributed
      the delta to an unrelated event. The anchor is now the state-change event
      emitted in *this* step, tracked by `ctx._pending_seal_anchor`.
    * seal when nothing changed. The call site runs after every step, and the
      only mutation it can see is a backtrack; emitting an empty delta per step
      would bloat the chain and decouple `event_id` from any real change.
    """
    ctx = _ctx()
    if not ctx:
        return
    before, after, new_ids = ctx.preview_graph_change(graph)
    if gd.state_hash(before) == gd.state_hash(after):
        ctx._pending_seal_anchor = ""
        return
    anchor = ctx._pending_seal_anchor or ctx._last_graph_update or ""
    ctx._pending_seal_anchor = ""
    ctx.commit_graph_change(before, after, new_ids, anchor, phase=phase)


def on_backtrack(actor: str, level_from: int, level_to: int,
                 deleted_levels: List[int], frontier_node: str) -> str:
    ctx = _ctx()
    if not ctx:
        return ""
    parent = ctx._last_graph_update or None
    ev = ctx.emit("BACKTRACK", actor_id=actor, actor_role="central",
                  source_class="SYSTEM_GENERATED",
                  causal_node_refs=[frontier_node] if frontier_node else [],
                  parent_event_ids=[parent] if parent else [],
                  # `applied` is what distinguishes a real state change from a
                  # backtrack that fired but had nothing above the demoted level
                  # to delete (observed on case 2: 1->1, deleted_levels=[]). A
                  # no-op backtrack must not become the parent of the next PLAN,
                  # because the next PLAN does not depend on it.
                  payload={"level_from": level_from, "level_to": level_to,
                           "deleted_levels": list(deleted_levels),
                           "applied": bool(deleted_levels)})
    # The graph mutation itself is sealed by the following on_seal_graph, which
    # adopts this event as the delta's cause.
    ctx._pending_seal_anchor = ev.event_id
    return ev.event_id


def on_drill_down(actor: str, level_from: int, level_to: int,
                  parent_node: str, candidate_labels: List[str], graph=None) -> str:
    ctx = _ctx()
    if not ctx:
        return ""
    before = after = new_ids = None
    if graph is not None:
        before, after, new_ids = ctx.preview_graph_change(graph)
    refs = list(new_ids or [])
    if parent_node:
        refs.append(parent_node)
    parent = ctx._last_graph_update or None
    ev = ctx.emit("DRILL_DOWN", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED", causal_node_refs=refs,
                  parent_event_ids=[parent] if parent else [],
                  payload={"level_from": level_from, "level_to": level_to,
                           "candidates": list(candidate_labels),
                           "applied": bool(candidate_labels)})
    if graph is not None:
        ctx.commit_graph_change(before, after, new_ids, ev.event_id,
                                phase="drill_down")
    return ev.event_id


def on_final_report(actor: str, prediction: str, report: str,
                    graph=None,
                    fsm_context: Optional[Dict[str, Any]] = None) -> str:
    ctx = _ctx()
    if not ctx:
        return ""
    focus_id = ctx.focus.get("node_id")
    deps: List[str] = list(ctx._expert_retrievals)
    if focus_id:
        deps.append(focus_id)
    # S2 §3.2: the report depends on the current reasoning state.
    parent = ctx._last_state_change or ctx._last_graph_update or None
    ev = ctx.emit("FINAL_REPORT", actor_id=actor, actor_role="central",
                  source_class="AGENT_GENERATED",
                  output_refs=deps, causal_node_refs=[focus_id] if focus_id else [],
                  parent_event_ids=[parent] if parent else [],
                  payload={"prediction": prediction,
                           "report": report,
                           "report_chars": len(report or ""),
                           "fsm_context": copy.deepcopy(fsm_context or {})})
    if graph is not None:
        ctx.seal_graph(graph, ev.event_id, phase="final_report")
    # `report` is the field the parser extracted, so binding by its hash is
    # exact -- the central agent passes this same string to this hook.
    on_llm_commit(ev.event_id, report, "REPORT")
    return ev.event_id


# ---------------------------------------------------------------------- export
def finish_task(render_png: bool = True) -> Optional[Dict[str, Any]]:
    """Verify the chain, replay the deltas and persist every per-task artifact.

    Returns a summary dict; returns None when provenance is disabled.
    """
    import json

    ctx = _ctx()
    if ctx is None:
        return None

    from .audit.graph_delta import GraphDelta, replay, state_hash
    from .audit.provenance_graph import build_provenance_graph, render_png as _png

    # 0. S3A: close the invocation ledger before anything else. A call that
    # never reached its parser is `failed`, not `accepted` -- leaving it
    # `pending` would read as "still running" forever.
    if ctx.ledger is not None:
        ctx.ledger.close_open_calls("failed", "OTHER")

    # 0b. S3B §13: record exactly what memory put into each prompt, so the
    # leakage guard scans what was really sent rather than re-rendering it and
    # hoping the two agree.
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_injections.jsonl"),
              "w", encoding="utf-8") as f:
        for inj in ctx.injections:
            f.write(json.dumps(inj, ensure_ascii=False) + "\n")

    # 1. hash-chain integrity
    report = verify_chain(ctx.events)
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_integrity.json"),
              "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

    # 2. graph delta log
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_deltas.jsonl"),
              "w", encoding="utf-8") as f:
        for d in ctx.deltas:
            f.write(json.dumps(d.to_dict(), ensure_ascii=False) + "\n")

    # Full snapshots preserve previous node contents, confidence values and
    # attributes. Deltas contain changed IDs and hashes, which alone cannot
    # restore a pre-anomaly state after an existing node was edited.
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_graph_states.jsonl"),
              "w", encoding="utf-8") as f:
        for index, state in enumerate(ctx.graph_states):
            delta = ctx.deltas[index] if index < len(ctx.deltas) else None
            f.write(json.dumps({
                "state_index": index,
                "after_event_id": delta.event_id if delta else "",
                "state": state,
            }, ensure_ascii=False) + "\n")

    if ctx.recovery_checkpoints:
        with open(os.path.join(ctx.out_dir,
                               f"{ctx.task_id}_recovery_checkpoints.jsonl"),
                  "w", encoding="utf-8") as f:
            for checkpoint in ctx.recovery_checkpoints:
                row = {key: copy.deepcopy(value)
                       for key, value in checkpoint.items()
                       if key not in {"bindings", "deltas"}}
                row["bindings"] = checkpoint["bindings"].to_dict()
                row["deltas"] = [delta.to_dict()
                                 for delta in checkpoint.get("deltas", [])]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 3. replay check: rebuild every state from the initial state + deltas
    initial = ctx.graph_states[0] if ctx.graph_states else {
        "nodes": {}, "edges": [], "start_signal_id": None}
    deltas_for_replay = list(ctx.deltas)
    # graph_states[i] is the state *produced by* deltas[i], so the after-states
    # line up index-for-index. Slicing here would compare each delta against its
    # successor and silently pass whenever the next delta did not touch the
    # same nodes.
    after_states = list(ctx.graph_states)
    if deltas_for_replay:
        initial = _state_before_first_delta(ctx, initial)
    _, checks = replay(initial, deltas_for_replay, after_states)
    replay_ok = all(c["ok"] for c in checks) and bool(checks)
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_replay.json"),
              "w", encoding="utf-8") as f:
        json.dump({"task_id": ctx.task_id, "n_deltas": len(checks),
                   "all_ok": replay_ok, "checks": checks}, f,
                  ensure_ascii=False, indent=2)

    # 4. cross-graph binding + grounding summary
    binding_dict = ctx.bindings.to_dict()
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_bindings.json"),
              "w", encoding="utf-8") as f:
        json.dump(binding_dict, f, ensure_ascii=False, indent=2)

    # 5. provenance graph P_t
    pgraph = build_provenance_graph(ctx.task_id, ctx.events)
    pgraph_dict = pgraph.to_dict()
    pgraph.save(os.path.join(ctx.out_dir, f"{ctx.task_id}_provenance_graph.json"))
    png_ok = False
    if render_png:
        png_ok = _png(pgraph_dict,
                      os.path.join(ctx.out_dir, f"{ctx.task_id}_provenance_graph.png"))

    summary = {
        "task_id": ctx.task_id,
        "n_events": len(ctx.events),
        "event_type_counts": _count(ctx.events, "event_type"),
        "source_class_counts": _count(ctx.events, "source_class"),
        "n_graph_states": len(ctx.graph_states),
        "n_deltas": len(ctx.deltas),
        "final_graph_hash": state_hash(ctx.graph_states[-1]) if ctx.graph_states else "",
        "replay_all_ok": replay_ok,
        "n_replay_steps": len(checks),
        "integrity_ok": report.ok,
        "n_integrity_issues": len(report.issues),
        "grounding": binding_dict["grounding_summary"],
        "provenance_graph_nodes": pgraph_dict["n_nodes"],
        "provenance_graph_edges": pgraph_dict["n_edges"],
        "provenance_png_rendered": png_ok,
        "n_warnings": len(ctx.warnings),
        "warnings": ctx.warnings,
    }
    with open(os.path.join(ctx.out_dir, f"{ctx.task_id}_summary.json"),
              "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def _state_before_first_delta(ctx: "TaskContext", initial: Dict[str, Any]) -> Dict[str, Any]:
    """The first recorded state already includes the first delta, so the replay
    origin is the empty graph the BeliefGraph started from."""
    return {"nodes": {}, "edges": [], "start_signal_id": None}


def _count(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        out[r.get(key, "")] = out.get(r.get(key, ""), 0) + 1
    return out


# ------------------------------------------------------------------- S4B §7-§8
# The Audit-Triggered Grounding Guard. One LLM call at most, between parsing a
# graph-update proposal and committing it.
#
# The layering is deliberate: `grounding_guard` holds the rule and is pure, and
# everything with a side effect — the retry call, the ledger entry, the action
# log — lives here. §8's decision can therefore be tested without a model, and
# §9.2's positive control exercises the real rule rather than a copy of it.
def _guard_log_path(ctx) -> str:
    return os.path.join(ctx.out_dir, "guard_actions.jsonl")


def _guard_record(ctx, record: Dict[str, Any]) -> None:
    """§8.1's GUARD_TRIGGER / GUARD_RETRY provenance.

    Written to its own file rather than as a new event type: `EVENT_TYPES` is
    frozen and the hash chain is built over it, so extending the vocabulary
    would change every artifact's integrity story. The guard is an intervention
    on a proposal, not a step in the reasoning, and giving it its own ledger
    keeps it out of the trace it is judging.
    """
    record = dict(record)
    record["task_id"] = ctx.task_id
    record["step_id"] = ctx.step_id
    record["timestamp"] = utc_now()
    with open(_guard_log_path(ctx), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _guard_inputs(ctx):
    """The three things `classify_tier` needs, taken from the live context.

    Assembled the same way `evidence_auditor.audit_task` assembles them from
    the artifact files, so the pre-commit classifier and the post-hoc audit
    cannot see different worlds.
    """
    records = list(ctx.bindings.records)
    analyses = [{"event_id": e.get("event_id", ""),
                 "analysis": (e.get("payload") or {}).get("analysis", "")}
                for e in ctx.events if e.get("event_type") == "ANALYSIS"]
    inputs = []
    for b in ctx.bindings.bindings.values():
        if b.source_class == "INPUT_OBSERVATION" or b.node_type == "Signal":
            inputs.append(b.label)
    return records, analyses, "\n".join(inputs)


def on_graph_candidate(response_dict: Dict[str, Any],
                       system_prompt: str = "",
                       user_prompt: str = "",
                       llm_args: Optional[Dict[str, Any]] = None
                       ) -> Optional[Dict[str, Any]]:
    """§7: classify a candidate delta, and guard it if the §8 rule fires.

    Returns the proposal to commit, or `None` meaning "commit what you parsed".
    `None` is returned on every path where the guard is off or does not fire,
    so the caller assigns nothing and the object it already holds is the one it
    commits — which is what makes §9.1's byte-identical requirement checkable
    rather than hopeful.
    """
    ctx = _ctx()
    if ctx is None or not tx_config.guard_enabled():
        return None

    from .evolution import grounding_guard as gg

    records, analyses, input_pool = _guard_inputs(ctx)
    cfg = tx_config.load().get("source_classification", {})
    decision = gg.evaluate(response_dict, records, analyses, input_pool, cfg)

    if not decision["trigger"]:
        return None

    _guard_record(ctx, {
        "guard_event": "GUARD_TRIGGER",
        "guard_id": decision["guard_id"],
        "trigger_rule": decision["trigger_rule"],
        "original_candidate_hash": decision["candidate_hash"],
        "offending_nodes": [o["candidate_id"] for o in decision["offenders"]],
        "offending_labels": [o["label"] for o in decision["offenders"]],
        "n_candidate_evidence": decision["n_candidate_evidence"],
    })

    # ---- §8.1: exactly one structured correction, recorded as its own call
    retry_dict = None
    retry_decision = None
    retry_response = ""
    max_retry = tx_config.guard_max_retry()
    if max_retry >= 1:
        prompt = (f"{user_prompt}\n\n--- Your previous graph update ---\n"
                  f"{json.dumps(response_dict, ensure_ascii=False)}\n\n"
                  f"--- Correction required ---\n{gg.RETRY_PROMPT}")
        try:
            from utils.public_function import (
                LLMOutputBudgetError,
                LLMUnsupportedResponseFormatError,
                llm_generate_response,
            )
            # The caller passes its own `llm_args`, so the retry runs on the
            # same model, temperature and token budget as the call it is
            # correcting. Reading them from config instead would be a second
            # source of truth for the same three values.
            args = dict(llm_args or {})
            try:
                from belief.graph_update_schema import (
                    GraphUpdateProposal,
                    strict_json_schema_response_format,
                )
                response_formats = [
                    strict_json_schema_response_format(
                        GraphUpdateProposal, name="graph_update_proposal"
                    ),
                    {"type": "json_object"},
                    None,
                ]
            except ImportError:
                response_formats = [None]

            last_format_error = None
            for response_format in response_formats:
                try:
                    retry_response, _meta = llm_generate_response(
                        user_prompt=prompt,
                        model_path=args.get("model_path", ""),
                        temperature=args.get("temperature", 1.0),
                        max_tokens=args.get("max_tokens", 1024),
                        system_prompt=system_prompt, return_meta=True,
                        stage="GRAPH_UPDATE",
                        response_format=response_format,
                    )
                    break
                except LLMUnsupportedResponseFormatError as exc:
                    last_format_error = exc
            else:
                raise last_format_error or RuntimeError(
                    "no compatible structured-output mode for guard retry")
        except LLMOutputBudgetError:
            # An opt-in capped research run must stop rather than translating
            # a budget breach into an ordinary failed guard retry.
            raise
        except Exception as exc:                      # noqa: BLE001
            retry_response = ""
            _guard_record(ctx, {
                "guard_event": "GUARD_RETRY",
                "guard_id": decision["guard_id"],
                "original_candidate_hash": decision["candidate_hash"],
                "retry_error": f"{type(exc).__name__}: {exc}",
                "retry_prompt_hash": gg.sha_text(prompt),
            })
        else:
            _guard_record(ctx, {
                "guard_event": "GUARD_RETRY",
                "guard_id": decision["guard_id"],
                "original_candidate_hash": decision["candidate_hash"],
                "retry_prompt_hash": gg.sha_text(prompt),
                "retry_response_hash": gg.sha_text(retry_response),
            })
            parsed = _txparse_guard(retry_response)
            # A parseable response with the wrong shape is still a failed
            # retry: §8.2's RETRY_FAILED is about whether a usable revised
            # delta came back, not about whether the JSON decoder succeeded.
            if parsed is not None and not _txguard_schema_ok(parsed):
                _guard_record(ctx, {
                    "guard_event": "GUARD_RETRY_SCHEMA_REJECTED",
                    "guard_id": decision["guard_id"],
                    "original_candidate_hash": decision["candidate_hash"],
                    "retry_response_hash": gg.sha_text(retry_response),
                    "reason": "retry parsed but nodes/edges have the wrong "
                              "shape; treated as RETRY_FAILED",
                })
                parsed = None
            if parsed is not None:
                retry_decision = gg.evaluate(parsed, records, analyses,
                                             input_pool, cfg)
                retry_dict = parsed

    committed, result, dropped = gg.apply_decision(
        response_dict, decision, retry_dict, retry_decision)

    _guard_record(ctx, gg.action_record(
        decision, result, retry_prompt=gg.RETRY_PROMPT,
        retry_response=retry_response, dropped=dropped,
        retry_decision=retry_decision))

    # §14's system-correctness clause: the guard must not introduce a new
    # failure. Downstream indexes `["nodes"]` and `["edges"]` directly, and a
    # retry response that omits one of them would raise there -- a guard that
    # breaks the run it was protecting. A committed proposal is therefore
    # normalised to carry all three keys, and any candidate whose node or edge
    # schema is wrong is rejected here rather than three frames later.
    return _txnormalise_guard(committed)


def _txnormalise_guard(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d)
    out["edit"] = out.get("edit") or []
    out["nodes"] = out.get("nodes") or []
    out["edges"] = out.get("edges") or []
    return out


def _txguard_schema_ok(d: Dict[str, Any]) -> bool:
    """Every node needs id/node_type/label; every edge src/dst/relation."""
    for n in (d.get("nodes") or []):
        if not isinstance(n, dict) or not {"id", "node_type", "label"} <= set(n):
            return False
    for e in (d.get("edges") or []):
        if not isinstance(e, dict) or not {"src", "dst", "relation"} <= set(e):
            return False
    return True


def _txparse_guard(text: str) -> Optional[Dict[str, Any]]:
    """Parse a retry response, or None. §8.2's RETRY_FAILED path.

    Returns None rather than raising, because a malformed retry is an expected
    outcome the guard has a defined response to, not an error.
    """
    try:
        from utils.public_function import parse_json_response
    except Exception:                                 # pragma: no cover
        return None
    try:
        data = parse_json_response(text)
    except Exception:                                 # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    # A proposal with neither list is not a graph update
    if "nodes" not in data and "edges" not in data:
        return None
    return data
