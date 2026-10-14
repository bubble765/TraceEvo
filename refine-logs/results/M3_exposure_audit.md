# Dataset exposure audit for E2

**Date**: 2026-09-29  
**Purpose**: Determine whether the current local DiagnosisArena cohort contains cases that can still serve as an unopened confirmatory set.

## Method

Scanned artifact paths (not patient contents) under the project stages `S0_baseline`, `S1_provenance`, `S1_runs`, `S2*`, `S3*`, `S4*`, `S5A_protocol_validation`, and `S5B_final_heldout`. Extracted `case-N` / `case_N` identifiers and reconciled the stage-level spans against the 138-row DiagnosisArena index range 0–137.

## Finding

Artifacts cover every index 0–137; there are no local indices absent from the scanned stages. The contiguous stage coverage is:

| Stage artifact family | Case indices seen |
|---|---|
| S0/S1 | 0–9 |
| S3A runs | 0–29 |
| S3B build/probe | 30–49 |
| S3D verified expansion | 50–59 |
| S4B baseline | 60–69 |
| S5A validation | 70–79 |
| S5B final held-out | 80–137 |

**Decision**: Do not designate any current DiagnosisArena index as a new held-out set. E2 needs a newly sourced or newly generated cohort with an exposure manifest, or must remain unrun. This audit establishes artifact exposure, not whether every case was used in every prior analysis.

## Limitations

The inventory is based on filenames and stage manifests; it does not establish patient-level deduplication across an external benchmark. Any replacement cohort must also be checked for duplicate or near-duplicate cases.
