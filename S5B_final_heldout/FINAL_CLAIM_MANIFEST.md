# FINAL CLAIM MANIFEST

Per `TraceEvo_S5A_Review_S5B_Final_Heldout_v1.md` §2. **Frozen at 2026-09-20T04:34:18.025+00:00, before the first read of case 80.**

The purpose of writing this now is that a claim fixed after seeing the block is a description of the block, not a claim. Each entry below has a stated held-out evidence source and a stated falsifier, so the final matrix (§19) can mark it supported or not against criteria that predate the data.

## Frozen method

* Core-v1 = Method-1 Dual-Graph Auditable Reasoning + Method-2 Audit-Gated Trustworthy Experience Consolidation.
* Memory injection OFF, Failure Memory activation OFF, role adaptation OFF, ATGG OFF.
* Backbone frozen at `deepseek-v4-flash`.
* Held-out = case 80-137, 58 cases, one pass.

## C1 — final claim

TraceEvo can build fine-grained, replayable execution provenance during GoS medical multi-agent reasoning, and bind execution provenance to the causal reasoning graph.

| | |
|---|---|
| held-out evidence | replay; cross-graph binding; source tier; integrity; natural trace |
| established if | seals verify, delta replay passes, bindings resolve, and the natural trace is complete on all 58 held-out cases |
| falsified if | a systematic seal, replay or binding failure on the held-out block |

## C2 — final claim

TraceEvo can detect controlled collaboration anomalies from provenance, evidence-source and integrity information, and localize the observable failure seam.

| | |
|---|---|
| held-out evidence | A1/A2/A3/A5 controlled injection; clean alert rate; observable-root localization |
| established if | per-class detection is reported with its real denominator and CIs, and observable-root top-1 is reported on held-out traces |
| falsified if | detection fails on the held-out block, or the numbers only hold on the injection procedure that produced them |
| caveat | precision and recall are measured against injections this codebase makes; that is consistency with its own fault model and not detection in the wild |

## C3 — conditional claim

A Gate that joins Outcome Verification with Audit avoids unsafe experience admission that either signal alone would allow.

| | |
|---|---|
| held-out evidence | verified_success; incorrect-clean; correct-but-poisoned twins |
| established if | the held-out block yields enough verified successes to build poisoned twins and the Gate's positive branch is exercised independently |
| if positive class sparse | C3-positive = development-supported only, stated as such (§2) |
| if positive class absent | HELDOUT_POSITIVE_CLASS_ABSENT: the positive branch is supported by the development controlled experiment and the final held-out block is reported as not having produced an independent replication |

## C4 — claimable

TraceEvo can turn audit results into traceable Success / Failure / Manual Review / Reject experience partitions.

> Framed as: Trustworthy Experience Consolidation — NOT 'verified continuous improvement of diagnostic performance'

| | |
|---|---|
| held-out evidence | gate candidates; gate verdict distribution; partition assignment on held-out traces |

## Explicitly NOT claimed

§2: none of these may be claimed in S5B regardless of the result.

| not claimed | why |
|---|---|
| Success Memory improves diagnostic accuracy | no experiment has shown it; the S3B probe had M0 (no memory) highest on both Match and Relevant |
| role-specific routing is effective | S4A NO_ROLE_SPECIFIC_ADAPTATION |
| ATGG improves natural task performance | S4B LOW_TRIGGER_DENSITY — never evaluated for efficacy |
| TraceEvo continuously improves medical diagnostic performance through online self-evolution | no behaviour experiment supports it and S5B runs none |

## Pre-registered case-study rules (§15)

§15 fixes these before the run so the final write-up cannot select the best-looking case:

| slot | rule |
|---|---|
| dual_graph_normal_example | case 80, whatever it shows (fallback: case 81, and only if the graph is unreadable, with the reason recorded as a technical drawing failure) |
| natural_p3_example | the smallest case_id carrying a P3 node that was used in reasoning; if none: no natural P3 case study exists on this block |
| controlled_anomaly_path | case 80's A1 injection (fallback: if A1 is not applicable to case 80, the first applicable case in ascending case_id order) |
| experience_gate_case | the smallest case_id with a heldout verified_success; if none: no final positive case study; the paper says so explicitly |

## What would make the whole block invalid (§5.3)

A P0-P3 rule implementation error, a Gate logic error, a wrong audit statistic, premature gold access, or final output invalidated by a Core code defect gives `FINAL_HELDOUT_INVALIDATED` and an immediate stop. The code is not fixed and case 80-137 is not re-run as if still unseen.
