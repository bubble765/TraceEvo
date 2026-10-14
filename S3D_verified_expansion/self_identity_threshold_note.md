# The 28.6789 threshold, renamed and demoted (§9.1)

**`self_identity_separation_threshold` = 28.6789** — renamed from "success retrieval threshold".

## what it was measured on

BM25 scores between each case 10-39 success memory and its own source case (self, n=6, 44.26-126.13) versus every other case (other, n=28, 0.43-13.10); threshold at the midpoint.

## what it proves

The retriever separates a case from itself by a factor of 3.4 in BM25 score, so the abstention mechanism is capable of abstaining rather than merely never being asked to.

## what it does not prove

That a new similar case retrieves the *right* historical experience. Self-versus-other is not the cross-case question. The dry-run on case 40-49 returning 0 hits is consistent with abstention working and equally consistent with the retriever having nothing useful to say; this measurement cannot tell the two apart.

## s4 constraint

S4 must not adopt 28.6789 as an online cross-case relevance threshold. Any threshold S4 uses must be derived from reviewed cross-case pairs, which is what the review queue exists to produce.

