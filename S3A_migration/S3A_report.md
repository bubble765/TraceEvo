# TraceEvo S3A report — invocation observability and blind migration audit

Backbone: `deepseek-v4-flash` (fixed for all phases, never changed mid-run).
Data: Dev-Small 0–9 for regression and the A4 recheck; **Migration-Holdout
10–29** for the blind audit. Cases 30–137 were not read or run.

S3A did the two things it was asked to do and nothing else: a second
observability channel for LLM calls, and a migration audit of the frozen S2
rules on cases never used before. **No Experience Memory, no Experience Gate,
no Capability Profile, no planner change, no self-evolution experiment.**

---

## Summary

| §7 GO condition | measured | |
|---|---|---|
| 1. 20/20 cases complete, or the failure has an auditable cause | *(see §M)* | |
| 2. ledger coverage = 100% | **469/469** on the replay control | ✅ |
| 3. ledger↔event binding is replayable | **288/288** model-produced events bound, 0 unbound | ✅ |
| 4. A4 case5/8 invocation root localized | **both** | ✅ |
| 5. no schema/DAG break on 10–29 | *(see §M)* | |
| 6. no order-of-magnitude evidence alert explosion | *(see §M)* | |
| 7. no gross absence-rule failure | *(see §M)* | |
| §8 non-invasiveness (ledger off = ledger on) | **identical on all 11 compared fields** | ✅ |

---

## Q1 — Does the ledger cover every actual LLM call?

Yes, on the control where it can be counted exactly. Over the Dev-Small replay
that T1 recorded (10 cases):

```text
LLM calls counted from the run log ("Generation Completed")   469
rows in the invocation ledger                                 469
```

Coverage is not asserted from that alone. Two things were checked against it:

- **Every ledger row joins to a recorded call.** `prompt_hash` is
  `request_key(payload)` — the same function the recorder keys on — so a row
  that described a request GoS never made could not exist. All 469 matched.
- **Every call site is instrumented.** The first version was not, and the gap
  was invisible in the totals: `evidence_retriever` — the call that produces
  `EVIDENCE_READ`, the only place an external record enters the pipeline — was
  uninstrumented and showed up as 81 rows stuck `pending` and closed as
  `failed`. The counts by stage are what exposed it, and they are now pinned:

  | stage | calls |
  |---|---|
  | `GRAPH_UPDATE` | 46 |
  | `CENTRAL_PLAN` | 26 |
  | `EXPERT_DECISION` | 132 |
  | `EXPERT_CONTENT` | 132 |
  | `EVIDENCE_RETRIEVAL` | 81 |
  | `REPORT` | 16 |
  | `OTHER` (belief text) | 36 |
  | **total** | **469** |

  `EXPERT_DECISION` = `EXPERT_CONTENT` = 132 is the invariant that makes a
  missing stage visible: the two stages alternate, so an imbalance means a call
  was dropped rather than absent.

## Q2 — Does ledger on change GoS behaviour? (§8)

No. Two replay arms over the identical recording, differing only in
`TRACEEVO_LEDGER`:

| compared | ledger off | ledger on | equal |
|---|---|---|---|
| prompt sequence hash | `37a66e6112bac731` | `37a66e6112bac731` | ✅ |
| response sequence hash | `4d7a642f6c83fbc0` | `4d7a642f6c83fbc0` | ✅ |
| predictions (10 cases) | identical | identical | ✅ |
| report hashes (10 cases) | identical | identical | ✅ |
| final causal graphs (10 cases) | identical | identical | ✅ |
| semantic provenance digests (10 cases) | identical | identical | ✅ |
| graph delta logs (10 cases) | identical | identical | ✅ |
| ledger rows | 0 | 469 | (the point) |

Replay exhaustion was checked first in both arms: a run that quietly did less
work would otherwise make the two arms trivially "equal".

One correction made here and worth recording. The event-file comparison cannot
be a byte hash: every event carries a wall-clock `timestamp`, and `event_hash` /
`prev_hash` are derived from it, so no two runs of the same replay are ever
byte-identical — the first attempt "failed" on that alone. The digest strips
those three fields and hashes the rest, payload and parent links included, so
the comparison is strict about everything that carries meaning.

