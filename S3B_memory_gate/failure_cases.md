# S3B failure cases

Failures found while building and running S3B. As in S3A, these are the part of
the work an artifact listing cannot recover: the code that exists now looks
correct, and only this file shows what the first version got wrong.

The first six are the ones that matter. Every one of them is a case of the
package reporting a clean result while doing nothing — a §13 guard that passed
after scanning zero injections, an exposure rate of 0.0 that could not have
been anything else, a §13-mandated check that was unreachable code. This is the
failure mode the whole package exists to catch, and it was committed six more
times while building it.

---

## FAILURE 1 — the §13 leakage guard reported `gold_leakage_count = 0` after scanning nothing

**What happened.** The first `--plan` run printed:

```json
{"gold_leakage_count": 0, "n_injections_scanned": 0, "passed": true,
 "verdict": "GO"}
```

A passing gate. It had examined no prompts, because the loop that builds the
injections skipped every one of them.

**Diagnosis.** `s3b_probe.plan()` derived a memory's source case by splitting
its id:

```python
src_task = mid.split("-")[-2] if mid.startswith("exp-") else ""
src_case = int(src_task.split("-")[1]) if src_task.startswith("case-") else None
if src_case is None:
    continue
```

Memory ids are `exp-case-30-43de9fb3ca05` and `poison-PZ1-case-30`. For the
first, `split("-")[-2]` is `"30"`, not `"case-30"`; for the second the branch is
not taken at all. `src_case` was therefore always `None`, and the loop body
never ran.

**Why it was hard to see.** `passed: true` and `verdict: "GO"` are exactly what
a clean scan prints. The tell was `n_injections_scanned: 0` sitting next to ten
cases that had all retrieved hits — but the verdict line is what a reader looks
at, and a guard that finds nothing and a guard that looks at nothing produce the
same verdict.

**Fixed.** The source case is carried on each record as `source_case_id`, written
by the gate that created it, and joined on `candidate_key` rather than re-derived
from an id. A memory with no recorded source is now a hard `SystemExit`, not a
`continue`: an injection the guard cannot resolve must not be reportable as
clean.

**Also fixed, at the same site.** `plan()` now compares
`n_injections_scanned` against the number of memories actually retrieved and
fails the gate if they differ, so a partial scan cannot produce a `GO` either.

---

## FAILURE 2 — the naive store was not the store the gate had described

**What happened.** `gate_results.csv` shows 48 admissions for the naive rule.
The store built from it held 20 entries.

**Diagnosis.** `build_stores()` matched gate rows to records by guessing:

```python
rec = next((v for v in poisoned.values()
            if v["task_id"] == row["task_id"]
            and v["memory_id"].endswith(row["candidate"].split("-")[-1])), None)
```

`row["candidate"]` is `case-30-PZ1`, so `.split("-")[-1]` is `PZ1`. Memory ids
are `poison-PZ1-case-30`, which does not end in `PZ1`. The lookup failed, the
fallback returned the first poisoned record for that task, and all four poison
kinds for a task were written to the same file. The 28 lost records were exactly
the PZ2/PZ3/PZ4 twins — the experiment's treatment condition.

**Why it was hard to see.** `store.add()` overwrites by `memory_id`, so the
collision was silent. The store looked plausible: 20 real files with real
payloads, on a rule that admits everything.

**Fixed.** Join on `candidate_key`, an explicit field written by the gate. The
count is now 48 retrievable for the naive rule, which is what the gate said.

---

## FAILURE 3 — every exposure rate was 0.0 because two id namespaces never met

**What happened.** With failures 1 and 2 fixed, `poisoned_experience_exposure_rate`
was still `0.0` for an arm that retrieved poisoned memories in 10 of 10 cases.

**Diagnosis.** The unsafe/poisoned sets were built from `gate_results.csv`, keyed
by the gate's name:

```python
unsafe_ids = {r["candidate"] for r in gate_rows if r["kind"] != "clean"}
# -> {"case-30-PZ1", ...}
```

and compared against retriever output, keyed by memory id:

```python
any(m in unsafe_ids for m in s["memory_ids"].split("|"))
# -> {"poison-PZ1-case-30", ...} vs {"case-30-PZ1", ...}  -> never equal
```

The intersection is empty for every input. `0.0` was not a measurement; it was
the only value the expression could produce.

**Fixed.** A `key_of` map from memory id to gate name, and the matched keys are
recorded per row in `retrieval_results.csv` so a reviewer can recompute the rate
without re-deriving the mapping. M1 now reads 1.0.

