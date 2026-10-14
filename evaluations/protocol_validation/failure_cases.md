# S5A failure cases

Failures found building S5A. The recurring shape this time is **a comparison
across things that are not comparable** — an arm that produces a file compared
against an arm that by definition does not, a category that cannot be honestly
instantiated counted in the headline, a boundary value read as a case number.
S5A is the protocol stage, so its failures are protocol failures.

---

## FAILURE 1 — the P0-P3 thresholds were read from the wrong place, and the hash said the ruleset was empty

**What happened.** The Core-v1 freeze's first run reported:

```
"p0_p3_rule_hash": "44136fa355b3678a"
```

`44136fa355b3678a...` is `sha256("{}")`. The freeze had looked for module-level
constants named `DIRECT_MIN_COVERAGE`, `SOFT_MIN_COVERAGE` and so on; they do
not exist. The thresholds live in `DEFAULT_CFG`, lowercase, in the same module.

**Why this is a recurrence and not a new bug.** S3D's freeze made the identical
mistake one level up — reading the absence regexes from `policy.py`, which has
none, and writing the digest of an empty dict. It was found then by noticing
that the recorded hash did not match S3A's, and the fix was to read the patterns
from the file that defines them. The lesson recorded at the time was *a digest
cannot be read*, and the S5A freeze was written without applying it.

**Why an empty hash is worse than a missing field.** A missing field is visibly
absent. `p0_p3_rule_hash: 44136fa...` is a value, and a reviewer checking that
the manifest "records the P0-P3 rule hash" would find that it does. The manifest
would certify that the tier thresholds were frozen while containing a digest of
nothing — in the stage whose whole purpose is certifying that the method was
frozen.

**Fixed, and made unrepresentable.** The freeze now reads `DEFAULT_CFG`,
`TIERS`, `CONTENT_LINEAGE` and `SELF_DECLARED`, and raises rather than writing a
manifest whose threshold set is empty:

```python
if not tier_rules["default_cfg"]:
    raise SystemExit(
        "P0-P3 thresholds not found; refusing to freeze a manifest whose "
        "rule hash would be the digest of an empty dict")
```

`test_the_freeze_records_the_real_p0_p3_thresholds` checks the value that was
written and names the empty-digest hash in the assertion message, so the
failure announces itself by name if it recurs.

---

## FAILURE 2 — a category that cannot be honestly instantiated was counted in the headline

**What happened.** §11 lists A0 among the protocol's anomaly classes and
attaches a condition: A0 is kept *只有存在可靠 degraded/failed instrumentation
时才保留*, and *若无可确定触发，不为凑类别模拟一个虚假 status*.

The first version of `s5a_anomaly.py` ran both A0 variants and let all 20 rows
fall into the primary group, where they scored `precision = recall = F1 = 1.000`
and pulled the headline F1 up.

**Diagnosis.** The `status_marked` variant writes `status = "degraded"` and
`reason_code = "BUDGET_EXHAUSTED"` into the artifact. S4A established that no
event in this pipeline ever carries a degraded or failed status — all 855 in the
dev block are `status=success`, `reason_code=NONE`. So the variant manufactures
the instrumentation whose *existence* §11 makes a precondition for keeping the
class, and then the detector finds the marker it just wrote. A detection rate of
1.000 on a class that cannot occur is not a result about detection.

**Why it is easy to miss.** The run reported `n_injections: 108` and a strong
F1. Every number was computed correctly from data that should not have existed.
The condition §11 attaches is not to the *measurement* but to the *class*, and
a pipeline that measures every class it can construct will silently measure
classes it was told not to.

**Fixed.** The `status_marked` variant is refused before it runs, with the
reason recorded per case, and A0 rows are grouped as `conditional` — outside the
primary precision/recall/F1, with their own count and their own note. The
headline dropped from n=98/F1=0.985 to n=78/F1=0.981, and the 20 rows that
disappeared were the ones that should never have been counted.

---

## FAILURE 3 — the non-invasiveness check compared a field that is absent by construction

**What happened.** §14 asks for a Core ON vs Core OFF replay comparison of
prompt sequence, response sequence, prediction, report and final graph. The
first version compared `final_graph_hash` read from the *TraceEvo* run summary.
With Core OFF there is no summary — the instrumented layer is off — so the
comparison reported:

