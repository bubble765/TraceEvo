import ast
import os
from datetime import datetime
from typing import Optional, Any, Dict, List, Set, Tuple
import math
import pickle
import re
from copy import deepcopy
import json
import time
import signal
import threading
import contextlib
import http.client
import yaml
from dataclasses import dataclass

from utils.traceevo_bridge import (emit as _txemit, recorder as _tx_recorder,
                                   ledger_open as _tx_ledger_open,
                                   ledger_response as _tx_ledger_response,
                                   ledger_parsed as _tx_ledger_parsed,
                                   request_key as _tx_request_key)


# ---------------------------------------------------------------------------
# TraceEvo S1: LLM failure taxonomy
# Retry decisions are made on exception type / HTTP status / gateway error
# type -- never on message text. Permanent failures fail fast.
# ---------------------------------------------------------------------------
class LLMError(RuntimeError):
    """Base class for LLM call failures."""
    retryable = False


class LLMTransientError(LLMError):
    """Server-side or transport failure that may succeed on retry."""
    retryable = True


class LLMPermanentError(LLMError):
    """Request/config failure that will fail identically on every retry."""
    retryable = False


class LLMUnsupportedResponseFormatError(LLMPermanentError):
    """The configured gateway does not support the requested JSON mode."""


class LLMOutputBudgetError(LLMPermanentError):
    """An opt-in capped run exceeded (or omitted) its completion-token usage."""


def _resolve_reasoning_effort(requested: Optional[str]) -> Tuple[Optional[str], bool]:
    """Apply the per-process TraceEvo override without changing shared config."""
    if requested is not None:
        return requested, False
    env_value = os.environ.get("TRACEEVO_LLM_REASONING_EFFORT")
    return (env_value or None), bool(env_value)


def _opt_in_output_cap() -> int:
    try:
        requested = int(os.environ.get("TRACEEVO_MAX_OUTPUT_TOKENS", "2048"))
    except (TypeError, ValueError):
        requested = 2048
    return min(max(requested, 1), 2048)


def _retry_attempts(configured: int, env_effort_active: bool) -> int:
    if not env_effort_active:
        return configured
    try:
        requested = int(os.environ.get("TRACEEVO_LLM_MAX_ATTEMPTS", "1"))
    except (TypeError, ValueError):
        requested = 1
    return min(max(requested, 1), max(configured, 1))


def _enforce_opt_in_completion_budget(meta: Dict[str, Any], model_path: str) -> None:
    """Stop a capped DeepSeek workflow before it can issue its next call."""
    if model_path != "deepseek-v4-flash":
        return
    try:
        completion_tokens = int(meta.get("completion_tokens"))
    except (TypeError, ValueError):
        completion_tokens = 0
    cap = _opt_in_output_cap()
    if completion_tokens <= 0 or completion_tokens > cap:
        raise LLMOutputBudgetError(
            "opt-in DeepSeek completion usage is missing or exceeds the "
            f"{cap}-token experiment budget (reported {completion_tokens}); "
            "stopping before another model call")


# Gateway error types that never succeed on retry (observed on the local
# opencode gateway; see S1_provenance/ for the probe transcript).
PERMANENT_ERROR_TYPES = {
    "autherror", "modelerror", "missingsessionid", "invalid_request_error",
    "invalidrequesterror", "permissionerror", "notfounderror", "badrequesterror",
    "authenticationerror", "contextlengtherror",
}
PERMANENT_HTTP_STATUS = {400, 401, 403, 404, 405, 413, 415, 422}
TRANSIENT_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


# Upstream cost table (CNY per 1k tokens), kept verbatim for the three models
# it knew about. Unpriced models record 0.0 rather than raising NameError.
_PRICES = {
    "gpt-5.1":       (0.00875, 0.07),
    "gpt-3.5-turbo": (0.0035, 0.0105),
    "o3":            (0.014, 0.056),
}


class LLMDeadlineExceeded(LLMTransientError):
    """A request exceeded its total wall-clock budget."""


@contextlib.contextmanager
def _wall_clock_deadline(seconds: float):
    """Hard wall-clock limit for the enclosed block.

    `http.client`'s timeout is per socket operation, not per request: a gateway
    that dribbles bytes can hold a connection open indefinitely while every
    individual recv() returns in time. That happened during S1 -- one request
    sat for 51 minutes with no timeout firing.

    SIGALRM only works on the main thread, so worker threads get no deadline
    (GoS is single-threaded, so this is the main thread in practice).
    """
    if (not seconds or seconds <= 0
            or threading.current_thread() is not threading.main_thread()
            or not hasattr(signal, "SIGALRM")):
        yield
        return

    def _handler(signum, frame):
        raise LLMDeadlineExceeded(
            f"request exceeded the {seconds:.0f}s wall-clock deadline")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _price_for(model_path: str, meta: Dict[str, Any]) -> float:
    prompt_price, completion_price = _PRICES.get(model_path, (0.0, 0.0))
    return (prompt_price * meta.get("prompt_tokens", 0) / 1000
            + completion_price * meta.get("completion_tokens", 0) / 1000)