---

## FAILURE 4 — a §13-mandated check that could never fire

**What happened.** A positive control appended each of four known-bad strings to
a real injection and re-ran the guard. Three fired. `exact_right_option` did not.

**Diagnosis.** `scan_prompt` handled the right option inside a loop shared with
the reference diagnosis:

```python
for key in ("final_diagnosis", "right_option"):
    v = str(gold.get(key) or "").strip()
    if len(v) >= 3 and v.lower() in low:
```

`Right Option` is a multiple-choice letter. `len("D") >= 3` is false, so the
branch was unreachable for every real dataset. §13 lists `Right Option` as a
mandatory scan and the scan could not fire on any input — and its silence was
indistinguishable from a clean result.

**Asymmetric risk.** This one ran the *safe* way: the check that was dead is a
detector, so its absence under-reports leaks rather than inventing them. A dead
check in the Gate would have admitted poison.

**Fixed.** The short option is matched by an answer-cue pattern
(`answer|option|choice|correct|selected|choose|picked|key` within 24 characters
of the letter), because a bare letter cannot be substring-searched without
matching the `d` in every word. Seven cases pin both directions in
`test_guard_checks_the_right_option`, including a cue followed by a *different*
option, which must not fire.

**Note.** This check was dead during the first `--plan` run, so that run's
`gold_leakage_count = 0` did not include a right-option scan. The plan phase was
re-run after the fix, before any behaviour experiment started, and the result is
unchanged at 0. No GoS process was running against that plan.

---

## FAILURE 5 — all three arms died before doing any work, because a path was relative

**What happened.** The first `--run` returned `rc=1` for M0, M1 and M2 within
seconds.

**Diagnosis.** `run_arm` passed the blinded dataset as a relative path:

```python
env["TRACEEVO_DATASET_CSV"] = os.path.join(build_dir, "dataset_blinded.csv")
```

and GoS is launched with `cwd=GOS`, so it resolved against `gos/` and did not
exist. `s3b_build.py` had the same line and had only worked because its `--out`
happened to be absolute when it was invoked.

**Why it matters even though it crashed loudly.** It crashed because pandas
raised `FileNotFoundError`. A path that resolved to *some* readable csv would
have run the entire experiment against the wrong dataset — and with
`TRACEEVO_DATASET_CSV` pointing at an unblinded file, that would have been a
gold leak with no error at all.

**The same bug's quiet half.** Fixing the dataset path stopped the crash and
left the same defect in the four CLI paths. `--out-csv`, `--html-dir`,
`--json-dir` and `--log-dir` were still relative, so every arm wrote its
`results.csv`, `logs/` and `graphs/` into `gos/S3B_runs/...` rather than into
its own directory. Nothing failed; the files simply appeared somewhere else,
and only an unrelated `git status` in the GoS copy surfaced them.

This half is the more instructive one. The fatal path was found because pandas
refused to open a missing file — the failure announced itself. The silent half
produced exactly the artifacts the pipeline expected, in a directory nobody was
looking at, and would have been discovered only when a reviewer went to read
them.

**Fixed.** `os.path.abspath` on all six paths in both runners.

---

## FAILURE 6 — every arm hit HTTP 401: the token was in a file nobody loaded

**What happened.** With failure 5 fixed, each arm reached its first model call
and died: `LLMPermanentError: API call error：HTTP 401 [autherror] Invalid API key`.

**Diagnosis.** The transport reads the bearer token from `TRACEEVO_API_KEY`
(`gos/configs/LLM_Configs.yaml`). The token lives in a git-ignored `.env.local`.
`traceevo/s2_cases.py` has `load_env_file()` and its runner calls it;
`s3b_build.py` and `s3b_probe.py` were copied from that runner and the call was
dropped, so the S3B scripts worked only when the token happened to be exported
in the invoking shell.

**Why it was hard to see.** It is an environment difference, not a code defect:
the same script succeeds or fails depending on how the operator's shell was
started, and the failure appears at the first API call rather than at startup.

**Fixed.** All four S3B runners (`s3b_build`, `s3b_probe`, `s3b_verify`,
`s3b_score`) call the existing `load_env_file()` from `s2_cases` rather than
reading the file themselves, so there is one implementation of "pick up the
token". Verified with a live call before relaunching: `deepseek-v4-flash`,
reply `ok`.

---

## FAILURE 7 — the verifier's return value contained the reference diagnosis

