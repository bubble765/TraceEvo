# TraceEvo S3D report

Per `TraceEvo_S3C_Review_S3D_Amendment_v1.md`. S3D ran case 50-59 with No-Memory
GoS under a seal-before-outcome protocol, and stops here.

**The headline number reached the threshold and the artifact it was supposed to
produce did not.** `headline_gold_safe = 5` (§6 asks for ≥ 5), but the fifth
success is held at `manual_review` by a frozen Gate rule that §5's six
conditions do not include, so the headline Success Store gained nothing. §12's
must-item 4 — *新 success 的真实 eligible path 可复核* — is not met, and there
is no eligible path to check.

This is a split verdict, not a pass and not the scarcity outcome. The finding is
the gap between the two bars, and it is reported as the main result below.

---

## 1. Case 50-59: outcome verification (§4)

Ten cases, all sealed, all seal-verified, none rejected.

| verdict | cases | n |
|---|---|---|
| verified_success | 51 | **1** |
| verified_incorrect | 53, 54, 56, 58 | 4 |
| uncertain | 50, 52, 55, 57, 59 | 5 |

Judge scores, in case order:

```
case-50  uncertain            [1, 1, 0]
case-51  verified_success     [2, 2, 2]
case-52  uncertain            [1, 1, 1]
case-53  verified_incorrect   [0, 0, 0]
case-54  verified_incorrect   [0, 0, 0]
case-55  uncertain            [1, 1, 1]
case-56  verified_incorrect   [0, 0, 0]
case-57  uncertain            [1, 1, 1]
case-58  verified_incorrect   [0, 0, 0]
case-59  uncertain            [1, 1, 0]
```

The verifier is `traceevo.evolution.outcome_verifier`, imported rather than
reimplemented — same prompt, same model, same temperature, three runs, same
0/1/2 rubric. `S3D_freeze_manifest.json` records the prompt's sha256 and the
judge configuration; the re-check after the run shows 27 files at their frozen
digests and zero drift. Raw judge output for all 30 calls is in `judge_raw/`.

## 2. Gold-safe triage (§5)

Six conditions, unchanged, reported per condition so a failure names itself.
Only case-51 reached the triage — it is the only `verified_success`.

| condition | case-51 |
|---|---|
| verified_success | ✅ |
| integrity_ok | ✅ |
| invocation_complete | ✅ |
| semantic_trace_complete | ✅ |
| no critical anomaly | ✅ (0 anomalies) |
| no unresolved decision-path P3 | ✅ (0 used P3) |

**`N_verified_success_50_59 = 1`, `N_gold_safe_50_59 = 1`,
`N_verified_success_but_unsafe = 0`.**

There is no case-22 in this block. case-51 has no anomaly at all and no P3 on
the decision path; it fails a condition case-22 also fails, but a different and
narrower one, and it fails it at the *Gate* rather than at the six-condition
test. That distinction is the whole of §4 below.

## 3. Headline count (§6)