def classify_api_error(status: int, error_obj: Any) -> LLMError:
    """Map an HTTP status + gateway error object onto the taxonomy."""
    if isinstance(error_obj, dict):
        err_type = str(error_obj.get("type") or "").lower()
        message = str(error_obj.get("message") or error_obj)
    else:
        err_type, message = "", str(error_obj)

    detail = f"HTTP {status} [{err_type or 'unknown'}] {message[:200]}"
    lowered = message.casefold()
    unsupported_markers = (
        "unsupported", "not supported", "does not support", "unknown parameter",
        "unknown field", "invalid parameter", "unrecognized", "not allowed",
        "unavailable", "not available",
        "must be one of",
    )
    explicit_output_format_param = (
        isinstance(error_obj, dict)
        and str(error_obj.get("param") or "").casefold()
        in {"response_format", "json_schema"}
    )
    mentions_output_format = (
        "response_format" in lowered or "json_schema" in lowered
    )
    unsupported_message = any(marker in lowered for marker in unsupported_markers)
    if (status in {400, 422}
            and (explicit_output_format_param
                 or (mentions_output_format and unsupported_message))):
        return LLMUnsupportedResponseFormatError(f"API call error：{detail}")

    if err_type in PERMANENT_ERROR_TYPES:
        return LLMPermanentError(f"API call error：{detail}")
    if status in PERMANENT_HTTP_STATUS:
        return LLMPermanentError(f"API call error：{detail}")
    if status in TRANSIENT_HTTP_STATUS or status >= 500:
        return LLMTransientError(f"API call error：{detail}")
    # Unrecognised status: treat 2xx-with-error-body as transient (the gateway
    # does this for its own failures), everything else as permanent.
    if 200 <= status < 300:
        return LLMTransientError(f"API call error：{detail}")
    return LLMPermanentError(f"API call error：{detail}")


# ---------------------------------------------------------------------------
# TraceEvo S0 patch: LLM transport configuration
# Upstream hardcoded the API key placeholder and api.chatanywhere.tech here.
# ---------------------------------------------------------------------------
_DEFAULT_LLM_CFG: Dict[str, Any] = {
    "transport": {
        "host": "api.chatanywhere.tech",
        "endpoint": "/v1/chat/completions",
        "scheme": "https",
        "timeout": 300,
        "session_header": "",
        "api_key_env": "",
    },
    "default_model_path": "gpt-5.1",
    "send_max_tokens": False,
}

_LLM_CFG: Optional[Dict[str, Any]] = None


def load_llm_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load configs/LLM_Configs.yaml, falling back to upstream defaults."""
    global _LLM_CFG
    if _LLM_CFG is not None and not force_reload:
        return _LLM_CFG
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs", "LLM_Configs.yaml",
    )
    loaded: Dict[str, Any] = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    cfg = json.loads(json.dumps(_DEFAULT_LLM_CFG))  # deep copy
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    _LLM_CFG = cfg
    return cfg


def default_model_path() -> str:
    return load_llm_config()["default_model_path"]


def parse_json_response(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if raw is None:
        raise ValueError("Empty response to parse as JSON")

    text = str(raw).strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S | re.I)
    if fence_match:
        text = fence_match.group(1).strip()
    elif text.lower().startswith("json"):
        text = text[4:].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        original_error = exc
    try:
        candidate = _first_json_candidate(text)
    except json.JSONDecodeError:
        raise original_error
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # An unclosed string may be a truncated clinical label/reasoning field.
    # Closing that quote would turn partial model output into apparently valid
    # content, so reject it and let the bounded model-correction path regenerate.
    if _has_unterminated_string(candidate):
        raise original_error

    # json-repair handles common model-output defects (single quotes, dangling
    # commas, missing delimiters, and incomplete final values). It is an
    # optional runtime dependency so older GoS environments still get the
    # conservative standard-library repair below.
    try:
        from json_repair import repair_json
    except ImportError:
        repair_json = None
    if repair_json is not None:
        try:
            repaired = repair_json(candidate, return_objects=True)
            if isinstance(repaired, (dict, list)):
                return repaired
            if isinstance(repaired, str):
                return json.loads(repaired)
        except Exception:
            pass

    repaired = _remove_trailing_commas(candidate)
    for attempt in (repaired, _close_open_json(repaired)):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            try:
                value = ast.literal_eval(_json_literals_to_python(attempt))
            except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
                continue
            if isinstance(value, (dict, list)):
                return value
    raise original_error


def _first_json_candidate(text: str) -> str:
    """Return the first complete object/array, or the first partial candidate.

    The scanner ignores braces inside quoted strings and does not accidentally
    return a nested object when the outer response is malformed.
    """
    start = None
    stack: List[str] = []
    quote: Optional[str] = None
    escaped = False
    closing = {"{": "}", "[": "]"}
    for index, char in enumerate(text):
        if start is None:
            if char in "{[":
                start = index
                stack.append(closing[char])
            continue
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "{[":
            stack.append(closing[char])
        elif char in "}]":
            if stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    return text[start:index + 1]
    if start is not None:
        return text[start:]
    raise json.JSONDecodeError("No JSON object or array found", text, 0)


def _remove_trailing_commas(text: str) -> str:
    out: List[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            out.append(char)
        elif char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(text) or text[lookahead] not in "]}":
                out.append(char)
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _close_open_json(text: str) -> str:
    stack: List[str] = []
    quote: Optional[str] = None
    escaped = False
    closing = {"{": "}", "[": "]"}
    for char in text:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "{[":
            stack.append(closing[char])
        elif char in "}]" and stack and char == stack[-1]:
            stack.pop()
    # Do not synthesize the end of a text value. Missing container delimiters
    # are recoverable; an unfinished string must be regenerated.
    if quote is not None:
        return text
    suffix = ""
    while stack:
        suffix += stack.pop()
    return text + suffix


def _has_unterminated_string(text: str) -> bool:
    quote: Optional[str] = None
    escaped = False
    for char in text:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
    return quote is not None


def _json_literals_to_python(text: str) -> str:
    """Translate JSON's bare literals for ``ast.literal_eval`` fallback."""
    out: List[str] = []
    quote: Optional[str] = None
    escaped = False
    index = 0
    replacements = {"true": "True", "false": "False", "null": "None"}
    while index < len(text):
        char = text[index]
        if quote is not None:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in ('"', "'"):
            quote = char
            out.append(char)
            index += 1
            continue
        matched = False
        for source, target in replacements.items():
            if text.startswith(source, index):
                before = text[index - 1] if index else " "
                end = index + len(source)
                after = text[end] if end < len(text) else " "
                if not (before.isalnum() or before == "_") \
                        and not (after.isalnum() or after == "_"):
                    out.append(target)
                    index = end
                    matched = True
                    break
        if not matched:
            out.append(char)
            index += 1
    return "".join(out)





