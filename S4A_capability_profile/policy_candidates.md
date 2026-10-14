# Policy candidates (§10-§11)

Sufficiency threshold: **n >= 30** attributed nodes used in reasoning, declared before evaluation.

| role | classification | grounding | execution |
|---|---|---|---|
| Laboratory_Physician | **NORMAL** | NORMAL | INSUFFICIENT_DATA |
| Pathologist | **NORMAL** | NORMAL | INSUFFICIENT_DATA |
| Radiologist | **NORMAL** | NORMAL | INSUFFICIENT_DATA |

## Why no guard was raised

roles_indistinguishable

Every role's P3-decision-use 95% interval lower bound sits below the
global baseline, so none is distinguishable from it in the unsafe
direction. The intervals are wide because the counts are small:

* **Laboratory_Physician** — 2 P3-used of 123 attributed nodes used in reasoning, posterior mean 0.0240, 95% CI [0.0050, 0.0570]; global 0.0286
* **Pathologist** — 1 P3-used of 44 attributed nodes used in reasoning, posterior mean 0.0435, 95% CI [0.0054, 0.1177]; global 0.0286
* **Radiologist** — 3 P3-used of 76 attributed nodes used in reasoning, posterior mean 0.0513, 95% CI [0.0143, 0.1097]; global 0.0286

§10.2 forbids widening the interval to manufacture a candidate,
and none was widened.

## What a guard would mean if one existed (§11)

Even a raised candidate would change no call in S4A. The actions
below are for a future S4B, and both are *verification-strength*
adjustments rather than capability withdrawals:

* `GROUNDING_GUARD` — require evidence source binding / provenance
  verification before this expert's analysis enters `UpdateGraph`.
  **Not** suppressing the expert.
* `EXECUTION_GUARD` — when a call returns degraded/failed, have the
  central agent start a fallback or second review. **Not**
  pre-emptively removing a medically necessary expert on the basis
  of a historical profile.

> 能力画像只能调节验证强度与协作策略，不能单凭历史风险剥夺当前病例所需的专业能力。