## Q3 — Can A4 case5/8 be localized to a model call? (§4)

Yes — and the shape is not the one §4 anticipated, which is the finding.

§4 expected `parse_status = discarded` on the injected call. Measured:

| | case 5 | case 8 |
|---|---|---|
| injected call | `call-case-5-00005` | `call-case-8-00005` |
| stage | `EXPERT_DECISION` | `EXPERT_DECISION` |
| `parse_status` | **accepted** | **accepted** |
| `committed_to_state` | false | false |
| the next call | `…-00006` `EXPERT_CONTENT` | `…-00006` `EXPERT_CONTENT` |
| its `parse_status` | **discarded** | **discarded** |
| its `discard_reason` | `DECISION_CONTENT_MISMATCH` | `DECISION_CONTENT_MISMATCH` |
| semantic event root | **n/a** | **n/a** |

The injected response is *accepted*, and that is correct rather than a
near miss: it is well-formed JSON with a legal `decision`, so the decision
parser has nothing to reject. What the injection causes is the **next** call
being thrown away — the content stage answers `retrieve` where the injected
decision said `analyze`, GoS raises its consistency error, and the step dies
without emitting anything.

So §4's goal is met in substance: **the root is localizable** — to a model call,
with the exact prompt hash the substitution used (`ae86418a36ec…` for case 5,
`827d671ab685…` for case 8, both matching the substitution sidecar's
`recorded_key`), and to the call it caused to be discarded. What S2.1 could
report only as `n/a` now has a named, replayable referent. Reporting it as
"the injected call was discarded" would have been wrong.

**Invocation root localization and semantic-event root localization are
different quantities**, as §4 requires, and they now have different answers:
the first is `yes, call-case-{5,8}-00005`, the second is still `n/a` by
construction.

### How this was reproduced without new live calls

The A4 injected runs were made in extend mode, so their recordings contain the
whole trajectory. Replaying them under `strict=true` reproduces the run
exactly, and one divergent prompt would raise instead of quietly going live.
The reconstruction is needed because the extend-mode file is not a linear
transcript — it is `[clean 0..N-1][injected live tail]` while the run executed
`[clean 0..4][injected live tail]`. Splicing those back gives the real
sequence:

| | case 5 | case 8 |
|---|---|---|
| clean prefix | 55 | 50 |
| recording | 119 | 114 |
| substitution at call | 4 | 4 |
| reconstructed transcript | 69 | 69 |
| strict replay | rc=0 | rc=0 |
| reproduced events digest = original | ✅ | ✅ |

No live call, no change to the injection content (§3). The reproduction is
checkable rather than asserted: the provenance digest of the reproduced run
equals the original injected run's.

## Q4 — How many calls are discarded after happening?

On the 10-case clean replay: **0** of 469. That is the right answer for clean
runs and it is also why the metric needs the injected runs to be meaningful.

On the A4 injections the disagreement count is **not** attributable to the
injection on its own:

| | injected run | clean run | attributable |
|---|---|---|---|
| case 5 | 2 | **3** | 1 |
| case 8 | 1 | 0 | 1 |

Case 5's *clean* run hit the same disagreement three times — more than its
injected run. The decision/content consistency check is a standing feature of
GoS at temperature 0.8, so reporting the injected count alone would have
credited the injection with behaviour it did not cause. Both counts are in
`a4_invocation_recheck/case{5,8}.json` under `discard_baseline`.

## Q5 — Do discarded calls enter semantic state?

No, and the ledger is what makes that checkable rather than assumed.

- 288 provenance events were produced by a model response; **all 288** bound to
  a call, all by exact text-hash binding.
- The only events with no call behind them are the three types that no LLM
  produces: `AGENT_CALL` (44), `BACKTRACK` (4), `DRILL_DOWN` (6).
- Discarded calls have `committed_to_state = false` and an empty
  `committed_event_ids`, by construction and by test.

A third category turned out to matter, and it is new in S3A: **accepted calls
that produced no event**. On the clean replay there are 145 — 132 decisions
(a decision produces nothing on its own; the content call is what commits) and
13 content calls whose retrieval query came back empty and which GoS silently
dropped. Those are calls that happened, were parsed, and left no trace in the
event log at all. Before the ledger they were indistinguishable from calls that
never happened.