@dataclass
class UsageStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    price_rmb: float = 0.0

    def update(self, meta: Dict[str, Any], price: float) -> None:
        self.calls += 1
        self.prompt_tokens += meta.get("prompt_tokens", 0)
        self.completion_tokens += meta.get("completion_tokens", 0)
        self.total_tokens += meta.get("total_tokens", 0)
        self.price_rmb += price


_usage_stats = UsageStats()


def _record_usage(meta: Dict[str, Any], price: float) -> None:
    _usage_stats.update(meta, price)
    print(
        "[LLM-API] Generation Completed | "
        f"Model：{meta.get('model', '')} | "
        f"Input token nums：{meta.get('prompt_tokens', 0)} | "
        f"Output token nums：{meta.get('completion_tokens', 0)} | "
        f"price(CNY)：{price:.8f}"
    )


def print_usage_summary() -> None:
    print(
        "[LLM-API] Total | "
        f"Invoke times：{_usage_stats.calls} | "
        f"Input token nums：{_usage_stats.prompt_tokens} | "
        f"Output token nums：{_usage_stats.completion_tokens} | "
        f"Total token nums：{_usage_stats.total_tokens} | "
        f"price(CNY)：{_usage_stats.price_rmb:.8f}"
    )

def get_usage_summary() -> None:
    return (
        "[LLM-API] Total | "
        f"Invoke times：{_usage_stats.calls} | "
        f"Input token nums：{_usage_stats.prompt_tokens} | "
        f"Output token nums：{_usage_stats.completion_tokens} | "
        f"Total token nums：{_usage_stats.total_tokens} | "
        f"price(CNY)：{_usage_stats.price_rmb:.8f}"
    )



