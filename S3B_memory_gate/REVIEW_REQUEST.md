# TraceEvo S3B — review request

Per `TraceEvo_S3A_Review_S3B_Amendment_v1.md` §16/§18. **S3B stops here.** No
Capability Profile, no agent weight or capability EMA, no dynamic agent
selection, no parameter tuning, no S4 experiment, and no case outside 30–49 read
or run. §18 forbids all of them and none was started.

The short version: **the Gate works, the corpus does not support the
comparison it was built for, and §15's final clause applies —
`NEED_MORE_VERIFIED_EXPERIENCE`.**

---

## 1. What S3B found

### The Gate holds, on two independent layers

38 poisoned candidates across four artifact-level poisonings, admitted **0**.
The naive rule admits all 38.

The first reading of `gate_results.csv` showed every rejection citing the same
reason — the hash chain — which would have made the result rest on one check.
It does not: all 38 also fire the anomaly detectors, and re-running the verdict
function with integrity forced `True` still yields **0 eligible, 38
`failure_experience`**. Two layers, both load-bearing. (`poison_defence_layers.json`)

§7.2's **second** counterexample is demonstrated on real data: AuditOnly admits
9 of 10 clean traces, all 9 not gold-safe, 3 known-wrong. TraceEvo admits 0 of
them, and takes the one trace AuditOnly rejects (case-32) as a Validated
Failure.

### §13's zero is a zero over the prompts that were actually sent

**30 injections scanned, 0 leaks**, over an exhaustive, controlled scan — and
the scan is of the real injectate, not a prediction of it.

This took four attempts, and the first three are why the fourth is trustworthy:

| attempt | why its zero was not evidence |
|---|---|
| 1 | the guard scanned **zero injections** (FAILURE 1) |
| 2 | its `Right Option` check was **unreachable code** — `len(option) >= 3` against a single letter (FAILURE 4) |
| 3 | it scanned prompts built with a **different query** from the one the run used, and retrieved different memories (FAILURE 15) |

The second pass exists because of the third: `s3b_injection_audit` re-scans
`{task}_injections.jsonl` — what `hooks.on_plan_prompt` actually returned — and
compares it against the plan. It now agrees on **20/20 cases, ids and block
lengths**, with 0 leaks. Attempts 1–2 were caught by a positive control on the
guard; attempt 3 could not have been, because the guard was working correctly
and aimed at the wrong text.

Four independent record-keeping passes to establish one zero. That is the honest
cost of the claim.

### The corpus produced no verified successes, and that blocks §15

`case 30–39`: **0 verified_success / 4 verified_incorrect / 6 uncertain**;
judge unanimity 7/10.

Three of the six uncertain are **unanimous score 1** — "a broad category
containing the reference". §4.2 is explicit that score 1 never admits a
success, so they are `uncertain`, correctly. They are also, plausibly, *almost*
right.

Consequences, none of them repairable inside S3B:

| §15 criterion | Status |
|---|---|
| 1. leakage = 0 | **Met** |
| 2. Poison AR < Naive | **Met** — 0.00 < 1.00 |
| 3. Poison AR < OutcomeOnly | **Not met** — 0.00 vs 0.00, vacuous |
| 4. Incorrect-Clean AR < AuditOnly | **Met** — 0.00 vs 0.90 |
| 5. Validated success retained | **Not met** — none exists |
| 6. manual_review not retrievable | **Met** |
| 7. memory on/off | **Met** |
| 8. entries traceable | **Met** — 48/48 |

Criterion 3 fails because OutcomeOnly never engages: with no `verified_success`
it admits nothing, so its 0/38 poison rate and TraceEvo's are the same number
for unrelated reasons. §7.2's **first** counterexample — *correct result,
untrustworthy process → OutcomeOnly admits it* — could not be constructed, since
it needs a correct trace to poison.

Criterion 5 fails because retention is 0/0, not 0%.

The two repairs that would make these numbers look better are both forbidden,
and neither was taken: promoting the unanimous score-1 cases would have produced
three `verified_success` cases and a non-vacuous §15.3 — and built "trusted"
memory out of diagnoses the judge called a broad category containing the answer.

