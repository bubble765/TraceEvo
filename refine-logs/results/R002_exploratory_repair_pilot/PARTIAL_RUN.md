# Partial pilot attempt — excluded from scoring

- Cohort: S5B cases 80 and 90, exposed development data only.
- Case 80 audit-conditioned request returned and was written to `case-80_audit.json`.
- The next request (case 80 generic) was interrupted while waiting for the gateway response. No output artifact was committed for it; whether the service completed generation is unknown.
- The first reply contained 9,355 output tokens although the caller requested 2,048. Root cause: the shared GoS LLM transport configuration did not send `max_tokens`.
- No judge/gold labels were read. This incomplete run has no generation manifest and is not scored or counted as an experiment result.
- A new run uses a separate output directory and enables the declared token cap process-locally. The shared GoS configuration and original case artifacts remain unchanged.
