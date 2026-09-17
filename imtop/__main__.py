"""``python -m imtop [image]`` - start the app."""
import sys

from .bootstrap import ensure_interpreter

# Before anything imports Qt: if this interpreter cannot run the UI, hand over
# to one that can (see bootstrap.py). Never returns in that case.
ensure_interpreter()

from .app import main  # noqa: E402  (must come after the check)

sys.exit(main())
