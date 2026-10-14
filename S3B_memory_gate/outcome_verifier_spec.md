# S3B-A — OutcomeVerifier specification (§4)

The only component in TraceEvo permitted to read a benchmark label. This file
states what it is, what it is allowed to see, and what it emits — the boundary
matters more than the implementation, because §1 D2 is a claim about what the
Gate *cannot* read, and that claim is only true if the reference never enters
its inputs.

---

## 1. Why it exists

S3A concluded that a trace cannot tell you whether a diagnosis was correct. The
auditors see process: provenance tiers, bindings, dependency edges, anomalies.
A trace can be impeccable and wrong, and S3B's whole design rests on that gap —
§7.2's second counterexample is a clean process producing a wrong answer.

DiagnosisArena ships a reference diagnosis for every case. Cases 30–49 therefore
have an external answer that arrives *after* the run. The verifier models the
real-world event this stands in for: a clinical confirmation or an expert review
arriving later, after the case has closed.

That simulation is only honest if it cannot influence what it judges.

## 2. Ordering (§4.1)

```
1. GoS executes
2. FINAL_REPORT emitted
3. trace sealed               <- artifact hashes computed
4. ---- boundary ----
5. OutcomeVerifier reads the gold label
6. verdict emitted
```

Steps 1–4 run in a process that read a dataset file with the gold columns
*physically removed* (`evolution/blinding.py`). Steps 5–6 run in a different
process, reading a different file, after the seal. This is enforced by
construction, not discipline: `s3b_build.py` does the run and seals;
`s3b_verify.py` is separate and refuses to start if `sealed_artifacts.json` is
absent.

The seal covers `events.jsonl`, `bindings.json`, `deltas.jsonl`,
`integrity.json` and `invocations.jsonl` per case, with a `complete` flag.

## 3. What it emits

```json
{
  "outcome_verdict": "verified_success | verified_incorrect | uncertain",
  "outcome_source": "posthoc_benchmark_verifier",
  "verdict_reason": "<why>",
  "judge_scores": [2, 2, 2],
  "judge_unanimous": true,
  "reference_sha256": "<sha256 of the reference used>"
}
```

Those six fields are the entire interface to the Gate. There is no field
carrying the reference, the option letter, or a judgement about the trace's
quality — a reviewer can confirm by grepping the emitted structure for
`reference_text`, which is named here specifically so its absence is checkable.

The Gate receives `outcome_verdict` and `outcome_source`. Nothing else.

## 4. The mapping (§4.2)

Three low-temperature judge runs per case. Each returns a score by the paper's
own rules: `2` exact match, `1` a broad category containing the reference, `0`
neither.

| Judge scores | Verdict |
|---|---|
| 3/3 = 2 | `verified_success` |
| 3/3 = 0 | `verified_incorrect` |
| anything else | `uncertain` |

"Anything else" includes a judge that raised, returned unparsable text, or
disagreed with itself. Consequences worth stating plainly:

* **`score = 1` never admits a success.** Any `1` in the three runs forces
  `uncertain`. This is the exact case where a plausible-sounding memory could be
  built from a diagnosis that was not actually right: a broad category that
  contains the reference may also contain several wrong answers.
* **A failing judge is never retried until it agrees.** Exactly `REPEATS = 3`
  attempts, then the verdict stands. `test_a_failing_judge_is_uncertain_and_not_retried`
  asserts the call count, because a verifier whose verdict depends on how many
  attempts it was given is not a verifier.
* **Disagreement is a result, not an error to be resolved.** There is no tiebreak
  and no majority rule. 2/3 unanimity is `uncertain`.

## 5. The raw transcript, and why it is kept separately

§4.2 requires every judge prompt and raw response to be kept. The prompt
necessarily contains the reference. Kept in the return value, that requirement
would put gold text into the structure flowing toward the Gate — which is
failure 7 in `failure_cases.md`, caught by
`test_verifier_output_contains_no_gold_text`.

The two requirements are separated instead:

| Location | Contents | Who reads it |
|---|---|---|
| return value | verdict + **redacted** prompt (`<reference withheld: sha256:…>`) | the Gate, the metrics |
| `judge_raw/<task>.json` | full prompt and raw response per run | the evaluation only |
| `judge_raw/case-N.raw.json` | per-case transcript, three runs | the evaluation only |

`reference_sha256` is what lets a reviewer confirm which reference was scored
without the reference entering anything downstream.

## 6. What it does not do

* It does not read the task text, the trace's events, or the audit summary. Its
  inputs are the final prediction, the report and the reference.
* It does not decide eligibility. It emits a verdict; the Gate combines it with
  the process signals, and §7.2's two counterexamples are exactly the cases
  where the two disagree.
* It does not score the process. A trace can be `verified_success` and still be
  rejected — that is the intended behaviour, and `gate_results.csv` shows it.
* It does not touch cases 0–29 or 50–137. S3B §2 restricts verification to the
  Memory-Build cases (30–39) and the probe cases (40–49).

## 7. Verification record

`outcome_results.csv` — one row per case: verdict, reason, the three scores,
unanimity, prediction, and `reference_sha256`.

`judge_raw/` — the full transcripts, including the failing ones.

`outcome_summary.json` — the verdict counts over the ten build cases.
