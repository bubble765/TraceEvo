# S5B failure cases

Fifteen failures found running the final held-out stage. The recurring shape this time
is **a value computed from the wrong object** — a retry number read as "the
attempt failed", the first row's keys read as "the columns", a jsonl read as
"the file that carries the verdicts", a frozen driver's hash read as "the
protocol". Every one of them produced a number that was internally consistent
and externally wrong.

Two of them are about the stage's own conduct rather than its code, and those
come first.

---

## FAILURE 1 — the block was started twice, and the first pass was kept rather than deleted

**What happened.** The freeze manifest was written at `04:34:18Z` and the run
started at `04:34:52Z`. Five driver scripts — `s5b_overhead.py`,
`s5b_anomaly.py`, `s5b_ablation.py`, `s5b_tables.py`, `s5b_finalize.py` — were
then edited, from an integration check run against already-published S5A
artifacts in `/tmp`, and from reading the run log to find the token lines. By
the time the edits stopped, cases 80, 81 and 82 had been sealed by a code
version that no longer existed.

**Why it invalidated the pass.** §5.1's one-pass rule and §5.2's "same frozen
code" exist so that a block is produced by one version of the method. Three
traces from a superseded version inside the final block is precisely the thing
they prevent, and it would not have been visible in any artifact: all three
were sealed, all three would have verified.

**What was done.** The run was stopped, `heldout_original` was moved to
`heldout_original_superseded/` and `seals/` to `seals_superseded/`, and
`discarded_pass.json` records what happened, why, and that no reference was
read for those three cases. **Nothing was deleted** — §5.2's "不得删除第一次失败
记录" is about not erasing what a run did, and a pass that is deleted is a pass
that can be denied.

**The freeze was not re-written.** Re-freezing after case 80 had been read
would have made the manifest's hashes match the final code while destroying
the property the manifest was created to establish. It stands as written, and
`driver_drift_report.json` discloses every post-freeze edit by name with its
timestamp and whether it preceded the first seal.

**Why it is recorded rather than quietly fixed.** The alternative — keeping
the three traces — would have produced an identical-looking block with a
mixed provenance. The failure mode this guards against is not a wrong number;
it is a block whose stated provenance is better than its actual one.

---

## FAILURE 2 — the attempt record labelled a successful retry as an infrastructure failure

**What happened.** `s5b_run.py` writes `rerun_reason` per attempt:

```python
"rerun_reason": ("infrastructure: "
                 + ("stall watchdog killed the process" if stalled else
                    f"process exited {_proc.returncode} without integrity.json")
                 if attempt > 1 else "first attempt")
```

`attempt > 1` is the retry *number*, not a statement that the attempt failed.
The failing attempt is attempt 1, which is always labelled `"first attempt"`,
and the retry is labelled `"infrastructure: process exited 0 without
integrity.json"` — a failure's description attached to a run that succeeded
and returned 0.

**Why it matters beyond the wording.** §5.2 permits a re-run *only* for
infrastructure failure. The field is the artifact a reviewer would check to
confirm that restriction was honoured, and it said "infrastructure" three
times for three re-runs that were not infrastructure at all. A field that
asserts compliance is worse than an absent one, because it is the thing that
gets believed.

**Fixed, but not in the runner.** The runner was mid-pass; editing it would
have invalidated the block a second time. `traceevo/s5b_run_audit.py` was
written instead as a separate read-only module that reconstructs each attempt
from `heldout_original/run.log` and states, in `run_audit.json`, which
records the runner got wrong and what actually happened. The runner's field is
superseded, not corrected in place, and `run_audit.json` says so.

---

## FAILURE 3 — three re-runs fall outside §5.2's list

**What happened.** `run_audit.json`:

```
n_attempts_logged: 61   n_cases_attempted: 58
completed: 58   failed: 3   in_progress: 0
retries: 3   (case-104, case-107, case-121)
failure causes: {MODEL_OUTPUT_FAILURE: 3}
verdict: 3 of 3 re-run(s) fall outside §5.2's list
```

All three died at `central_agent.act()` with

```
ValueError: Not founded, current list：['Laboratory_Physician', 'Pathologist', 'Radiologist']
```