def llm_generate_response(
    user_prompt: str,
    model_path: str,  
    temperature: float = 1.0,
    max_tokens: int = 1024,
    system_prompt: Optional[str] = "",
    return_meta: bool = False,
    api_base: Optional[str] = None,
    api_endpoint: Optional[str] = None,
    stage: str = "",
    reasoning_effort: Optional[str] = None,
    response_format: Optional[Dict[str, Any]] = None,
) -> Any:

    cfg = load_llm_config()
    transport = cfg["transport"]
    reasoning_effort, env_effort_active = _resolve_reasoning_effort(
        reasoning_effort)
    api_base = api_base or transport["host"]
    api_endpoint = api_endpoint or transport["endpoint"]

    api_key_env = transport.get("api_key_env") or ""
    api_key = os.environ.get(api_key_env, "") if api_key_env else ""
    if not api_key:
        # Fall back to the upstream placeholder so the failure mode is identical
        # when no key is configured; an unauthenticated endpoint will reject it.
        api_key = "Your-API-Key"
    if not model_path:
        raise ValueError("model_path（Model name）Must not be empty！like'gpt-3.5-turbo'")

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    messages.append({"role": "user", "content": user_prompt.strip()})

    payload_dict = {
        "model": model_path,
        "messages": messages,
        "temperature": temperature,
        "stream": False
    }
    if reasoning_effort is not None:
        if reasoning_effort not in {"none", "low", "high", "max"}:
            raise ValueError("reasoning_effort must be one of none/low/high/max")
        payload_dict["reasoning_effort"] = reasoning_effort
    if cfg.get("send_max_tokens"):
        payload_dict["max_tokens"] = max_tokens
    if response_format is not None:
        payload_dict["response_format"] = response_format

    # TraceEvo S3A §3: open the invocation-ledger entry *before* anything can
    # parse or discard this response. Observational only -- the call id is
    # carried to the caller through the payload's side channel, never through
    # the returned text, so GoS's own behaviour cannot depend on it.
    _tx_call_id = _tx_ledger_open(stage, _tx_request_key(payload_dict),
                                  model_path)

    # TraceEvo S1: LLM record/replay (amendment §6 T1). In replay mode a served
    # response never touches the network, so a run is bit-reproducible and any
    # instrumentation-induced change to a prompt surfaces as a cache miss.
    _tx_rec = _tx_recorder()
    if _tx_rec is not None and _tx_rec.serves_from_cache():
        _cached = _tx_rec.lookup(payload_dict)
        if _cached is not None:
            _tx_ledger_response(_tx_call_id, _cached.response, "replay")
            _cached_meta = dict(_cached.meta)
            _cached_meta.update({"model": model_path, "temperature": temperature,
                                 "max_tokens": max_tokens, "response": _cached.response,
                                 "replayed": True})
            _record_usage(_cached_meta, _price_for(model_path, _cached_meta))
            if return_meta:
                return _cached.response, _cached_meta
            return _cached.response

    payload = json.dumps(payload_dict)

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.0.3 Safari/605.1.15"
    }
    session_header = transport.get("session_header") or ""
    if session_header:
        headers["x-opencode-session"] = session_header
    if reasoning_effort is not None:
        headers["x-traceevo-reasoning-effort"] = reasoning_effort

    # S1 cleanup: retry on typed transient failures only. S0 matched on message
    # substrings, which also retried permanent failures (bad model name, bad
    # auth, bad params) five times before giving up.
    retry_cfg = cfg.get("retry", {}) or {}
    max_attempts = _retry_attempts(
        int(retry_cfg.get("max_attempts", 1)), env_effort_active)
    base_delay = float(retry_cfg.get("base_delay", 2.0))
    max_delay = float(retry_cfg.get("max_delay", 60.0))

    last_error: Optional[Exception] = None
    response_json: Optional[Dict[str, Any]] = None
    for attempt in range(1, max_attempts + 1):
        response_data = ""
        try:
            with _wall_clock_deadline(transport.get("request_deadline", 0)):
                if transport.get("scheme", "https") == "http":
                    conn = http.client.HTTPConnection(api_base, timeout=transport.get("timeout", 300))
                else:
                    conn = http.client.HTTPSConnection(api_base, timeout=transport.get("timeout", 300))
                conn.request("POST", api_endpoint, payload, headers)

                res = conn.getresponse()
                status = res.status

                response_data = res.read().decode("utf-8")
                conn.close()

            response_json = json.loads(response_data)

            if "error" in response_json:
                raise classify_api_error(status, response_json["error"])

            last_error = None
            break
        except LLMError as e:
            last_error = e
        except json.JSONDecodeError as e:
            # Truncated / non-JSON body: almost always a dropped connection.
            last_error = LLMTransientError(
                f"API response parse error（Not JSON format）：{response_data[:200]}")
            last_error.__cause__ = e
        except (http.client.HTTPException, ConnectionError, TimeoutError, OSError) as e:
            last_error = LLMTransientError(f"API link error：{type(e).__name__}: {e}")
            last_error.__cause__ = e
        except Exception as e:
            last_error = LLMPermanentError(f"API call error：{type(e).__name__}: {e}")
            last_error.__cause__ = e

        if attempt >= max_attempts or not last_error.retryable:
            raise last_error
        delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
        print(f"[LLM-API] attempt {attempt}/{max_attempts} failed "
              f"[{type(last_error).__name__}] ({str(last_error)[:120]}); retrying in {delay:.1f}s")
        time.sleep(delay)

    if last_error is not None:
        raise last_error

    choice = response_json["choices"][0]
    # print("choice:", choice)
    if "message" not in choice or "content" not in choice["message"]:
        print("API return error", response_json)
        generated_text = ""
    else:
        generated_text = choice["message"]["content"].strip()

    usage = response_json.get("usage", {}) or {}
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "model": model_path,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "response": generated_text
    }

    _record_usage(meta, _price_for(model_path, meta))

    _tx_ledger_response(_tx_call_id, generated_text, "live")

    # TraceEvo S1: persist the request/response pair while recording (T1).
    if _tx_rec is not None and _tx_rec.stores_responses():
        _tx_rec.store(payload_dict, generated_text, meta)

    # The local R003 guard experiment opts in through the process environment.
    # Preserve its usage and ledger records first, then fail before the caller
    # can proceed to a subsequent model/tool step if the gateway missed its cap.
    # Explicitly capped repair/scorer scripts perform their own artifact-aware
    # checks so they can persist an oversized response before stopping.
    if env_effort_active:
        _enforce_opt_in_completion_budget(meta, model_path)

    if return_meta:
        return generated_text, meta
    return generated_text




def softmax_normalize(candidates):
    """
    candidates: List[Tuple[str, str|None, str, float]]
    return:     List[Tuple[str, str|None, str, float]] with last value normalized to probabilities
    """

    scores = [c[-1] for c in candidates]
    max_s = max(scores)
    exps = [math.exp(s - max_s) for s in scores]
    total = sum(exps) if exps else 1.0
    probs = [e / total for e in exps]

    out = []
    for (label, comp, why, _), p in zip(candidates, probs):
        out.append((label, comp, why, p))
    return out


