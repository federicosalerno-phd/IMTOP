"""IMTOP, IMage-TO-Print wound dressings.

Calibrate, segment and measure a wound from a single photograph, then preview
and export a printable 3D patch.

The package is split so that each piece can be changed on its own:

``config``        paths, tunables and environment overrides, the only module
                  that knows where things live on disk.
``core``          pure computation: geometry, imaging, metrics, report,
                  segmentation back-ends. No Qt, no UI, importable from
                  scripts and tests.
``bridge``        the QWebChannel object the web UI talks to. Marshals calls
                  onto the GUI thread and delegates everything else to ``core``.
``app``           the Qt window and process bootstrap.
``ui``            the web front-end (HTML + CSS + JS modules).
"""

__version__ = "2.0.0"
__all__ = ["__version__"]
