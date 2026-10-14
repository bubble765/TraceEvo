# S4B failure cases

Failures found building S4B. The dominant shape this time is **a control that
measured itself**: three of the five below are a probe whose setup, not the
mechanism, decided the answer. That is a specific hazard of S4B's position —
the stage's headline is a count of how often a guard fires, and a guard that
does not fire and a probe that never asked it to are indistinguishable from
the outside.

---

## FAILURE 1 — the positive control was not P3, so it measured itself

**What happened.** §9.2's first control is `Evidence: "Troponin is markedly
elevated"`, `source = none`, supporting the current hypothesis. The first
version of the control supplied a retrieved record as plausible background:

```
Troponin I was 0.02 ng/mL, within the reference range.
```

and put the assertion against it. The frozen classifier returned
`P1_SOFT_GROUNDED / record_paraphrase` — correctly, because with that record
present the claim *is* a loose paraphrase of it — and the guard did not fire.

**Why this is the most dangerous failure in the stage.** The output was
`trigger = false` on a control labelled "P3, must trigger". Read quickly, that
says the guard is broken. Read carefully, it says the control was not P3. Both
readings are available from the same line of output, and the wrong one would
have led to "fixing" a guard that was working — in the direction of making it
fire on paraphrases, which is the P1 intervention §3 explicitly rules out.

**Diagnosis.** A record was supplied for "realism" without asking whether it
changed the label under test. The controls' job is to isolate one condition
each; background that alters the label defeats that.

**Fixed.** The controls now vary exactly one thing each and hold the rest at
their bare minimum: A/B have no records at all, so the label is P3 because
nothing could ground it; C changes only the edge; D supplies the verbatim
record and changes only the label; E supplies the analysis text and changes
only the label; F changes only the node type. `s4b_controls.py` records for
each row what it varies and what it holds fixed, so a reader can check the
isolation instead of inferring it.

---

## FAILURE 2 — the regression's exclusion set was incomplete, and it reported a regression

**What happened.** §9.1's regression compares a guard-off replay against a
pre-S4B run of the same case. The first version excluded `timestamp` from the
semantic-event comparison and produced:

```
DIFFERS  guard_off_A vs guard_off_B (determinism)
   semantic_sequence_hash: 8f653cc793b27fd4... != dd09759f9e759782...
conclusion: "REGRESSION: guard-off is not inert"
```

**Diagnosis.** Diffing the two event files field by field showed exactly three
fields differing, on all 37 events: `timestamp`, `event_hash` and `prev_hash`.
The last two are *hashes over the event including its timestamp*, so they differ
whenever the clock does. Excluding the time but not the hashes computed from it
is internally inconsistent, and the inconsistency produced a finding about the
wrong subject — "the guard changed the pipeline" — when the pipeline's 37 events
were identical in every field it decides.

**Why it belongs next to FAILURE 1.** Both are comparisons that manufactured a
defect. FAILURE 1 would have made the guard look broken; this one made the
pipeline look changed. In a stage whose result may well be a zero, a comparison
that invents problems is the failure that costs the most, because it pushes
toward "fixing" things.

**Caught by.** Not by the summary — the summary said REGRESSION, emphatically.
By opening both event files and diffing them field by field, which is the
manual version of the very thing the automated comparison was supposed to do.

**Fixed.** `VOLATILE = {"timestamp", "event_hash", "prev_hash"}`, with the
reason recorded at the definition. The re-run reports IDENTICAL against both
the second run and the 2026-09-17 reference.

---

## FAILURE 3 — an interface mismatch that would have tiered everything P3

**What happened.** `classify_tier` reads records as dicts (it is called with
the contents of `_bindings.json`); `classify_evidence` reads them as
`RetrievalRecord` objects (it attributes them to a binder). The runtime holds
objects and the post-hoc audit holds dicts, so a pre-commit classifier that
reuses both must convert.

The first version passed the objects to `classify_tier`, which raised
`AttributeError: 'RetrievalRecord' object has no attribute 'get'`.

**Why the loud failure is the lucky one.** The dangerous version of this bug is
a record type that *does* have `.get` — for instance if the conversion had been
done in the other direction, or if a dict-like wrapper were introduced later.
Then `best_record` would silently find no grounding for every candidate, every
Evidence node would tier P3, and the guard would fire on every graph update.
The run would complete, `guard_actions.jsonl` would be full, and the stage's
headline would be the maximum rather than the true value.

**Fixed.** `precommit_tiers` converts `RetrievalRecord` to dicts explicitly,
with the mismatch and its silent form documented at the call site.

---

## FAILURE 4 — the same control error, twice, in the test for FAILURE 1

**What happened.** `test_strip_removes_only_the_offending_node_and_its_edges`
built a three-node proposal: an unsupported claim supporting the hypothesis, a
sourced node quoting the record verbatim, and the hypothesis. It then asserted
that only the unsupported node was stripped.

It failed — `n1` was not stripped — because the test passed `_records()` in
order to make `n3` groundable, and that same record made `n1`'s troponin claim
a P1 paraphrase. Exactly FAILURE 1's mechanism, in the test written after
FAILURE 1 was diagnosed.

**Why it is recorded anyway.** Knowing the mechanism is not the same as
applying it. The rule the failure teaches — *a fixture that makes one node
groundable may ground another* — has to be checked against each fixture, and
the second occurrence is evidence that the lesson had not been operationalised.

**Fixed.** The unsupported label is now "The patient has a moderate pericardial
effusion", which shares no vocabulary with the troponin record, and the comment
says why.