— the planner named an expert that is not in the expert list. §5.2 lists
network interruption, API 5xx, unexpected process exit and filesystem
failure. **A model's own output ending a case is none of those.** The
process did exit unexpectedly in the literal sense, which is the reading that
would let this pass; but the cause is upstream of the exit and it is the
model, not the machine.

**Not fixed, disclosed.** Nothing was re-run, excluded or re-sealed; all three
retries are inside the block and their traces are the sealed ones. The
question §5.2 raises — whether a case that dies this way should be recorded as
incomplete rather than retried — is left to the reviewer. It is a judgement
about protocol, not a bug, and making it unilaterally after seeing the result
is exactly what §5.2's restriction is for.

**A second observation, offered as context and not as a justification.** The
failure point is `on_dispatch_failed`, which is the same hook the A3 injector
manufactures (`unresolvable_expert`). So this block produced roughly 5%
natural A3-shaped failures at run time. That is a fact about the corpus, and
it is also the reason the retry rate is 3 rather than 0.

---

## FAILURE 4 — a CSV writer took its columns from the first row

**What happened.** The Gate aborted:

```
File "traceevo/s3c_expand.py", line 89, in _write_csv
ValueError: dict contains fields not in fieldnames: 'reason'
```

`_write_csv` used `fieldnames=list(rows[0].keys())`. `poison_successes`
produces rows that are not all the same shape: a twin the poisoner declines
carries an extra `reason`.

**Why it had never fired.** S3C's twins were all applicable, so every row had
the same keys. S5A's Gate took the absent-positive-class branch and built no
twins at all. This is the first block where a twin was `NOT_APPLICABLE`, and
the latent assumption surfaced on the first input that violated it.

**Fixed.** The writer now unions the keys, which is what every other writer in
this project already does. Nothing computed changes: the twin results were
produced before this call and the twin file is written after it. The fix
decides only whether a CSV exists.

**Not a frozen rule.** `traceevo/s3c_expand.py` is not among the 27 files in
`Final_Core_v1_freeze_manifest.json`'s `file_hashes`, and
`s5b_freeze.py --verify` reports 0 drift across those 27 after the change.

---

## FAILURE 5 — the Gate read the rule decisions from a file that does not carry them

**What happened.** The first successful Gate run reported:

```
correct-but-poisoned admission, all five rules: 0/19
```

**Diagnosis.** `B0_Naive` admits every candidate by construction
(`baseline_naive` returns `ELIGIBLE` unconditionally). A table in which Naive
admits nothing is therefore not a result about the Gate; it is a result about
where the numbers were read from. `poison_successes` writes two files:

```
success_poisoned_candidates.csv    per-rule decisions, all *_admits_success
success_poisoned_candidates.jsonl  the poisoned candidates (memory ids, payloads)
```

The module read the jsonl. It carries no `*_admits_success` field at all, so
every `t.get(key)` returned `None`, and `None` is not a false admission — it
is the absence of a reading, rendered as `0/19`.

**The check that should have been in the code is now in the code.** A rule
that admits everything must admit something:

```python
if rows and naive_admits == 0:
    raise SystemExit("B0_Naive admitted 0 of N poisoned twins ...")
```

**Fixed.** The module reads the CSV. The corrected result is the one in
Table 4 — Naive 19/19, OutcomeOnly 19/19, AuditOnly 2/19, IntegrityOnly 0/19,
TraceEvo 0/19.

**Disclosed timing.** This edit was made *after* the held-out traces were
sealed and after a first Gate run had been seen. It is listed in
`driver_drift_report.json` with `predates_run: false`. The justification is
not that the edit was harmless but that the correct value is determined by the
frozen Gate's own code: `baseline_naive` is unconditional, and the CSV it
should have been read from says `True` nineteen times. What the edit chose was
which of two files is authoritative, and the file that carries the verdicts is
the one the writer wrote them to.

---

## FAILURE 6 — comparing CSV strings to `True` with `is`

**What happened.** The fix for FAILURE 5 introduced the same all-zero table
from the other side. CSV values arrive as strings, so

```python
sum(1 for r in rows if r.get(f"{rule}_admits_success") is True)
```

counts zero rows however many say `"True"`.

**Why it is recorded separately.** It is a different defect that produces the
identical output, and it would have been diagnosed as FAILURE 5 recurring —
sending the next reader to look at the file paths again. Two causes, one
symptom; the fix for the first made the second reachable. It is now an
explicit string comparison.

---

