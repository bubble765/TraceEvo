# S3D failure cases

Failures found building S3D, in the order they were found. S3D exists because
S3A did not seal; the first entry is about the seal itself, which is the right
place for the first one to be.

The pattern is continuous with S3C's. S3B's failures were *an artifact that
read as a finding while being an absence*; S3C's were *a number that was never
computed*. S3D's first is a third shape: **a check that covered less than the
thing it was named after.** A seal called "the seal" that hashes five of ten
artifacts is not a weaker seal, it is a different claim.

---

## FAILURE 1 — the seal covered 5 of the 10 artifacts §3.3 names

**What happened.** §3.3 step 2 requires that, per case,

```
events / ledger / deltas / graph / prediction / report 落盘
```

step 3-4 turn those into a manifest and a sealed hash. The first version of
`SUFFIXES` in `s3d_run.py` listed:

```python
("_events.jsonl", "_bindings.json", "_deltas.jsonl",
 "_integrity.json", "_invocations.jsonl")
```

After case 50 ran and sealed, listing the artifact directory showed ten files:

```
_bindings.json      _deltas.jsonl       _events.jsonl
_injections.jsonl   _integrity.json     _invocations.jsonl
_provenance_graph.json  _provenance_graph.png
_replay.json        _summary.json
```

`_provenance_graph.json` is §3.3's *graph*. `_summary.json` carries
`final_graph_hash`, `integrity_ok` and the grounding counts. `_replay.json` is
the replay verdict. Three of the five named artifacts were outside a seal whose
entire purpose was to bind the artifacts §3.3 names.

**Diagnosis.** The list was chosen by asking "which of these does verification
read?" — and verification reads the events file for the prediction and the
audit bundle for the conditions, so five felt sufficient. That question has the
wrong answer for a seal. A seal's job is not to cover what the verifier happens
to touch today; it is to make "these are the artifacts this run produced"
checkable by someone who does not know what the verifier reads. Choosing a
subset by relevance to the verifier is choosing it by the one criterion that
guarantees the subset will be wrong for the next reader.

It is also the subset that silently stops covering a new artifact: the runner
begins writing a file, the seal does not notice, and every case still reports
`match`.

**Why it was caught cheaply.** `ls` on the artifact directory, for an unrelated
reason — checking that case 50 had actually finished writing before the seal
step ran. The seal reported `match: True` for every case throughout. A check
that passes is not evidence that it covers what its name says, and here the
only thing that disagreed was looking at the directory.

**Fixed.** `SUFFIXES` is now all ten, with the reason recorded at the
definition, and `s3d_verify.py` imports it from `s3d_run.py` rather than
restating it — two lists that must agree are two lists that can drift.

**Consequence for the existing seals.** case-50 and case-51 were sealed under
the five-suffix list. Both are re-sealed over all ten before the OutcomeVerifier
runs; the five-suffix seals are retained alongside as `*.partial.json` rather
than overwritten, because the point of the record is what was sealed when, not
what the seal looks like now. No reference had been read for either case at the
time of re-sealing — the ordering §3.3 constrains is seal-before-gold, and gold
had not been read.

**Test.** `test_the_seal_covers_every_artifact_the_runner_writes` pins the set,
and `test_a_tampered_artifact_fails_the_seal` is the positive control that makes
the pinning mean something. S3C FAILURE 3 — an abstention mechanism that could
not abstain — is the reason the positive control is written before the seal is
trusted rather than after.

---

## FAILURE 2 — the seal test pinned the broken set

**What happened.** `test_the_seal_covers_every_artifact_the_runner_writes`
asserted:

```python
assert sealed == {"events.jsonl", "bindings.json", "deltas.jsonl",
                  "integrity.json", "invocations.jsonl"}
```

which is exactly the incomplete set FAILURE 1 is about. The test passed for
the entire run and would have **failed on the fix**. It was written as an
equality against a literal copied from the code it was testing, so it could
only ever confirm that the code had not changed — never that the code was
right.

**Why this is its own entry.** The other failures are wrong numbers. This one
is a test that made a wrong number harder to correct, which is the opposite of
what a test is for. A test that restates the implementation is a change
detector; a test that restates the *requirement* is a check. §3.3 step 2 lists
the artifacts by name, and the version of this test that was worth writing
asserts against that list — or better, against the run directory.

**Fixed.** Two tests now: one against the §3.3 list, one against the files case
50 actually produced. The second is the one that would have caught FAILURE 1
without anyone having to notice it in `ls`.

---

## FAILURE 3 — a check for a gold leak that reported a leak in prose

**What happened.** §7 requires that each stored entry have "reference answer
absent". The first version checked:

```python
str(right_option).lower() not in payload_text.lower()
```

