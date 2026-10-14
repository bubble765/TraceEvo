# TraceEvo S4B — review request

Per `TraceEvo_S4A_Review_S4B_Amendment_v1.md` §19. **S4B stops here.**

No case 70-137 read or run; P0-P3 rules unmodified; P1 Gate not relaxed; no
role routing; no Success Memory injected; no Failure Memory activated; ATGG-v1
not modified; no S4C work started.

The short version: **the guard was built to the standard §9 asks for and the
block could not test it. Zero of ten baseline cases carry a decision-path P3 —
the block contains no P3 node at all — so §10's first branch fired and the
Guard arm was not run.**

---

## 1. What S4B found

### The machinery is sound

```
Guard OFF vs pre-S4B recording (2026-09-17) : IDENTICAL
Guard OFF vs a second Guard-OFF run         : IDENTICAL
strict replay, zero divergence / underrun   : true
guard action log written while disabled     : none
```

Strict replay raises on a single differing prompt or one extra LLM call, so
"identical" is a statement about the bytes GoS sent. Final graph hash,
prediction, report and the full 37-event semantic sequence match a run made
before the guard existed.

Six positive controls, all passing, each isolating one condition:

| id | candidate | tier | edge | trigger | isolates |
|---|---|---|---|---|---|
| A | P3 unsupported | P3 | support | **yes** | §9.2 |
| B | P3 unsupported | P3 | refute | **yes** | §8's two relations |
| C | P3 unsupported | P3 | refines | no | the **edge** condition |
| D | verbatim record | **P0** | support | no | the **tier** condition |
| E | expert synthesis | P2 | support | no | §9.2 |
| F | Hypothesis | — | support | no | the **node-type** condition |

C, D and F are the load-bearing ones: without them a silent guard and a broken
one are the same observation.

### The block has no P3 in it

| case | 60 | 61 | 62 | 63 | 64 | 65 | 66 | 67 | 68 | 69 |
|---|---|---|---|---|---|---|---|---|---|---|
| P3 used | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| P3 unused | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| P2 on decision path | 2 | 2 | 3 | 1 | 3 | 5 | 3 | 4 | 7 | 3 |

Counted without gold — `s4b_risk.py` reads events, bindings, deltas and the
blinded dataset, and has no code path that can open a diagnosis.

The decision-path evidence that *is* there is `P2_DERIVED`: hypotheses, absence
claims, expert synthesis, agent inference. That is the reasoning the pipeline
is built to do, correctly labelled. An earlier version of the metric counted it
as "ungrounded Evidence" and reported 1-3 risk nodes per case — `failure_cases.md`
FAILURE 6.

---

## 2. Why the block is quiet, and how far that can be pushed

| block | cases | P3-used cases | rate |
|---|---|---|---|
| 10-29 | 20 | 5 | 0.250 |
| 30-39 | 10 | 1 | 0.100 |
| 50-59 | 10 | 4 | 0.400 |
| **60-69** | **10** | **0** | **0.000** |

Under the pooled historical rate, ten clean cases happen about **5.6%** of the
time (6.7% under the Beta(11,31) posterior predictive). Low enough to say this
block is quieter than the corpus; not low enough to say anything changed.

**This is not evidence that ATGG does not work.** The rule fires on the
controls; there was no P3 for it to fire on. A guard that is never asked is not
a guard that failed.

---

## 3. Recommendation — `LOW_TRIGGER_DENSITY`

§15's option, by §10's own rule rather than by judgement: 0 of 10, and the rule
for 0 is *stop*. The stage stopped for the price of a baseline arm rather than
a full experiment, which is what §10's branch structure was for.

Three questions for the review, none of which S4B can settle:

1. **Is case 60-69 the right block?** It is the next unopened one and was
   frozen for this. But on the historical rate a ten-case block was always
   marginal — at 0.25 P3-cases per case, ten cases yield a median of 2-3, and
   0 or 1 is not rare. §10 anticipated exactly this.
2. **Widen the block, or change the target?** §19 forbids editing ATGG-v1 and
   retesting on the same cases, so any option needs a fresh decision: more
   cases (70-79 is reserved and unopened), a broader trigger, or a different
   operationalisation of "unsupported".
3. **Was P3-only the right first guard?** This block's evidence is that P2
   carries the decision path and P3 does not occur. One block, so weak
   evidence — but it is the second time this corpus has shown P3 to be rare
   while P1/P2 carry the decision path. A guard aimed at the tier that appears
   would be aimed at something the data can test.

**This stage's own reading is (3)**, with (1) as the practical constraint: the
tier the guard watches is absent, and the tier that is present is the one §3
rules out. That is a design question about what ATGG should guard, and it
belongs to the review.

---

## 4. One decision this stage made on its own, flagged for disagreement

**The gold on case 60-69 was not read.** §11 conditions outcome verification on
both arms being sealed; there is one arm. `outcome_results.csv` and
`judge_raw/` carry `NOT_PRODUCED` records.

The cost is real: the baseline's diagnostic quality for case 60-69 is now
unknown and could have been measured. It was not, because no part of §10's
decision uses it, and because reading it would spend the block's gold-unopened
status — the only remaining value 60-69 has if the review re-scopes it.

If the reviewer would rather have those numbers than the option to re-scope,
that is a call this stage is content to have made the other way. The ten seals
verify and `s3d_verify.py` is in place.

---

## 5. What this does and does not establish

**Established.** ATGG-v1 is frozen, hashed and derived from named artifacts.
The trigger rule is the frozen P0-P3 classifier applied pre-commit, not a second
one. It fires exactly on P3+SUPPORT/REFUTE and on nothing else in six
controlled cases. Disabling it is provably inert against a recording made
before it existed. Ten baseline cases are sealed with verifiable hashes. Zero
P3 in the block, counted under blind.

**Not established.** That ATGG reduces P3 use — it was never triggered. Its
marginal cost, since there were no retries. Whether the block's quietness is
about the cases or the rate. Anything about diagnosis, which was not measured.

**Carried forward.** S4A's `NO_ROLE_SPECIFIC_ADAPTATION` stands. The reusable
success corpus is 4. The 58-row cross-case review queue is still `UNREVIEWED`.

---

## 6. How to check this without trusting the summary

1. `python3 -m pytest S4B_adaptive_guard/tests/test_s4b.py` — 21 tests.
2. `positive_control_results.csv` — the six controls, each with what it varies
   and what it holds fixed.
3. `regression_results.csv` + `regression_summary.json` — the guard-off
   comparison, including the three excluded fields and why.
4. `baseline_risk_summary_atgg_off.csv` — the per-case P3 counts the branch
   was decided on.
5. `guard_policy_manifest.json` — the frozen policy, its two inactive guards
   and their reasons.
6. `failure_cases.md` — seven failures. Four are a measurement whose setup,
   definition or timing decided the answer — including the freeze manifest,
   which was first written while the guard was still being implemented and
   therefore pinned a half-built pipeline. It was re-frozen and the re-freeze
   says so; `freeze_manifest.premature.json` is kept alongside.
7. `safety_metrics.csv`, `overhead_results.csv`, `leakage_check.json`.

## 7. Artifacts

§17's list is complete. Where Phase 1 stopped, the Guard-arm files carry
`NOT_PRODUCED` records with the reason rather than being empty: an empty file
and a file nobody wrote look the same to a later reader.