## FAILURE 7 — a failed run left the previous run's output on disk

**What happened.** After the `NameError`, `gate_final.json` still held the
previous run's numbers. Reading it back — which is what any downstream script
or reviewer does — produced a complete, plausible Gate result from a run that
had crashed.

**Why it is the same family as FAILURE 5.** Both are a value that is real but
answers a different question than the one asked: 0/19 was a true statement
about the jsonl, and the stale `gate_final.json` was a true statement about an
earlier run.

**Fixed.** `_clear_outputs` deletes the module's outputs at the start of every
run, so a crash leaves a gap rather than a previous answer.

---

## FAILURE 8 — the claim matrix reported a leakage count from a scan that had not run yet

**What happened.** §19's row read:

```
* Gold leakage: — (non-vacuous scan).
```

The scan was fine (0 leaks over 58 candidates × 3,306 cross-case pairs). But
`main()` called `claim_matrix()` before `leakage()`, so the matrix read a file
that did not exist yet and wrote an em dash.

**Why it matters in a stage whose deliverable is a claim matrix.** "—" in that
row is indistinguishable from "the scan found nothing to report", and the
sentence around it says "non-vacuous", which asserts the opposite. A missing
number inside a sentence about that number is worse than a missing row.

**Fixed.** The scan runs first. The row now reads `0 (non-vacuous scan)`.

---

## FAILURE 9 — the audit module's own three failures

`s5b_run_audit.py` was written to catch FAILURE 2 and made three of the same
kind of error before it worked.

**(a) The success marker regex matched nothing.** The bridge prints
`[TraceEvo] case 104: 46 events, ...` — a space, not a hyphen. The pattern
wrote `case-\d+`, so it matched zero attempts out of 47 and reported every one
of them as failed with no exception. *An audit module that says everything
failed is worse than no audit module*, because its output is a catastrophe
report that a reader will act on. The block was in fact healthy at the time.

**(b) The numerator and the denominator counted different populations.** The
verdict printed `4 of 3 re-run(s) fall outside §5.2's list`: the numerator
counted *failed attempts* and the denominator counted *re-runs*, and a failed
attempt that had no successor was included in one and not the other. §5.2
governs re-runs, so the denominator is the re-runs and each is judged by the
failure that caused it. A failed attempt with no successor is a different
object and is now reported separately.

**(c) An attempt still in progress was counted as a failure.** `case-124` had
been running for two minutes; its log block had no completion marker yet, so
the module called it a crash. A log with no completion marker and a log with a
crash are the same bytes until you know whether anything is still writing. The
fix is a third state — `IN_PROGRESS`, keyed on `attempt_history.jsonl`, which
the runner writes per case only once its attempts are over — plus the console
marker as an independent second signal, so a disagreement between the two is
visible rather than averaged away.

---

## FAILURE 10 — a measured zero rendered as "not measured"

**What happened.** `s2_matrix.per_class_metrics` returns `f1 = None` for a
class that was injected and never detected, because precision is 0/0. Passed
through unchanged, that `None` renders as an em dash in every table — so a
class that was tested and completely missed is displayed identically to a
class that was never tested.

