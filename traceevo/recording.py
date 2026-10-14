"""LLM response record/replay (amendment §6 T1).

GoS samples at temperature 0.8, so two runs of *identical* code legitimately
differ. That makes "did instrumentation change behaviour?" unanswerable by
comparing two live runs. Record/replay removes the randomness:

    record  -> run once, persist every (request -> response) pair
    replay  -> serve the recorded responses in order

Replay is **positional**, not content-addressed. That distinction matters:

  * the same prompt can be sent twice and legitimately return different text
    at temperature 0.8. A content-addressed cache would collapse those two
    calls into one entry and silently replay the wrong response -- this
    actually happened during S1 development and broke the run at call 255.
  * therefore each recorded entry is served to the request at the same
    ordinal position, and the request key is *checked* rather than looked up.
    A key mismatch means the prompt or the control flow changed, which is
    exactly the signal T1 is looking for.

With `strict: true` any mismatch raises instead of silently calling the
network, so T1 is a proof rather than a statistical argument.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .audit.event_schema import canonical_json, sha256_hex

MODE_OFF = "off"
MODE_RECORD = "record"
MODE_REPLAY = "replay"
# MODE_EXTEND makes a recording run resumable: requests whose position already
# exists in the recording are served from it (with the usual divergence check),
# and anything past the recorded prefix is called live and appended. A run that
# died on request N therefore restarts at roughly request N instead of from
# scratch, while the transcript stays a single consistent sequence.
MODE_EXTEND = "extend"


def request_key(payload: Dict[str, Any]) -> str:
    """Stable identity for one request. Covers exactly what determines the
    prompt — model, temperature and the full message list — so any change to a
    prompt string produces a different key."""
    material = {
        "model": payload.get("model"),
        "messages": payload.get("messages"),
        "temperature": payload.get("temperature"),
    }
    return sha256_hex(canonical_json(material))


@dataclass
class CachedResponse:
    key: str
    model: str
    response: str
    meta: Dict[str, Any]


class ResponseCache:
    """One recording file per run, shared by all cases."""

    def __init__(self, mode: str, path: str, strict: bool = True,
                 substitutions_path: str = ""):
        self.mode = mode or MODE_OFF
        self.substitutions: List[Dict[str, Any]] = []
        self._sub_used: List[int] = []
        self.substitutions_applied = 0
        self.substitution_log: List[Dict[str, Any]] = []
        # Env var rather than a constructor argument so the T1 driver and the
        # case-study runner can enable substitution without touching main.py.
        substitutions_path = (substitutions_path
                              or os.environ.get("TRACEEVO_SUBSTITUTE", ""))
        if substitutions_path and os.path.exists(substitutions_path):
            with open(substitutions_path, "r", encoding="utf-8") as f:
                spec = json.load(f)
            self.substitutions = list(spec.get("substitutions") or [])
            self._sub_used = [0] * len(self.substitutions)
        self.path = path
        self.strict = strict
        self._lock = threading.Lock()
        self._queue: List[CachedResponse] = []
        self._cursor = 0
        self.hits = 0
        self.misses = 0
        self.recorded = 0
        self.extended = 0
        self._last_was_cached = False
        self._extending = False
        self.served_log_path = f"{path}.served"
        with open(self.served_log_path, "w", encoding="utf-8"):
            pass
        if self.mode in (MODE_REPLAY, MODE_EXTEND):
            self._load()

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(
                f"replay mode requires a recording at {self.path}")
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self._queue.append(CachedResponse(
                    key=row["key"], model=row.get("model", ""),
                    response=row.get("response", ""), meta=row.get("meta") or {}))

    def serves_from_cache(self) -> bool:
        """True when lookups may return a recorded response.

        Callers must use this instead of comparing `mode` to a literal: `extend`
        serves from the cache too, and a `== "replay"` check silently bypassed
        the cache for every request during the first extend-mode run.
        """
        return self.mode in (MODE_REPLAY, MODE_EXTEND)

    def stores_responses(self) -> bool:
        """True when live responses should be persisted."""
        return self.mode in (MODE_RECORD, MODE_EXTEND)

    def _log_served(self, key: str) -> None:
        with open(self.served_log_path, "a", encoding="utf-8") as f:
            f.write(key + "\n")

    def served_keys(self) -> List[str]:
        with open(self.served_log_path, "r", encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]

    # ------------------------------------------------------------------- api
    def lookup(self, payload: Dict[str, Any]) -> Optional[CachedResponse]:
        self._last_was_cached = False
        if self.mode not in (MODE_REPLAY, MODE_EXTEND):
            return None
        key = request_key(payload)

        if self._cursor >= len(self._queue):
            # Past the recorded prefix.
            if self.mode == MODE_EXTEND:
                self.extended += 1
                self._extending = True
                return None          # fall through to a live call
            self.misses += 1
            if self.strict:
                raise KeyError(
                    f"replay underrun: request #{self._cursor} has no recording "
                    f"entry (recording has {len(self._queue)}). Instrumentation "
                    f"issued an extra LLM call.\n"
                    f"  model={payload.get('model')} key={key[:16]}")
            return None

        entry = self._queue[self._cursor]
        if entry.key != key:
            self.misses += 1
            if self.strict:
                raise KeyError(
                    f"replay divergence at request #{self._cursor}: the prompt "
                    f"differs from the recording, so instrumentation changed "
                    f"what GoS sent to the model.\n"
                    f"  recorded key={entry.key[:16]} actual key={key[:16]}\n"
                    f"  model={payload.get('model')} "
                    f"messages={len(payload.get('messages') or [])}")
            return None

        self._cursor += 1
        self.hits += 1
        self._last_was_cached = True
        self._log_served(key)

        # Response substitution (S2 §5/§8.2). Replacing what the *model*
        # returns is the injection surface that leaves GoS untouched: the
        # pipeline consumes the substituted output through its own code path
        # and produces the graph, deltas and downstream prompts itself. Only
        # useful together with MODE_EXTEND, because the divergent downstream
        # prompts no longer match the recording and must be called live.
        sub = self._match_substitution(payload)
        if sub is not None:
            self.substitutions_applied += 1
            # The call index is what the case-study comparison needs: calls
            # before it are byte-identical to the clean run, calls after it are
            # the ones whose prompts may legitimately diverge. Recorded to a
            # sidecar because `report_recorder` never runs on a crashed task
            # (which is exactly the A3 case study).
            self.substitution_log.append({
                "call_index": self._cursor - 1,
                "next_call_index": self._cursor,
                "recorded_key": entry.key,
                "response": sub,
            })
            self._flush_substitution_log()
            return CachedResponse(key=entry.key, model=entry.model,
                                  response=sub, meta=dict(entry.meta or {},
                                                          substituted=True))
        return entry

    def _flush_substitution_log(self) -> None:
        path = self.path + ".substitutions.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"substitutions": self.substitution_log,
                           "n_recorded_entries": len(self._queue)}, f,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _match_substitution(self, payload: Dict[str, Any]) -> Optional[str]:
        """First unspent rule whose `prompt_contains` all appear in the prompt."""
        if not self.substitutions:
            return None
        blob = canonical_json(payload.get("messages") or [])
        for i, rule in enumerate(self.substitutions):
            if self._sub_used[i] >= int(rule.get("occurrence", 1)):
                continue
            needles = rule.get("prompt_contains") or []
            if all(n in blob for n in needles):
                self._sub_used[i] += 1
                return rule.get("response", "")
        return None

    def substitution_stats(self) -> Dict[str, Any]:
        return {"loaded": len(self.substitutions),
                "applied": self.substitutions_applied,
                "rules": [{"prompt_contains": r.get("prompt_contains"),
                           "note": r.get("note", ""),
                           "used": self._sub_used[i]}
                          for i, r in enumerate(self.substitutions)]}

    def store(self, payload: Dict[str, Any], response: str,
              meta: Dict[str, Any]) -> None:
        """Append every request in order. No de-duplication: the same prompt
        sent twice is two independent samples and must be replayable twice."""
        if self.mode not in (MODE_RECORD, MODE_EXTEND):
            return
        if self._last_was_cached:
            # Served from the existing recording; nothing new to append.
            self._last_was_cached = False
            return
        key = request_key(payload)
        with self._lock:
            entry = CachedResponse(key=key, model=payload.get("model", ""),
                                   response=response, meta=meta or {})
            self._queue.append(entry)
            # In extend mode the cursor must follow the appended entry: without
            # this, the next request compares itself against the entry we just
            # appended and reports a bogus divergence.
            self._cursor = len(self._queue)
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "key": key,
                    "model": entry.model,
                    "response": entry.response,
                    "meta": entry.meta,
                }, ensure_ascii=False) + "\n")
            self._log_served(key)
            self.recorded += 1

    # --------------------------------------------------------------- control
    def verify_exhausted(self) -> Dict[str, Any]:
        """After a replay run: every recorded entry must have been consumed."""
        ok = self._cursor == len(self._queue)
        return {"ok": ok, "consumed": self._cursor,
                "recorded": len(self._queue),
                "unconsumed": len(self._queue) - self._cursor}

    def stats(self) -> Dict[str, Any]:
        return {"mode": self.mode, "path": self.path,
                "entries_loaded": len(self._queue),
                "hits": self.hits, "misses": self.misses,
                "recorded": self.recorded, "extended": self.extended,
                "cursor": self._cursor,
                "substitutions_applied": self.substitutions_applied}
