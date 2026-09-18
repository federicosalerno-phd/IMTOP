"""The window and the process bootstrap (``python -m imtop [image]``).

The window itself is not IMTOP's any more. It is SlantUI's: the frameless
Qt Quick shell, the native Windows frame behind it, the animated maximise and
the six chrome slots the page's title bar calls. That code was written here
first and moved out to ``slantui.shell`` so the next application starts with
it instead of copying this file; what is left below is the part that is
IMTOP and nothing else, which is the name, the icon, the palette, the
backend, and the photo a command line may hand the app at start.

The one thing an application has to arrange for itself is how the page
reaches the library's files. The page is loaded from ``file://``, so it can
only link what is on disk next to it, and SlantUI's own files live wherever
pip put the package. :func:`write_library_files` answers that: the five
stylesheets and the four scripts, in load order, as one file each, written
next to ``ui/index.html`` before the window opens. The page then stays a
plain static file with two relative links in it, and the same two lines work
from a checkout and from an installed copy.
"""
from __future__ import annotations

import sys

from slantui import css, js
from slantui.shell import Application, Window

from .bridge import Backend
from .config import (APP_NAME, APP_TITLE, APP_USER_MODEL_ID, USE_GPU, UI_DIR, ASSETS_DIR,
                     ensure_dirs)

# The palette the page sets in <html data-palette>. The window takes the
# colour behind the page from it, so the shade that shows while the page
# loads, and in the strip a live resize has not painted yet, is defined once
# in SlantUI and not copied into IMTOP as a hex value.
PALETTE = "gold-dark"


def write_library_files() -> None:
    """Put SlantUI's stylesheets and scripts next to the page.

    Rewritten on every start, so an edit inside the installed SlantUI shows
    up the next time the window opens. Both files are generated and are in
    ``.gitignore``.
    """
    (UI_DIR / "slantui.css").write_text(css.bundle(), encoding="utf-8")
    (UI_DIR / "slantui.js").write_text(js.bundle(), encoding="utf-8")


def build_window(startup_image: str | None = None) -> Window:
    """The window with IMTOP's backend on it.

    Split out of :func:`main` because the harnesses in ``dev/`` open the same
    window without the rest of the bootstrap. The backend is built first: it
    carries ``startup_image``, which the page asks for as soon as it loads,
    and the window has to be able to hand it over on the first call.
    """
    backend = Backend()
    backend.startup_image = startup_image
    return Window(UI_DIR / "index.html", bridge=backend, title=APP_TITLE,
                  icon=ASSETS_DIR / "logo.ico", palette=PALETTE)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    startup_image = next((a for a in argv[1:] if not a.startswith("-")), None)

    ensure_dirs()
    write_library_files()
    app = Application(APP_NAME, app_id=APP_USER_MODEL_ID, gpu=USE_GPU, argv=argv)
    win = build_window(startup_image)
    win.show()
    return app.run()