---

## 2. What this does and does not establish

**Established.** Poison cannot pass the Gate (two layers). Leakage is 0 over an
exhaustive, controlled scan. `manual_review` is isolated in the store, not the
caller. Every entry is traceable. AuditOnly is blind to outcome on real data.

**Not established.** That the Gate preserves *good* experience — none existed.
That TraceEvo beats OutcomeOnly on poison — OutcomeOnly never engaged. §7.2's
first counterexample. Anything about accuracy improvement.

**The behaviour probe ran, and its one clean result is about exposure.**

All three arms completed 10 of 10 cases.

| | M1 Naive | M2 Trusted |
|---|---|---|
| cases retrieving a poisoned twin | **10 / 10** | **0 / 10** |
| what was retrieved | 10 poisoned twins, 6 `verified_incorrect`, 4 `uncertain` | one `failure_experience`, ten times |

Trained memory never surfaced a poisoned twin; naive memory did so in every
case. That is §15's behavioural criterion met on the poison half.

**And not met on the unsafe half** (1.00 vs 1.00). M2's 1.00 is a single memory —
case-32's Validated Failure — rendered as a `[Failure Prevention]` block that
§6.2 designed to be retrievable. The rate calls it unsafe because it is not a
`verified_success`; the decomposition is in the report, and both readings are
reported rather than the flattering one.

**On the process metrics, M2 is not better than no memory at all.** Unsupported
Evidence 0.053 vs M0's 0.030; P3-used-in-reasoning 0.30 vs 0.20. On ten cases
those are one or two nodes and prove nothing — but they point the wrong way, and
M0 (no memory) scored highest on both Match and Relevant. Any later claim that
memory improves diagnosis has to explain that first.

**Probe caveat, unchanged.** The trusted store holds **one** memory, so this
measures exposure, not capability.

---

## 3. Two decisions for the reviewer

### 3.1 The corpus, not the Gate, is the blocker

Ten build cases yielded zero verified successes. Widening the build set is the
direct remedy, and it is a corpus decision, not a rule change — §2 restricts
S3B to 30–49 and S3B cannot make it alone.

Worth carrying into that decision: **three of the six uncertains are unanimous
score-1.** If a wider corpus produces many of those, the binding constraint on
S3B becomes the judge's discriminative power between "right" and "broadly
right", not the Gate's strictness.

### 3.2 The `failure_experience` channel is the productive one here

The only memory the trusted store could admit is a Validated Failure. §6.2's
machinery works and produced something usable: guidance about what to avoid.
If the build set stays small, that is where the value is.

---

## 4. How to check this without trusting the summary

1. `python3 -m pytest traceevo/tests/test_evolution.py` — 43 tests.
2. `leakage_guard_positive_control.json` — the four spike-ins that make §13's
   zero meaningful rather than merely reported.
3. `failure_cases.md` — fifteen failures and one limitation, six of which
   are "the artifact looked clean and was not". They are the honest index of
   how much of the above to believe.
5. `leakage_scan_runtime.json` — the §13 second pass, and the plan-vs-run
   agreement that makes the first pass's zero meaningful.
4. `poison_defence_layers.json` — the counterfactual behind the two-layer claim.
6. `judge_raw/` — including the disagreeing runs on cases 36, 37, 39, which
   are reported as disagreement rather than resolved.

## 5. Artifacts

§16's list is complete. `S3B_report.md` §17.11–§17.15 carries the probe results;
`probe_results.csv`, `probe_judge_results.csv` and `probe_judge_results_raw/`
carry the underlying numbers.

Fifteen failures are recorded in `failure_cases.md`. Six of them are the same
mistake wearing different clothes — **an artifact that read as a finding while
being an absence**: a guard that scanned nothing, two exposure rates that could
only ever be 0.0, a §13 check that could never fire, memory arms that ran
without memory, and a guard aimed at prompts the planner never received. The
last of those was found only by keeping a second, independent record of what was
actually sent, which is the practice worth carrying into S4.