| block | cases | verified_success | gold-safe | eligible |
|---|---|---|---|---|
| 10-29 (S3C, S3A holdout) | 20 | 5 | **4** | 4 |
| 30-39 (S3B build) | 10 | 0 | **0** | 0 |
| 50-59 (S3D expansion) | 10 | 1 | **1** | **0** |
| **headline** | 40 | 6 | **5** | **4** |
| 0-9 (legacy, excluded by §2) | 10 | 2 | 2 | 2 |
| 40-49 (probe, not in §6's sum) | 10 | — | — | — |

```
headline_gold_safe = 4 + 0 + 1 = 5   >= 5   §6 threshold MET
```

The exclusion of 0-9 and 40-49 is by construction, not by judgment: §2 sets
`headline_eligible = false` on legacy, and §6's sum names three blocks. The
legacy two cannot move the number, exactly as in S3C.

## 4. The finding — §5's gold-safe is not §7's eligible

case-51 passes all six of §5's conditions and the frozen Gate sends it to
`manual_review`:

```
2 P1 soft-grounded node(s) on the decision path;
§6.1 sends these to review rather than admitting them
```

The two nodes are `node-17` (`Head CT/MRI shows no acute intracranial lesions`)
and `node-23` (`Radiologist notes Histoplasma blood antigen cross-reacts with
Blastomyces, limiting its specificity`) — both audit-labelled
`soft_grounded_review`, both `used_in_reasoning`.

**§5's six conditions do not include §6.1's soft-grounding rule.** That is what
makes a case gold-safe and not eligible at the same time. The four S3C headline
successes all have `used_P1 = 0` and all reached `eligible`; case-51 has
`used_P1 = 2` and does not.

```
                  used_P1  used_P3  gold_safe  Gate eligibility
case-11  (S3C)        0        0       True     eligible
case-20  (S3C)        0        0       True     eligible
case-21  (S3C)        0        0       True     eligible
case-27  (S3C)        0        0       True     eligible
case-51  (S3D)        2        0       True     manual_review
```

**This is not a defect in either rule, and it is not a reason to change one.**
The two bars answer different questions. §5's conditions ask whether a trace's
outcome and process can be *trusted enough to verify against*; §6.1's P1 rule
asks whether its strategy is *clean enough to copy*. A decision path that leans
on expert synthesis and absence claims can arrive at the right answer — which
is precisely what case-51 did — and still not be a pattern worth handing to the
next case. §6.3 is explicit that `manual_review` is not a soft yes.

So case-51 is a second member of the family case-22 opened: **outcome correct,
process not clean enough to reuse.** case-22 fails on an unresolved P3 and an
A1 anomaly; case-51 fails on soft grounding. Neither may be counted as usable,
and neither may be quietly dropped — both are the mechanism working.

### Consequence for §12

| # | must-item | status |
|---|---|---|
| 1 | case 50-59 seal-before-outcome | **met** — 10/10 sealed, 10/10 verified |
| 2 | frozen config / audit / judge / Gate unchanged | **met** — 27 files, 0 drift; `gos` diff byte-identical to S3C's |
| 3 | `headline_gold_safe >= 5` | **met** — 5 |
| 4 | new success's real eligible path checkable | **NOT MET** — case-51 has no eligible path |
| 5 | correct-but-poisoned TraceEvo admission 0 | **met** — 0/4, with a confound, §6 below |
| 6 | gold leakage = 0 | **met** — 0 across 5 entries, 4 injectable, 80 cross-case pairs |
| 7 | failure memory still out of initial Plan | **met** — `initial_plan_ids() == []` |
| 8 | 28.6789 demoted to diagnostic | **met** — renamed, §8 below |
| 9 | cross-case review queue generated | **met** — 58 rows, all `UNREVIEWED` |
| 10 | case 60-137 not read or run | **met** — see §11 |

Nine of ten. The failure is item 4, and it is the item the whole stage exists to
satisfy.

## 5. The seal (§3.3)

```
seals written:            10   (+10 five-suffix partial seals retained)
seals verified:           10
seals rejected:            0
artifacts per case:       10
input_artifact_hash == recomputed, all 10 cases
```

The seal hashes every artifact the runner writes — events, bindings, deltas,
integrity, invocations, injections, provenance graph (JSON and PNG), replay
verdict and summary. The first version hashed five of the ten and is
`failure_cases.md` FAILURE 1; the ten cases were re-sealed over the full set
before the OutcomeVerifier ran, and the superseded seals are kept as
`seals/case-XX_seal.partial.json` rather than overwritten.

**The check was shown to fail before it was trusted.**
`test_a_tampered_artifact_fails_the_seal` modifies one artifact and asserts the
seal breaks and names which one; `test_a_deleted_artifact_fails_the_seal` covers
the other way an artifact stops matching; `test_check_seals_returns_a_row_per_seal`
stops either from passing vacuously. This is S3C FAILURE 3 applied forward — an
abstention mechanism reporting zero abstentions was indistinguishable from one
that worked until it was made to abstain.

Memory was off, and that is measured rather than asserted: the run wrote **0**
injection records across all ten cases (`*_injections.jsonl`), so the treatment
was absent from the artifacts, not merely from the environment.

## 6. Correct-but-poisoned twins, and their confound (§8)

Four twins of case-51, one per poison type, built by
`traceevo.s3c_expand.poison_successes` and injected by
`traceevo.s3b_gate.poison` — both imported, not reimplemented, so "the Gate's
result did not change with the new corpus" is a claim about one implementation
on new inputs.

| rule | correct-but-poisoned admitted |
|---|---|
| B0 Naive | 4 / 4 |
| B1 OutcomeOnly | **4 / 4** |
| B2 AuditOnly | 1 / 4 |
| B3 IntegrityOnly | **0 / 4** |
| **TraceEvo** | **0 / 4** |

§8's two requirements hold: OutcomeOnly > 0, TraceEvo = 0. Note B3 also
reaches 0/4 — the same pattern S3C found (B3 matches TraceEvo on poisoned
admission while admitting every clean wrong answer), so this block does not
separate them on the poison column either. And the cumulative table, S3C's
twins plus S3D's:

| block | successes poisoned | twins | TraceEvo admitted | OutcomeOnly admitted |
|---|---|---|---|---|
| 10-29 (S3C) | 4 | 16 | 0 | 16 |
| 50-59 (S3D) | 1 | 4 | 0 | 4 |
| **cumulative** | 5 | 20 | **0** | **20** |

**But the 0/4 is weaker than it looks and the difference is reported rather than
absorbed.** case-51 does not reach `eligible` even *clean*. So 0/4 is not
evidence that the Gate survives poisoning of this success — the Gate was never
going to admit it. Forcing `integrity_ok = True` isolates the layers:

| twin | anomaly | layer 2 verdict | caught by the anomaly layer? |
|---|---|---|---|
| PZ1 | A1 | `failure_experience` | yes |
| PZ2 | *(none)* | `manual_review` | **no** — held by the pre-existing P1 reason |
| PZ3 | A0 | `failure_experience` | yes |
| PZ4 | A5 | `failure_experience` | yes |

PZ2 (retrieval misbinding) produces **no anomaly at all**; with integrity forced
True its rejection comes from the same two P1 nodes that already held the clean
case. That rejection is a property of case-51, not a defence against
misbinding. S3C's 0/16 on four real `eligible` successes remains the
poison-resistance result; S3D's 0/4 is a robustness check whose confound is
stated. `new_success_poisoned_defence_layers.json` carries the per-twin detail.

## 7. The success store (§7)

```
new gold-safe successes in 50-59 : 1  (case-51)
admitted to the headline store   : 0
seeded from S3C                  : 4  (cases 11, 20, 21, 27)
headline store entries           : 4
legacy store entries             : 2  (cases 5, 9) — separate corpus
manual_review partition          : 1  (case-51, stored and not retrievable)
```

case-51 is stored in the `manual_review` partition — §6.3 keeps these for a
human — and the store is built with `initial_plan_partitions=("success",)`, so
it answers nothing to an initial-plan caller. Its seven §7 fields are all
present and all check out:

| field | value |
|---|---|
| gold diagnosis absent | ✅ (checked against the rendered block) |
| prediction absent | ✅ |
| reference answer absent | ✅ |
| source trace hash present | ✅ |
| audit summary hash present | ✅ |
| outcome verdict present | ✅ |
| extractor version present | ✅ |

The three "absent" fields are checked by calling the frozen `leakage_guard` on
`render([memory])` — the string the model would actually receive — rather than
by substring tests over the payload dict. That distinction has teeth: the first
version tested `right_option.lower() not in payload` and reported a leak for
case-51, whose right option is the letter `"D"` and whose payload therefore
"contains" it inside `Radiologist`. It is `failure_cases.md` FAILURE 3, and it
is S3B FAILURE 4 recurring in new code.

## 8. Gold leakage (§7/§13)

```
entries scanned      : 5   (every partition, not only the retrievable set)
injectable scanned   : 4
cross-case pairs     : 80  (memory x case 40-59, self excluded)
self  gold_leakage   : 0
cross gold_leakage   : 0
gold_leakage_count   : 0
vacuous              : false
```

**`gold_leakage_count = 0`, over a non-empty set.** The first version scanned
`initial_plan_retrievable()`, which is empty here because case-51 is a
`manual_review`; it would have printed `passed: true` from a loop that ran zero
times — S3C FAILURE 3 exactly, in the one check §13 makes a hard requirement.
The scan now covers every stored partition and reports `vacuous` separately
from `passed`.

## 9. Cross-case retrieval audit (§9) — and why 28.6789 is not a threshold

The expanded headline store holds 4 memories (cases 11, 20, 21, 27). Every
query case in 40-59, top-3, self-memories excluded, query text from the blinded
copy via `build_retrieval_key` — the same function the runtime uses, over the
same two fields visible before the task starts. No source or gold diagnosis
enters.

```
queries   : 20 (case 40-49, 50-59)
rows      : 58  (case-50 had one non-zero-scoring memory; the retriever
                 reports nothing rather than something irrelevant)
top-1     : min 1.011  median 6.666  max 15.121
all rows  : min 0.76   max 15.121
bands     : <2: 9   2-5: 23   5-10: 22   10+: 4
top-1 by memory : case-27 x6, case-20 x5, case-11 x5, case-21 x4
```

**`above_self_identity_threshold: 0`.** Not one of the 58 cross-case scores
reaches 28.6789; the maximum is 15.12, below even S3C's measured "other" ceiling
of 13.10 plus a little. So the threshold S3C derived separates *self* from
*other* and would abstain on **20 of 20** genuine cross-case queries.

That is §9.1's point demonstrated rather than asserted:

- what it proves — the abstention mechanism works, because it abstains;
- what it cannot prove — that a similar new case retrieves the *right*
  historical experience. A threshold set above every cross-case score makes
  retrieval impossible, and "0 hits" cannot distinguish "correctly abstaining"
  from "nothing to say".

**S4 must not inherit it as an online cross-case threshold.**
`self_identity_threshold_note.md` records the rename to
`self_identity_separation_threshold` and the reason. It stays useful as a
diagnostic: it is a real, measured separation of a case from itself.

One descriptor worth recording without judging it: top-1 is spread across all
four memories (6/5/5/4), so the ranking is discriminating rather than collapsing
onto a single generic memory. Whether any of those pairs is *appropriate* is
what the review queue is for.

## 10. Review queue (§10)

`cross_case_retrieval_review.csv`, 58 rows, fields exactly as §10 lists them,
**every `review_label` = `UNREVIEWED`**, and **no relevance column exists** —
a column that exists gets filled by something. No final diagnosis was used to
fill anything; the query summaries are the pre-task text and the memory
summaries are strategy fields only.

The label distribution is `{UNREVIEWED: 58}` and that is the intended output.
§10 leaves the judgment to external review or human sampling; this stage
produces the queue and does not answer it.

## 11. What was not read, and what was not changed

**Not read or run:** case 60-137, in any form. The runner refuses any case list
outside 50-59 before opening a file, and §9's query range stops at 59.

**Not changed:** no rule, threshold, regex or policy was modified. The re-check
after the run reports **27 frozen files at their recorded digests, zero drift**,
and the `gos` working-tree diff is byte-identical to the one S3C recorded
(`diff` against `S3C_experience_expansion/git_diff.patch` returns nothing), so
no GoS code changed either. The absence regexes in the freeze match
`S3A_migration/rule_freeze_manifest.json` hash for hash.

**Not implemented:** no Capability Profile, no agent-capability EMA, no
adaptive agent selection, no Planner modification, no memory injection, no
risk-triggered failure retrieval.

## 12. Failure memory (§11)

```
stored                   : true
auditable                : true
initial_plan_retrievable : false
initial_plan_ids         : []
activation_status        : inactive_until_S4
risk_triggered_retrieval : not implemented
failure_global_retrieval : not reopened
```

Sparse success retrieval did not reopen failure retrieval. §11's rule is
enforced by the store's `initial_plan_partitions=()`, so a caller that asks
gets `[]` rather than a caller that has been asked not to ask.

## 13. Answers to §15's fifteen questions

1. **verified_success / incorrect / uncertain in 50-59?** 1 / 4 / 5.
2. **Gold-safe success?** 1 (case-51).
3. **Verified-success-but-unsafe?** None in 50-59 by §5's six conditions. But
   case-51 is `verified_success` + `manual_review` at the Gate — the same family
   as case-22 at a different bar, and treated the same way: not counted as
   usable.
4. **Headline total (10-39 + 50-59)?** **5.**
5. **≥ 5?** Yes, by §6's count. No, by §12 item 4: the fifth success is not
   retrievable.
6. **Does the score-1 shape recur?** Yes, undiminished: 5 of 10 cases were
   uncertain, 3 unanimous `1|1|1` and 2 mixed `1|1|0`. Across S3C and S3D,
   8 + 3 = 11 unanimous `1|1|1`. The binding constraint on this corpus is the
   judge's power to separate "right" from "broadly right", not the Gate.
7. **Did new successes complete seal → outcome → gate → store?** Yes, all the
   way to the store — and the Gate stopped the last step. case-51 is sealed,
   verified, triaged, gates to `manual_review`, and is stored there.
8. **New correct-but-poisoned twins?** 4 twins, 0 admitted by TraceEvo, 4 by
   OutcomeOnly — with a confound, §6, that limits what that zero supports.
9. **Leakage still 0?** Yes, over 5 entries and 80 cross-case pairs, not
   vacuously.
10. **Failure memory still out of initial Plan?** Yes, `initial_plan_ids() == []`.
11. **Headline / legacy entries in the expanded store?** 4 headline, 2 legacy,
    in separate stores; 1 `manual_review`.
12. **Why is 28.6789 not a cross-case relevance threshold?** Because all 58
    cross-case scores fall below it (max 15.12). It separates a case from
    itself; as an online threshold it would retrieve nothing, ever.
13. **Cross-case score distribution?** 0.76-15.12, top-1 median 6.67;
    9 rows < 2, 23 in 2-5, 22 in 5-10, 4 at 10+. No block effect between 40-49
    (top-1 median 8.37) and 50-59 (6.25).
14. **An obviously transferable strategy pair, or all needing human judgment?**
    **None can be declared transferable from this data, and this stage did not
    try.** §10 forbids filling relevance from the final diagnosis, and no gold
    relevance label exists for cross-case pairs. Top-1 is spread across all four
    memories, which says the ranking discriminates; it does not say the pairs
    are apt. The queue is the deliverable, not an answer to it.
15. **Recommendation?** **None of the three cleanly.** The count reached 5 but
    the corpus did not grow: §12 item 4 fails while items 1-3 and 5-10 pass.
    `VERIFIED_SUCCESS_SCARCITY_PERSISTS` would misdescribe a count of 5;
    `GO S4A` would misdescribe a store with no new entry in it. §14 below names
    a fourth option and explains why it is the accurate one.

## 14. Recommendation — `NEEDS_GATE_SCOPE_DECISION`

**Not `GO S4A`.** §12's must-items are a conjunction and item 4 fails. S4's
premise is a corpus of reusable validated successes; S3D's headline count of 5
contains only 4 that the Gate will actually admit, and the fifth was stopped by
a rule S3D is forbidden to change. Proceeding would mean carrying a number into
S4 that overstates the corpus by exactly the amount S3D was run to fix.

**Not `VERIFIED_SUCCESS_SCARCITY_PERSISTS`** either. That verdict describes a
block that produced no successes; this block produced one, and the count did
reach 5. Reporting scarcity would be reporting a number rather than the reason
the number did not help.

**What actually happened** is narrower and needs a decision this stage cannot
make: **the two bars in the pipeline disagree, and no one has decided which one
counts for S4.**

```
§5's six conditions  ->  headline_gold_safe = 5   (the number §6 asks for)
§6.1's P1 rule       ->  headline_eligible   = 4   (the number a store can use)
```

Three ways forward, in the order this report would rank them:

1. **Accept 4 and treat `headline_gold_safe` as a diagnostic.** S4 starts with
   four reusable successes and the S3D block contributes none. The honest
   corpus is smaller than the headline and the paper should say 4.
2. **Widen the build block (60-69), accepting that more cases may just add more
   `manual_review`s.** case-51 is one case and the P1 rate on this block is
   unknown; on S3C's four successes it was zero. The prior from 40 cases is that
   roughly one success appears per ten cases, so a further block is a real cost
   for an uncertain yield.
3. **Reconsider whether §6.1's P1 rule should apply to the *headline count* as
   well as to admission.** This is a rules question, not a data question, and
   §D1 freezes the rule — which is why this report does not resolve it.

**The report's own reading is (1).** §6.1's P1 rule is the one that asks whether
a strategy is worth copying, and that is the question S4 depends on. A count
that includes a strategy the Gate would not hand to another case is a count of
the wrong thing. But that is an argument, not a decision, and the two numbers
are both in `headline_success_summary.json` for whoever makes it.

**S3D stops here.** No Capability Profile, no EMA, no agent-selection change, no
failure-memory activation, no use of the unreviewed cross-case retrievals in a
Planner, no change to the judge, the Gate or the audit rules, no backbone
change, and no automatic consumption of case 60+.
