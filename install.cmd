@echo off
setlocal
rem ---------------------------------------------------------------------------
rem  IMTOP installer. Double-click this file; everything else happens in a
rem  window. This only hands over to installer\install.ps1, a WPF window
rem  written in PowerShell (every Windows 10/11 has it), passing any arguments
rem  along (-Cpu, -Cuda, -NoLaunch, -SmokeTest, ...).
rem
rem  The console you may see for a moment is this file starting PowerShell;
rem  it closes by itself. The installer writes its log to
rem  %LOCALAPPDATA%\IMTOP\install.log.
rem ---------------------------------------------------------------------------
set "PS1=%~dp0installer\install.ps1"
if not exist "%PS1%" (
  echo Cannot find installer\install.ps1 next to this file.
  echo Extract the whole zip first, then run install.cmd from the extracted folder.
  echo.
  pause
  exit /b 1
)
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%PS1%" %*
exit /b 0