case-51's `right_option` is `"D"`. The check therefore asked whether the letter
"d" appears in the payload's JSON — which it does, in `Radiologist`,
`verified`, `guidance`. It reported `reference_answer_absent = False` for a
payload containing no answer at all.

**Why this is a recurrence, not a new bug.** S3B FAILURE 4 is this exactly:
`exact_right_option` was dead code because a `len(v) >= 3` guard made a
one-letter option uncheckable, and it was found by a spike-in positive control
rather than by reading. The frozen `leakage_guard` was fixed then, by requiring
an answer cue word before the letter (`_ANSWER_CUE`). S3D's new admission check
did not call the guard — it paraphrased it, and paraphrasing a guard does not
inherit its fixes.

**Cost if it had shipped.** It reports a leak that is not there, which is the
safe direction, and misses a leak that is, which is not. A `False` on an
admission check is visible; the same expression returning `True` for a payload
that *did* contain "the correct answer is D" would have been invisible, because
that is the answer the check exists to give.

**Fixed.** The §7 checks now call `scan_prompt` on `render([memory])` — the
string the model would actually receive — and read the verdict from the guard's
`kinds`, with `test_a_single_letter_option_is_not_a_leak` asserting both
directions: prose containing "d" is not a leak, and "The correct answer is D."
is.

---

## FAILURE 4 — `n_admitted` counted storage, not admission

**What happened.** After the fix that stores `manual_review` entries (so the
audit trail survives — §6.3 keeps them for a human), the admission counter was
still incremented on `rec["stored"]`. `stored` is now true for case-51. The run
printed `"n_admitted": 1` and `new_admitted_memory_ids: ["exp-case-51-..."]`
while the headline store's retrievable entry count was 0.

**Why it matters.** It is the same shape as S3C FAILURE 4 — a count filled from
the wrong expression — and it fails in the direction that flatters the stage:
"the headline store gained an entry" is the result S3D was run to obtain, and
the number said so while the store disagreed.

**Fixed.** `admitted` is now `verdict["eligibility"] == gate.ELIGIBLE`, which
is the only thing that places an entry in the recallable success partition, and
`test_admission_counts_eligible_not_stored` pins the two apart.

---

## FAILURE 5 — the leakage rescan scanned nothing and passed

**What happened.** `leakage_rescan` scanned `store.initial_plan_retrievable()`.
With case-51 in `manual_review`, that list is empty — the loop body never ran,
and the summary was:

```
n_memories_scanned  : 0
gold_leakage_count  : 0
passed              : true
```

**Why this is the worst of the five.** It is S3C FAILURE 3 verbatim: an
abstention mechanism that could not abstain, now a leak scan that could not
find a leak. `passed: true` from zero iterations is the single most dangerous
output in this stage, because §13 makes `gold_leakage_count = 0` a hard
requirement and this satisfies it by construction.

**Caught by.** The `0` next to `n_memories_scanned` in the same JSON object.
The number that made it visible was the one nobody was reading.

**Fixed.** The scan covers every stored partition and reports `vacuous`
separately from `passed`, so a zero over an empty set cannot present as a clean
result. `n_injectable_scanned` is reported alongside `n_entries_scanned`,
because "5 scanned, 4 injectable" and "0 scanned" are the same headline number
and opposite findings.

---

## FAILURE 6 — the "expanded" store was not expanded

**What happened.** `build_store` built `expanded_success_store` from case 50-59
alone. Since case-51 did not reach `eligible`, the store held zero retrievable
entries. §9's audit would have run `BM25Index` over an empty document list and
reported a score distribution of `None` — a retrieval audit of nothing.

**Diagnosis.** "Expanded" was read as "the new block" rather than "the store,
with the new block added". §9 says *利用扩展后的 headline Success Store* — the
headline store after extension, which is four S3C entries plus whatever S3D
adds.

**Fixed.** `_seed_from_s3c` copies the four headline successes (cases 11, 20,
21, 27) into the expanded store before S3D's own entries are added; the legacy
two go to a separate `legacy_success_store` per §7. The audit now runs over 4
memories × 20 query cases.

---

## A note on what these six have in common

FAILURE 1 and 2 are about a check whose *scope* was wrong; 3, 4 and 5 are about
a check whose *denominator* was wrong; 6 is about a noun. Every one was found
by comparing two things that were supposed to agree — `ls` against the seal
list, a `0` against its own neighbour, a printed count against the store — and
none by reading the code that produced it.

That is the same conclusion S3C reached from a different direction, and it is
now three stages old. The rule S3D would hand to S4 is narrower than "check
your work": **for every number you report, print the count of things it was
computed over, next to it.** Three of these six are invisible without it and
obvious with it.
