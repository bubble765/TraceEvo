# Success and failure cases (§12 Q12)

Every case below is reproducible from `injection_ground_truth.jsonl` +
`anomaly_results.csv`; no case is paraphrased from memory.

---

## SUCCESS 1 — A4 corrupted analysis: detected 10/10, root exact

**Injection.** Case 3, `A4/corrupt_analysis`. The first expert's ANALYSIS body
is replaced with a fabricated finding, keeping its event id, its
`parent_event_ids`, its `input_refs` and its `status="success"`. The event hash
chain and the whole delta chain are re-sealed, so every integrity check passes.
Nothing in the *structure* of the trace is wrong.

**Detection.** `A4 / ungrounded_analysis_quantity`, root = exactly the injected
`ANALYSIS` event, Top-1 ✓.

**Why it works.** The rule does not try to judge whether the analysis is
well-reasoned — an expert legitimately ranges over the whole graph state it can
see, and per-sentence coverage flagged 74% of clean analyses. The rule looks
only at **quantities**: a measurement with a unit ("42 ng/mL", "18%", "32 mmHg")
or a bare decimal > 1 ("7.4") either appears somewhere in the material the
expert had, or it was invented. On the ten clean Dev-Small tasks, one of ~30
numeric claims is ungrounded and it is a language-mismatch artefact.

**The near-miss that shaped the design.** `"a standardized uptake value of
7.4"` has no unit, so the first version of the extractor saw no quantity at all
and the injection went undetected on 5 of 10 cases. The fix (bare decimals > 1)
raised A4 recall from 0.50 to 1.00 — but the *reason* it is > 1 and not ≥ 0 is
another clean-data finding: the model writes its own confidence inline as
`"(leading hypothesis, 0.45)"`, and including those made every such analysis
look corrupted.

---

## SUCCESS 2 — A5 suffix truncation: a gap the hash chain cannot see

**Injection.** Case 7, `A5/truncate`. The last three events are removed,
including the `FINAL_REPORT`. No re-sealing.

**The chain does not notice.** `verify_chain` returns `ok=True`. Every
surviving event still points at its predecessor, and a hash chain has no notion
of how long it was supposed to be. This is asserted as a documented limitation
in `test_a5_truncation_is_invisible_to_the_hash_chain`, not hidden.

**The completeness check does.** `integrity_auditor.detect_completeness` applies
the invariants a complete run must satisfy — the log ends in exactly one
`FINAL_REPORT`; every non-ingest `GRAPH_UPDATE` / applied `BACKTRACK` /
`DRILL_DOWN` has a delta; every `EVIDENCE_READ`'s record exists in the
bindings; every `parent_event_ids` resolves. Truncation violates the first.
A5 detection is 40/40 = **100%**, with zero false integrity alarms on the 69
clean and semantic-injection traces.

**Why it matters beyond the metric.** Adding the completeness layer was
necessary to reach §9's 100% A5 gate, and it is a genuine strengthening of S1's
integrity story rather than a threshold tweak.

---

## FAILURE 1 — A1 precision 0.59 (strict) / 0.91 (clean-conditioned)

**What happens.** The A1 rule fires 7 times on traces injected with something
*other* than A1: A0→A1 (2), A2→A1 (2), A3→A1 (1), A4→A1 (1), plus 1 on a clean
trace (the case-4 boundary node).

**Diagnosis — most of this is not a false positive.** Degrading an ANALYSIS
(A0) or tampering a retrieval record (A2) genuinely does strip downstream
Evidence nodes of their support; the nodes really are unsourced afterwards.
Flagging them is correct. The strict precision number counts them as errors
because the trace's label says "A0", not "A1" — an artefact of single-label
evaluation over a genuinely multi-anomaly trace.

**What is actually wrong.** The single clean-trace positive: case 4 node-12,
"Patient received acupuncture for midback pain before symptom onset". It is a
restatement of the case history in the model's own words, and its input
coverage is 0.43 against a `input_inference_min_coverage` of 0.70. Lowering the
threshold to catch it would have to move to ≤0.29, which is *inside* the
natural cluster of ungrounded nodes (0.00–0.29) — the overfitting §4.2
forbids. It is left as a reported finding for human adjudication.

**Not fixed by tuning.** Per §9's instruction, this is reported as a
false-positive taxonomy entry rather than suppressed:

| sub-population | count | judgement |
|---|---|---|
| genuine propagation from A0/A2/A4 | 5 | correct behaviour, mis-scored by single-label evaluation |
| A3 collateral | 1 | unexplained; under investigation |
| clean-trace boundary case | 1 | adjudication required; threshold deliberately not tuned |

---

## FAILURE 2 — A2 `wrong_record_binding` missed on case 0

