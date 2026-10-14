"""S3B: audited experience admission.

The pieces are deliberately separate because §4.1 requires a strict order:

    GoS runs          -> nothing here is involved
    trace sealed      -> artifact hashes computed
    OutcomeVerifier   -> the first thing allowed to read the benchmark label
    Experience Gate   -> reads the verdict + the audit, never the label
    Memory Store      -> only what the Gate admitted

`blinding.py` is what makes the first line true rather than promised: the run
reads a dataset copy with the gold columns removed, so during the run the
label is not in the process at all.
"""