def collect_lineage_subgraph(G, node_id: str) -> Dict[str, Any]:
    """
    return format：
    {
      "node_id": { ...full_node_payload, "id": node_id },
      "ancestors_via_refines": [ ...full_node_payload... ],  
      "evidence": {
        "support": [ {"src":..., "dst":..., ...full_edge_payload...}, ... ],
        "refute":  [ {"src":..., "dst":..., ...full_edge_payload...}, ... ]
      },
      "alarm_causal": {
        "nodes": { alarm_id: full_node_payload_with_id, ... },
        "edges": [ {"src":..., "dst":..., ...full_edge_payload...}, ... ]
      },
      "subgraph": {
        "nodes": { node_id: full_node_payload_with_id, ... },
        "edges": [ {"src":..., "dst":..., ...full_edge_payload...}, ... ]
      }
    }
    """


    def _coalesce(*values):
        for val in values:
            if isinstance(val, str):
                val = val.strip()
            if val not in (None, ""):
                return val
        return None

    def compact_alarm_attrs(raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}

        attrs_inner = raw.get("attrs")
        if isinstance(attrs_inner, dict):
            data = attrs_inner.get("data")
            if isinstance(data, dict):
                alarm_data = data
            else:
                alarm_data = attrs_inner
        else:
            alarm_data = {}

        labels = alarm_data.get("labels") if isinstance(alarm_data.get("labels"), dict) else {}

        info = {
            "alarm_id": raw.get("alarm_id"),
            "name": raw.get("name"),
            "message": raw.get("message"),
            "severity": raw.get("severity"),
            "alert_name": _coalesce(alarm_data.get("alertName"), labels.get("alertname"), raw.get("name")),
            "instance": _coalesce(alarm_data.get("instance"), labels.get("instance"), labels.get("hostname"), labels.get("host_id")),
            "metric": _coalesce(alarm_data.get("metricKey"), labels.get("metricKey"), labels.get("metrics_name_en"), labels.get("metrics_name_zh")),
            "value": _coalesce(labels.get("itemvalue"), alarm_data.get("value")),
            "unit": _coalesce(labels.get("metric_unit"), alarm_data.get("unit")),
            "startAt": _coalesce(alarm_data.get("startAt"), alarm_data.get("startsAt"), labels.get("startAt")),
            "triggerAt": _coalesce(alarm_data.get("triggerAt")),
            "description": _coalesce(labels.get("description"), labels.get("description_en"), alarm_data.get("description"), alarm_data.get("descriptionEn")),
        }
        return {k: v for k, v in info.items() if v not in (None, "")}

    def node_payload(nid: str) -> Dict[str, Any]:
        out = deepcopy(G.nodes[nid])   
        out["id"] = nid
        if out.get("type") == "Alarm":
            out["attrs"] = compact_alarm_attrs(out.get("attrs") or {})
        return out

    def edge_payload(src: str, dst: str) -> Dict[str, Any]:
        e = deepcopy(G.edges[(src, dst)])  
        e["src"] = src
        e["dst"] = dst
        return e

    def parents_of_refines(cur: str) -> List[str]:
        out = []
        for (src, dst), e in G.edges.items():
            if dst == cur and e.get("type") == "refines":
                out.append(src)
        return out

    lineage_nodes: Set[str] = {node_id}
    lineage_edges_refines: List[Tuple[str, str]] = []
    visited: Set[str] = set()
    stack: List[str] = [node_id]

    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)

        for p in parents_of_refines(cur):
            lineage_nodes.add(p)
            lineage_edges_refines.append((p, cur))
            if G.nodes[p].get("type") != "Alarm":
                stack.append(p)

    support_edges, refute_edges = [], []
    evidence_nodes: Set[str] = set()
    for (src, dst), e in G.edges.items():
        if dst in lineage_nodes and e.get("type") in ("support", "refute"):
            rec = edge_payload(src, dst)
            evidence_nodes.add(src)
            (support_edges if e.get("type") == "support" else refute_edges).append(rec)


    alarm_nodes = {
        nid for nid in lineage_nodes if G.nodes.get(nid, {}).get("type") == "Alarm"
    }
    alarm_causal_edges: List[Dict[str, Any]] = []
    alarm_causal_node_ids: Set[str] = set()
    if alarm_nodes:
        for (src, dst), e in G.edges.items():
            if e.get("type") != "alarm_causal":
                continue
            if src in alarm_nodes or dst in alarm_nodes:
                alarm_causal_edges.append(edge_payload(src, dst))
                alarm_causal_node_ids.add(src)
                alarm_causal_node_ids.add(dst)

    alarm_causal_nodes = {
        nid: node_payload(nid)
        for nid in alarm_causal_node_ids
    }


    sub_nodes: Dict[str, Dict[str, Any]] = {}
    for nid in lineage_nodes.union(evidence_nodes):
        sub_nodes[nid] = node_payload(nid)

    sub_edges: List[Dict[str, Any]] = []

    for src, dst in lineage_edges_refines:
        sub_edges.append(edge_payload(src, dst))

    sub_edges.extend(support_edges)
    sub_edges.extend(refute_edges)


    lineage_json = {
        "node_id": node_payload(node_id),
        "ancestors_via_refines": [
            node_payload(nid) for nid in lineage_nodes if nid != node_id
        ],
        "evidence": {
            "support": support_edges,
            "refute": refute_edges
        },
        "alarm_causal": {
            "nodes": alarm_causal_nodes,
            "edges": alarm_causal_edges,
        },
        "subgraph": {
            "nodes": sub_nodes,
            "edges": sub_edges
        }
    }
    return lineage_json


def lineage_to_json_str(lineage: Dict[str, Any]) -> str:

    return json.dumps(lineage, ensure_ascii=False)