**Why the direction matters.** Both readings are wrong but only one is
flattering, and it is the one an em dash suggests. The ablation's `w/o
ProvenanceTier` arm drops A1 to zero; before the fix that cell was blank.

**Fixed.** A class with injections and no detections reports `f1 = 0.0` with
`f1_zero_by_no_detection: true` and its counts beside it. The macro average
now also covers every injected class rather than silently becoming a
three-class average exactly when a class failed completely.

---

## FAILURE 11 — the ablation patched a class attribute that does not exist

**What happened.** The `w/o CrossGraph` arm raised on its first line:

```
AttributeError: module 'traceevo.audit.anomaly_detector' has no attribute 'TaskArtifacts'
```

`anomaly_detector` imports `TaskArtifacts` *inside* `detect_task`, not at
module level, so the name is not an attribute of the module at import time.
The arm had to patch the class where it is defined (`traceevo.audit.injector`)
instead.

**Why it was caught cheaply.** The plumbing check ran every arm against
already-published S5A artifacts in a temporary directory before the held-out
block was spent. An ablation module that raises is louder than one that
returns a wrong number, and this one raised immediately.

---

## FAILURE 12 — the attempt history was buffered until the end of the block

**What happened.** `_attempt_log` was called once, after the loop over all 58
cases. When the first pass was stopped at case 82, **no attempt history
existed at all** — the records for cases 80, 81 and 82 were in memory and died
with the process.

**Why it is the condition's own subject.** §5.2 requires "attempt history
preserved" precisely so that what a run did survives the run. Buffering the
record until the run finishes preserves it only in the case where nothing
needed preserving.

**Fixed.** The log is flushed per case. The discarded pass's history is gone
and cannot be reconstructed; what is recorded instead is
`discarded_pass.json`, which states the counts and says the per-attempt detail
was lost to the buffering.

---

## FAILURE 13 — the token field was declared unavailable while the API was reporting it

**What happened.** S5A reported `TOKEN_UNAVAILABLE` for this pipeline, citing
§14's "不得估算". The gateway's log carries one line per call:

```
[LLM-API] Generation Completed | ... | Input token nums：1329 | Output token nums：901
```

The field was a false statement about the instrument, and §14's instruction is
conditional on the API *not* reporting usage. Reported this time: 6,940,081
input + 4,169,188 output = 11,109,269 tokens.

**A residual inconsistency, stated rather than smoothed.** The log carries
2,295 calls with usage; the ledger carries 2,160. The two instruments count
different things — the ledger records what the recorder saw through its hook,
the log records every generation the transport served — and the 135-call gap
is not reconciled here. Both numbers are reported with their definitions
rather than one being presented as the other.

---

## FAILURE 14 — the same `is True` defect, in the third place it was written

**What happened.** §11's table reported `Poison Admission` as 0/19 for **every**
rule, including `B0_Naive`. Naive admits every candidate by construction, so
0/19 was again impossible on its face.

`build_population` had its own copy of the comparison:

```python
"eligible" if t.get(f"{rule}_admits_success") is True else None
```

The values come from a CSV, so they are the strings `"True"`/`"False"`, and
`is True` matches neither. Every poison row's eligibility became `None`. The
table then reported `None` as a rate of 0.

**Why it is recorded as its own failure rather than as FAILURE 6 recurring.**
The identical expression was fixed in `s5b_gate.run` (FAILURE 6) and left in
`build_population`, twenty lines away, in the same file. A fix applied where
the symptom appeared is not a fix applied where the defect is, and the second
location was found only because the first one's check — *Naive cannot admit
nothing* — happened to apply here too.

**A rule that generalises it.** Any value crossing a CSV boundary is a string.
The project now has three instances of this in one stage, all of them written
by hand, none of them caught by a type checker, all caught by a
range-plausibility check on the output.

---

## FAILURE 15 — a formatting helper's numeric default invented a zero

**What happened.** §11's `Failure Capture Rate` for the AuditOnly arm has no
denominator — AuditOnly never assigns `failure_experience`, so it has nothing
to capture. `_ratio(0, 0)` correctly returned `rate: None`, and the table
rendered it as **`0.0000`**.

**Diagnosis.** The cell went through a helper with a numeric default:

```python
def _num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default
```

`float(None)` raises, the default returns, and a rate that does not exist is
printed as a rate of zero. This is S5A FAILURE 5 in a different instrument:
that one rounded a real measurement to nothing, this one manufactures a
measurement from an absent one.

**Fixed.** Rates and intervals render through helpers that keep an absent
value absent — an em dash, not a zero. The rule the two failures share:
**a formatting layer may not supply a value.** Its job is to render what is
there, and a default is a value.

---

## A note on what these have in common

Ten of the fifteen are **a value computed from the wrong object**, and in
every case the value was internally consistent — the arithmetic was right, the
formatting was right, and the answer was about something else. That is a
harder class than the previous stages' "a comparison across things that are
not comparable", because there is nothing to see at the point of use: `0/19`
looks like a measurement, `"infrastructure"` looks like a reason, `—` looks
like a result.

Two of them were caught only because a value was impossible on its face —
Naive admitting nothing, every attempt failing — which suggests the general
check: **for every number the stage produces, state a value it cannot take and
assert against it.** The Gate now does this for Naive. The others are checks
that could be added and, in this stage, were found by hand.

And one is not about code at all: the block was started twice. That failure
had no symptom, because both passes would have sealed and verified. It was
found by noticing that the freeze predated the code rather than by anything
the pipeline reported.
