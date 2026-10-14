# Final claim-evidence matrix

§19. The Final Heldout column is filled from this block's artifacts; the Development and Validation columns are carried from the earlier stages' own reports. A row is SUPPORTED only if the held-out artifacts say so — not because an earlier stage did.

| Claim | Development | Validation | Final Heldout | Status |
|---|---|---|---|---|
| Dual-graph traceability | yes | yes | yes | SUPPORTED |
| Controlled anomaly detection | yes | yes | yes (402 injections) | SUPPORTED |
| Observable-root localization | yes | yes | 0.978 | SUPPORTED |
| Incorrect-clean blocked by joint gate | yes | yes | yes | SUPPORTED |
| Correct-but-poisoned blocked | yes | yes | TraceEvo admits 0/19, OutcomeOnly admits 19/19 | SUPPORTED |
| Memory improves diagnosis | no | no | not tested | NOT CLAIMED |
| Role routing improves safety | no | no | not tested | NOT CLAIMED |
| ATGG improves natural tasks | unproven | low-trigger | not tested | NOT CLAIMED |

## What the held-out block did and did not reproduce

* Seals: 58 sealed, 58 verified, 0 rejected.
* Gold leakage: 0 (non-vacuous scan).
* Outcomes: {'verified_incorrect': 31, 'uncertain': 20, 'verified_success': 7}.
* Gate positive-class branch: **HELDOUT_POSITIVE_CLASS_ADEQUATE** (7 verified successes, 5 gold-safe).

A claim marked `development-supported only` is not a failure of the method; it is a statement about which block carries the evidence, and §2 requires it to be said in exactly those terms.