## Q6 — How does the 10–29 tier distribution compare with 0–9?

*(see §M)*

## Q7 — Do the S2 absence/derivation rules still hold on new cases?

*(see §M)*

## Q8 — What are the holdout alert rates?

*(see §M — reported as alert rates, not FPR: the holdout has no gold labels,
so a finding there is not known to be false.)*

## Q9 — Which alerts need a human?

*(see §M, and `migration_review_queue.csv`)*

## Q10 — Is the audit fit to be an Experience Gate input?

Not as it stands, and the reason is specific rather than general.

The audit is good at what it does: the ledger closes the S2.1 hole, bindings
are exact, and the rules survive transport to unseen cases *(§M quantifies
this)*. But §5's admission rule needs `outcome judged correct`, and
`experience_eligibility_contract.md` §3.1 shows that field is **not computable
from a trace**. Every available proxy — self-reported confidence, absence of
anomalies, agreement with retrieved records — fails in a way that would store
a guess as a fact.

The consequence is concrete: **on unlabelled data, `Validated Success` cannot
be issued.** What the audit *can* support is `Validated Failure` (a localized
anomaly root needs no correctness judgement) and `manual_review`. S3B should be
designed around that limit rather than around a Gate that admits successes it
cannot actually validate.

## Q11 — Are any contract fields uncomputable?

One: `outcome`. Everything else — `integrity_ok`, `invocation_complete`,
`semantic_trace_complete`, `has_critical_anomaly`, `evidence_profile`,
`uncommitted_calls`, `eligibility`, `reasons` — is computable today from
artifacts S3A produces. The field-by-field table is in
`experience_eligibility_contract.md` §3.

## Q12 — GO / NEED_REVISION / STOP?

*(see §M)*

---

## §M — Blind migration audit, cases 10–29

Frozen before the first holdout case, verified unchanged after the last:

```json
{"frozen": true, "unchanged": true, "differences": []}
```

One violation happened and is in `failure_cases.md` F6: the ledger module was
edited after the manifest was written, `--verify` caught it, the run was stopped
after one case, the manifest regenerated, and the run restarted from case 10.
The partial first attempt is kept at
`S3A_runs/s3a_migration_aborted_freeze_race/`.

### The run

**20/20 cases completed.** No aborts, no missing traces, no schema or DAG
break. 748 live LLM calls, all recorded in the ledger.

| §6.2 metric | holdout 10–29 | Dev-Small 0–9 |
|---|---|---|
| `clean_trace_alert_rate` | **0.2500** (5/20) | 0.1000 (1/10) |
| `evidence_alert_rate` | **0.0333** (5/150) | 0.0112 (1/89) |
| `decision_content_discard_rate` | 0.0107 (8/748) | 0.0000 (0/469) |
| `uncommitted_call_rate` | 0.2968 (222/748) | 0.3092 (145/469) |

| P0–P3 over **Evidence** nodes | holdout (150) | Dev-Small (89) |
|---|---|---|
| P0 `DIRECT_GROUNDED` | 87 — **58.0%** | 52 — 58.4% |
| P1 `SOFT_GROUNDED` | 2 — 1.3% | 1 — 1.1% |
| P2 `DERIVED` | 56 — **37.3%** | 35 — 39.3% |
| P3 `UNSUPPORTED_ASSERTION` | 5 — **3.3%** | 1 — 1.1% |
| `externally_grounded` | 87 — **58.0%** | 52 — 58.4% |
| `lineage_complete` | 126 — **84.0%** | 59 — 66.3% |

`uncommitted_call_rate` is the one §6.2 metric with no S2 counterpart. It is
stable across the two datasets (0.297 vs 0.309), which is what a structural
property should look like: decisions commit nothing by design, and the rate is
dominated by them rather than by anything case-specific.

### Verdict: **GO**, with the alert rate at the review boundary and no headroom