**What happens.** 18 of 19 A2 injections detected; case 0's binding swap is
missed (recall 0.947).

**Diagnosis.** The detector requires that the *recorded* `match_tier`
(`verbatim` / `paraphrase`) cannot be reproduced from the record the binding
names. On case 0 the swapped-in record happens to reach the paraphrase
threshold against the label anyway — the injected "wrong" record is not wrong
enough. This is a genuine detector limit, not a metric artefact: the rule
decides binding validity by re-running the same classifier that made the
binding, so a swap between two textually similar records is invisible to it.

**Not fixed.** Tightening the threshold would trade this case for false
positives on clean traces, where paraphrase bindings legitimately sit close to
the boundary. It is recorded as a known limit; distinguishing two similar
records needs a signal the current features do not carry (e.g. binding-time
candidate sets, which the pipeline does not persist).

---

## FAILURE 3 — A4 corruption does not propagate in the *artifact* matrix

**What happens.** For all 10 artifact-level A4 injections,
`semantic_reach.reached_graph = false`: the fabricated finding never appears in
a graph node. The corruption lives in the ANALYSIS body, but the graph nodes in
the recorded trace were produced by the *original* `GRAPH_UPDATE` responses,
which do not contain the fabricated text.

**Diagnosis.** This is a limitation of the artifact-mutation mechanism, not of
the detector, and it means the artifact matrix cannot answer §7's propagation
question for A4. A4's `affected_events` are still computed from the DAG and the
metric is 1.0 — but that measures the plumbing, not whether the error reached
the conclusion.

**Consequence.** The propagation claim for A4 rests entirely on the end-to-end
response-substitution study, which re-runs the pipeline so the corrupted
analysis *is* what the central agent reads. That study has now run — and it
also returns L0, for a different and more interesting reason. See FAILURE 5.

---

## FAILURE 4 — two prompt-level defects found by the audit but out of S2 scope

Neither is an injected anomaly. Both were surfaced while validating detectors
and are recorded because they affect measurements:

1. **Language mixing.** Two of the clean analyses (case 2, case 8) are written
   entirely in Chinese while the case, the records and the report are in
   English. The quantity extractor handles this (numbers are
   language-independent, with a CJK unit table), but the *finding* rule does
   not, and a fabrication written in Chinese would evade it.
2. **Confidence leakage.** The model writes its own confidence scores inline in
   analysis prose (`"(leading hypothesis, 0.45)"`), which are not clinical
   claims at all. Any quantity-based rule has to exclude them.

Both are upstream prompt/behaviour issues that S2 must **not** fix (§13: no
changes to GoS reasoning). They are listed so S3 does not rediscover them as
detector bugs.

---

## FAILURE 5 — an injection at the decision/content seam leaves no trace at all

This is the end-to-end A4 result, and it is a limitation of the *audit*, not of
the injection.

**What happens.** Two response-substitution studies (cases 8 and 5) replaced the
first expert's decision-stage response with a fabricated analysis. Both
injections clearly reached the pipeline — the substituted response is in the
run log verbatim, `prompt_changed = true`, and the run goes live from the next
call. And in both cases `semantic_reach` is **L0**: the fabricated text appears
in no graph node, no event payload and no report.

**Diagnosis.** GoS's expert agent runs a *decision* stage and a *content* stage
(`agents/expert_agent.py:79` and `:126`). Only `{type, decision}` is read out of
the decision response, and the content stage's prompt is built from
`decision_result["decision"]` alone (`prompts/prompt_generation.py:333-334`). A
fabricated analysis delivered at the decision stage is therefore dropped before
it can become state: the decision flips `retrieve → analyze`, the content stage
disagrees, the step is discarded as a parse error, and **no event is emitted for
it**.

**Consequence, in two parts.**

1. §5.4's check is **not applicable**, not failed. There is no injected ANALYSIS
   event for the auditor to name — so `root_localized.applicable = false` and
   the §7 cell is `n/a`. Rendering that as "no" would report a detection miss
   the detector never had a chance to make.
2. An anomaly class that leaves no event is invisible to trace-only auditing.
   This is the same shape as FAILURE 2 in SUCCESS 2 (A5 truncation) and it is
   the strongest argument so far that the S2 audit boundary needs a second
   observability channel, not more rules over the same events.

**Reporting correction made here.** The decision/content disagreement that the
injection triggers also fires spontaneously in clean runs (case 5's clean run:
3; case 8's clean run: 0). The case-study result records both counts, so the
injection is not credited with a standing GoS behaviour.

**Not a reason to change GoS.** Per §3 and §13, nothing in the pipeline was
adjusted to make the injection propagate. §8 accepts L0, and L0 is what the
measurement says.
