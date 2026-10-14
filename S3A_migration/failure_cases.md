# S3A failure cases

Failures found while building and running S3A. They are here because they are
the part of the work a reviewer cannot recover from the artifacts: the code
that exists looks correct, and only these show what the first version got wrong.

Four of the nine are instrumentation bugs that made the ledger *look* complete
while recording the wrong thing — the exact failure mode this package exists to
prevent, committed four times while building it. Failure 9 is the migration
finding; the last entry is a correction to a delivered S2 artifact.

---

## FAILURE 1 — the ledger recorded 469 calls and learned nothing about any of them

**What happened.** The first full run produced 469 ledger rows, every one of
them `parse_status = failed`, `discard_reason = OTHER`, `committed_to_state =
false`. The file was well-formed, had the right number of rows, and said
nothing.

**Diagnosis.** `traceevo_bridge.ledger_parsed` guarded with:

```python
if _hooks is None or not call_id:
    return
```

The call sites pass `call_id = ""` deliberately, asking the ledger to resolve
the most recent pending call rather than threading an id through GoS's own
variables. The guard treated that empty string as "missing" and returned before
reaching the ledger. Every parse outcome was dropped on the floor, and the
`finish_task` sweep then closed all 469 calls as `failed`.

**Why it was hard to see.** The ledger's own summary was self-consistent: 469
calls, 469 failures, no warnings. Nothing crashed. Only the A4 recheck — which
must find a *discarded* call and found none — exposed it.

**Fixed.** Guard removed; an empty id is meaningful and documented as such.

---

## FAILURE 2 — the A4 discard had nowhere to land

**What happened.** With failure 1 fixed, both A4 rechecks still reported
`n_discarded = 0` for runs whose own logs say
`Failed Parsing: Content stage result inconsistent with decision stage`.

**Diagnosis.** Rejection resolved via `pending_call_id()`, which returns the
most recent *pending* call. By the time GoS rejects the content-stage response,
its parser has already accepted it — nothing is pending — so the resolution
returned `""` and the rejection vanished.

This is exactly the seam §4 is about. The injected decision response parses
*successfully*; the discard happens one stage later. A resolution rule that
only knows about pending calls cannot express it.

**Fixed.** `rejectable_call_id()` — pending, else the most recently accepted
call — plus `test_rejection_applies_to_a_call_the_parser_already_accepted`.

---

## FAILURE 3 — the retrieval call was uninstrumented, and the totals hid it

**What happened.** After failures 1 and 2, 74 calls were still closing as
`failed`. They were all `stage = OTHER`, which was supposed to mean "belief
text".

**Diagnosis.** `evidence_retriever` makes its own LLM call and was never given
a stage. 117 `OTHER` rows against 36 belief calls was the tell. This is not a
minor omission: that call produces `EVIDENCE_READ`, the only point where an
external record enters the pipeline, and it is the call the whole A2 binding
story rests on.

**Why the totals hid it.** Total calls still matched the run log, because the
uninstrumented call was still *counted* — just as a row that never got a parse
outcome. A coverage check that only compares totals would have passed.

**Fixed.** `EVIDENCE_RETRIEVAL` added to the stage enum, with the reasoning for
extending §3.2's illustrative list recorded in `invocation_ledger.py`.

---

## FAILURE 4 — every row shared one list

**What happened.** A refactor replaced the row literal with `dict(_TEMPLATE)`
so the schema could be generated from the template. Two tests went red: one
row's `committed_event_ids` contained `['evt-1', 'evt-1', 'evt-2']`.

**Diagnosis.** `dict()` is shallow. The template's `committed_event_ids` list
was shared by every row, so committing one event appended it to all of them.
Worse than a cosmetic bug: it made `committed_to_state` read as true for calls
that produced nothing — the opposite of what the ledger exists to say.

**Fixed.** The list is re-created per row; `test_rows_do_not_share_their_event_list`
pins it.

---

## FAILURE 5 — the recording is not a transcript of the run that made it

**What happened.** The A4 recheck's first attempt used strict replay over the
injected run's recording and died at request 5 with
`recorded key=ea797fcb… actual key=4cd11d54…`.

**Diagnosis.** In extend mode the recording keeps the *file* it started with
and appends to it. The file is therefore

```text
[clean 0..N-1][injected run's live tail N..M]
```

while the run executed

```text
[clean 0..4][injected run's live tail N..M]
```

Replaying it linearly serves the clean run's call 5 where the injected run had
already diverged.

