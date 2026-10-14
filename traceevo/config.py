"""TraceEvo configuration.

Reads `configs/TraceEvo_Configs.yaml` from the GoS working copy. When the file
is absent, or `traceevo.enabled` is false, every hook becomes a no-op and the
pipeline must behave exactly like the S0 baseline (amendment §5).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import yaml

_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "provenance_only": True,
    "emit_graph_snapshots": True,
    "replay": {"mode": "off", "recording_path": "", "strict": True},
    "source_classification": {
        "verbatim": True,
        "paraphrase": True,
        "paraphrase_min_token_coverage": 0.6,
    },
    "output_dir": "",
    # S3A §3: the Model Invocation Ledger is a second observability channel, so
    # it gets its own switch. §8 compares ledger off against ledger on over the
    # same recording, which is only meaningful if the two are independently
    # selectable.
    "ledger": {"enabled": True},
    # S3B §15: memory must be switchable off, and off must restore plain GoS.
    # `enabled: false` -> no store is opened, `on_plan_prompt` returns its
    # input unchanged, and the plan prompt is byte-identical to GoS's own.
    "memory": {"enabled": False, "store_path": "", "top_k": 2,
               "min_score": 0.5},
    "post_report_audit": {"enabled": False, "max_retries": 1},
    # S4B §9.1: like memory, the guard has its own switch and off must restore
    # GoS exactly. `enabled: false` -> `on_graph_candidate` returns None before
    # touching anything, so the proposal the caller applies is the object it
    # parsed, unchanged.
    "guard": {"enabled": False, "policy_version": "ATGG-v1", "max_retry": 1},
}

_cfg: Optional[Dict[str, Any]] = None
_gos_dir: Optional[str] = None


def find_gos_dir() -> str:
    """Locate the GoS working copy that this package is instrumenting."""
    global _gos_dir
    if _gos_dir:
        return _gos_dir
    env = os.environ.get("TRACEEVO_GOS_DIR")
    if env and os.path.isdir(env):
        _gos_dir = os.path.abspath(env)
        return _gos_dir
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), "gos"),   # TraceEvo/gos
        os.path.dirname(here),                        # traceevo/ sits inside gos/
    ]
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "configs", "Agent_Configs.yaml")):
            _gos_dir = os.path.abspath(cand)
            return _gos_dir
    raise RuntimeError(
        "Cannot locate the GoS working copy. Set TRACEEVO_GOS_DIR or run from "
        "the TraceEvo project root.")


def config_path() -> str:
    return os.path.join(find_gos_dir(), "configs", "TraceEvo_Configs.yaml")


def load(force_reload: bool = False) -> Dict[str, Any]:
    global _cfg
    if _cfg is not None and not force_reload:
        return _cfg
    cfg = dict(_DEFAULTS)
    path = config_path()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        for key, value in (loaded.get("traceevo") or {}).items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                merged = dict(cfg[key])
                merged.update(value)
                cfg[key] = merged
            else:
                cfg[key] = value

    # Environment overrides keep the T1 driver from rewriting the config file
    # between the three comparison runs.
    if os.environ.get("TRACEEVO_ENABLED") is not None:
        cfg["enabled"] = os.environ["TRACEEVO_ENABLED"] == "1"
    if os.environ.get("TRACEEVO_OUTPUT_DIR"):
        cfg["output_dir"] = os.environ["TRACEEVO_OUTPUT_DIR"]
    replay = dict(cfg.get("replay") or {})
    if os.environ.get("TRACEEVO_REPLAY_MODE"):
        replay["mode"] = os.environ["TRACEEVO_REPLAY_MODE"]
    if os.environ.get("TRACEEVO_REPLAY_PATH"):
        replay["recording_path"] = os.environ["TRACEEVO_REPLAY_PATH"]
    if os.environ.get("TRACEEVO_REPLAY_STRICT") is not None:
        # Only the response-substitution case studies turn this off, and only
        # because a divergent prompt there is the *intended* effect: the
        # injected response changes what GoS sends next, so the recording
        # cannot match and the run must call live. T1 keeps strict=true, which
        # is what makes "identical outputs" a proof rather than a hope.
        replay["strict"] = os.environ["TRACEEVO_REPLAY_STRICT"] == "1"
    cfg["replay"] = replay
    ledger = dict(cfg.get("ledger") or {})
    if os.environ.get("TRACEEVO_LEDGER") is not None:
        ledger["enabled"] = os.environ["TRACEEVO_LEDGER"] == "1"
    cfg["ledger"] = ledger
    memory = dict(cfg.get("memory") or {})
    for env_key, key in (("TRACEEVO_MEMORY", "enabled"),
                         ("TRACEEVO_MEMORY_STORE", "store_path"),
                         ("TRACEEVO_MEMORY_TOP_K", "top_k"),
                         ("TRACEEVO_MEMORY_MIN_SCORE", "min_score")):
        if os.environ.get(env_key) is not None:
            memory[key] = os.environ[env_key]
    if isinstance(memory.get("enabled"), str):
        memory["enabled"] = memory["enabled"] == "1"
    cfg["memory"] = memory

    post_audit = dict(cfg.get("post_report_audit") or {})
    if os.environ.get("TRACEEVO_POST_REPORT_AUDIT") is not None:
        post_audit["enabled"] = os.environ["TRACEEVO_POST_REPORT_AUDIT"] == "1"
    if os.environ.get("TRACEEVO_POST_REPORT_AUDIT_RETRIES") is not None:
        post_audit["max_retries"] = int(os.environ["TRACEEVO_POST_REPORT_AUDIT_RETRIES"])
    cfg["post_report_audit"] = post_audit

    guard = dict(cfg.get("guard") or {})
    if os.environ.get("TRACEEVO_GUARD") is not None:
        guard["enabled"] = os.environ["TRACEEVO_GUARD"] == "1"
    if os.environ.get("TRACEEVO_GUARD_MAX_RETRY") is not None:
        guard["max_retry"] = int(os.environ["TRACEEVO_GUARD_MAX_RETRY"])
    cfg["guard"] = guard

    _cfg = cfg
    return cfg


def guard_enabled() -> bool:
    """S4B §9.1. False means GoS runs exactly as it did before S4B."""
    if not enabled():
        return False
    return bool((load().get("guard") or {}).get("enabled", False))


def guard_max_retry() -> int:
    return int((load().get("guard") or {}).get("max_retry", 1))


def guard_policy_version() -> str:
    return str((load().get("guard") or {}).get("policy_version", "ATGG-v1"))


def memory_enabled() -> bool:
    """S3B §15. False means GoS runs exactly as it did before S3B."""
    if not enabled():
        return False
    return bool((load().get("memory") or {}).get("enabled", False))


def memory_top_k() -> int:
    return int((load().get("memory") or {}).get("top_k", 2))


def memory_min_score() -> float:
    return float((load().get("memory") or {}).get("min_score", 0.5))


def post_report_audit_enabled() -> bool:
    return bool((load().get("post_report_audit") or {}).get("enabled", False))


def post_report_audit_retries() -> int:
    return max(0, int((load().get("post_report_audit") or {}).get("max_retries", 1)))


_memory_store = None


def memory_store():
    """The configured store, or None. Loaded once per process."""
    global _memory_store
    if not memory_enabled():
        return None
    if _memory_store is not None:
        return _memory_store
    path = (load().get("memory") or {}).get("store_path") or ""
    if not path:
        return None
    from .evolution.memory_store import MemoryStore
    # The initial central Plan receives only verified-success strategies.
    # Failure memories remain available to explicitly recovery-oriented tools.
    _memory_store = MemoryStore(
        path, enabled=True, initial_plan_partitions=("success",))
    return _memory_store


def ledger_enabled() -> bool:
    """S3A: is the Model Invocation Ledger recording?"""
    if not enabled():
        return False
    return bool((load().get("ledger") or {}).get("enabled", True))


def gos_default_model() -> str:
    """The backbone model the GoS working copy is configured to use.

    Single source of truth: `default_model_path` in configs/LLM_Configs.yaml.
    S2 §1 D1 needs the *name* of the model every event was produced by; it is
    read from here rather than hardcoded so it cannot drift from the model the
    pipeline actually calls.
    """
    path = os.path.join(find_gos_dir(), "configs", "LLM_Configs.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except OSError:
        return ""
    return str(cfg.get("default_model_path") or "")


def enabled() -> bool:
    """True when provenance instrumentation is active."""
    if os.environ.get("TRACEEVO_DISABLED") == "1":
        return False
    return bool(load().get("enabled", False))


def output_dir() -> str:
    cfg = load()
    out = cfg.get("output_dir") or ""
    if not out:
        out = os.path.join(os.path.dirname(find_gos_dir()), "traceevo_runs")
    os.makedirs(out, exist_ok=True)
    return out
