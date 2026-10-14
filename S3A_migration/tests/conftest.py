import os
import sys

# Make the TraceEvo package and the instrumented GoS working copy importable
# no matter where pytest is invoked from.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for path in (_ROOT, os.path.join(_ROOT, "gos")):
    if path not in sys.path:
        sys.path.insert(0, path)