**Why this was a gift rather than an obstacle.** The failure *is* the check:
strict replay refused rather than quietly going live, which is what makes the
eventual rc=0 meaningful. Splicing the two pieces and replaying strictly
reproduces the injected run exactly — verified by matching the reproduced
provenance digest against the original run's, both cases.

---

## FAILURE 6 — a rule was edited after it was frozen

**What happened.** `rule_freeze_manifest.json` was written, the holdout run was
started, and then `invocation_ledger.py` was edited (the failure-4 fix).
`--verify` reported the mismatch.

**Diagnosis.** Not a subtle one: the freeze is only meaningful if it precedes
the run. This was self-inflicted, and it was caught by the verification step
rather than by remembering.

**Fixed.** The run was stopped after one case, the partial output archived to
`S3A_runs/s3a_migration_aborted_freeze_race/`, the manifest regenerated, and
the run restarted from case 10 against the frozen code. The archived run is
kept rather than deleted so the gap is visible in the filesystem, not just in
this paragraph.

**What this says about the freeze check.** It is not ceremony: it caught a real
violation, on me, within an hour of being written.

---

## FAILURE 7 — §4's expected shape was wrong, and the measurement was right

§4 expects the injected model response to show `parse_status = discarded`. It
shows `accepted`, in both cases.

**Diagnosis.** The substituted response *is* well-formed JSON with a legal
`decision`, so the decision parser is correct to accept it. GoS's expert agent
reads only `{type, decision}` from that stage and regenerates content in a
second one; the rejection lands on the *next* call, which is the one that
answers `retrieve` where the injected decision said `analyze`.

**Not "fixed".** The code was not changed to make the injected call read as
discarded; the result is reported as measured, and §4's goal — a localizable
model-call root — is met by the pair of calls rather than by one field.
Rendering this as a pass by relabelling would have been the easy wrong thing.

---

## FAILURE 8 — byte-identical event files are impossible, and the first comparison said so

The §8 regression's first verdict was FAIL on `event_hashes` alone. Every
semantic field, every prediction, every graph and every prompt matched.

**Diagnosis.** Events carry a wall-clock `timestamp`, and `event_hash` /
`prev_hash` derive from it, so no two runs of the same replay can ever be
byte-identical. A byte-hash comparison was measuring the clock.

**Fixed.** The digest drops exactly those three fields and hashes everything
else, payload and parent links included. The three are named in the source with
the reason, so a future reader does not "restore" the stricter-looking
comparison.

---

## FAILURE 9 — the frozen absence rule misses two phrasings it was never shown

Not a bug in the code. A finding about the boundary, and the one thing S3A was
built to discover.

S2 §Q11 named the absence regex as the package's highest overfitting risk: the
alternation was extended node-by-node on Dev-Small, so it fits those ten cases
by construction. It was frozen for S3A (D2) and not touched.

On the holdout it produced 5 P3 nodes out of 150 Evidence. Two are unambiguous
misses:

| node | why it misses |
|---|---|
| "No fever was documented at presentation" | the alternation has `no <word> identified/found/noted/…`, not `was documented` |
| "No infectious exposure or positive cultures have been identified" | the `no <word> … identified` form has a 20-character gap limit; this gap is longer |

Verified by running the frozen regex against both strings: `guess_semantic_kind`
returns `clinical_evidence`, which is why they were tiered P3 rather than P2.

**Not fixed.** Widening the alternation would drop the holdout P3 count from 5
to 3 and the clean-trace alert rate from 0.25 to 0.20 — a rule tuned on the
data it is being evaluated against, which is exactly what §7 forbids and what
D2 was written to prevent. The miss is recorded, located, and left in place.

What this does and does not say: the rule still classifies absence claims at
roughly the Dev-Small rate (23 vs 18 nodes), so it has not collapsed. What it
says is that its coverage is *locale-shaped* — it knows the phrasings ten cases
produced. The next time this rule is touched, it should be replaced by
something that does not enumerate phrasings, which is the migration audit's
eventual conclusion to hand to S3B.

---

## Correction to a delivered S2 artifact

While comparing tier mixes I found that `S2_audit/S2_report.md` Q2b said
`lineage_complete` was "False for 27" of 89 Evidence nodes, which contradicts
its own `evidence_boundary_analysis.csv`: the false cases are 18 + 6 + 3 + 2 + 1
= **30**, and 30 + 59 = 89. The breakdown printed beside it was correct; the
total was not.

Corrected in place with a dated note rather than silently, because the CSV it
disagrees with is the artifact a reviewer would check it against. No metric
changes — every downstream number in S2 was computed from the CSV.
