"""HTML to PDF, with the engine the app already carries.

QtWebEngine can print any page it can render, so the PDF is the very same
report the HTML file holds, laid out by the very same Chromium. That is worth
more than it sounds: there is no second renderer to keep in sync, and no extra
dependency to install on the operator's machine.

The page is loaded from a temporary *file*, never through ``setHtml``: the
report embeds its images as data URLs and ``setHtml`` refuses anything past
2 MB, which a two-figure report reaches without trying.

Printing is asynchronous, in two hops (load, then print), so the caller hands
in a callback instead of waiting. Every path through here calls it exactly
once, including the watchdog: a UI waiting for an answer that never comes is
worse than one told the export failed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from .qt import (QMarginsF, QPageLayout, QPageSize, QTimer, QUrl,
                 QWebEnginePage, USE_QT6)

PDF_TIMEOUT_MS = 90_000        # generous: 24 figures on a slow machine still make it

# Chromium honours the layout handed to printToPdf, not the CSS @page rule, so
# the margins live here. The @page rule in the report is for a human pressing
# Ctrl+P on the HTML file.
PAGE_MARGINS_MM = 10.0

_JOBS: list = []               # keeps pages alive until their callback has run


def _page_layout():
    if USE_QT6:
        size = QPageSize(QPageSize.PageSizeId.A4)
        orient = QPageLayout.Orientation.Portrait
        unit = QPageLayout.Unit.Millimeter
    else:                                          # pragma: no cover - PyQt5 fallback
        size = QPageSize(QPageSize.A4)
        orient = QPageLayout.Portrait
        unit = QPageLayout.Millimeter
    m = PAGE_MARGINS_MM
    return QPageLayout(size, orient, QMarginsF(m, m, m, m), unit)


class _PdfJob:
    """One page, one temp file, one callback. Self-destructs when done."""

    def __init__(self, html: str, out_path: Path, done: Callable[[bool, str], None]):
        self.out = Path(out_path)
        self.done = done
        self.finished = False

        fd = tempfile.NamedTemporaryFile(prefix="imtop_report_", suffix=".html",
                                         delete=False, mode="w", encoding="utf-8")
        fd.write(html)
        fd.close()
        self.tmp = Path(fd.name)

        self.page = QWebEnginePage()
        self.page.loadFinished.connect(self._loaded)
        self.page.pdfPrintingFinished.connect(self._printed)
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self._finish(False, "PDF export timed out"))
        self.timer.start(PDF_TIMEOUT_MS)

        _JOBS.append(self)
        self.page.load(QUrl.fromLocalFile(str(self.tmp)))

    def _loaded(self, ok: bool) -> None:
        if self.finished:
            return
        if not ok:
            self._finish(False, "the report page could not be rendered")
            return
        try:
            self.out.parent.mkdir(parents=True, exist_ok=True)
            self.page.printToPdf(str(self.out), _page_layout())
        except Exception as e:
            self._finish(False, str(e))

    def _printed(self, file_path: str, success: bool) -> None:
        self._finish(bool(success), str(file_path) if success else "printing failed")

    def _finish(self, ok: bool, msg: str) -> None:
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        try:
            self.tmp.unlink()
        except OSError:
            pass
        try:
            self.done(ok, msg)
        finally:
            # Drop the page on the next turn of the event loop: it is still the
            # sender of the signal we are inside of.
            QTimer.singleShot(0, lambda: _JOBS.remove(self) if self in _JOBS else None)


def html_to_pdf(html: str, out_path: Path, done: Callable[[bool, str], None]) -> None:
    """Render ``html`` into ``out_path``. ``done(ok, message)`` runs on the GUI
    thread once, whatever happens."""
    _PdfJob(html, out_path, done)
