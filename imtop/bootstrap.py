"""Make IMTOP start from *any* launcher, even the wrong Python.

The app must run whether it is started from the desktop shortcut, the taskbar,
``launch.bat``, ``python -m imtop`` or an editor's Run button. The editor is the
awkward one: it uses whichever interpreter happens to be selected, and on this
machine that can be a Python with no usable Qt (PyQt6-WebEngine publishes no
wheels for 3.13/3.14, and a bare PyQt5 has no QtWebEngine at all).

So before anything imports Qt: if this interpreter cannot run the app, look for
one that can and hand over to it. ``IMTOP_PYTHON`` overrides the search;
``IMTOP_REEXEC=1`` disables it (and is set on the child, so this can happen at
most once).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
REEXEC_FLAG = "IMTOP_REEXEC"

# Printed by the child probe below; kept on one line so it is easy to parse.
_PROBE = (
    "import importlib.util as u;"
    "qt=bool(u.find_spec('PyQt6.QtWebEngineWidgets') or u.find_spec('PyQt5.QtWebEngineWidgets'));"
    "sam=bool(u.find_spec('torch') and u.find_spec('segment_anything'));"
    "print('qt=%d sam=%d' % (qt, sam))"
)


def _find(module: str) -> bool:
    """Is *module* importable here? Never actually imports the extension."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def qt_available() -> bool:
    """True when this interpreter has a Qt binding with QtWebEngine."""
    return _find("PyQt6.QtWebEngineWidgets") or _find("PyQt5.QtWebEngineWidgets")


def sam_available() -> bool:
    return _find("torch") and _find("segment_anything")


def candidates() -> list[Path]:
    """Interpreters worth trying, best first.

    The console build (``python.exe``) is used instead of ``pythonw.exe``: the
    parent process waits for it, and any error still has somewhere to go.
    """
    out: list[Path] = []
    override = os.environ.get("IMTOP_PYTHON")
    if override:
        out.append(Path(override))
    out.append(PROJECT_DIR / ".venv" / "Scripts" / "python.exe")        # the installer's venv
    out.append(PROJECT_DIR / ".venv" / "bin" / "python")                # same, POSIX
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "Python" / "Python311" / "python.exe")
    out.append(Path(sys.base_prefix) / "python.exe")                    # the venv's own base
    seen, uniq = set(), []
    here = os.path.normcase(str(Path(sys.executable).resolve()))
    for p in out:
        try:
            key = os.path.normcase(str(p.resolve()))
        except OSError:
            continue
        if key in seen or key == here or not p.is_file():
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def probe(exe: Path) -> tuple[bool, bool]:
    """``(has_qt, has_sam)`` for another interpreter. Never raises."""
    try:
        out = subprocess.run([str(exe), "-c", _PROBE], capture_output=True,
                             text=True, timeout=25).stdout
    except (OSError, subprocess.SubprocessError):
        return False, False
    return "qt=1" in out, "sam=1" in out


def ensure_interpreter(argv: list[str] | None = None) -> None:
    """Hand over to a working interpreter if this one cannot run the app.

    Returns normally when the current interpreter is fine (the common case,
    and it costs one ``find_spec``), or when no better one was found, the
    import of :mod:`imtop.qt` then raises a message that says what to install.
    """
    if os.environ.get(REEXEC_FLAG) == "1":
        return
    if qt_available():
        return

    args = list(sys.argv[1:] if argv is None else argv)
    fallback: Path | None = None
    for exe in candidates():
        has_qt, has_sam = probe(exe)
        if not has_qt:
            continue
        if not has_sam and fallback is None:
            fallback = exe          # runs, but without SAM: keep looking first
            continue
        fallback = exe
        break
    if fallback is None:
        return

    sys.stderr.write(
        "[IMTOP] %s cannot run the UI (no Qt WebEngine) - restarting with %s\n"
        % (sys.executable, fallback)
    )
    env = dict(os.environ, **{REEXEC_FLAG: "1"})
    raise SystemExit(subprocess.call([str(fallback), "-m", "imtop", *args],
                                     cwd=str(PROJECT_DIR), env=env))
