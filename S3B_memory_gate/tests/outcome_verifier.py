"""S3B-A §4: the only component allowed to read the benchmark label.

S3A's conclusion was that a trace cannot tell you whether a diagnosis was
right. S3B does not overturn that — it changes the question. DiagnosisArena
ships a reference diagnosis for every case, so `case 30-49` have an external
answer that arrives *after* the run. The verifier models the real-world event
this experiment is standing in for: a clinical confirmation or expert review
arriving later.

Three rules make that a simulation of external feedback rather than a leak.

1. **It runs after sealing**, on traces whose hashes are already computed. It
   cannot influence the run it judges, because the run is over.
2. **It emits a verdict, not an answer.** The Gate receives
   `verified_success | verified_incorrect | uncertain` and a source tag. The
   reference string never leaves this module — §1 D2 is explicit that the Gate
   must not be able to read it, and the only way to guarantee that is for it
   never to be in the Gate's inputs.
3. **It is conservative in the direction that matters.** §4.2: only 3/3 on
   score 2 is a success, only 3/3 on score 0 is a failure, everything else —
   including a judge that fails, refuses to parse, or disagrees with itself —
   is `uncertain`. `score=1` ("broad category that includes") never admits a
   success: it is the exact case where a plausible-sounding memory could be
   built from a diagnosis that was not actually right.

A judge that fails is never retried until it agrees. §4.2 forbids it, and a
verifier whose verdict depends on how many attempts it was given is not a
verifier.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

# Same 2/1/0 rules as the upstream judge, restated for a single case. §4.2 asks
# for per-case scoring rather than the paper's 5-case batches so that one case
# cannot colour another's score.
JUDGE_SYSTEM_PROMPT = """You are an expert in diagnosing challenging cases. You will receive a student's answer (containing final diagnosis: prediction + report) and the corresponding reference diagnosis.
Score the diagnosis according to these rules:
2 = Student's diagnosis exactly matches the reference diagnosis;
1 = Student's diagnosis is a broad category that includes the reference diagnosis;
0 = Student's diagnosis does not meet the criteria for 1 or 2.

Output Format (clearly mark the case number and the score):
1. Case 1 - Disease name: score X;"""

_SCORE_RE = re.compile(
    r"(?m)^\s*(\d+)\s*[\.\)]\s*Case\s*(\d+)\b.*?score\s*[:：]?\s*(\d+)")

REPEATS = 3
VERDICT_SUCCESS = "verified_success"
VERDICT_INCORRECT = "verified_incorrect"
VERDICT_UNCERTAIN = "uncertain"
OUTCOME_SOURCE = "posthoc_benchmark_verifier"


def build_judge_prompt(prediction: str, report: str,
                       reference: str) -> str:
    student = f"{prediction}\n{report}".strip()
    return (f"Group 1 (Case 1):\nHere is the student’s answer for this case:\n"
            f"1. Case 1: {student}\n\n"
            f"Here is the reference diagnosis for this case:\n"
            f"1. Case 1: {reference}\n")


def parse_score(response: str) -> Optional[int]:
    m = _SCORE_RE.search(response or "")
    if not m:
        return None
    score = int(m.group(3))
    return score if score in (0, 1, 2) else None


def verdict_from_scores(scores: List[Optional[int]]) -> Dict[str, Any]:
    """§4.2's mapping. Anything short of unanimity is `uncertain`."""
    if len(scores) != REPEATS or any(s is None for s in scores):
        return {"outcome_verdict": VERDICT_UNCERTAIN,
                "reason": "the judge did not return a parsable score every time"}
    if all(s == 2 for s in scores):
        return {"outcome_verdict": VERDICT_SUCCESS, "reason": "3/3 score 2"}
    if all(s == 0 for s in scores):
        return {"outcome_verdict": VERDICT_INCORRECT, "reason": "3/3 score 0"}
    if 1 in scores:
        return {"outcome_verdict": VERDICT_UNCERTAIN,
                "reason": ("at least one judge returned score 1 (a broad "
                           "category containing the reference), which §4.2 "
                           "does not admit as a validated success")}
    return {"outcome_verdict": VERDICT_UNCERTAIN,
            "reason": "the three judge runs did not agree"}


