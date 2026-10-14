# Partial pilot attempt — excluded from scoring

- Cohort: S5B cases 80 and 90, exposed development data only.
- Case 80 audit-conditioned request returned and was written to `case-80_audit.json`.
- The next request (case 80 generic) was interrupted while awaiting the gateway response. No output artifact was committed for it; whether the service completed/billed it is unknown.
- The repair request was configured with `max_tokens=2048` and the process-local transport flag enabled. The returned usage still reported 7,429 completion tokens, so the gateway/model route did not provide an effective bound (or counts reasoning tokens beyond it).
- No judge/gold labels were read. This incomplete run has no generation manifest and is not scored or counted as a rescue result.
- Original GoS trace files and shared model configuration remain unchanged.
