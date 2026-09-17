@echo off
setlocal
cd /d "%~dp0"
REM Start IMTOP. Prefers the environment created by the installer (.venv), then a
REM per-user Python 3.11, then whatever "py"/"python" resolves to.
REM   Pass an image path to open it at start:  launch.bat photo.jpg
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m imtop %*
    goto :eof
)
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if exist "%PYEXE%" (
    "%PYEXE%" -m imtop %*
) else (
    py -3.11 -m imtop %* 2>nul || python -m imtop %*
)
if errorlevel 1 pause
