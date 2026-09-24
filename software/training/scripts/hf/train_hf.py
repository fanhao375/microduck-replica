"""Back-compat shim: the submission logic moved to mjlab_microduck.hf_jobs.

Prefer the integrated flag:
    uv run train <task> <train args...> --hf-jobs [--namespace <ns>] [...]

This script keeps the old invocation working:
    uv run scripts/hf/train_hf.py <task> [submission flags] <train args...>
"""

import sys

from mjlab_microduck.hf_jobs import submit

if __name__ == "__main__":
    sys.exit(submit(sys.argv[1:]))