```
core_off_vs_on_identical: false
differences: {"final_graph_hash": [null, "4c091a3a..."], "n_events": [0, 37]}
```

and §15 condition 5 failed, producing a verdict of `S5A_NEED_REVISION` on a run
where the two arms had produced the *same prediction* and the same report.

**Diagnosis.** §14's "final graph" is GoS's own graph output, which both arms
write; `n_events` and the TraceEvo graph hash exist only when Core is ON, and
their absence is the definition of Core being off. Comparing them across arms
asks whether the instrumented arm wrote instrumentation.

**Why the direction matters.** The failure pushed toward `S5A_NEED_REVISION` —
"the method fails its own non-invasiveness condition" — when the method was
non-invasive. A comparison that manufactures a defect sends the stage to fix
something that is working, and here it would have sent the freeze itself back
for revision.

**Fixed.** The across-arm comparison uses `prediction`, `report_hash` and
`gos_final_graph_hash`; the Core-ON-only fields are compared between the two
Core-ON runs, where they are both defined, and the asymmetry is named in
`on_only_note`. Re-run: `core_off_vs_on_identical: true`, `core_on_repeatable:
true`, condition 5 passes.

---

## FAILURE 4 — a test that rejected a legitimate exclusive bound

**What happened.** `test_no_case_beyond_the_validation_block_is_referenced`
scanned each S5A module for any integer above 79 and failed on:

```
s5a_freeze.py references case 80 near 'OOT, "S5A_protocol_validation")\n
VALIDATION = list(range(70, 80))\nMUST_NOT_READ ='
```

`range(70, 80)`'s 80 is an exclusive bound. The guard against reading the
final held-out block rejected the line that *declares the boundary*.

**Why it is recorded.** It is the same defect as FAILURE 3 in a different
instrument: a check whose rule does not match its purpose, failing for a reason
unrelated to what it protects. The corrected test matches case *identifiers*
(`case-N` literals, and `range(a, b)` pairs) rather than bare integers, and
allows the one bound (`138`) that `range(80, 138)` legitimately contains.

A test that fails on correct code is not merely noisy. Its failure is
indistinguishable from a real violation, so the response to it — loosen the
check, or worse, change the code — is chosen under a false signal.

---

## FAILURE 5 — a timing reported as 0.0 because it was rounded to nothing

**What happened.** §14 asks for the local processing overhead. The first
`overhead_results.csv` reported:

```
gate_seconds: 0.0   (all ten cases)
```

**Diagnosis.** `gate.evaluate` is a pure function over a few dicts and takes
about 12 microseconds. Rounded to four decimal places of a second that is
`0.0` — the same value a timing that never ran would produce.

**Why it matters in a stage about measurement discipline.** §14 explicitly
separates local processing cost from network cost so the former can be quoted
as a property of the implementation. A column of zeros invites the reading
"the gate costs nothing", which is a stronger and different claim from "the
gate costs ~13 µs", and it is unfalsifiable from the table.

**Fixed.** The gate is reported in microseconds alongside seconds
(`gate_microseconds_per_case_median: 12.8`) with a note saying why, and the
audit — millisecond-scale, where four decimals are honest — is left in seconds.
The rule is the one S3D's failures produced: *a number's precision must be
chosen by its magnitude, not by a formatting default.*

---

## A note on what these five have in common

All five are a comparison or a measurement whose *rules* did not match its
*purpose*: a digest read from a file without the thing it digests, a category
counted without the precondition attached to it, a field compared across arms
where only one arm defines it, a guard that rejects its own boundary, and a
precision that erases its own value.

The first four each pushed toward a **false negative about the method** — the
freeze looked complete, the detection looked strong, the non-invasiveness
looked broken, the boundary guard looked violated. In a protocol-validation
stage, where the deliverable is a verdict about readiness, a wrong finding in
that direction is the expensive one: it either sends the method back for a
revision it does not need, or it certifies a method on evidence that does not
exist.

S3D's rule was *print the count of things each number was computed over*. S5A's
is: **for every comparison, state what each side is allowed to contain.**
