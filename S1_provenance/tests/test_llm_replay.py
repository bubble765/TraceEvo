"""T1 (unit part): record/replay must reproduce a run exactly.

Regression cover for the bug that broke the first S1 T1 attempt: the recorder
was content-addressed, so when the same prompt was sent twice (which happens
naturally at temperature 0.8) the second, different response was discarded and
the replay diverged at call 255 of case 8.
"""
import json
import os

import pytest

from traceevo.recording import MODE_RECORD, MODE_REPLAY, ResponseCache, request_key


def _payload(text, model="m", temperature=0.8):
    return {"model": model, "temperature": temperature,
            "messages": [{"role": "user", "content": text}]}


def test_same_prompt_records_and_replays_two_different_responses(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    p = _payload("step")

    rec = ResponseCache(MODE_RECORD, path)
    rec.store(p, "first answer", {"prompt_tokens": 10})
    rec.store(p, "second answer", {"prompt_tokens": 10})   # same prompt, new sample
    assert rec.recorded == 2, "duplicate prompts must not be collapsed"

    rep = ResponseCache(MODE_REPLAY, path)
    assert rep.lookup(p).response == "first answer"
    assert rep.lookup(p).response == "second answer"
    assert rep.verify_exhausted()["ok"]


def test_replay_detects_a_changed_prompt(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    rec = ResponseCache(MODE_RECORD, path)
    rec.store(_payload("original prompt"), "answer", {})

    rep = ResponseCache(MODE_REPLAY, path)
    with pytest.raises(KeyError) as exc:
        rep.lookup(_payload("a different prompt"))
    assert "divergence" in str(exc.value)


def test_replay_detects_an_extra_call(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    rec = ResponseCache(MODE_RECORD, path)
    rec.store(_payload("only one"), "answer", {})

    rep = ResponseCache(MODE_REPLAY, path)
    rep.lookup(_payload("only one"))
    with pytest.raises(KeyError) as exc:
        rep.lookup(_payload("only one"))          # one request too many
    assert "underrun" in str(exc.value)


def test_exhaustion_check_flags_an_unconsumed_tail(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    rec = ResponseCache(MODE_RECORD, path)
    rec.store(_payload("a"), "1", {})
    rec.store(_payload("b"), "2", {})
    rec.store(_payload("c"), "3", {})

    rep = ResponseCache(MODE_REPLAY, path)
    rep.lookup(_payload("a"))
    check = rep.verify_exhausted()
    assert not check["ok"] and check["unconsumed"] == 2


def test_off_mode_is_inert(tmp_path):
    cache = ResponseCache("off", str(tmp_path / "rec.jsonl"))
    assert cache.lookup(_payload("x")) is None
    cache.store(_payload("x"), "y", {})
    assert cache.recorded == 0
    assert not os.path.exists(str(tmp_path / "rec.jsonl"))


def test_key_covers_model_and_temperature():
    a = request_key(_payload("p", model="m1", temperature=0.8))
    b = request_key(_payload("p", model="m2", temperature=0.8))
    c = request_key(_payload("p", model="m1", temperature=0.0))
    assert len({a, b, c}) == 3


def test_extend_serves_the_recorded_prefix_without_reappending(tmp_path):
    """A run interrupted by a hung request must resume, not start over."""
    from traceevo.recording import MODE_EXTEND
    path = str(tmp_path / "rec.jsonl")
    rec = ResponseCache(MODE_RECORD, path)
    rec.store(_payload("a"), "ans-a", {})
    rec.store(_payload("b"), "ans-b", {})

    ext = ResponseCache(MODE_EXTEND, path)
    assert ext.lookup(_payload("a")).response == "ans-a"
    ext.store(_payload("a"), "ans-a", {})          # must NOT duplicate
    assert ext.lookup(_payload("b")).response == "ans-b"
    ext.store(_payload("b"), "ans-b", {})
    # Past the prefix: no cached entry, caller does a live call.
    assert ext.lookup(_payload("c")) is None
    ext.store(_payload("c"), "ans-c", {})
    # Regression: a live call appended in extend mode must advance the cursor,
    # otherwise the next request is compared against the entry just appended.
    assert ext.lookup(_payload("d")) is None

    rows = [json.loads(ln) for ln in open(path) if ln.strip()]
    assert [r["response"] for r in rows] == ["ans-a", "ans-b", "ans-c"]


def test_mode_helpers_cover_extend(tmp_path):
    """Regression: the call site compared `mode == "replay"`, so extend mode
    silently bypassed the cache and every request went live."""
    from traceevo.recording import MODE_EXTEND
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")          # replay/extend require the file to exist
    caches = {m: ResponseCache(m, str(tmp_path / f"{m}.jsonl") if m not in
                               (MODE_REPLAY, MODE_EXTEND) else str(empty))
              for m in ("off", MODE_RECORD, MODE_REPLAY, MODE_EXTEND)}
    assert not caches["off"].serves_from_cache()
    assert not caches[MODE_RECORD].serves_from_cache()
    assert caches[MODE_REPLAY].serves_from_cache()
    assert caches[MODE_EXTEND].serves_from_cache()

    assert not caches["off"].stores_responses()
    assert caches[MODE_RECORD].stores_responses()
    assert not caches[MODE_REPLAY].stores_responses()
    assert caches[MODE_EXTEND].stores_responses()


def test_extend_still_rejects_divergence_in_the_recorded_prefix(tmp_path):
    from traceevo.recording import MODE_EXTEND
    path = str(tmp_path / "rec.jsonl")
    ResponseCache(MODE_RECORD, path).store(_payload("a"), "ans-a", {})

    ext = ResponseCache(MODE_EXTEND, path)
    with pytest.raises(KeyError):
        ext.lookup(_payload("something else"))


def test_recording_is_append_only_and_reloadable(tmp_path):
    path = str(tmp_path / "rec.jsonl")
    rec = ResponseCache(MODE_RECORD, path)
    for i in range(4):
        rec.store(_payload(f"q{i}"), f"a{i}", {"prompt_tokens": i})
    rows = [json.loads(ln) for ln in open(path) if ln.strip()]
    assert len(rows) == 4
    assert [r["response"] for r in rows] == ["a0", "a1", "a2", "a3"]