def save_pkl(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        pickle.dump(data, f)

def load_pkl(path):
    with open(path, 'rb') as f:
        return pickle.load(f)

def log_to_file(log_msg: str, log_path: str = "project_log.txt"):
    try:
        
        log_dir = os.path.dirname(log_path)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_log = f"[{time_str}] | {log_msg.strip()}\n"
        
        with open(log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(final_log)
    
    except Exception as e:
        print(f"❌ Log write failure：{str(e)}")
    


def evidence_retriever(query: str, evidence_texts) -> str:

    system_prompt = """
You are a precise evidence retriever with only one core task: 
First understand the information need behind the input query (whether it's a keyword or a complete sentence), then extract all relevant text fragments from the given evidence texts that answer or relate to this need, and return them exactly as they appear (no rephrasing, summarization, or additional explanation).

### Rules:
1. Output ONLY the matched evidence text(s) (concatenate multiple fragments with newline if needed) – no other words, punctuation, or comments;
2. If NO relevant content is found in the evidence texts for the query's information need, return an empty string ("");
3. Strictly preserve the original wording of the matched evidence texts (do not modify any characters);
4. Return all matching content (do not omit any relevant fragments);
5. Focus on the core information need of the query, not just literal keyword matching (e.g., "query about brain CT diagnosis" should match fragments with brain CT diagnostic results).

### Examples:
Example 1 (Query: Multiple matching fragments):
- Evidence texts: ["Sarah was at library 18:00-20:00", "Library is 10min from classroom", "David lost watch at 19:00"]
- Input query: "library"
- Output: "Sarah was at library 18:00-20:00\nLibrary is 10min from classroom"""

    user_prompt = f"""
Here are the evidence texts: {evidence_texts}.\n
Here is the input query: {query}.
"""
    # S0 patch: upstream hardcoded "gpt-5.1" here.
    response, meta = llm_generate_response(user_prompt=user_prompt, model_path=default_model_path(), temperature=0, max_tokens=2048, system_prompt=system_prompt, return_meta=True, stage="EVIDENCE_RETRIEVAL")

    # S3A: the retrieval response is plain text, not JSON, so there is no
    # parser to report to -- it is accepted as-is by definition. Without this
    # the call would sit `pending` and be closed as `failed` at task end.
    _tx_ledger_parsed("", "accepted", "NONE", response)

    # TraceEvo S1 hook: this is the only place a real external record enters
    # the pipeline, so it owns the stable record_id (amendment §4.5).
    _txemit("on_evidence_read", query, response)

    return response














import json
import os

def generate_causal_graph_html(graph_list, html_path):
    os.makedirs(os.path.dirname(html_path), exist_ok=True)
    
    # 
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Causal Graph Visualization</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Font Awesome -->
    <link href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css" rel="stylesheet">
    <!-- D3.js -->
    <script src="https://d3js.org/d3.v7.min.js"></script>
    
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        primary: '#3b82f6',
                        secondary: '#64748b',
                        signal: '#ef4444',
                        evidence: '#22c55e',
                        hypothesis: '#f97316',
                        background: '#f8fafc'
                    },
                    fontFamily: {
                        sans: ['Inter', 'system-ui', 'sans-serif']
                    }
                }
            }
        }
    </script>
    
    <style type="text/tailwindcss">
        @layer utilities {
            .node-card {
                @apply p-3 rounded-lg shadow-md transition-all duration-300 cursor-pointer;
            }
            .node-card:hover {
                @apply shadow-lg transform -translate-y-1;
            }
            .signal-node {
                @apply bg-red-100 border-l-4 border-signal;
            }
            .evidence-node {
                @apply bg-green-100 border-l-4 border-evidence;
            }
            .hypothesis-node {
                @apply bg-orange-100 border-l-4 border-hypothesis;
            }
            .nav-button {
                @apply px-4 py-2 rounded-md bg-primary text-white hover:bg-blue-600 transition-colors duration-200;
            }
            .nav-button:disabled {
                @apply bg-gray-300 cursor-not-allowed;
            }
            .tooltip {
                @apply absolute bg-white p-2 rounded shadow-lg z-50 max-w-xs;
                pointer-events: none;
                opacity: 0;
                transition: opacity 0.2s;
            }
        }
    </style>
