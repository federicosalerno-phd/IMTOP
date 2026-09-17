"""Compatibility launcher: ``python wound_app.py`` still starts IMTOP.

Kept because it is the file people (and editors' Run buttons) reach for. The
application itself lives in the ``imtop`` package - ``python -m imtop``.
Scripts that used to import ``Backend`` from here should use ``imtop.core``
instead; see ``tools/batch_run.py`` for the headless pattern.
"""
import sys
from pathlib import Path

# Running this file directly does not put the project on sys.path in every
# launcher, so make the import work regardless of the working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from imtop.bootstrap import ensure_interpreter

ensure_interpreter()

from imtop.app import main  # noqa: E402  (must come after the interpreter check)

if __name__ == "__main__":
    sys.exit(main())
