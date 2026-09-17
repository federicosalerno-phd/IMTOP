# IMTOP wound annotator

[![Paper](https://img.shields.io/badge/Paper-PDF-b31b1b.svg)](IMTOP_paper.pdf)

A Windows desktop tool that **calibrates, segments and measures a wound from a
single photograph**, previews and exports a printable 3D patch, and writes a
report. It is the software released alongside the IMTOP (*IMage-TO-Print
wound dressings*) [paper](IMTOP_paper.pdf).

Segmentation back-ends: **Segment Anything (SAM, ViT-B)**, **GrabCut** and
**Watershed**, plus a manual trace you can edit point by point.

## Install (Windows 10 / 11)

1. Download the latest `IMTOP-vX.Y.Z.zip` from the
   [Releases page](https://github.com/federicosalerno-phd/IMTOP/releases).
2. Extract it to a normal folder, for example `C:\IMTOP` or `Documents\IMTOP`.
   Avoid a OneDrive-synced folder: the private environment the installer
   builds is thousands of small files, and OneDrive tries to upload every one.
3. Double-click **`install.cmd`**.
   If Windows shows "Windows protected your PC", click *More info*, then
   *Run anyway*: the installer is a plain script, not a signed binary.
4. Press **Install**, wait, press **Launch IMTOP**.

Nothing else is needed: no Python to set up, no packages to install by hand,
no admin rights. IMTOP appears on the Desktop and in the Start Menu.

What the installer does, in order:

- finds a 64-bit **Python 3.11** on the PC, or installs one for the current
  user (with `winget`, or with the python.org installer if `winget` is missing);
- creates a private environment in `.venv` inside the IMTOP folder;
- installs **PyTorch 2.6** (the CPU build by default; the CUDA 12.4 build if it
  finds an NVIDIA GPU and you leave the box ticked, which is a much larger
  download) and everything in `requirements.txt`;
- checks that every module imports;
- writes `IMTOP.lnk` on the Desktop and in the Start Menu, with the logo.

It shows what it is doing, with a progress bar and, admittedly, some sarcasm.
The full log is in `%LOCALAPPDATA%\IMTOP\install.log`.

Running `install.cmd` again on an installed copy is safe: every step that is
already done is skipped, so it is also how you update after extracting a new
release over the old folder. To uninstall, delete the folder, the two
shortcuts, and `%LOCALAPPDATA%\IMTOP` (models and reports).

### Requirements

| | |
|---|---|
| OS | Windows 10 or 11, 64-bit |
| Disk | about 2 GB (packages, the SAM model, the environment) |
| Internet | during the install (about 600 MB, or 3 GB for the GPU build) and once more the first time SAM is used (375 MB model) |
| GPU | optional. An NVIDIA card makes SAM answer in a second instead of ten |

### The SAM model

The SAM ViT-B checkpoint (375 MB) is not in the download. The app fetches it
the first time you run SAM, with a progress bar, into
`%LOCALAPPDATA%\IMTOP\models`. A `sam_vit_b.pth` placed next to `install.cmd`
is used instead, so a copy you already have is never downloaded twice.
GrabCut and Watershed work without it.

The encoder needs about 1.8 GB of video memory and shares the card with the
window itself, so on a 4 GB laptop GPU with a browser open there may not be
enough left. The app checks before every run and works on the processor instead
when there is not, which takes about ten seconds for a 1280x720 photo and says
so on the progress bar. `IMTOP_SAM_DEVICE=cpu` (or `cuda`) settles it by hand.

## Using IMTOP

The window walks through six steps; every step is reachable at any time and a
tick on a tab means that step's work was done, not that the step was visited.

1. **Image**: open a photo. Drop it on the card in the middle of the stage or
   click that card, press **Open image** in the side panel, or pass the path on
   the command line.
2. **Calibration**: click the two ends of a reference of known length to set
   the scale in px/mm, or skip it and work in pixels.
3. **Manual segmentation**: trace the wound with control points (spline
   preview, undo/redo). **Draft outline (SAM)** asks the model for a first
   outline instead of tracing it by hand. It needs a wound that clearly stands
   out from the skin, it can fail, and what it gives back is a machine outline:
   check it, drag the points, then run the comparison again from this step.
4. **Auto segmentation**: run SAM, GrabCut or Watershed; the result is drawn
   over your trace.
5. **Metrics**: 22 measures in two columns: overlap (Dice, IoU, uncovered
   and excess area, the coverage cost index), areas and their relative error,
   distances (Hausdorff, average surface distance), perimeters, compactness,
   bounding boxes and aspect ratios, in mm and mm² when calibrated and in
   pixels otherwise. Clicking a row draws that measure on the photo. Export the
   metrics as JSON, the trace as a ground-truth mask (PNG), or a **report**.
6. **3D patch**: preview the extruded patch for both contours and export an
   STL.

The **report** is a PDF (A4, printed by the app itself, no internet needed):
the summary cards, the facts of the run, the photo and the overlay, a short
guide to reading the numbers, the 22 metrics with a one-line meaning each, one
figure per metric showing what it was measured on, and the 3D patch parameters.
Type an `.html` name in the save dialog to get the same document as a
self-contained web page instead.

`Ctrl` + mouse wheel zooms the photo, centred on the cursor. Whole-page zoom is
disabled by design.

### Configuration (environment variables, all optional)

| Variable | Default | Purpose |
|---|---|---|
| `SAM_CHECKPOINT` | `sam_vit_b.pth` next to the app, else `%LOCALAPPDATA%\IMTOP\models\sam_vit_b.pth` | Path to the SAM checkpoint. |
| `IMTOP_MODELS_DIR` | `%LOCALAPPDATA%\IMTOP\models` | Where downloaded models go. |
| `IMTOP_REPORTS_DIR` | `%LOCALAPPDATA%\IMTOP\reports` | Default folder for exports when no image folder applies. |
| `IMTOP_SOFTWARE_RENDER` | unset | Set to `1` if the window comes up black or empty (broken GPU driver). |
| `IMTOP_PYTHON` | unset | Interpreter to hand over to when the app is started with a Python that cannot run it. |

### Troubleshooting

- **The installer window never appears**: open `%LOCALAPPDATA%\IMTOP\install.log`.
  A corporate policy that blocks PowerShell scripts is the usual cause; the
  installer only needs `powershell.exe` with `-ExecutionPolicy Bypass`, which
  is what `install.cmd` passes.
- **"pip could not install..."**: no internet, or a proxy. pip honours
  `HTTPS_PROXY`. Press *Try again* once the connection is back; finished steps
  are skipped.
- **The app window is black or empty**: set `IMTOP_SOFTWARE_RENDER=1` and
  start it again.
- **Started from an editor and it complains about Qt WebEngine**: the editor
  picked another Python. IMTOP needs 3.11 (PyQt6-WebEngine ships no wheels for
  3.13 or 3.14). The app repairs this by itself when it can find a working
  interpreter (`.venv` first); `IMTOP_PYTHON` forces one.

## For developers

```bash
git clone https://github.com/federicosalerno-phd/IMTOP.git
cd IMTOP
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m imtop            # or: python wound_app.py, or launch.bat
```

Swap `/cpu` for `/cu124` in the first `pip install` for an NVIDIA GPU. Or just
double-click `install.cmd`: the result is the same `.venv`.

Repository layout:

```
imtop/                    the application package (python -m imtop [image])
  app.py                  the window (a Qt Quick window holding the web view), process
                          bootstrap, taskbar identity
  shell.qml               the window's only content: the QML WebEngineView that shows ui/
  bridge.py               the API the UI calls over QWebChannel
  printing.py             HTML to PDF through QtWebEngine
  config.py               paths, tunables, environment overrides
  core/                   geometry, imaging, metrics, session, results, report,
                          segmentation back-ends (no Qt: tools and tests import it headless)
  ui/                     the web UI: index.html + css/ + js/ + assets/ (logo.png, logo.ico)
  vendor/                 three.min.js + OrbitControls.js for the 3D viewer
installer/install.ps1     the installer window (WPF in PowerShell) + quips.txt
install.cmd               double-click entry point
launch.bat                starts the app from .venv (or a per-user Python 3.11)
wound_app.py              compatibility launcher, same as python -m imtop
requirements.txt          pinned dependencies of the app
tools/batch_run.py        reproducibility: Experiment A, headless, on imtop.core
tools/montecarlo_distortion.py   reproducibility: Monte Carlo error budget of the patch
tools/requirements-analysis.txt  pandas + matplotlib, for the script above
Graphs/                   R scripts and CSVs behind the paper figures (rendered outputs are git-ignored)
.github/workflows/release.yml    tag vX.Y.Z -> zip -> GitHub Release
```

The UI is loaded from a `file://` URL, so the scripts are classic scripts in
dependency order, not ES modules. The look is defined once, in
`imtop/ui/css/tokens.css`; the canvas and the 3D materials read the same
tokens at start-up.

Not in the repository (see `.gitignore`): the SAM checkpoint, the clinical
images and masks, `dev/` (local test scripts), `.venv/`.

### Releasing

1. Set `APP_VERSION` in `imtop/config.py`.
2. `git tag vX.Y.Z && git push origin vX.Y.Z`.

The workflow refuses a tag that does not match `APP_VERSION`, zips the tracked
files with `git archive` (so nothing git-ignored can leak) and attaches
`IMTOP-vX.Y.Z.zip` to a GitHub Release with generated notes.

## Reproducing the paper analysis

`tools/batch_run.py` re-runs the segmentation back-ends over an image/mask
dataset with the **same core code as the app** and writes a tidy metrics CSV
in the published `results_all.csv` schema (the metric key names never change;
units are reported separately):

```bash
.venv\Scripts\python tools/batch_run.py --images <images_dir> --gt <masks_dir> --out results_all.csv
```

`tools/montecarlo_distortion.py` consumes `Graphs/results_clean.csv` to model
the geometric distortion of the fabricated patch, writing
`montecarlo_summary.csv`, `montecarlo_samples.csv`, `findings.md` and four
figures into the project root, from any working directory:

```bash
.venv\Scripts\python -m pip install -r tools/requirements-analysis.txt
.venv\Scripts\python tools/montecarlo_distortion.py             # full run
.venv\Scripts\python tools/montecarlo_distortion.py --selftest  # sanity checks only
```

The `Graphs/` R scripts render the paper figures.

## License

Released under the [MIT License](LICENSE).

## Citation

If you use this software, please cite it using [`CITATION.cff`](CITATION.cff).