</head>
<body class="bg-background min-h-screen">
    <div class="container mx-auto px-4 py-8">
        <header class="mb-6">
            <h1 class="text-3xl font-bold text-gray-800">Causal graph</h1>
            <p class="text-gray-600">Causal graph</p>
        </header>

        <div class="flex flex-col lg:flex-row gap-6">

            <div class="lg:w-1/4 bg-white p-4 rounded-lg shadow-md">
                <h2 class="text-xl font-semibold mb-4 text-gray-700">Navi</h2>
                <div class="flex justify-between items-center mb-6">
                    <button id="prevBtn" class="nav-button">
                        <i class="fa fa-arrow-left mr-2"></i>Before
                    </button>
                    <span id="counter" class="text-lg font-medium text-gray-700">1 / """ + str(len(graph_list)) + """</span>
                    <button id="nextBtn" class="nav-button">
                        Next<i class="fa fa-arrow-right ml-2"></i>
                    </button>
                </div>
                
                <div class="mb-6">
                    <h3 class="text-lg font-medium mb-2 text-gray-700">Legend</h3>
                    <div class="space-y-2">
                        <div class="flex items-center">
                            <div class="w-4 h-4 rounded-full bg-signal mr-2"></div>
                            <span>Signal (Signal)</span>
                        </div>
                        <div class="flex items-center">
                            <div class="w-4 h-4 rounded-full bg-evidence mr-2"></div>
                            <span>Evidence (Evidence)</span>
                        </div>
                        <div class="flex items-center">
                            <div class="w-4 h-4 rounded-full bg-hypothesis mr-2"></div>
                            <span>Hypothesis (Hypothesis)</span>
                        </div>
                    </div>
                </div>
                
                <div>
                    <h3 class="text-lg font-medium mb-2 text-gray-700">statis</h3>
                    <div id="nodeStats" class="text-sm text-gray-600">
                    </div>
                </div>
            </div>
            
            <div class="lg:w-3/4 bg-white p-4 rounded-lg shadow-md">
                <div class="flex justify-between items-center mb-4">
                    <h2 id="graphTitle" class="text-xl font-semibold text-gray-700">Causal graph 1</h2>
                    <div>
                        <button id="zoomInBtn" class="px-3 py-1 rounded bg-gray-200 hover:bg-gray-300 mr-2">
                            <i class="fa fa-search-plus"></i>
                        </button>
                        <button id="zoomOutBtn" class="px-3 py-1 rounded bg-gray-200 hover:bg-gray-300 mr-2">
                            <i class="fa fa-search-minus"></i>
                        </button>
                        <button id="resetZoomBtn" class="px-3 py-1 rounded bg-gray-200 hover:bg-gray-300">
                            <i class="fa fa-refresh"></i>
                        </button>
                    </div>
                </div>
                

                <div id="graphContainer" class="border border-gray-200 rounded-lg h-[600px] overflow-hidden">
   
                </div>
                
       
                <div id="nodeDetails" class="mt-4 p-4 border border-gray-200 rounded-lg hidden">
                    <h3 class="text-lg font-medium mb-2 text-gray-700">Node</h3>
                    <div id="nodeDetailsContent">
                      
                    </div>
                </div>
            </div>
        </div>
    </div>
    

    <div id="tooltip" class="tooltip"></div>
    
    <script>
       
        const graphData = """ + json.dumps(graph_list) + """;
        let currentGraphIndex = 0;
        let svg, g, simulation, link, node;
        let width, height;
        let zoom;
        
       
        document.addEventListener('DOMContentLoaded', function() {
            initGraph();
            updateNavigation();
            updateNodeStats();
            setupEventListeners();
        });
        
       
        function initGraph() {
            const container = document.getElementById('graphContainer');
            width = container.clientWidth;
            height = container.clientHeight;
            
         
            svg = d3.select('#graphContainer')
                .append('svg')
                .attr('width', width)
                .attr('height', height);
            
      
            zoom = d3.zoom()
                .scaleExtent([0.1, 4])
                .on('zoom', function(event) {
                    g.attr('transform', event.transform);
                });
            
            svg.call(zoom);
            
      
            g = svg.append('g');
            
         
            loadGraph(currentGraphIndex);
        }
        
      
        function loadGraph(index) {
            const graph = graphData[index];
            
       
            document.getElementById('graphTitle').textContent = 'Causal graph ' + (index + 1);
            
      
            g.selectAll('*').remove();
            
       
            const nodes = [];
            for (const nodeId in graph.nodes) {
                if (graph.nodes.hasOwnProperty(nodeId)) {
                    nodes.push({
                        id: nodeId,
                        ...graph.nodes[nodeId]
                    });
                }
            }
            
 
            let links = (graph.edges || []).map(function(e) {
                return {
                    source: e.source || e.src,
                    target: e.target || e.dst,
                    type: e.type,
                    attrs: e.attrs || {}
                };
            });
            
           
            links = links.filter(function(link) {
                const sourceExists = nodes.some(function(n) { return n.id === link.source; });
                const targetExists = nodes.some(function(n) { return n.id === link.target; });
                
                if (!sourceExists) {
                    console.warn('Source node not found:', link.source);
                }
                if (!targetExists) {
                    console.warn('Target node not found:', link.target);
                }
                
                return sourceExists && targetExists;
            });
            
         
            simulation = d3.forceSimulation(nodes)
                .force('link', d3.forceLink(links).id(function(d) { return d.id; }).distance(100))
                .force('charge', d3.forceManyBody().strength(-300))
                .force('center', d3.forceCenter(width / 2, height / 2))
                .force('collision', d3.forceCollide().radius(70));
            
  
            link = g.append('g')
                .attr('class', 'links')
                .selectAll('line')
                .data(links)
                .enter()
                .append('line')
                .attr('stroke-width', 2)
                .attr('stroke', function(d) { return getLinkColor(d.type); })
                .attr('stroke-opacity', 0.6);
            
  
            const nodeGroups = g.append('g')
                .attr('class', 'nodes')
                .selectAll('.node-group')
                .data(nodes)
                .enter()
                .append('g')
                .attr('class', 'node-group')
                .call(d3.drag()
                    .on('start', dragstarted)
                    .on('drag', dragged)
                    .on('end', dragended));
            
    
            nodeGroups.append('circle')
                .attr('r', 20)
                .attr('fill', function(d) { return getNodeColor(d.type); })
                .attr('stroke', '#fff')
                .attr('stroke-width', 2);
            
        
            nodeGroups.append('text')
                .attr('text-anchor', 'middle')
                .attr('dominant-baseline', 'central')
                .attr('fill', '#fff')
                .attr('font-size', '12px')
                .text(function(d) { return getNodeIcon(d.type); });
     
            nodeGroups.append('text')
                .attr('dy', 40)
                .attr('text-anchor', 'middle')
                .attr('fill', '#333')
                .attr('font-size', '12px')
                .text(function(d) { return truncateText(d.label, 20); });
            

            nodeGroups.on('click', showNodeDetails);
            
  
            nodeGroups.on('mouseover', function(event, d) {
                const tooltip = d3.select('#tooltip');
                tooltip.transition()
                    .duration(200)
                    .style('opacity', .9);
                tooltip.html(
                    '<div class="font-medium">' + d.type + ': ' + truncateText(d.label, 50) + '</div>' +
                    (d.score !== undefined ? '<div>Score: ' + d.score + '</div>' : '')
                )
                    .style('left', (event.pageX + 10) + 'px')
                    .style('top', (event.pageY - 28) + 'px');
            })
            .on('mouseout', function() {
                d3.select('#tooltip').transition()
                    .duration(500)
                    .style('opacity', 0);
            });
            
     
            simulation.on('tick', function() {
                link
                    .attr('x1', function(d) { return d.source.x; })
                    .attr('y1', function(d) { return d.source.y; })
                    .attr('x2', function(d) { return d.target.x; })
                    .attr('y2', function(d) { return d.target.y; });
                
                nodeGroups
                    .attr('transform', function(d) { return 'translate(' + d.x + ',' + d.y + ')'; });
            });
        }
        
 
        function getLinkColor(type) {
            switch(type) {
                case 'support': return '#22c55e'; 
                case 'refute': return '#ef4444'; 
                default: return '#999';
            }
        }

        function getNodeColor(type) {
            switch(type) {
                case 'Signal': return '#ef4444';
                case 'Evidence': return '#22c55e'; 
                case 'Hypothesis': return '#f97316'; 
                default: return '#64748b'; 
            }
        }
        

        function getNodeIcon(type) {
            switch(type) {
                case 'Signal': return 'S';
                case 'Evidence': return 'E';
                case 'Hypothesis': return 'H';
                default: return '?';
            }
        }
        

        function truncateText(text, maxLength) {
            if (!text) return '';
            return text.length > maxLength ? text.substring(0, maxLength) + '...' : text;
        }
        

        function showNodeDetails(event, d) {
            const detailsPanel = document.getElementById('nodeDetails');
            const detailsContent = document.getElementById('nodeDetailsContent');
            
       
            let html = '<div class="mb-2"><span class="font-medium">Type:</span><span class="px-2 py-1 rounded text-white" style="background-color: ' + getNodeColor(d.type) + '">' + d.type + '</span></div>';
            html += '<div class="mb-2"><span class="font-medium"Label:</span><p class="mt-1">' + d.label + '</p></div>';
            
          
            if (d.score !== undefined) {
                html += '<div class="mb-2"><span class="font-medium">Confidence:</span><span>' + d.score + '</span></div>';
            }
            
           
            if (d.attrs && Object.keys(d.attrs).length > 0) {
                html += '<div class="mb-2"><span class="font-medium">Attributes:</span><ul class="mt-1">';
                
                for (const [key, value] of Object.entries(d.attrs)) {
                    html += '<li><span class="font-medium">' + key + ':</span> ' + value + '</li>';
                }
                
                html += '</ul></div>';
            }
            
            detailsContent.innerHTML = html;
            detailsPanel.classList.remove('hidden');
        }
        

        function updateNavigation() {
            const prevBtn = document.getElementById('prevBtn');
            const nextBtn = document.getElementById('nextBtn');
            const counter = document.getElementById('counter');
            
            prevBtn.disabled = currentGraphIndex === 0;
            nextBtn.disabled = currentGraphIndex === graphData.length - 1;
            counter.textContent = (currentGraphIndex + 1) + ' / ' + graphData.length;
        }
        

        function updateNodeStats() {
            const graph = graphData[currentGraphIndex];
            const nodes = graph.nodes;
            
            let signalCount = 0;
            let evidenceCount = 0;
            let hypothesisCount = 0;
            
            for (const nodeId in nodes) {
                const node = nodes[nodeId];
                switch(node.type) {
                    case 'Signal': signalCount++; break;
                    case 'Evidence': evidenceCount++; break;
                    case 'Hypothesis': hypothesisCount++; break;
                }
            }
            
            const statsHtml = '<div class="mb-1">Total nodes: ' + Object.keys(nodes).length + '</div>' +
                             '<div class="mb-1">Signal Node: ' + signalCount + '</div>' +
                             '<div class="mb-1">Evidence Node: ' + evidenceCount + '</div>' +
                             '<div>Hypothesis Node: ' + hypothesisCount + '</div>';
            
            document.getElementById('nodeStats').innerHTML = statsHtml;
        }
        
        function setupEventListeners() {

            document.getElementById('prevBtn').addEventListener('click', function() {
                if (currentGraphIndex > 0) {
                    currentGraphIndex--;
                    loadGraph(currentGraphIndex);
                    updateNavigation();
                    updateNodeStats();
                    document.getElementById('nodeDetails').classList.add('hidden');
                }
            });
            
            document.getElementById('nextBtn').addEventListener('click', function() {
                if (currentGraphIndex < graphData.length - 1) {
                    currentGraphIndex++;
                    loadGraph(currentGraphIndex);
                    updateNavigation();
                    updateNodeStats();
                    document.getElementById('nodeDetails').classList.add('hidden');
                }
            });
            
  
            document.getElementById('zoomInBtn').addEventListener('click', function() {
                svg.transition().call(zoom.scaleBy, 1.3);
            });
            
            document.getElementById('zoomOutBtn').addEventListener('click', function() {
                svg.transition().call(zoom.scaleBy, 0.7);
            });
            
            document.getElementById('resetZoomBtn').addEventListener('click', function() {
                svg.transition().call(zoom.transform, d3.zoomIdentity);
            });
       
            window.addEventListener('resize', function() {
                const container = document.getElementById('graphContainer');
                width = container.clientWidth;
                height = container.clientHeight;
                
                svg.attr('width', width).attr('height', height);
                simulation.force('center', d3.forceCenter(width / 2, height / 2));
                simulation.alpha(0.3).restart();
            });
        }
        
  
        function dragstarted(event) {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            event.subject.fx = event.subject.x;
            event.subject.fy = event.subject.y;
        }
        
        function dragged(event) {
            event.subject.fx = event.x;
            event.subject.fy = event.y;
        }
        
        function dragended(event) {
            if (!event.active) simulation.alphaTarget(0);
            event.subject.fx = null;
            event.subject.fy = null;
        }
    </script>
</body>
</html>"""
    

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
   
