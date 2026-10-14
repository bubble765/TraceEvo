# S2.1 case study summary

Generated from the runs in this directory (`case_studies.json`); no
cell is prefilled. Terminology per S2.1 §4: the A3 study is a
**live reproduction of a naturally discovered defect**, not a
naturally captured trace.

| Case | Injection | Prompt changed | Graph changed | Semantic level | Root localized | Reached report |
|---|---|---:|---:|---|---:|---:|
| A3-case1 | invalid dispatch | — | — | aborted | yes | no |
| A4-case5 | corrupted analysis | yes | yes | L0 | n/a | no |
| A4-case8 | corrupted analysis | yes | yes | L0 | n/a | no |

## How to read this

- 2 of 2 A4 studies reached the pipeline: the substituted response was served and the next prompt the run computed differs from the clean one. Nothing about the injection was void.
- `Graph changed = yes` is **not** evidence that the fabricated text propagated. Once the substitution is served, every later call is live rather than replayed, so the graph is a fresh sample; the fabricated text itself is what §5.3 tracks, and it is absent from every node.
- In 2 of 2 cases the fabricated response was absorbed before any event was emitted, which is why §5.4's root is `n/a` rather than `no`: the auditor cannot name a root for an anomaly that never became an event. An injection delivered at the expert's decision/content seam is invisible to a trace-only audit, the same way A5 truncation is.
- Both cases are L0, which §8 accepts. The reason is structural and reproducible, not sampling noise: it is the same mechanism in both.


---

## A3-case1 — live reproduction of a naturally discovered defect

- process return code: `1`
- events recorded before the process died: 4
- PLAN event: `evt-case-1-00003`
- failed dispatch: `evt-case-1-00004` reason `INVALID_EXPERT_DISPATCH`
- requested expert: `Laboratory Physician`
- roster visible to the dispatcher: `['Laboratory_Physician', 'Pathologist', 'Radiologist']`
- near-miss re-derived from the trace alone: `Laboratory_Physician`
- `ValueError` present in the failure log: yes
- FINAL_REPORT present: no (the task aborted, so it must be absent)
- integrity auditor found an incomplete task: yes
- anomalies: `[['A5', 'missing_final_report', 'evt-case-1-00004'], ['A3', 'name_form_mismatch', 'evt-case-1-00004']]`

## A4-case5 — end-to-end corrupted analysis

**§5.1 did the injection enter the pipeline?**

- substituted call index: 4
- first call after it: 5
- clean prompt hash: `ea797fcb69c8c25bbf68be0fd6ccbb9b`
- injected prompt hash: `4cd11d54aceb6a830fa7ea8dc68eef1a`
- **prompt_changed = yes**

**§5.2 did the causal graph change?**

- nodes: 15 clean → 22 injected
- added nodes: `['node-16', 'node-17', 'node-18', 'node-19', 'node-20', 'node-21', 'node-22']`
- removed nodes: `[]`
- changed edges: `['node-1->node-11', 'node-1->node-12', 'node-1->node-13', 'node-10->node-12', 'node-10->node-13', 'node-10->node-14', 'node-11->node-13', 'node-11->node-8', 'node-12->node-13', 'node-12->node-6', 'node-12->node-7', 'node-12->node-8', 'node-13->node-8', 'node-14->node-13', 'node-14->node-15', 'node-14->node-16', 'node-14->node-17', 'node-14->node-18', 'node-14->node-8', 'node-15->node-13', 'node-15->node-8', 'node-19->node-11', 'node-19->node-12', 'node-19->node-13', 'node-19->node-14', 'node-19->node-15', 'node-19->node-16', 'node-19->node-17', 'node-19->node-18', 'node-19->node-6', 'node-19->node-7', 'node-19->node-8', 'node-20->node-15', 'node-20->node-16', 'node-21->node-12', 'node-21->node-14', 'node-21->node-15', 'node-22->node-15', 'node-4->node-11', 'node-4->node-12', 'node-4->node-13', 'node-4->node-14', 'node-8->node-13', 'node-8->node-14', 'node-9->node-11', 'node-9->node-12', 'node-9->node-14']`
- changed node labels: `['node-10', 'node-11', 'node-12', 'node-13', 'node-14', 'node-15', 'node-9']`
- **graph_changed = yes**