**What happened.** `test_verifier_output_contains_no_gold_text` failed. §4.2
requires every judge prompt to be kept, and the judge prompt necessarily
contains the reference, so `verify_trace` was returning the gold text inside
`judge_runs` — in the structure that flows on to the Gate.

**Diagnosis.** Two requirements pulling against each other, resolved wrongly:
the prompt was stored where the verdict lives.

**Fixed.** Separated. The return value carries the verdict and a *redacted*
prompt (`<reference withheld: sha256:...>`); the full transcript is written to
`raw_dir/`, where only the evaluation reads. `reference_sha256` lets a reviewer
confirm which reference was used without the reference entering anything
downstream. `test_verifier_output_contains_no_gold_text` now asserts the gold
string does not appear in `json.dumps(result)`.

---

## FAILURE 8 — the Gate recorded forbidden payload content and admitted it anyway

**What happened.** `test_forbidden_payload_content_is_rejected` failed. A
candidate whose payload carried the reference diagnosis was annotated with a
reason string and then continued down the success path.

**Diagnosis.** `evaluate()` appended to `reasons` and fell through. A reader
counting admissions would have seen a pass with a note attached.

**Fixed.** `return {"eligibility": REJECT, ...}` immediately. §5.2's "must never
be stored" list is a hard reject, not a note.

---

## FAILURE 9 — all ten verifications returned `uncertain`, safely and uselessly

**What happened.** The first `s3b_verify.py` run returned `uncertain` for all 10
cases, including cases whose traces were complete.

**Diagnosis.** `ModuleNotFoundError: No module named 'utils'` — the judge
transport lives in `gos/utils/public_function.py`, and the traceevo package
could not import it. `verify_trace` catches any judge exception and maps it to
`uncertain` per §4.2, so the failure was contained but invisible in the verdict:
"the judge disagreed" and "the judge never ran" produce the same word.

**Fixed.** `sys.path.insert(0, GOS)` in `s3b_verify.py` and `s3b_score.py`. This
is the failure that argues for keeping the verifier's raw judge transcript —
the verdict alone could not distinguish the two.

---

## LIMITATION 1 — PZ2 exists for 8 of 10 cases, not 10

**What happened.** The poisoned corpus is 38 candidates, not 40: PZ1 10, PZ2 8,
PZ3 10, PZ4 10. PZ2 (`inject_a2`, variant `wrong_record_binding`) is absent for
case-32 and case-33.

**Diagnosis.** The `wrong_record_binding` variant needs a second record to
mis-bind to, and those two cases do not have one. `s3b_gate.py` catches the
injector's exception and records the skip in `poison_rows`.

**Not fixed, deliberately.** The injector is S2's frozen artifact. Changing it
to manufacture a second binding would be editing a frozen rule to make an
experiment look complete, which D1 forbids. The asymmetry is reported rather
than smoothed: PZ2's denominators are 8 where the others are 10, and every rate
involving PZ2 is computed over the candidates that exist.

---

## A correction to a delivered artifact

`S2_report.md` Q2b states "False for 27". The correct figure is 30
(18 + 6 + 3 + 2 + 1). Corrected in place with a dated note; the arithmetic is
reproduced there.

---

## FAILURE 10 — a work-in-progress trace was scored as a corrupted one

**What happened.** A dry run of the scorer against M0's partial traces reported
`integrity_all_ok: False` and a critical anomaly on case-42. Both would have
been findings about the *run*. Neither was: case-42 was still executing.

**Diagnosis.** `arm_metrics` treated a case as scoreable if `_events.jsonl`
existed — and that file is created when the task starts, not when it finishes.
`audit_bundle` then reads `_integrity.json`, finds nothing, and defaults
`integrity_ok` to `False`. A trace being written is indistinguishable from a
trace whose chain is broken.

**Why it matters.** M0 is the *control* arm. "The no-memory baseline produced a
trace with a broken chain and a critical anomaly" is a reportable defect in the
baseline, and it would have been false. This is the sixth instance in S3B of an
artifact reading as a finding when it was an absence.

**Fixed.** `s3b_score.finished()` requires `_integrity.json`, which
`finish_task` writes last, and both `arm_metrics` and `judge_arm` skip cases
that have not finished, recording why. Verified: the count went from 3 to 2.

**Not fixed by timing.** It would be easy to treat this as "run the scorer at
the right moment". The guard is in the code because the right moment is not
knowable from outside — and because the previous five failures in this file were
each found by someone running a tool at a moment when it silently did the wrong
thing.

---

## FAILURE 11 — one unrecoverable case silently deleted the rest of its arm

