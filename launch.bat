@echo off
cd /d "%~dp0"
REM Launch the wound annotator with a per-user Python 3.11 that has the SAM deps
REM (torch + segment_anything). Override the interpreter with the WOUND_PYTHON env
REM var, or set SAM_CHECKPOINT to point at a checkpoint stored outside this folder.
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" wound_app.py
) else (
    py -3.11 wound_app.py 2>nul || python wound_app.py
)
pause