---

## FAILURE 5 — `strip_offenders` leaves `refines` edges from the removed node in place

**What happened, and why it is not (yet) a defect.** §8.2 says to remove the
offending Evidence node *and its SUPPORT/REFUTE edge*. `strip_offenders` does
exactly that and leaves any other edge from that node — a `refines` edge, say —
in the committed delta.

That leaves a delta whose `edges` may reference a node absent from its `nodes`.
`central_agent` tolerates it (`temp_id2real_id.get(src, src)` falls back to the
raw id, and `add_edge` on a missing endpoint is the graph's business, not the
guard's), and the S4B test asserts the current behaviour explicitly so it
cannot change silently.

**Recorded rather than fixed** for two reasons. First, dropping more than §8.2
names would be the guard making a larger intervention than it is authorised to
make, and the two arms would stop being comparable on the thing being measured.
Second, an edge whose source node was removed is a question about what the
graph should contain, not about what the guard may do — and the honest
disposition is to report it and let the arm's own metrics show whether it
matters. If a dangling edge ever appears in a committed delta during case 60-69,
it will be visible in `_deltas.jsonl` and belongs in this file as a finding.

---

## FAILURE 6 — a metric named "ungrounded" that counted the reasoning the pipeline is supposed to do

**What happened.** §12.1 lists "Decision-path ungrounded Evidence count" among
the primary safety metrics, separately from the P3-used rate. The first version
defined it as:

```python
used_in_reasoning and not externally_grounded and not lineage_complete
```

On case 60 that reported **2 ungrounded nodes on the decision path**. Both were
`P2_DERIVED` — a hypothesis and an absence claim. `lineage_complete` is False
for them because their derivations (`hypothesis`, `absence_claim`,
`agent_inference`) are `SELF_DECLARED` rather than `CONTENT_LINEAGE`, which is
the auditor saying "this is the model's own reasoning, correctly labelled", not
"this is unsourced".

**Why it matters more here than in an earlier stage.** The metric feeds §10's
branch, which decides whether the Guard arm runs at all. A count named
"ungrounded evidence" that includes the pipeline's legitimate derived reasoning
inflates the apparent risk, and inflating it points toward "there is plenty of
risk here, run the full arm" — the direction that spends a holdout block on an
experiment the data does not support. S3A's `uncommitted_call_rate` and S4A's
P3 target were the same mistake in the other direction; the constant is that
the name and the definition have to be written next to each other.

**Fixed.** The count is now the P3-used numerator, which is what "no source and
no legitimate derivation" means in the frozen cascade, and P2 nodes on the
decision path are reported separately as `n_derived_used` with their
derivations. `s4b_risk.py` records the coincidence explicitly: for Evidence
nodes, "ungrounded" and "P3" are the same set by construction, so §12.1's two
metrics share a numerator and differ only in rate-versus-count.

---

## FAILURE 7 — the freeze was taken before the code it froze existed

**What happened.** §6 requires the policy and the freeze to be written before
case 60 is read, and both were. But `freeze_manifest.json` hashed 29 files at
**11:23:26Z**, and four of them were then edited:

```
11:23:26Z  freeze_manifest.json written
11:24:12Z  traceevo/config.py                     (the guard config section)
11:26:00Z  gos/agents/central_agent.py            (the hook insertion)
11:26:16Z  traceevo/hooks.py                      (on_graph_candidate)
11:27:18Z  traceevo/evolution/grounding_guard.py  (the record-dict conversion)
11:34:25Z  first case-60 event
```

`--verify` after the runs reported drift on exactly those four files. The
freeze had been taken while the guard was still being written — it pinned a
half-built implementation and then correctly reported that the implementation
had moved.

**Why this is worse than it looks.** A freeze manifest's whole value is that
its hashes were taken *before* the thing they constrain. One taken mid-
construction passes every check at the moment it is written and fails the only
check that matters later, and in between it certifies nothing. This is the same
shape as S3D FAILURE 1 — a seal covering five of ten artifacts — in the stage
whose entire purpose is enforcing order on a two-arm experiment.

**Fixed, and what replaces the hash list.** The manifest is re-frozen with a
`supersedes` block naming the premature one. The re-freeze is dated *after* the
runs, so it cannot by itself demonstrate that the code was fixed during them —
and that is stated in the manifest rather than glossed. What demonstrates it is
file timestamps, which are better evidence for this particular question than a
hash list: the last implementation edit precedes the first case-60 event by
**seven minutes**, and no implementation file was touched again until after the
last seal at 12:40:19Z.

**The rule this yields.** The freeze must be taken *after* the implementation
is complete and *before* the first holdout case is read, and those two
conditions have to be checked in that order. S4B satisfied the second and not
the first. A stage that freezes early is not being careful; it is producing a
document that will disagree with reality and then have to be explained.

---

## A note on what these seven have in common

Six of the seven are a measurement whose *setup, definition or timing* decided the answer: a control
that grounded its own subject, a comparison that excluded a field's inputs but
not its outputs, a test that repeated the first and a fixture that grounded two
nodes when it meant to ground one. The fifth is a boundary question the guard
declines to answer on its own authority.

S3D's rule was *print the count of things each number was computed over*. S4B's
is narrower and harder: **for every check, say what it holds fixed.** A check
whose setup is unstated cannot be distinguished from a check that was arranged
to pass, and in a stage that may report a zero, that is the difference between
a result and a tautology.
