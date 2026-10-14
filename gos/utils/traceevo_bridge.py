"""TraceEvo S1 bridge: lets GoS call provenance hooks without a hard dependency.

Two failure modes are deliberately distinguished:

* the `traceevo` package is not importable  -> every call is a silent no-op,
  so plain GoS still runs exactly as in S0 (this is what keeps
  `traceevo.enabled: false` honest).
* the package IS importable but a hook raises -> the exception propagates.
  The amendment (§5) requires hooks to fail fast during development rather
  than silently changing what the pipeline does.
"""
from __future__ import annotations

from typing import Any

try:  # package may be absent when running the un-instrumented baseline
    from traceevo import hooks as _hooks
except Exception:  # pragma: no cover - exercised by the S0 compatibility run
    _hooks = None


def available() -> bool:
    return _hooks is not None


def emit(hook_name: str, *args: Any, **kwargs: Any) -> Any:
    """Invoke a TraceEvo hook by name. No-op when TraceEvo is unavailable."""
    if _hooks is None:
        return None
    fn = getattr(_hooks, hook_name, None)
    if fn is None:
        raise AttributeError(f"traceevo.hooks has no hook named {hook_name!r}")
    return fn(*args, **kwargs)


def recorder() -> Any:
    """The process-wide LLM response cache, or None. Works with provenance off."""
    if _hooks is None:
        return None
    return _hooks.recorder()


# ------------------------------------------------------------------- S3A §3
# The Model Invocation Ledger. Same no-op discipline as `emit`: absent package
# or disabled ledger means these return "" and nothing is recorded.
def ledger_open(stage: str, prompt_hash: str, model_id: str = "",
                actor_id: str = "") -> str:
    """Open a ledger entry for one LLM call. Returns "" when not recording."""
    if _hooks is None:
        return ""
    return _hooks.on_llm_open(stage, prompt_hash, model_id, actor_id)


def ledger_response(call_id: str, response_text: str,
                    source: str = "live") -> None:
    if _hooks is None or not call_id:
        return
    _hooks.on_llm_response(call_id, response_text, source)


def ledger_parsed(call_id: str, status: str, reason: str = "NONE",
                  emitted_text: str = "") -> None:
    # An empty `call_id` is meaningful, not missing: it asks the ledger to
    # resolve the most recent still-pending call. Guarding on it here silently
    # dropped every parse outcome, which showed up as 469/469 calls `pending`
    # and closed as `failed` at task end.
    if _hooks is None:
        return
    _hooks.on_llm_parsed(call_id, status, reason, emitted_text)


def ledger_parse(raw: Any, emitted: Any = None) -> Any:
    """`parse_json_response(raw)`, with the outcome reported to the ledger.

    Behaviourally identical to the plain call, including on failure: the
    exception is re-raised untouched. The only difference is that the ledger
    learns whether the parser accepted or rejected the response, which is the
    whole point of §3.2's `parse_status`.

    `emitted` is the text the caller will later commit -- a string, or a
    callable taking the parsed object. Recording it here is what makes the
    later event binding exact rather than positional.
    """
    from utils.public_function import parse_json_response
    try:
        obj = parse_json_response(raw)
    except Exception:
        ledger_parsed("", "failed", "PARSER_ERROR")
        raise
    text = ""
    if callable(emitted):
        try:
            text = str(emitted(obj) or "")
        except Exception:
            text = ""
    elif emitted:
        text = str(emitted)
    ledger_parsed("", "accepted", "NONE", text)
    return obj


def ledger_state(target: str) -> None:
    """The response became GoS state without becoming a provenance event."""
    if _hooks is None:
        return
    _hooks.on_llm_state(target)


def ledger_reject(reason: str = "INVALID_SCHEMA") -> None:
    """The parser accepted the JSON but the caller rejected the content.

    This is the A4 case: GoS parsed the substituted decision response fine and
    then discarded the step because its content stage disagreed.
    """
    ledger_parsed("", "discarded", reason)


def request_key(payload: Any) -> str:
    """The recording key for a request payload -- the prompt hash."""
    if _hooks is None:
        return ""
    from traceevo.recording import request_key as _rk
    return _rk(payload)
