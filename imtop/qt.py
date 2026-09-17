"""Qt binding shim: PyQt6 first, PyQt5 as a fallback.

Every other module imports Qt names from here, so the fallback lives in exactly
one place. ``USE_QT6`` tells callers which API flavour they got where the two
differ (scoped enums, ``exec`` vs ``exec_``).
"""
from __future__ import annotations

import sys

_QT6_ERR: BaseException | None = None
_QT5_ERR: BaseException | None = None

try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,  # noqa: F401
                                 QWidget, QFileDialog)
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage  # noqa: F401
    from PyQt6.QtWebEngineQuick import QtWebEngineQuick  # noqa: F401
    from PyQt6.QtQuick import QQuickView  # noqa: F401
    from PyQt6.QtWebChannel import QWebChannel  # noqa: F401
    from PyQt6.QtCore import (QObject, pyqtSlot, pyqtSignal, QUrl, Qt,  # noqa: F401
                              QTimer, QEvent, QMarginsF, QSize, QMetaObject, Q_ARG, QVariant,
                              QRect, QVariantAnimation, QEasingCurve)
    from PyQt6.QtGui import QColor, QIcon, QPageLayout, QPageSize  # noqa: F401

    USE_QT6 = True
    QUEUED = Qt.ConnectionType.QueuedConnection
    WEB_ATTR = QWebEngineSettings.WebAttribute
except ImportError as exc_qt6:  # pragma: no cover - depends on the install
    _QT6_ERR = exc_qt6
    try:
        from PyQt5.QtWidgets import (QApplication, QMainWindow, QVBoxLayout,  # noqa: F401
                                     QWidget, QFileDialog)
        from PyQt5.QtWebEngineWidgets import (QWebEngineView, QWebEngineSettings,  # noqa: F401
                                              QWebEnginePage)
        from PyQt5.QtWebEngine import QtWebEngine as QtWebEngineQuick  # noqa: F401
        from PyQt5.QtQuick import QQuickView  # noqa: F401
        from PyQt5.QtWebChannel import QWebChannel  # noqa: F401
        from PyQt5.QtCore import (QObject, pyqtSlot, pyqtSignal, QUrl, Qt,  # noqa: F401
                                  QTimer, QEvent, QMarginsF, QSize, QMetaObject, Q_ARG, QVariant,
                                  QRect, QVariantAnimation, QEasingCurve)
        from PyQt5.QtGui import QColor, QIcon, QPageLayout, QPageSize  # noqa: F401

        USE_QT6 = False
        QUEUED = Qt.QueuedConnection
        WEB_ATTR = QWebEngineSettings
    except ImportError as exc_qt5:
        # Neither binding is usable. A bare ModuleNotFoundError here only tells
        # the user that *some* submodule is missing, which is never the real
        # problem: the real problem is almost always the wrong interpreter (a
        # Python with no wheels for PyQt6-WebEngine yet). Say so.
        _QT5_ERR = exc_qt5
        _py = "%d.%d" % sys.version_info[:2]
        _hint = ""
        if sys.version_info >= (3, 13):
            _hint = ("\n  Python %s is too new: PyQt6-WebEngine does not publish "
                     "wheels for it yet.\n  Use Python 3.11." % _py)
        raise ImportError(
            "IMTOP needs PyQt6 with QtWebEngine, and this interpreter has neither "
            "PyQt6 nor a complete PyQt5.\n"
            "  interpreter : %s  (Python %s)\n"
            "  PyQt6       : %s\n"
            "  PyQt5       : %s%s\n"
            "  Fix         : run the app with the project venv "
            "(launch.bat / install.cmd), or install the bindings here:\n"
            "                \"%s\" -m pip install PyQt6 PyQt6-WebEngine"
            % (sys.executable, _py, exc_qt6, exc_qt5, _hint, sys.executable)
        ) from exc_qt5


def run_app(app: QApplication) -> int:
    """``app.exec()`` on Qt6, ``app.exec_()`` on Qt5."""
    return app.exec() if USE_QT6 else app.exec_()