def verify_trace(trace: Dict[str, Any], reference: str,
                 judge_fn, case_index: int,
                 raw_dir: str = "") -> Dict[str, Any]:
    """Score one sealed trace three times and return the verdict.

    `judge_fn(system, user) -> (response, meta)` is injected so the tests can
    drive this without a model, and so the caller controls the transport.

    **The return value contains no gold text.** §4.2 wants every judge prompt
    kept, and the judge prompt necessarily contains the reference — so the two
    requirements pull against each other unless the raw record is separated
    from the verdict. They are separated here:

        return value     verdict + redacted prompts. This is what flows
                         onward, and it cannot leak because the string is not
                         in it.
        `raw_dir/`       the full prompts, per §4.2, written where only the
                         evaluation reads. Never an input to the Gate.

    `reference_sha256` lets a reviewer confirm which reference was used without
    the reference itself entering anything downstream.

    This was not the first version. The first wrote the full prompt into the
    returned structure, and
    `test_verifier_output_contains_no_gold_text` failed on it — which is the
    argument for having written that test.
    """
    from .blinding import sha256_file  # noqa: F401  (re-exported for callers)
    import hashlib

    prediction = str(trace.get("prediction") or "")
    report = str(trace.get("report") or "")
    user_prompt = build_judge_prompt(prediction, report, reference)

    ref_hash = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    redacted = user_prompt.replace(
        reference, f"<reference withheld: sha256:{ref_hash[:16]}>")

    runs: List[Dict[str, Any]] = []
    raw_runs: List[Dict[str, Any]] = []
    scores: List[Optional[int]] = []
    for i in range(REPEATS):
        try:
            response, meta = judge_fn(JUDGE_SYSTEM_PROMPT, user_prompt)
            error = ""
        except Exception as exc:                   # judge/transport failure
            response, meta, error = "", {}, f"{type(exc).__name__}: {exc}"
        score = parse_score(response) if not error else None
        scores.append(score)
        runs.append({
            "run": i + 1,
            "system_prompt": JUDGE_SYSTEM_PROMPT,
            "user_prompt": redacted,
            "raw_response": response,
            "score": score,
            "error": error,
            "meta": {k: v for k, v in (meta or {}).items() if k != "response"},
        })
        raw_runs.append({"run": i + 1, "system_prompt": JUDGE_SYSTEM_PROMPT,
                         "user_prompt": user_prompt, "raw_response": response,
                         "score": score, "error": error})

    if raw_dir:
        os.makedirs(raw_dir, exist_ok=True)
        with open(os.path.join(raw_dir, f"case-{case_index}.raw.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"case_index": case_index,
                       "reference_sha256": ref_hash,
                       "note": ("raw judge transcript, §4.2. Contains the "
                                "reference by necessity; read by the "
                                "evaluation only, never by the Gate."),
                       "runs": raw_runs}, f, ensure_ascii=False, indent=2)

    outcome = verdict_from_scores(scores)
    return {
        "case_index": case_index,
        "outcome_verdict": outcome["outcome_verdict"],
        "outcome_source": OUTCOME_SOURCE,
        "verdict_reason": outcome["reason"],
        "judge_scores": scores,
        "judge_unanimous": len(set(s for s in scores if s is not None)) == 1
                          and None not in scores,
        "reference_sha256": ref_hash,
        "judge_runs": runs,
        # Explicitly absent, and named so a reviewer can grep for it:
        # "reference_text" is never written here.
    }


def make_judge_fn(model: str = "", temperature: float = 0.0):
    """The real judge transport: lowest temperature, one case per call."""
    def _judge(system_prompt: str, user_prompt: str):
        from utils.public_function import llm_generate_response, \
            default_model_path
        return llm_generate_response(
            user_prompt=user_prompt,
            model_path=model or default_model_path(),
            temperature=temperature,
            max_tokens=512,
            system_prompt=system_prompt,
            return_meta=True)
    return _judge