**What happened.** The first full probe run left the three arms with different
case counts:

| Arm | Completed | Stopped at |
|---|---|---|
| M0_NoMemory | 2 / 10 | case-42 |
| M1_NaiveMemory | 3 / 10 | case-43 |
| M2_TrustedMemory | 7 / 10 | case-47 |

Nothing reported a problem. `--run` printed `rc=1` per arm and moved on, so the
only visible signal was that the arms finished at different times.

**Diagnosis.** GoS raises on an invalid expert dispatch and does not catch it:

```python
raise ValueError(f"Not founded, current list：{[a.name for a in self.experts]}")
```

`main.py` iterates `--cases` inside a single process and has no per-case
handler, so that exception ends the process. Every case after the failing one is
never attempted. The batch runner turned "case-42 could not be completed" into
"cases 42 through 49 do not exist".

**Not an instrumentation bug, and not fixed in GoS.** The crash is deliberate:
`central_agent.py` records the failed dispatch through `on_dispatch_failed` and
then re-raises "unchanged. The hook must not swallow it, repair it, or alter the
message." That is S2 deciding that an invalid dispatch is fatal, which is the
honest reading of upstream behaviour. The fix therefore belongs in the runner.

**Why it is worth reporting rather than quietly re-running.** The three arms
stopped at three different points, and the arms are the comparison. A report
that scored M0's 2 traces against M2's 7 would have compared different case
sets while looking like a comparison of memory conditions — and the missing
cases are missing *because of how the arm ran*, which is correlated with the
arm only by chance.

**Fixed.** `run_arm` now runs one case per process, so an unrecoverable case
costs one case. It also gained `--resume`, which re-runs only cases with no
`_integrity.json` and clears their partial artifacts first — necessary because a
crashed attempt leaves a trace that looks finished from the outside.

**Why re-running is legitimate here.** Temperature is 0.8, so a case that
crashed on a bad dispatch may complete on another attempt; the failure is a
model decision, not a property of the case. And the case loop in `main.py`
constructs `BeliefGraph`, `BeliefFSM`, the expert agents and the central agent
fresh inside the loop, so cases do not share state and re-running one cannot
contaminate another. Verified by reading the loop rather than assumed.

---

## FAILURE 12 — `--resume` deleted the traces it was supposed to resume from

**What happened.** Immediately after FAILURE 11 was fixed, the first resume run
printed:

```
=== M0_NoMemory ===
  M0_NoMemory: 0 already complete, 10 to run
```

M0 had 2 completed cases. It was re-run from zero, having destroyed them.

**Diagnosis.** The resume logic was added *below* the existing directory reset:

```python
if os.path.isdir(run_dir):
    shutil.rmtree(run_dir)          # unguarded
os.makedirs(run_dir, exist_ok=True)
...
if resume:
    todo = [c for c in case_list if not exists(.../_integrity.json)]
```

The `rmtree` ran first, so by the time `todo` was computed the arm directory was
empty and every case correctly looked unfinished. The tool then did exactly what
it said — resumed nothing, ran everything — while the data it was meant to
preserve was already gone.

**Cost.** M0's two completed traces. They are reproducible (the memory stores
are fixed and cases share no state), so the loss is hours of wall clock, not
evidence.

**Why it is worth its own entry rather than folding into FAILURE 11.** It is the
same mistake in a different costume. FAILURE 11 was "the runner discards
results the operator did not intend to discard". This is that sentence again,
one function higher, introduced by the fix for the first one. A guard that
depends on the order of a destructive call is not a guard.

**Fixed, and made self-checking.** `rmtree` now runs only when `resume` is
False, and a resume against an arm with **zero** completed cases raises:

```
--resume was given for M0_NoMemory but there is nothing to resume: ...
Refusing to treat that as a fresh start, because it is also what a bug that
deleted the traces would look like.
```

The condition being refused is precisely the state this bug produced. It could
not have fired on the first run — the bug and the guard are the same shape, and
only one of them existed.

**Verified before relaunching.** `_completed_cases` was called directly against
M1 and M2 and returned `[40, 41, 42]` and `[40..46]` — the counts observed on
disk — rather than being assumed correct.

---

## FAILURE 13 — the memory arms ran with no memory, and reported success

**What happened.** M2 completed all ten probe cases. Every trace had
`chain=OK, replay=OK`. The run exited `rc=0`. And it had never injected
anything:

```
M2_TrustedMemory: 10/10 complete, 0/10 with a runtime injection
```