None of §7's automatic flags tripped — but `clean_trace_alert_rate = 0.2500`
sits exactly on `> 0.25`. One more alerted trace in the holdout would have
forced `NEED_REVIEW`. This is the same position S2 was in with a 10.0% clean
FPR against a ≤10% gate, and it is reported the same way: as measured, with the
margin named.

**All five alerts are the same rule.** `A1 / evidence_tier_p3_used`, one node in
each of cases 12, 18, 19, 22, 28. There is no suggestion of a rule family
breaking down on new data; there is one rule firing at a rate worth a human's
attention.

## Q6 — How does the 10–29 tier distribution compare with 0–9?

Remarkably stable, on the dimension that matters most for an Experience Gate.

`externally_grounded` — the flag D1 introduced so P2 could not be read as
external evidence — is **58.0% on the holdout against 58.4% on Dev-Small**. A
tier boundary that had been overfitted to ten cases would be expected to move
here; it did not.

The mix is likewise close: P0 58.0% vs 58.4%, P2 37.3% vs 39.3%. The one real
movement is P3, 1.1% → 3.3%, which is 1 node becoming 5 — small numbers, and
Q7 is about what those 5 actually are.

`lineage_complete` rose from 66.3% to 84.0%, which is worth naming rather than
burying in a table. It is not a rule improvement — the rule is frozen. It means
the holdout cases produced proportionally fewer self-declared derivations
(recommendations, domain knowledge, bare inferences) and more content with a
concrete antecedent. Since that mix is a property of the cases, not the
auditor, it also means **Dev-Small's 66.3% is not a stable estimate** of how
much of this pipeline's Evidence lacks a content lineage.

## Q7 — Do the S2 absence/derivation rules still hold on new cases?

They did not collapse, and they do show exactly the failure D2 predicted.

The absence rule is still doing its job at a similar volume — 23 Evidence nodes
classify as `absence_claim` on the holdout against 18 on Dev-Small, and 17 of
the 150 carry the `absence_claim` derivation. What changed is *which* phrasings
it misses. All five P3 nodes classify as `clinical_evidence`, i.e. no
derivation rule claimed any of them. Reading them:

| case | node | what it is |
|---|---|---|
| 22 | "No fever was documented at presentation" | **absence claim the regex misses** |
| 28 | "No infectious exposure or positive cultures have been identified" | **absence claim the regex misses** |
| 19 | "Patient's consciousness is preserved during the jerking episodes" | negative clinical finding; arguably an absence claim |
| 12 | "Echocardiography shows plethoric inferior vena cava" | measurement claim, coverage 0.17 against its record |
| 18 | "Lymphocyte subset analysis shows T and B lymphopenia" | measurement claim, coverage 0.20 against its record |

