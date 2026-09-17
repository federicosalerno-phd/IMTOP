"""The Qt window and the process bootstrap (``python -m imtop [image]``).

The window is a Qt Quick window (``QQuickView``) holding one QML
``WebEngineView`` (``imtop/shell.qml``). It used to be a widget
``QWebEngineView`` inside a ``QMainWindow``; that was changed on 2026-09-17
for one measured reason: with widgets, every resize of the window goes
through Qt's widget backing store, which on Qt 6 presents through the GPU
three times per resize with a vsync wait each time, 50 to 80 ms per step
whatever the graphics backend, so a live resize of the window stuttered at
under 20 fps. A Qt Quick window has one swap chain and a resize costs one
frame: the content follows the window edge at the display's rate, with vsync
on (presenting without it tore the image into a horizontal wave on Windows
11). The page, the bridge and the backend did not change; the dev tools talk
to the page through :meth:`MainWindow.run_js`.
"""
from __future__ import annotations

import os
import sys

from .config import (APP_NAME, APP_TITLE, APP_USER_MODEL_ID, USE_GPU, UI_DIR, ASSETS_DIR,
                     PACKAGE_DIR, ensure_dirs)
from .qt import (QApplication, QQuickView, QtWebEngineQuick, QObject, QUrl, Qt, QColor, QIcon,
                 QSize, QMetaObject, Q_ARG, QVariant, QRect, QVariantAnimation, QEasingCurve,
                 USE_QT6, run_app)
from .bridge import Backend

# Matches the UI's page background (Review Desk palette: Bg), so nothing flashes
# a different shade while the page loads or while the window is resized.
WINDOW_BG = "#0D0D0F"
SHELL_QML = PACKAGE_DIR / "shell.qml"

# ── the Win32 frame behind the frameless window ──────────────────────────────
# Qt's frameless window is a bare WS_POPUP, and Windows only animates minimise,
# restore, maximise and close for windows that carry a real frame (WS_CAPTION
# and WS_THICKFRAME): without one the window snapped in and out of the taskbar
# instead of sliding. The fix is the one Chromium and VS Code use. Keep Qt's
# frameless hint, so Qt reports zero frame margins and keeps pinning a
# maximised window to the work area through MINMAXINFO; give the HWND the
# frame styles anyway; and answer WM_NCCALCSIZE with "the client area is the
# whole window", which removes the caption and the borders again while the
# styles, and with them the animations, the DWM shadow and Aero Snap, stay.
WM_NCCALCSIZE = 0x0083
WM_NCACTIVATE = 0x0086
WS_FRAME = (0x00C00000     # WS_CAPTION
            | 0x00040000   # WS_THICKFRAME
            | 0x00020000   # WS_MINIMIZEBOX
            | 0x00010000   # WS_MAXIMIZEBOX
            | 0x00080000)  # WS_SYSMENU
GWL_STYLE = -16
SWP_FRAMECHANGED = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010   # + NOMOVE NOSIZE NOZORDER NOACTIVATE
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = -2          # 0xFFFFFFFE: no border at all (Windows 11)
DWMWA_TRANSITIONS_FORCEDISABLED = 3

# ── the window animates its own maximise, restore and minimise ───────────────
# Windows' own transitions scale a snapshot of the last frame over a fixed
# curve and, for a window like this one, showed up as a jump. The window
# animates its geometry itself instead: the content is laid out and drawn at
# every intermediate size (cheap since the Quick shell), so the ramp is
# gradual and alive. The DWM transition is switched off only around the
# state change itself, so nothing animates twice.
STATE_ANIM_MS = 240
MIN_SIZE = QSize(1024, 640)