M1 was in the same state. The plan phase had predicted 8/10 and 10/10 hits
respectively, using the same stores, the same `k`, the same similarity floor.

**Diagnosis.** `run_arm` passed the store as a relative path:

```python
env["TRACEEVO_MEMORY_STORE"] = _store_root(out_dir, arm)   # "S3B_runs/..."
```

GoS runs with `cwd=gos`, so the path resolved to
`gos/S3B_runs/memory_probe/memory_store/M2_TrustedMemory`. And
`MemoryStore.__init__` does `os.makedirs(..., exist_ok=True)` for each
partition — so GoS did not fail to open a missing directory. **It created an
empty one and read from it.**

```
gos/S3B_runs/.../M2_TrustedMemory/{success,failure,manual_review,rejected}
  -> 0 files

S3B_runs/.../M2_TrustedMemory/
  -> 49 files
```

`on_plan_prompt` then found no hits, returned the prompt unchanged, and the
`{task}_injections.jsonl` records were written empty. Nothing errored, because
nothing was wrong from GoS's point of view: it was handed a path to an empty
store and it used it correctly.

**This is FAILURE 5's third occurrence**, and the only one that produced no
symptom at all. The first two:

| | what was relative | how it surfaced |
|---|---|---|
| FAILURE 5a | `TRACEEVO_DATASET_CSV` | crashed loudly — pandas could not open it |
| FAILURE 5b | `--out-csv`, `--log-dir`, … | silent misplacement of results.csv and logs |
| **FAILURE 13** | `TRACEEVO_MEMORY_STORE` | **silent absence of the treatment** |

Each time the fix was applied to the paths that had been *observed* to break,
and the next unobserved one was left. The pattern is the fix strategy, not the
bug: I was repairing instances instead of the class. All six paths in both
runners are now `abspath`, and the two memory paths are checked.

**Why it is the worst of the three.** FAILURE 5a cost a restart. FAILURE 5b
cost tidiness. This one would have produced a complete, plausible S3B report
stating that trusted memory does not change planner behaviour — a null result
that is really the absence of a treatment. The M1-vs-M2 contrast is the entire
point of §14, and both arms were secretly M0. Two hours of M2 traces were
discarded, and M1's three as well.

**Fixed, with two checks that would each have caught it.**

*Preflight, before a memory arm starts.* `_check_store` refuses to run when the
path is missing **or holds no retrievable entry**, and prints the resolved
absolute path and the count:

```
M1_NaiveMemory: store /Users/.../memory_store/M1_NaiveMemory (48 retrievable)
M2_TrustedMemory: store /Users/.../memory_store/M2_TrustedMemory (1 retrievable)
```

Verified against all four cases: the correct stores pass (48 and 1), the empty
`gos/` store fails with "holds no retrievable entry", a nonexistent path fails
with "the directory does not exist".

*Post-run, after every memory arm.* `run_arm` counts cases whose
`{task}_injections.jsonl` is non-empty. If a memory arm completed every case and
injected nothing, it says so:

```
!! M2_TrustedMemory completed every case but injected nothing. The treatment
   was not applied; these traces are a memory-off run wearing a memory-on label.
```

The preflight guards the path; the post-check guards the outcome. The bug
defeated both halves of the pipeline without touching either.

**Kept as evidence.** `gos/S3B_runs/memory_probe/memory_store/` is left in
place — the empty store GoS created for itself is the clearest statement of
what happened.

---

## FAILURE 14 — the request deadline could not fire, and three arms hung for 85 minutes

**What happened.** All three arms stopped producing output within seconds of
each other (21:25–21:26) and produced nothing for the next 85 minutes. Their GoS
processes were alive with 2.6 seconds of CPU each — blocked, not working.

**Diagnosis.** `sample` on a stuck process:

```
_buffered_readline -> _bufferedreader_raw_read -> sock_recv_into
  -> sock_call_ex -> internal_select -> poll
```

Blocked in `poll()` waiting on a socket read.

That is the exact failure the deadline was written for. From
`_wall_clock_deadline`'s own docstring:

> `http.client`'s timeout is per socket operation, not per request: a gateway
> that dribbles bytes can hold a connection open indefinitely while every
> individual `recv()` returns in time. That happened during S1 — one request sat
> for 51 minutes with no timeout firing.

