# S4A failure cases

Failures found building S4A. The pattern from the previous three stages holds,
and S4A adds a fifth shape: **a statistic computed over the wrong unit.**

S3B's failures were absences that read as findings. S3C's were numbers that were
never computed. S3D's were checks whose scope or denominator was wrong. S4A's
are the same family as S3D's, in a stage where every number is a ratio and the
denominator is a choice.

---

## FAILURE 1 — the P3 target was scored against §6.2's wrong denominator

**What happened.** §9.3's target is `y_p3_use = 1 if 当前 role 产生 P3 且
used_in_reasoning else 0`. The first version made the unit "every attributed
evidence node", so the negative class was every non-P3 node and the predictor
was the role's historical `p3_decision_use_rate`.

That predictor is defined by §6.2 as:

```
p3_decision_use_rate
    = P3 used_in_reasoning / attributable evidence used_in_reasoning
```

Its denominator is nodes *used in reasoning*. The Bernoulli it was being asked
to predict had the denominator "all attributed nodes". The two differ by a
factor of about 1.55 (243 used of 377 attributed).

**Why it matters.** The mismatch does not produce a random error. It biases the
comparison against the role profile, because the historical rate is
systematically larger than the eval base rate it is predicting, so the profile
predicts a higher probability than the outcome warrants and takes a Brier
penalty proportional to the gap. Read at face value the result would have been
"the role profile is worse than the global baseline" — the conclusion this
stage in fact reaches, but for a reason manufactured by the evaluator rather
than found in the data. A stage whose headline is a negative result has to be
more careful than usual that the negative was measured.

**Also in the first version.** The leftover nodes were then filled by appending
rows with `role = "UNATTRIBUTED_FILLER"` and filtering them out afterwards — a
construct that cannot be right, since a row that has to be removed later was
never a row. Removing the need for it is what produced the correct construction:
one row per node used in reasoning, `p3_used` of them positive.

**Fixed.** The unit is the attributable evidence node *used in reasoning*, which
is §6.2's own denominator, and the global baseline was changed to match. The
mismatch is described in the code comment at the call site so the next person
to change a denominator sees why it was chosen.

---

## FAILURE 2 — a Brier over cases reported as a Brier over calls

**What happened.** §9.2 says the unit is *每个 expert invocation*. The first
version emitted one row per `(case, role)` pair:

```python
for o in eval_obs:
    for role in ROLES:
        if not v["present"]:
            continue
        t1_rows.append({...})
```

which gave n = 21. The correct loop gives n = 288 (the eval block's expert
calls: 138 + 72 + 78).

**Why it matters.** Both are averages of squared errors, both are called
"Brier", and they answer different questions: one weights each case equally,
the other weights each call. With roles appearing in different numbers of cases
and called different numbers of times per case, the two can disagree in sign.
The number would have been reported under §9.2's name while not being §9.2's
statistic.

**Caught by.** Printing the targets table and seeing `n=21` next to a block that
the ledger says contains 288 expert calls. The count was visible next to the
number it was computed over — which is exactly the rule S3D's failure note
recommended, applied one stage later, and it worked on the first number it was
pointed at.

**Fixed.** One row per invocation. `n=288, positives=0` for §9.2; `n=288,
positives=4` for the secondary discard target.

---

## FAILURE 3 — `discard_reason` is the string `"NONE"`, which is truthy

**What happened.** A first-pass availability probe counted discarded calls as:

```python
if r.get("discard_reason"):
    byrole[role]["discarded"] += 1
```

The ledger stores `discard_reason: "NONE"` for calls that were *not* discarded.
`"NONE"` is a non-empty string, so every call counted as discarded, and the
probe reported:

```
Central_Agent    n=508  discarded=508
```

**Why this is worth an entry despite being in a throwaway script.** The wrong
reading — "every central-agent call is discarded" — is a dramatic finding about
the central agent, and it was one `print` away from being believed. It is the
same class as the failures above: a number whose meaning was decided by the
truthiness of a sentinel rather than by the value. The corrected probe reads
`parse_status == "discarded"` and finds 17.

**Fixed.** The probe was corrected and the two facts it had conflated —
`discard_reason` as a *label* and `parse_status` as a *status* — are used for
what they are. No deliverable code was affected; this is recorded because the
near-miss is the informative part.

---

## FAILURE 4 — a test that asserted the interval should be zero

**What happened.** `test_small_samples_get_wide_intervals` asserted:

```python
lo, hi = _beta_interval(0, 2)
assert lo == 0.0 and hi > 0.7
```

It failed: `lo = 0.0084`. That is the 2.5% quantile of Beta(1, 3), which is what
Beta(1,1) smoothing *should* produce for zero successes in two trials.

**Why this is the same failure as S3D's FAILURE 2.** The test encoded a belief
about the implementation rather than about the requirement, and the belief was
wrong. Had I "fixed" the failure by making `_beta_interval` return 0.0 for
zero counts, the test would have passed and §8's requirement would have been
broken in exactly the way §8 names — `0 failures / 2 calls` presented as
`risk = 0`. The corrective pressure from a wrong test runs toward a wrong
implementation.

**Fixed.** The test now asserts what §8 actually wants: the lower bound is small
but **not** zero, the upper bound is above 0.7, and both shrink with n. The
comment records the value and why it is not zero, so the next reader does not
repeat the reasoning.

---

## FAILURE 5 — the verdict shipped with two criteria set to `null`

**What happened.** `s4a_validate.decide` evaluates §13's six conditions, but
items 2 (no future leakage) and 6 (guard candidate or proof of indistinguish-
ability) are decided in `s4a_actionable`. The first version wrote them as:

```python
crit[2] = {"met": None, "note": "set by s4a_actionable.leakage_check"}
crit[6] = {"met": None, "note": "set by s4a_actionable: guard candidate, ..."}
```

and computed `go_conditions_met` by filtering out the `None`s — so the flag was
`all(...)` over a set that had quietly lost two of its members.

**Why it matters.** The filter is the defect. `go_conditions_met = all(v for v
in crit.values() if v["met"] is not None)` returns `True` for a criteria set
where every entry is `None`, and in this run it was evaluating four of six
conditions while presenting itself as §13's conjunction. A reviewer reading
`"go_conditions_met": false` would have no way to see that two of the six were
never evaluated — and would also have no way to see that the JSON and the report
disagreed, because the report would have described six.

**Fixed.** `close_criteria` fills both in and re-derives the flag with the
complete set, and `decide` no longer filters — a `None` now propagates as "not
established" rather than being skipped. §13 item 6 is satisfied by the second of
its two halves, and the JSON records which half.

---

## A note on what these five have in common

Four of the five are about **what a number was computed over**: the denominator
of a rate, the unit of a Brier, the sentinel behind a boolean, the conditions
behind a flag. The fifth is a test that would have pushed an implementation away
from its requirement.

The rule S3D handed forward — *print the count of things each number was
computed over, next to the number* — caught FAILURE 2 the first time it was
applied. FAILURE 1 and FAILURE 5 are the same rule stated two other ways: a
ratio owes its denominator, and a conjunction owes its member count. S4A would
restate it as: **a summary statistic is not a number, it is a number plus the
set it ranges over, and the two must be reported together or neither is a
measurement.**