The first two are unambiguous. The regex covers `not (been) identified` and
`no <word> identified`, but not `no <long phrase> have been identified` nor
`no <word> was documented` — the alternation was extended node-by-node on
Dev-Small (S2 §Q11 named this as the package's highest overfitting risk) and
those two phrasings simply never came up there.

**Per §7, none of this was fixed.** The regex is byte-identical to the frozen
manifest. The migration audit's job was to find out whether the boundary
travels, and the honest answer is: *mostly yes, with a known and now
locatable soft spot.* Widening the alternation to cover these two phrasings
would take the holdout P3 count from 5 to 3 and the clean-trace alert rate from
0.25 to 0.20 — which is exactly the tuning §7 forbids, and exactly why the
number is left where it is.

The two measurement claims (cases 12, 18) are not a rule defect at either end.
They need a human to read the record and say whether the echocardiography
report supports "plethoric inferior vena cava". That is what the review queue
is for.

## Q8 — What are the holdout alert rates?

Reported as **alert rates, not FPR**. The holdout has no gold labels; a finding
there is not known to be false, and naming it a false positive would import a
claim the data cannot support.

```text
clean_trace_alert_rate   0.2500   (5 of 20 traces, one alert each)
evidence_alert_rate      0.0333   (5 of 150 Evidence nodes)
```

§7's named concerns, checked directly:

- **Order-of-magnitude explosion?** No. 3.3% against 1.1% is a factor of 3 on
  counts of 5 and 1 — not meaningful as a ratio, and far from an explosion.
- **P3 rate > 10%?** No, 3.3%.

## Q9 — Which alerts need a human?

`migration_review_queue.csv`, 12 rows: the 5 P3 nodes, the 5 A1 findings they
produced, and 2 P1 soft-grounded nodes. Each carries the node text, the record
it was compared against, the score, the record context, and a specific question
to answer.

Ranked by how much a human decision would change:

1. **cases 22 and 28** — these are absence claims, and a human will agree
   quickly. Their value is that they delimit the frozen rule's known blind spot.
2. **cases 12 and 18** — genuine unknowns. Either the record supports the
   finding and the threshold is too high, or the model asserted a measurement
   that is not in the record, which is a real fabrication.
3. **case 19** — a judgement call about whether a preserved-consciousness
   observation is an absence claim at all.
4. **the 2 P1 nodes** — lowest priority; P1 means a candidate record was found
   above the soft threshold, and they are in the queue for completeness rather
   than because anything looks wrong.

## Q12 — GO / NEED_REVISION / STOP?

**GO.**

§7's seven conditions, against the measurement:

| condition | met | evidence |
|---|---|---|
| 1. 20/20 complete or auditable failure | ✅ | 20/20 complete, 0 aborts |
| 2. ledger coverage 100% | ✅ | 469/469 on the control; stage counts balance on the holdout |
| 3. ledger↔event binding replayable | ✅ | 288/288 model-produced events bound by text hash, 0 unbound |
| 4. A4 case5/8 invocation root localized | ✅ | both, `call-case-{5,8}-00005` (Q3) |
| 5. no schema/DAG break on 10–29 | ✅ | 20/20 clean chains, 0 DAG anomalies |
| 6. no order-of-magnitude alert explosion | ✅ | 0.0333 vs 0.0112 |
| 7. no gross absence-rule failure | ✅ *(with a named soft spot)* | Q7: 2 clear misses in 150 nodes, located, left unfixed per §7 |

**What GO does not mean.** It does not mean the audit is ready to be an
Experience Gate. Q10 shows the blocker is `outcome`, not the rules, and it is
not something more audit work can fix. GO here means: the audit boundary
transported to unseen cases without needing to be adjusted, which is the
precondition §9 set for S3B and the only claim this measurement supports.

**The one thing I would put in front of a reviewer first** is that the clean
alert rate is 0.25 against a 0.25 threshold. The PASS is real but it has no
margin, and a reviewer who wants a margin should say so now — because the only
ways to get one are to widen the absence rule (forbidden by §7 on this data)
or to accept a smaller holdout. Neither is mine to decide.

---

## Changes to GoS

No GoS reasoning logic was altered. The S3A edits to the working copy are all
observational: a keyword-only `stage` argument on `llm_generate_response`, the
ledger's open/response calls inside it, parse-outcome reporting at the call
sites, and commit binding inside the existing provenance hooks. Every one is a
no-op when `traceevo.enabled` is false, and §8's regression shows the ledger
itself changes nothing. `git_diff.patch` has the full diff.

The one addition to §3.2's stage enum is `EVIDENCE_RETRIEVAL`, explained in
`invocation_ledger.py`: the enum in §3.2 is illustrative, and filing the
retrieval call under `OTHER` would leave the ledger blind to the most
audit-relevant call in the pipeline.

## Test suite

133 tests; `pytest S3A_migration/tests` runs standalone. The S3A additions are
`test_invocation_ledger.py` (13 tests), of which the load-bearing ones are:

| test | pins |
|---|---|
| `test_discarded_call_is_the_a4_shape` | the record S2.1 could not produce |
| `test_entry_exists_before_the_parser_runs` | the entry predates the parse |
| `test_commit_binds_by_the_committed_text` | exact, not positional, binding |
| `test_commit_refuses_to_guess_when_no_call_carries_the_text` | the degraded fallback is not bound to a model call |
| `test_rejection_applies_to_a_call_the_parser_already_accepted` | the A4 seam's rejection has somewhere to land |
| `test_rows_do_not_share_their_event_list` | a shallow-copy bug that made every row's commit list the same list |
| `test_ledger_survives_a_process_that_dies_mid_parse` | the A3 shape: the file is readable after a crash |
