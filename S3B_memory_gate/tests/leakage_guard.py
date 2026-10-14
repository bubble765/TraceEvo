"""S3B-G §13: the mandatory ground-truth leakage scan.

§13 makes this a hard gate rather than a check: `gold_leakage_count = 0`, and
"if a leak appears once, S3B STOP, no behaviour experiments".

It is worth being precise about what a leak would be, because the obvious scan
is too weak. Checking only for the exact reference-diagnosis string would miss
the realistic failure: a memory that stores a *paraphrase* of the historical
answer ("lightning-induced retinopathy") when the reference is
"Lightning-induced retinopathy with delayed-onset cystoid macular
abnormalities". A planner that reads that learns the answer.

So the scan runs four kinds of check against every prompt that will be sent:

1. **exact gold** — the reference diagnosis and the right option letter;
2. **exact prediction** — the historical case's own final answer, which §12
   forbids for the same reason;
3. **n-gram overlap** — content n-grams shared between the prompt and the
   reference, so a near-copy is caught even when no full string matches;
4. **case identity** — the historical case's row id and any long verbatim run
   from its complaint text.

The n-gram check is deliberately not a similarity score. §13 asks for a count,
and a count of *which* n-grams leaked is what a reviewer needs in order to see
what went wrong.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set

NGRAM_N = 5

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "was", "were", "is", "are", "be", "been", "being", "his", "her", "their",
    "patient", "at", "as", "by", "from", "that", "this", "these", "those",
    "he", "she", "it", "they", "no", "not", "did", "has", "had", "have",
}


def _tokens(text: str) -> List[str]:
    return [w for w in _TOKEN_RE.findall(str(text or "").lower())
            if w not in _STOP and len(w) > 2]


def ngrams(text: str, n: int = NGRAM_N) -> Set[str]:
    t = _tokens(text)
    return {" ".join(t[i:i + n]) for i in range(max(0, len(t) - n + 1))}


# The right option is a multiple-choice letter, so it cannot be found by
# substring search -- and it must not be found by one either, because the
# letter "d" occurs inside ordinary words. It has to appear next to a cue that
# presents it as *the answer*, which is the only form in which it leaks
# anything.
_ANSWER_CUE = (r"\b(?:answers?|options?|choices?|correct|selected|select|"
               r"choose|chose|picked|pick|key)\b[^.\n]{0,24}?\b")
_option_re_cache: Dict[str, Any] = {}


def _option_re(opt: str):
    if opt not in _option_re_cache:
        _option_re_cache[opt] = re.compile(_ANSWER_CUE + re.escape(opt) + r"\b",
                                           re.IGNORECASE)
    return _option_re_cache[opt]


def _right_option_leak(prompt: str, option: str) -> Optional[str]:
    """Return the matched fragment if the option letter is presented as an
    answer, else None.

    This check used to be folded into the loop below as
    `len(v) >= 3 and v.lower() in low`. A single-letter option is shorter than
    3 characters, so the branch was unreachable: §13 lists `Right Option` as a
    mandatory scan and the scan could not fire on any real dataset. It was
    found by spiking a known option into a real injection and noticing that
    three of the four controls fired and this one did not -- a check that
    cannot fail is not a check that passed.
    """
    opt = str(option or "").strip()
    if not opt:
        return None
    if len(opt) >= 3:
        return opt if opt.lower() in (prompt or "").lower() else None
    for mm in _option_re(opt).finditer(prompt or ""):
        return mm.group(0)[:120]
    return None


def scan_prompt(prompt: str, gold: Dict[str, Any],
                source_predictions: Optional[Iterable[str]] = None,
                source_case_text: str = "") -> Dict[str, Any]:
    """One injected prompt against one source case's gold.

    Returns `leaks`, a list of typed findings. An empty list is the only
    passing result.
    """
    leaks: List[Dict[str, Any]] = []
    low = (prompt or "").lower()

    v = str(gold.get("final_diagnosis") or "").strip()
    if len(v) >= 3 and v.lower() in low:
        leaks.append({"kind": "exact_final_diagnosis", "match": v[:120]})

    opt_hit = _right_option_leak(prompt, str(gold.get("right_option") or ""))
    if opt_hit:
        leaks.append({"kind": "exact_right_option", "match": opt_hit})

    for pred in (source_predictions or []):
        p = str(pred or "").strip()
        if len(p) >= 8 and p.lower() in low:
            leaks.append({"kind": "exact_prediction", "match": p[:120]})

    ref_ngrams = ngrams(str(gold.get("final_diagnosis") or ""))
    prompt_ngrams = ngrams(prompt)
    shared = sorted(ref_ngrams & prompt_ngrams)
    if shared:
        leaks.append({"kind": "reference_ngram_overlap",
                      "match": "; ".join(shared[:5]),
                      "n": len(shared)})

    if source_case_text:
        case_ngrams = ngrams(source_case_text)
        shared_case = sorted(case_ngrams & prompt_ngrams)
        if shared_case:
            leaks.append({"kind": "source_case_verbatim",
                          "match": "; ".join(shared_case[:3]),
                          "n": len(shared_case)})

    case_id = str(gold.get("case_id") or "").strip()
    if case_id and case_id in prompt:
        leaks.append({"kind": "case_id", "match": case_id})

    return {"leaks": leaks, "n_leaks": len(leaks),
            "n_prompt_ngrams": len(prompt_ngrams)}


def guard(injections: List[Dict[str, Any]]) -> Dict[str, Any]:
    """§13's gate over every injection the probe is about to make.

    `injections` is a list of dicts with `prompt`, `gold`,
    `source_predictions`, `source_case_text`, `target_case_index`. The guard
    does not stop the caller — it returns a verdict, and the runner treats
    `gold_leakage_count > 0` as STOP.
    """
    results = []
    total = 0
    for inj in injections:
        r = scan_prompt(inj.get("prompt", ""), inj.get("gold") or {},
                        inj.get("source_predictions"),
                        inj.get("source_case_text", ""))
        r["target_case_index"] = inj.get("target_case_index")
        r["source_memory_ids"] = inj.get("source_memory_ids", [])
        total += r["n_leaks"]
        results.append(r)
    return {
        "gold_leakage_count": total,
        "n_injections_scanned": len(injections),
        "passed": total == 0,
        "verdict": ("GO" if total == 0 else
                    "STOP: leakage detected, no behaviour experiment is "
                    "permitted (§13)"),
        "results": results,
    }