**§5.3 semantic level**

- **L0** — reached a graph node: no; reached the report text: no
- report token coverage: 0.2353 (clean run's report: 0.0588)
- graph nodes carrying the injected text: `[]`

**Where the injected response actually went** (read back from the inject run's log)

- times the fabricated response was served at the decision stage: `1`
- decision/content disagreements in the injected run: `2` (in the clean run: `3`)
- events carrying the injected text: `[]`
- absorbed before any event was emitted: yes
- the fabricated response was served at the expert's decision stage; that stage is read for {type, decision} only, so the fabricated content is dropped, the content stage generates content for the flipped decision, the two stages disagree, the step is discarded as a parse error and no event is emitted for it. The disagreement check also fires in the clean runs (see the counts above), so the injection's contribution is one additional discarded step, not a new failure mode.

**§5.4 root localization**

- injected ANALYSIS event: ``
- auditor-reported roots: `[]`
- **match = not measured**
- applicable = no  — no event carries the injected text, so there is no root for the auditor to name; the injection's only footprint is in the run log, not the event log
- analysis calls: 6 clean → 7 injected
- anomalies: `[]`

## A4-case8 — end-to-end corrupted analysis

**§5.1 did the injection enter the pipeline?**

- substituted call index: 4
- first call after it: 5
- clean prompt hash: `2686d649bd39d354fd3a54a95a28dc21`
- injected prompt hash: `d67203e9f1de31138c942c02e2d962aa`
- **prompt_changed = yes**

**§5.2 did the causal graph change?**

- nodes: 11 clean → 20 injected
- added nodes: `['node-12', 'node-13', 'node-14', 'node-15', 'node-16', 'node-17', 'node-18', 'node-19', 'node-20']`
- removed nodes: `[]`
- changed edges: `['node-1->node-10', 'node-1->node-11', 'node-1->node-12', 'node-1->node-20', 'node-10->node-11', 'node-10->node-6', 'node-11->node-10', 'node-11->node-8', 'node-15->node-20', 'node-19->node-11', 'node-19->node-16', 'node-19->node-6', 'node-19->node-8', 'node-20->node-11', 'node-20->node-16', 'node-20->node-8', 'node-3->node-10', 'node-4->node-10', 'node-5->node-10', 'node-5->node-20', 'node-8->node-16', 'node-8->node-17', 'node-8->node-18', 'node-9->node-10', 'node-9->node-11', 'node-9->node-12', 'node-9->node-7']`
- changed node labels: `['node-10', 'node-11', 'node-9']`
- **graph_changed = yes**

**§5.3 semantic level**

- **L0** — reached a graph node: no; reached the report text: no
- report token coverage: 0.0588 (clean run's report: 0.0)
- graph nodes carrying the injected text: `[]`

**Where the injected response actually went** (read back from the inject run's log)

- times the fabricated response was served at the decision stage: `1`
- decision/content disagreements in the injected run: `1` (in the clean run: `0`)
- events carrying the injected text: `[]`
- absorbed before any event was emitted: yes
- the fabricated response was served at the expert's decision stage; that stage is read for {type, decision} only, so the fabricated content is dropped, the content stage generates content for the flipped decision, the two stages disagree, the step is discarded as a parse error and no event is emitted for it. The disagreement check also fires in the clean runs (see the counts above), so the injection's contribution is one additional discarded step, not a new failure mode.

**§5.4 root localization**

- injected ANALYSIS event: ``
- auditor-reported roots: `['evt-case-8-00049']`
- **match = not measured**
- applicable = no  — no event carries the injected text, so there is no root for the auditor to name; the injection's only footprint is in the run log, not the event log
- analysis calls: 5 clean → 7 injected
- anomalies: `[['A1', 'evidence_tier_p3_used', 'evt-case-8-00049']]`

