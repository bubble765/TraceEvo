# Final limitations

Written from this block's artifacts. Each item names the file that shows it.

## 1. Detection is measured against this codebase's own fault model

Precision and recall in Table 2 are computed over injections this repository makes. That is a statement about the detector's consistency with its fault model, not about detection in the wild. n = 402 primary injections. A real-world anomaly distribution would exercise failure modes nothing here generates.

## 2. The Gate's positive branch

The block produced 5 gold-safe verified successes and 19 poisoned twins, which clears §10.4's threshold; the held-out positive-class figures are reported with Wilson intervals and their small denominators are still small denominators.

## 3. Match/Relevant is not the outcome verdict

Match 0.086207 and Relevant 0.37931 come from the frozen GoS-style evaluator; the experience outcomes ({'verified_incorrect': 31, 'uncertain': 20, 'verified_success': 7}) come from a different judge with a different prompt. §6.1 forbids reading one as the other, and neither is a diagnosis-quality claim by TraceEvo — S5B ran no behaviour experiment.

## 4. Overhead excludes the network

Local processing is a property of the implementation. Model calls 2160, wall clock 29489.3 s — the gateway was observed hanging for minutes during S4B, so wall clock measures the network as much as the method and is not used for any claim. Token usage is `TOKEN_UNAVAILABLE`: §14 forbids estimating it.

## 5. Natural evidence tiers are the same shape as every block

P0 317, P1 4, P2 438, P3 16 nodes. P1 remains absent and P3 remains rare, which is why ATGG is off and why the tiers are not used as a hard guard: case-51 was a verified success whose decision path carried P1 nodes.

## 6. Single corpus, single backbone

One benchmark (DiagnosisArena), one backbone (frozen at `deepseek-v4-flash` throughout), one language pair. Nothing here shows the results transfer to another model or another corpus.

## 7. Not claimed

* Success Memory improves diagnostic accuracy.
* Role-specific routing is effective.
* ATGG improves natural task performance.
* TraceEvo continuously improves medical diagnostic performance through online self-evolution.

None of these is supported by any experiment in this project, and §2 of the S5B instruction forbids claiming them regardless of the held-out result.