The fix was SIGALRM with a 900-second budget. It did not work here, and the
reason is subtle: **Python installs signal handlers with `SA_RESTART`**, so when
the alarm fires while the main thread is inside `poll()`, the kernel restarts
the call rather than returning `EINTR`. The Python-level handler never runs, and
the exception that was supposed to abort the request is never raised. The
deadline mechanism is inert against precisely the blocking pattern it exists to
interrupt.

The gateway itself was healthy throughout — a direct call during the stall
returned in 6.4 seconds. The hung requests were stuck in their own connections.

**Why this one is different from the others in this file.** Failures 1–13 are
things the harness did wrong. This is a guard that was already there, already
documented as protecting against this exact scenario, and did not. A wrong
result is findable; a protection that silently does not protect was trusted
instead.

**Cost.** 85 minutes of wall clock across three arms, and the three in-flight
cases restarted from zero.

**Fixed.** A watchdog on *output* rather than elapsed time. `_run_case` polls
the case's log every 20 seconds and kills the process after 30 minutes with no
new bytes.

The distinction matters. A cap on total duration would have killed M0's case-46,
which legitimately took 140 minutes. A stalled process, by contrast, writes
nothing for tens of minutes at a stretch. The watchdog keys on the symptom that
separates a hang from a slow case, not on the thing they share.

`request_deadline` is left as it is — it is not wrong, it is inert on this
platform, and changing it would require touching the S1 transport that every
other stage depends on. The watchdog is in the runner, where the observing
happens.

**Verified before being trusted.** New code on the path that just failed does
not get the benefit of the doubt. Three synthetic cases, run against the real
`_run_case`:

| Case | Setup | Result |
|---|---|---|
| a hung process | `sleep(600)`, 8s threshold | killed at 40s, `stalled=True`, rc=-9 |
| a slow but working one | prints every 2s for 12s, 8s threshold | **not** killed, rc=0 |
| watchdog disabled | `stall_timeout=0` | runs to completion, `stalled=False` |

The middle row is the one that matters. A watchdog that only catches hangs is
easy; a watchdog that does not kill a case for being slow is the whole design,
and case-46 — which legitimately took 140 minutes — is the case it must not
break.

---

## FAILURE 15 — the guard scanned prompts the planner never saw

**What happened.** The second-pass audit (`s3b_injection_audit`) re-ran the §13
guard over the injections the runs actually recorded, and reported a mismatch
on every case it compared:

```
M1 case-45: planned 1280 chars  vs  runtime 1226
M1 case-47: planned 1239 chars  vs  runtime 1253
M2 case-43: planned 0 ids       vs  runtime 1
```

Not a leak — the runtime scan found `gold_leakage_count = 0` over 24
injections. The problem was that the first-pass guard had been scanning
*different prompts*.

**Diagnosis.** Two different query strings, built in two places:

```python
# s3b_probe.plan()          -- what the guard scanned
f"{case_info}\n{physical}"

# gos/Run/DiagnosisArena.py -- what the run used
f"##Case information: {case_info}\n## Physical Examination: {physical}"
```

The labels add four content words — `case`, `information`, `physical`,
`examination` — none of which are stopwords. BM25 is sensitive to them. On
case-45 the same store returned completely different memories:

| query | retrieved |
|---|---|
| no labels | `exp-case-37-58844de05a79`, `poison-PZ1-case-37` |
| with labels | `exp-case-34-a068579663c9`, `poison-PZ1-case-34` |

So the plan phase's `gold_leakage_count = 0` was a statement about prompts that
were never sent, and its exposure rates described a retrieval that did not
happen. §13's gate is only meaningful if it guards the real injectate.

**Why it survived the positive control.** The control spiked known-bad strings
into *plan-phase* blocks and confirmed the guard fired. The guard was working.
The blocks it was working on were the wrong ones. A control that only tests
whether an instrument fires cannot detect that it is aimed at the wrong thing —
that needs a second independent record of what actually happened, which is what
`s3b_injection_audit` reads.

**Fixed.** `runtime_query()` builds the string exactly as `DiagnosisArena` does,
and `plan()` uses it. Re-running `--plan` with the corrected query scans 30
injections (was 28) and still reports 0 leaks — but now over the same prompts
the runs produced.

**The authoritative scan is the runtime one.** The first-pass guard runs before
any GoS process and can only ever be a prediction. The second pass reads
`{task}_injections.jsonl`, which is what `hooks.on_plan_prompt` actually
returned. Where they disagree, the second is the measurement and the first is a
bug report.

**How this was found.** Not by reviewing the guard — by writing down an
independent record of the injections and comparing. Every failure in this file
that mattered came from having two observations of the same event and checking
whether they agreed.