def chromium_flags() -> str:
    """Flags for the embedded Chromium.

    Chromium's own defaults are kept on purpose. Its GPU blocklist stays in
    force, so a machine whose driver Chromium knows to be broken falls back to
    software compositing and still shows the app, and rasterisation is left to
    Chromium. Nothing here has to help the image zoom along any more: the photo
    is painted on the 2D canvas by ``ui/js/canvas.js``, one layer the size of
    the stage, so there is no large layer for a tile rasteriser to fall behind
    on and paint black.
    """
    if not USE_GPU:
        return "--disable-gpu"
    if not USE_QT6:
        # The Qt5/Chromium-83 compositor starves its vsync source; drive frames
        # from a timer there. Qt6's compositor does not want this.
        return "--disable-gpu-vsync --disable-frame-rate-limit"
    return ""


def set_app_user_model_id() -> None:
    """Give the process its own taskbar identity (Windows only).

    Without it the taskbar files the window under pythonw.exe: the button gets
    Python's icon once the window is gone, and "Pin to taskbar" pins a bare
    interpreter that opens nothing. The installer writes the same id on the
    Start Menu shortcut, so a pinned IMTOP starts IMTOP. Must run before the
    first window is created.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def configure_environment() -> None:
    """Must run before ``QApplication`` is created.

    The window follows the display's scale (a 150 % display gets a 150 % UI and
    the page sees devicePixelRatio 1.5); the canvas code sizes its backing
    stores from that ratio, so nothing is blurry on a scaled display.

    The scene graph keeps vsync: without it, Windows 11 hands the window's
    flip-model swap chain straight to the screen and a pan tears into a
    horizontal wave. With the Quick shell a vsynced resize is one frame per
    step anyway (measured with dev/perf_window.py).
    """
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags()
    if not USE_GPU:
        # Qt's side on the software (WARP) device; Chromium's side is software
        # through the flag above. For machines with a broken driver.
        os.environ.setdefault("QSG_RHI_PREFER_SOFTWARE_RENDERER", "1")


class MainWindow(QQuickView):
    """The window has no native frame.

    Its title bar is part of the page (``imtop/ui/js/titlebar.js``), which is
    what lets it carry the app's own look instead of a second, differently
    styled strip on top of it. Moving and resizing are handed back to the
    window manager through ``startSystemMove`` / ``startSystemResize``, so
    Windows keeps doing snapping, edge magnetism and the drop shadow.
    """

    def __init__(self, startup_image: str | None = None):
        super().__init__()
        self.setTitle(APP_TITLE)          # still the taskbar label
        self.setFlag(Qt.WindowType.FramelessWindowHint, True)
        # The .ico carries 16..256 px renditions, so Windows picks a real 16 px
        # icon for the title bar instead of squashing the 256 px one.
        for name in ("logo.ico", "logo.png"):
            icon = ASSETS_DIR / name
            if icon.is_file():
                self.setIcon(QIcon(str(icon)))
                break
        self.setColor(QColor(WINDOW_BG))
        self.resize(1280, 800)
        self.setMinimumSize(MIN_SIZE)
        self.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        self._anim = None               # the running geometry animation, if any
        self._normal_rect = None        # where to come back to from maximised
        self._pre_min = None            # (rect, was maximised) before a minimise

        self.backend = Backend(self)
        self.backend.startup_image = startup_image
        self.backend.window = self
        self._native_frame = False      # set once the HWND carries the frame styles
        self._js_callbacks: dict[int, object] = {}
        self._js_token = 0

        self._load_ui()
        self.windowStateChanged.connect(self._on_state_changed)

    def _load_ui(self) -> None:
        """Load ``imtop/shell.qml``, which points its WebEngineView at
        ``imtop/ui/index.html``.

        A file:// URL (not ``setHtml``) is what makes the page's own relative
        ``css/``, ``js/`` and ``../vendor/`` references resolve, and it is how
        the UI stays a set of ordinary editable files.
        """
        index = UI_DIR / "index.html"
        if not index.is_file():
            raise FileNotFoundError(f"UI not found: {index}")
        if not SHELL_QML.is_file():
            raise FileNotFoundError(f"window shell not found: {SHELL_QML}")
        ctx = self.rootContext()
        ctx.setContextProperty("uiUrl", QUrl.fromLocalFile(str(index)))
        ctx.setContextProperty("uiBackground", WINDOW_BG)
        self.setSource(QUrl.fromLocalFile(str(SHELL_QML)))
        if self.status() != QQuickView.Status.Ready:
            raise RuntimeError("window shell failed to load: "
                               + "; ".join(e.toString() for e in self.errors()))
        root = self.rootObject()
        self.channel = root.findChild(QObject, "channel")
        self.channel.registerObject("backend", self.backend)
        root.jsResult.connect(self._on_js_result)

    # ── the page, for the dev tools ──────────────────────────────────────
    def run_js(self, script: str, callback=None) -> None:
        """Run ``script`` in the page; ``callback(value)`` gets its result
        (a JSON string, usually) on the GUI thread, once."""
        self._js_token += 1
        token = self._js_token
        if callback is not None:
            self._js_callbacks[token] = callback
        QMetaObject.invokeMethod(self.rootObject(), "run",
                                 Q_ARG("QVariant", QVariant(script)),
                                 Q_ARG("QVariant", QVariant(token)))

    def _on_js_result(self, token: int, value) -> None:
        cb = self._js_callbacks.pop(int(token), None)
        if cb is not None:
            cb(value)

    # ── what the bridge and the tests ask of a "window" ──────────────────
    def isMaximized(self) -> bool:
        return bool(self.windowState() & Qt.WindowState.WindowMaximized)

    def isMinimized(self) -> bool:
        return bool(self.windowState() & Qt.WindowState.WindowMinimized)

    def windowHandle(self):
        """The bridge hands drags and resizes to ``windowHandle()``; here that
        is the window itself."""
        return self

    def setWindowTitle(self, title: str) -> None:
        self.setTitle(title)

    def _on_state_changed(self, state) -> None:
        """Tell the page when Windows maximised or restored us, so the title
        bar's own button shows the right glyph. Coming back from the taskbar,
        grow from the small rectangle the window was minimised at."""
        self.backend.windowMaximized.emit(self.isMaximized())
        if self._pre_min is not None and not self.isMinimized():
            rect, was_max = self._pre_min
            self._pre_min = None
            if was_max:
                self._normal_rect = rect
                target = self.screen().availableGeometry()
                self._animate(self.geometry(), target, self._finish_maximise)
            else:
                self._animate(self.geometry(), rect, self._restore_min_size)

    # ── animated state changes ───────────────────────────────────────────
    def _dwm_transitions(self, enabled: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            flag = ctypes.c_int(0 if enabled else 1)
            dwm.DwmSetWindowAttribute(wintypes.HWND(int(self.winId())), DWMWA_TRANSITIONS_FORCEDISABLED,
                                      ctypes.byref(flag), 4)
        except Exception:
            pass

    def _animate(self, start: QRect, end: QRect, done) -> None:
        """Move the window's geometry from ``start`` to ``end`` over
        STATE_ANIM_MS with an ease-out, then call ``done``."""
        if self._anim is not None:
            self._anim.stop()
        anim = QVariantAnimation(self)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setDuration(STATE_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self.setGeometry)

        def finished():
            self._anim = None
            self.setGeometry(end)
            done()
        anim.finished.connect(finished)
        self._anim = anim
        anim.start()

    def _restore_min_size(self) -> None:
        self.setMinimumSize(MIN_SIZE)

    def _finish_maximise(self) -> None:
        self._dwm_transitions(False)
        QQuickView.showMaximized(self)
        self._dwm_transitions(True)
        self._restore_min_size()

    def showMaximized(self) -> None:
        if self.isMaximized() or self._anim is not None:
            return
        self._normal_rect = self.geometry()
        self._animate(self.geometry(), self.screen().availableGeometry(), self._finish_maximise)

    def showNormal(self) -> None:
        if self._anim is not None:
            return
        if not self.isMaximized():
            QQuickView.showNormal(self)
            return
        start = self.geometry()
        target = self._normal_rect or QRect(start.x() + 80, start.y() + 60, 1280, 800)
        self._dwm_transitions(False)
        QQuickView.showNormal(self)
        self.setGeometry(start)          # stay full size, the animation brings it down
        self._dwm_transitions(True)
        self._animate(start, target, lambda: None)

    def showMinimized(self) -> None:
        if self.isMinimized() or self._anim is not None:
            return
        start = self.geometry()
        was_max = self.isMaximized()
        if was_max:
            self._dwm_transitions(False)
            QQuickView.showNormal(self)
            self.setGeometry(start)
            self._dwm_transitions(True)
        # Recorded after the state change above: that change fires
        # windowStateChanged, which would otherwise consume it at once.
        self._pre_min = (self._normal_rect if was_max and self._normal_rect else start, was_max)
        # Shrink towards the bottom edge, where the taskbar is, to a quarter.
        w, h = start.width() // 4, start.height() // 4
        end = QRect(start.center().x() - w // 2, self.screen().availableGeometry().bottom() - h, w, h)
        self.setMinimumSize(QSize(0, 0))

        def done():
            self._dwm_transitions(False)
            QQuickView.showMinimized(self)
            self._dwm_transitions(True)
        self._animate(start, end, done)

    # ── the native frame ─────────────────────────────────────────────────
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._native_frame:
            self._dress_native_window()

    def _dress_native_window(self) -> None:
        """Give the HWND a real frame (see the note at the top), round the
        corners and remove Windows 11's one-pixel border.

        The corners: a framed window gets them for free, a frameless one has
        to ask, or it is the only square window on the desktop. The border:
        Windows 11 draws a light hairline around every window, and this app
        draws no outlines anywhere, so the DWM is told to draw none.
        Older Windows refuse the two attributes; nothing else depends on them.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = wintypes.HWND(int(self.winId()))
            get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            set_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_uint]

            # nativeEvent() must already answer WM_NCCALCSIZE when the frame
            # change below sends it, or the caption would appear for a frame.
            self._native_frame = True
            set_long(hwnd, GWL_STYLE, get_long(hwnd, GWL_STYLE) | WS_FRAME)
            user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FRAMECHANGED)

            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            pref = ctypes.c_int(DWMWCP_ROUND)
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref), 4)
            none = ctypes.c_int(DWMWA_COLOR_NONE)
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, ctypes.byref(none), 4)
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        """The two messages that make a framed HWND look frameless.

        WM_NCCALCSIZE with wParam set: answering 0 without touching the
        rectangle makes the client area the whole window, so no caption and no
        border are ever laid out. Qt pins a maximised window to the work area
        itself (MINMAXINFO, because of the frameless hint), so nothing has to
        be inset here. WM_NCACTIVATE: passed to DefWindowProc with lParam -1,
        which is the documented way to say "do not repaint the caption", the
        one place a 1 px caption line could still show when focus changes.

        Everything else answers (False, 0), which is what the base
        implementation answers. It is not called: in PyQt6, calling
        ``super().nativeEvent()`` from an override crashes the process.
        """
        if self._native_frame and sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_NCCALCSIZE and msg.wParam:
                    return True, 0
                if msg.message == WM_NCACTIVATE:
                    proc = ctypes.windll.user32.DefWindowProcW
                    proc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                    proc.restype = ctypes.c_ssize_t
                    return True, int(proc(msg.hWnd, WM_NCACTIVATE, msg.wParam, -1))
            except Exception:
                pass
        return False, 0

    def closeEvent(self, event) -> None:
        self.backend.shutdown()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    startup_image = next((a for a in argv[1:] if not a.startswith("-")), None)

    set_app_user_model_id()
    configure_environment()
    ensure_dirs()
    if USE_QT6:
        try:
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        except Exception:
            pass
    else:
        QApplication.setAttribute(Qt.AA_DisableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, False)

    QtWebEngineQuick.initialize()     # before the application object, as Qt requires
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")            # the file dialogs
    win = MainWindow(startup_image)
    win.show()
    return run_app(app)
