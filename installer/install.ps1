<#
  install.ps1 - the IMTOP installer. Started by install.cmd (double-click).

  One WPF window, drawn like the app itself (the Review Desk look: dark fills,
  gold accent, no outlines, no dividers, the tapered title band), that does the
  whole job with no prerequisites on a stock Windows 10/11:

    1. python    finds a 64-bit Python 3.11 (registry, the usual folders, the
                 py launcher); if there is none it installs one for this user
                 with winget, or with the python.org installer if winget is not
                 there. Never 3.12+: PyQt6-WebEngine has no wheels for them.
    2. venv      creates .venv inside the app folder (replaces one made by
                 another Python, keeps one that already works)
    3. pip       upgrades pip inside the venv (not fatal if it fails)
    4. torch     installs PyTorch 2.6.0: the CPU wheel by default, the CUDA
                 12.4 wheel when an NVIDIA GPU is found and the box is ticked
    5. deps      pip install -r requirements.txt
    6. check     imports everything the app needs, from the venv
    7. shortcut  IMTOP.lnk on the Desktop and in the Start Menu, with the logo
                 and the app's AppUserModelID (so pinning works)

  Meanwhile the window shows the step, a gold progress bar, what pip is doing
  and a sarcastic line from quips.txt. Everything is logged to
  %LOCALAPPDATA%\IMTOP\install.log. Running it again on an installed copy is
  fine: every step short-circuits when its work is already done.

  Switches (install.cmd passes them through):
    -Cpu / -Cuda        force the PyTorch build (default: CUDA if NVIDIA GPU)
    -NoLaunch           no "Launch" button at the end
    -Plan               print what would be done and exit, no window
    -SmokeTest          open the window off screen, run a simulated install,
                        save screenshots (-SmokeShots <dir>) and exit
    -Unattended         no ready screen: install at once, close the window
                        when done (exit 0) or failed (exit 1); with
                        -SmokeShots the last screen is saved as a PNG
    -ShortcutDir <dir>  write both .lnk files there (tests), not Desktop/Start

  Pure ASCII on purpose, so it parses the same under any code page; the quips
  are in quips.txt (UTF-8). No em dash anywhere the user can see.
#>
[CmdletBinding()]
param(
    [switch]$Cpu,
    [switch]$Cuda,
    [switch]$NoLaunch,
    [switch]$Plan,
    [switch]$SmokeTest,
    [switch]$Unattended,
    [string]$SmokeShots = "",
    [string]$ShortcutDir = ""
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch { }

# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir
$DataDir   = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "IMTOP" } else { Join-Path $env:TEMP "IMTOP" }
$LogPath   = Join-Path $DataDir "install.log"
$QuipsPath = Join-Path $ScriptDir "quips.txt"
$IconPath  = Join-Path $Root "imtop\ui\assets\logo.ico"
$LogoPath  = Join-Path $Root "imtop\ui\assets\logo.png"
$VenvDir   = Join-Path $Root ".venv"
$VenvPy    = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyw   = Join-Path $VenvDir "Scripts\pythonw.exe"
$ReqFile   = Join-Path $Root "requirements.txt"

$PyMajorMinor  = "3.11"
$PyFullVersion = "3.11.9"
$PyExeUrl      = "https://www.python.org/ftp/python/$PyFullVersion/python-$PyFullVersion-amd64.exe"
$PyExeBytes    = 26216840
$WingetId      = "Python.Python.3.11"
$TorchVersion  = "2.6.0"
$TorchSpec     = @("torch==2.6.0", "torchvision==0.21.0")
$TorchIndex    = @{ cpu = "https://download.pytorch.org/whl/cpu"; cu124 = "https://download.pytorch.org/whl/cu124" }
$SizeCpu       = "about 600 MB"
$SizeCuda      = "about 3 GB"

# The taskbar identity lives in imtop/config.py; read it from there so the two
# can never drift apart.
$Aumid = "FedericoSalerno.IMTOP"
try {
    $m = Select-String -Path (Join-Path $Root "imtop\config.py") -Pattern '^APP_USER_MODEL_ID = "([^"]+)"' | Select-Object -First 1
    if ($m) { $Aumid = $m.Matches[0].Groups[1].Value }
} catch { }

New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("`r`n==== IMTOP installer  {0}  root={1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Root)

if ($Cpu -and $Cuda) { throw "-Cpu and -Cuda exclude each other." }

# ---------------------------------------------------------------------------
# Functions shared by the window (this thread) and the worker (its own
# runspace). Kept as text so the same definitions can be handed to both.
# ---------------------------------------------------------------------------
$Shared = @'
function Get-PythonInfo([string]$exe) {
    # "3.11 64 1" = major.minor, bits, venv module present. $null if not usable.
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    try {
        $probe = "import sys,struct,importlib.util as u;print('%d.%d %d %d' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P')*8, int(u.find_spec('venv') is not None)))"
        $out = & $exe -c $probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $parts = "$out".Trim().Split(' ')
        return @{ Exe = $exe; Version = $parts[0]; Bits = [int]$parts[1]; Venv = ($parts[2] -eq '1') }
    } catch { return $null }
}

function Find-Python311([string]$want = '3.11') {
    # Candidates, best first. Only a 64-bit interpreter with the venv module counts.
    $cands = New-Object System.Collections.ArrayList
    foreach ($hive in 'HKCU', 'HKLM') {
        foreach ($key in "$($hive):\Software\Python\PythonCore\$want\InstallPath",
                         "$($hive):\Software\Python\PythonCore\$want-64\InstallPath") {
            try {
                $k = Get-ItemProperty -Path $key -ErrorAction Stop
                if ($k.ExecutablePath) { [void]$cands.Add($k.ExecutablePath) }
                elseif ($k.'(default)') { [void]$cands.Add((Join-Path $k.'(default)' 'python.exe')) }
            } catch { }
        }
    }
    $tag = $want.Replace('.', '')
    if ($env:LOCALAPPDATA) { [void]$cands.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python$tag\python.exe")) }
    if ($env:ProgramFiles) { [void]$cands.Add((Join-Path $env:ProgramFiles "Python$tag\python.exe")) }
    [void]$cands.Add("C:\Python$tag\python.exe")
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $exe = & py "-$want" -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { [void]$cands.Add("$exe".Trim()) }
        }
    } catch { }
    $seen = @{}
    foreach ($c in $cands) {
        if (-not $c) { continue }
        $key = $c.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $info = Get-PythonInfo $c
        if ($info -and $info.Version -eq $want -and $info.Bits -eq 64 -and $info.Venv) { return $info }
    }
    return $null
}

function Get-NvidiaGpu {
    # The name of the first NVIDIA adapter, or ''.
    try {
        $gpus = Get-CimInstance Win32_VideoController -ErrorAction Stop | Where-Object { $_.Name -match 'NVIDIA' }
        if ($gpus) { return ($gpus | Select-Object -First 1).Name }
    } catch { }
    try { if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { return 'NVIDIA GPU' } } catch { }
    return ''
}

function Get-VenvPython([string]$venvPy) {
    # The venv's Python, if it exists and still runs (its base may have been removed).
    if (-not (Test-Path -LiteralPath $venvPy)) { return $null }
    return Get-PythonInfo $venvPy
}

function Get-TorchBuild([string]$py) {
    # "2.6.0+cpu", "2.6.0+cu124", ... or '' when torch does not import.
    if (-not (Test-Path -LiteralPath $py)) { return '' }
    try {
        $out = & $py -c "import torch;print(torch.__version__)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return "$out".Trim() }
    } catch { }
    return ''
}

function Get-ShortcutDirs([string]$override) {
    if ($override) { return @($override) }
    return @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
}

$script:ShortcutCs = @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
namespace Imtop {
  [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
  class ShellLink { }
  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
  interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
    void Resolve(IntPtr hwnd, uint fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
  }
  [StructLayout(LayoutKind.Sequential, Pack = 4)]
  struct PropertyKey { public Guid fmtid; public uint pid; public PropertyKey(Guid f, uint p) { fmtid = f; pid = p; } }
  [StructLayout(LayoutKind.Explicit, Size = 24)]
  struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr ptr; }
  [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
  interface IPropertyStore {
    void GetCount(out uint cProps);
    void GetAt(uint iProp, out PropertyKey pkey);
    void GetValue(ref PropertyKey key, out PropVariant pv);
    void SetValue(ref PropertyKey key, ref PropVariant pv);
    void Commit();
  }
  public static class Shortcut {
    public static void Create(string lnk, string target, string args, string workDir, string icon, string description, string aumid) {
      IShellLinkW link = (IShellLinkW)new ShellLink();
      link.SetPath(target);
      link.SetArguments(args);
      link.SetWorkingDirectory(workDir);
      link.SetDescription(description);
      if (!string.IsNullOrEmpty(icon)) link.SetIconLocation(icon, 0);
      link.SetShowCmd(1);
      if (!string.IsNullOrEmpty(aumid)) {
        IPropertyStore store = (IPropertyStore)link;
        PropertyKey key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
        PropVariant pv = new PropVariant();
        pv.vt = 31;
        pv.ptr = Marshal.StringToCoTaskMemUni(aumid);
        try { store.SetValue(ref key, ref pv); store.Commit(); }
        finally { Marshal.FreeCoTaskMem(pv.ptr); }
      }
      ((IPersistFile)link).Save(lnk, true);
    }
  }
}
"@

function New-ImtopShortcut([string]$lnk, [string]$target, [string]$arguments, [string]$workDir, [string]$icon, [string]$aumid) {
    # Returns 'aumid' when the shortcut carries the AppUserModelID, 'plain' when
    # it had to fall back to WScript.Shell (no id: pinning gives a bare pythonw).
    $dir = Split-Path -Parent $lnk
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    try {
        if (-not ('Imtop.Shortcut' -as [type])) { Add-Type -TypeDefinition $script:ShortcutCs -ErrorAction Stop }
        [Imtop.Shortcut]::Create($lnk, $target, $arguments, $workDir, $icon, 'IMTOP wound annotator', $aumid)
        return 'aumid'
    } catch {
        $sh = New-Object -ComObject WScript.Shell
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = $target
        $s.Arguments = $arguments
        $s.WorkingDirectory = $workDir
        $s.Description = 'IMTOP wound annotator'
        if ($icon) { $s.IconLocation = "$icon,0" }
        $s.Save()
        return 'plain'
    }
}
'@

. ([scriptblock]::Create($Shared))

# ---------------------------------------------------------------------------
# What this machine has
# ---------------------------------------------------------------------------
$Gpu = Get-NvidiaGpu
$DefaultBuild = 'cpu'
if ($Cuda) { $DefaultBuild = 'cu124' } elseif (-not $Cpu -and $Gpu) { $DefaultBuild = 'cu124' }

if ($Plan) {
    $py = Find-Python311 $PyMajorMinor
    $venv = Get-VenvPython $VenvPy
    $how = if (Get-Command winget -ErrorAction SilentlyContinue) { 'winget' } else { 'python.org' }
    Write-Output ("root=" + $Root)
    if ($py) { Write-Output ("python=" + $py.Exe + " (" + $py.Version + ", " + $py.Bits + "-bit)") }
    else { Write-Output ("python=(none, would install " + $PyFullVersion + " via " + $how + ")") }
    Write-Output ("gpu=" + $(if ($Gpu) { $Gpu } else { '(none)' }))
    Write-Output ("torch=" + $DefaultBuild)
    if ($venv) { Write-Output ("venv=" + $VenvDir + " (exists, Python " + $venv.Version + ", torch " + $(Get-TorchBuild $VenvPy) + ")") }
    else { Write-Output ("venv=" + $VenvDir + " (missing)") }
    Write-Output ("shortcuts=" + ((Get-ShortcutDirs $ShortcutDir) -join '; '))
    Write-Output ("aumid=" + $Aumid)
    Write-Output ("log=" + $LogPath)
    exit 0
}

# ---------------------------------------------------------------------------
# The worker: runs the seven steps in its own runspace and reports through a
# synchronized hashtable. Text, like $Shared, because it is handed over as a
# script. Native commands run with stderr merged, every line logged.
# ---------------------------------------------------------------------------
$WorkerBody = @'
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PIP_NO_INPUT = '1'

function Log([string]$line) {
    try { Add-Content -LiteralPath $Cfg.LogPath -Encoding UTF8 -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $line) } catch { }
}
function Say([string]$detail) { $S.Detail = $detail; Log $detail }
function Step([string]$key, [int]$index, [string]$title) {
    if ($S.Cancel) { throw "Cancelled." }
    $S.Step = $key; $S.StepIndex = $index; $S.Title = $title; $S.Frac = 0.0; $S.Detail = ''
    Log ("== " + $title)
}
function Run([string]$exe, [string[]]$argv, [scriptblock]$onLine = $null) {
    # A native command with stdout and stderr merged. Every line goes to the log
    # and to $onLine. Returns the exit code. $ErrorActionPreference is Continue
    # here on purpose: under Stop, the first stderr line would be fatal.
    Log ("> " + $exe + " " + ($argv -join ' '))
    $global:LASTEXITCODE = 0
    & $exe @argv 2>&1 | ForEach-Object {
        $line = "$_".TrimEnd()
        if ($line) {
            Log $line
            if ($onLine) { try { & $onLine $line } catch { } }
        }
    }
    return $LASTEXITCODE
}
function Download([string]$url, [string]$path, [long]$expected) {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    $wc = New-Object System.Net.WebClient
    $wc.Headers['User-Agent'] = 'IMTOP-installer'
    $task = $wc.DownloadFileTaskAsync($url, $path)
    while (-not $task.IsCompleted) {
        Start-Sleep -Milliseconds 250
        try {
            if ($expected -gt 0 -and (Test-Path -LiteralPath $path)) {
                $S.Frac = [math]::Min(0.95, (Get-Item -LiteralPath $path).Length / [double]$expected)
            }
        } catch { }
        if ($S.Cancel) { $wc.CancelAsync(); throw "Cancelled." }
    }
    if ($task.IsFaulted) { throw $task.Exception.InnerException }
}

# pip's lines, turned into a detail line and a rough fraction. "Progress N of M"
# is what pip >= 25.1 prints with --progress-bar raw; older pips get 'off'.
$script:collected = 0
$PipLine = {
    param([string]$l)
    if ($l -match '^Progress (\d+) of (\d+)') {
        $n = [double]$Matches[1]; $m = [double]$Matches[2]
        if ($m -gt 0) { $S.Frac = [math]::Max($S.Frac, 0.05 + 0.80 * ($n / $m)) }
        $S.Detail = ("Downloading {0}, {1:N0} of {2:N0} MB" -f $script:current, ($n / 1MB), ($m / 1MB))
    } elseif ($l -match '^\s*Downloading (\S+?)-[\d.].*\((\d+(?:\.\d+)?) (kB|MB|GB)\)') {
        $script:current = $Matches[1]
        $S.Detail = ("Downloading {0} ({1} {2})" -f $Matches[1], $Matches[2], $Matches[3])
    } elseif ($l -match '^\s*Collecting (\S+)') {
        $script:collected++
        $S.Detail = "Collecting " + $Matches[1]
        if ($S.Step -eq 'deps') { $S.Frac = [math]::Min(0.75, $script:collected / 28.0) }
    } elseif ($l -match '^\s*Requirement already satisfied: (\S+)') {
        $S.Detail = "Already there: " + $Matches[1]
    } elseif ($l -match '^Installing collected packages') {
        $S.Detail = "Installing the downloaded packages"
        $S.Frac = [math]::Max($S.Frac, 0.88)
    } elseif ($l -match '^Successfully installed') {
        $S.Detail = "Installed"
        $S.Frac = 1.0
    } elseif ($l -match '^ERROR: (.*)') {
        $S.Detail = $Matches[1]
    }
}

try {
    if ($Cfg.Simulate) {
        # -SmokeTest: the same seven steps, with sleeps instead of work.
        $plan = @(
            @('python', 'Python 3.11', @('Looking for Python 3.11', 'Using C:\Users\you\AppData\Local\Programs\Python\Python311\python.exe')),
            @('venv', 'Private environment', @('Creating .venv')),
            @('pip', 'pip', @('Upgrading pip')),
            @('torch', 'PyTorch', @('Installing PyTorch 2.6.0 (cpu build)', 'Downloading torch (204.2 MB)', 'Downloading torch, 96 of 204 MB', 'Installing the downloaded packages')),
            @('deps', 'Packages', @('Collecting PyQt6', 'Collecting opencv-python', 'Downloading PyQt6_WebEngine_Qt6 (72.1 MB)', 'Installing the downloaded packages')),
            @('check', 'Check', @('torch 2.6.0+cpu cuda False')),
            @('shortcut', 'Shortcuts', @('Shortcut: C:\Users\you\Desktop\IMTOP.lnk', 'Shortcut: C:\Users\you\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\IMTOP.lnk'))
        )
        for ($i = 0; $i -lt $plan.Count; $i++) {
            Step $plan[$i][0] $i $plan[$i][1]
            $lines = $plan[$i][2]
            for ($j = 0; $j -lt $lines.Count; $j++) {
                Say $lines[$j]
                $S.Frac = ($j + 1) / [double]($lines.Count + 1)
                Start-Sleep -Milliseconds 220
            }
            $S.Frac = 1.0
        }
        $S.Done = $true
        return
    }

    # -- 1. python --------------------------------------------------------
    Step 'python' 0 'Python 3.11'
    $py = Find-Python311 $Cfg.PyMajorMinor
    if (-not $py) {
        Say "Python 3.11 is not on this PC. Installing it for this user."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Say "Installing Python 3.11 with winget"
            $S.Frac = 0.1
            $code = Run 'winget' @('install', '--id', $Cfg.WingetId, '--exact', '--scope', 'user', '--silent',
                                   '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
            Log ("winget exit " + $code)
            $py = Find-Python311 $Cfg.PyMajorMinor
        }
        if (-not $py) {
            Say ("Downloading Python " + $Cfg.PyFullVersion + " from python.org (25 MB)")
            $exe = Join-Path $env:TEMP ("python-" + $Cfg.PyFullVersion + "-amd64.exe")
            Download $Cfg.PyExeUrl $exe $Cfg.PyExeBytes
            Say "Installing Python quietly (this user only, no admin rights)"
            $p = Start-Process -FilePath $exe -PassThru -Wait -ArgumentList @('/quiet', 'InstallAllUsers=0', 'PrependPath=0',
                    'Include_launcher=1', 'InstallLauncherAllUsers=0', 'Include_test=0', 'Include_doc=0', 'Include_tcltk=0', 'Shortcuts=0')
            Log ("python.org installer exit " + $p.ExitCode)
            Remove-Item -LiteralPath $exe -Force -ErrorAction SilentlyContinue
            $py = Find-Python311 $Cfg.PyMajorMinor
        }
        if (-not $py) {
            throw ("Python 3.11 could not be installed automatically. Install the 64-bit Python " + $Cfg.PyFullVersion +
                   " from python.org (https://www.python.org/downloads/release/python-3119/), then run install.cmd again.")
        }
    }
    Say ("Using " + $py.Exe)
    $S.Frac = 1.0

    # -- 2. venv ----------------------------------------------------------
    Step 'venv' 1 'Private environment'
    $venv = Get-VenvPython $Cfg.VenvPy
    if ($venv -and $venv.Version -eq $Cfg.PyMajorMinor -and $venv.Bits -eq 64) {
        Say ".venv already exists and runs Python 3.11, keeping it"
    } else {
        if (Test-Path -LiteralPath $Cfg.VenvDir) {
            Say "Replacing the old .venv (wrong Python, or broken)"
            Remove-Item -LiteralPath $Cfg.VenvDir -Recurse -Force
        }
        Say "Creating .venv"
        $S.Frac = 0.3
        $code = Run $py.Exe @('-m', 'venv', $Cfg.VenvDir)
        if ($code -ne 0 -or -not (Test-Path -LiteralPath $Cfg.VenvPy)) {
            throw ("Could not create the virtual environment (python -m venv failed, exit " + $code + "). See the log.")
        }
    }
    $S.Frac = 1.0

    # -- 3. pip -----------------------------------------------------------
    Step 'pip' 2 'pip'
    Say "Upgrading pip"
    $S.Frac = 0.2
    $code = Run $Cfg.VenvPy @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet') $PipLine
    if ($code -ne 0) { Say "pip could not upgrade itself; carrying on with the one in the venv" }
    $pipVer = [version]'0.0'
    $out = & $Cfg.VenvPy -m pip --version 2>$null
    if ("$out" -match 'pip (\d+)\.(\d+)') { $pipVer = [version]("$($Matches[1]).$($Matches[2])") }
    $bar = if ($pipVer -ge [version]'25.1') { 'raw' } else { 'off' }
    Log ("pip " + $pipVer + ", progress-bar " + $bar)
    $S.Frac = 1.0

    # -- 4. torch ---------------------------------------------------------
    Step 'torch' 3 'PyTorch'
    $want = $Cfg.TorchBuild
    $have = Get-TorchBuild $Cfg.VenvPy
    if ($have -eq ($Cfg.TorchVersion + '+' + $want)) {
        Say ("PyTorch " + $have + " is already installed")
    } else {
        if ($have) {
            Say ("Removing PyTorch " + $have + " to install the " + $want + " build")
            [void](Run $Cfg.VenvPy @('-m', 'pip', 'uninstall', '-y', 'torch', 'torchvision', '--quiet'))
        }
        Say ("Installing PyTorch " + $Cfg.TorchVersion + " (" + $want + " build)")
        $script:collected = 0
        $argv = @('-m', 'pip', 'install') + $Cfg.TorchSpec + @('--index-url', $Cfg.TorchIndex[$want], '--prefer-binary', '--progress-bar', $bar)
        $code = Run $Cfg.VenvPy $argv $PipLine
        if ($code -ne 0) { throw ("pip could not install PyTorch (exit " + $code + "). Check the internet connection, then see the log.") }
    }
    $S.Frac = 1.0

    # -- 5. deps ----------------------------------------------------------
    Step 'deps' 4 'Packages'
    Say "Installing the packages in requirements.txt"
    $script:collected = 0
    $code = Run $Cfg.VenvPy @('-m', 'pip', 'install', '-r', $Cfg.ReqFile, '--prefer-binary', '--progress-bar', $bar) $PipLine
    if ($code -ne 0) { throw ("pip could not install the packages (exit " + $code + "). See the log.") }
    $S.Frac = 1.0

    # -- 6. check ---------------------------------------------------------
    Step 'check' 5 'Check'
    Say "Importing everything the app needs"
    $S.Frac = 0.3
    $probe = "import PyQt6.QtWebEngineWidgets, numpy, scipy, cv2, PIL, torch, segment_anything, imtop.core; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
    $code = Run $Cfg.VenvPy @('-c', $probe) { param($l) Say $l }
    if ($code -ne 0) { throw "The environment was built, but the app's modules do not import from it. See the log." }
    $S.Frac = 1.0

    # -- 7. shortcuts -----------------------------------------------------
    Step 'shortcut' 6 'Shortcuts'
    $dirs = Get-ShortcutDirs $Cfg.ShortcutDir
    $n = 0
    foreach ($dir in $dirs) {
        $lnk = Join-Path $dir 'IMTOP.lnk'
        $how = New-ImtopShortcut $lnk $Cfg.VenvPyw '-m imtop' $Cfg.Root $Cfg.IconPath $Cfg.Aumid
        Say ("Shortcut: " + $lnk + $(if ($how -eq 'plain') { ' (without the taskbar id)' } else { '' }))
        $n++
        $S.Frac = $n / [double]$dirs.Count
    }
    $S.Done = $true
} catch {
    $S.Error = $_.Exception.Message
    Log ("ERROR: " + $_.Exception.Message)
    if ($_.ScriptStackTrace) { Log $_.ScriptStackTrace }
}
'@

# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Drawing

try {
    Add-Type -Namespace Imtop -Name Native -MemberDefinition @'
[DllImport("shell32.dll", CharSet = CharSet.Unicode)]
public static extern int SetCurrentProcessExplicitAppUserModelID(string AppID);
[DllImport("dwmapi.dll")]
public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
[DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
public static extern IntPtr GetWindowLongPtr(IntPtr hWnd, int nIndex);
[DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")]
public static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong);
[DllImport("user32.dll")]
public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
[DllImport("user32.dll")]
public static extern bool GetWindowRect(IntPtr hWnd, out RECT rc);
[DllImport("user32.dll")]
public static extern bool GetClientRect(IntPtr hWnd, out RECT rc);
[DllImport("comctl32.dll")]
public static extern bool SetWindowSubclass(IntPtr hWnd, SubclassProc proc, IntPtr id, IntPtr data);
[DllImport("comctl32.dll")]
public static extern IntPtr DefSubclassProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
public delegate IntPtr SubclassProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam, IntPtr id, IntPtr data);
[StructLayout(LayoutKind.Sequential)]
public struct RECT { public int Left, Top, Right, Bottom; }
static SubclassProc keep;
// A bare WS_POPUP window is not animated by Windows when it minimises or
// closes. Give it the frame styles, and answer WM_NCCALCSIZE with "the client
// is the whole window" so no caption and no border are ever laid out.
public static void Frame(IntPtr h) {
    keep = new SubclassProc(Proc);
    SetWindowSubclass(h, keep, (IntPtr)1, IntPtr.Zero);
    long style = GetWindowLongPtr(h, -16).ToInt64();
    style |= 0x00C00000L | 0x00040000L | 0x00020000L | 0x00080000L;   // caption, thickframe, minimizebox, sysmenu
    SetWindowLongPtr(h, -16, (IntPtr)style);
    SetWindowPos(h, IntPtr.Zero, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010);
}
static IntPtr Proc(IntPtr h, uint msg, IntPtr w, IntPtr l, IntPtr id, IntPtr data) {
    if (msg == 0x0083 && w != IntPtr.Zero) return IntPtr.Zero;              // WM_NCCALCSIZE
    if (msg == 0x0086) return DefSubclassProc(h, msg, w, (IntPtr)(-1));     // WM_NCACTIVATE: no caption repaint
    return DefSubclassProc(h, msg, w, l);
}
public static string Describe(IntPtr h) {
    RECT wr, cr;
    GetWindowRect(h, out wr); GetClientRect(h, out cr);
    return string.Format("style=0x{0:X8} window={1}x{2} client={3}x{4}",
        GetWindowLongPtr(h, -16).ToInt64() & 0xFFFFFFFFL, wr.Right - wr.Left, wr.Bottom - wr.Top, cr.Right - cr.Left, cr.Bottom - cr.Top);
}
'@
    [void][Imtop.Native]::SetCurrentProcessExplicitAppUserModelID($Aumid + ".Installer")
} catch { }

# Palette: Get-Palette from the Review Desk, the same values as imtop/ui/css/tokens.css.
$P = @{
    Bg = '#0D0D0F'; Panel = '#101013'; Panel2 = '#121216'; Panel3 = '#16161B'; Control = '#1E1E24'
    Track = '#1A1A20'; Hover = '#33333B'; Text = '#E6E6EC'; TextSoft = '#C8C8D0'; TextDim = '#8A8A94'
    TextFaint = '#4E4E57'; Muted = '#6E6E78'; Accent = '#F5C542'; AccentHi = '#FFD45E'; AccentDim = '#C9A033'
    Ink = '#0D0D0F'; Card = '#16161B'; CardSel = '#2A2519'; CardErr = '#2C1F1E'; TextErr = '#E8705B'; Err = '#B0524A'
    Band = '#1E1E24'
}

$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="IMTOP Installer" Width="640" Height="450"
        WindowStyle="None" ResizeMode="NoResize" AllowsTransparency="False"
        Background="#0D0D0F" Foreground="#E6E6EC" FontFamily="Segoe UI" FontSize="12.5"
        WindowStartupLocation="CenterScreen" UseLayoutRounding="True" SnapsToDevicePixels="True"
        TextOptions.TextFormattingMode="Display" TextOptions.TextRenderingMode="ClearType">
  <Window.Resources>
    <Style TargetType="TextBlock">
      <Setter Property="FontFamily" Value="Segoe UI"/>
      <Setter Property="TextWrapping" Value="Wrap"/>
    </Style>

    <!-- Buttons are fills, never outlines: rest, hover, pressed are three fills.
         Keyboard focus is an inset ring. -->
    <Style x:Key="Gold" TargetType="Button">
      <Setter Property="Foreground" Value="#0D0D0F"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Height" Value="32"/>
      <Setter Property="Padding" Value="18,0,18,0"/>
      <Setter Property="Margin" Value="8,0,0,0"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="#F5C542" CornerRadius="5" Padding="{TemplateBinding Padding}">
              <Border x:Name="f" BorderBrush="Transparent" BorderThickness="1.5" CornerRadius="3" Margin="-14,3,-14,3">
                <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
              </Border>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#FFD45E"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="b" Property="Background" Value="#C9A033"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="f" Property="BorderBrush" Value="#0D0D0F"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <Style x:Key="Muted" TargetType="Button">
      <Setter Property="Foreground" Value="#C8C8D0"/>
      <Setter Property="FontSize" Value="12.5"/>
      <Setter Property="FontWeight" Value="Normal"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="Height" Value="32"/>
      <Setter Property="Padding" Value="16,0,16,0"/>
      <Setter Property="Margin" Value="8,0,0,0"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="#1E1E24" CornerRadius="5" Padding="{TemplateBinding Padding}">
              <Border x:Name="f" BorderBrush="Transparent" BorderThickness="1.5" CornerRadius="3" Margin="-12,3,-12,3">
                <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
              </Border>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#33333B"/>
                <Setter Property="Foreground" Value="#E6E6EC"/>
              </Trigger>
              <Trigger Property="IsPressed" Value="True">
                <Setter TargetName="b" Property="Background" Value="#1E1E24"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="f" Property="BorderBrush" Value="#F5C542"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- The window buttons in the thin part of the band. -->
    <Style x:Key="WinBtn" TargetType="Button">
      <Setter Property="Width" Value="42"/>
      <Setter Property="Height" Value="28"/>
      <Setter Property="Foreground" Value="#8A8A94"/>
      <Setter Property="Cursor" Value="Arrow"/>
      <Setter Property="Focusable" Value="False"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="Transparent">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#33333B"/>
                <Setter Property="Foreground" Value="#E6E6EC"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="WinClose" TargetType="Button" BasedOn="{StaticResource WinBtn}">
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="Transparent">
              <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#B0524A"/>
                <Setter Property="Foreground" Value="#FFFFFF"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>

    <!-- A tick box that is a fill: grey at rest, gold with an ink tick when on. -->
    <Style x:Key="Tick" TargetType="CheckBox">
      <Setter Property="Foreground" Value="#C8C8D0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="CheckBox">
            <Border x:Name="row" Background="Transparent" CornerRadius="5" Padding="6,5,10,5" Margin="-6,0,0,0">
              <StackPanel Orientation="Horizontal">
                <Border x:Name="box" Width="17" Height="17" CornerRadius="4" Background="#1E1E24" VerticalAlignment="Center">
                  <Path x:Name="tick" Data="M3.5,8.5 L7,12 L13.5,5" Stroke="#0D0D0F" StrokeThickness="2"
                        StrokeStartLineCap="Round" StrokeEndLineCap="Round" StrokeLineJoin="Round" Visibility="Collapsed"/>
                </Border>
                <ContentPresenter Margin="10,0,0,0" VerticalAlignment="Center"/>
              </StackPanel>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsChecked" Value="True">
                <Setter TargetName="box" Property="Background" Value="#F5C542"/>
                <Setter TargetName="tick" Property="Visibility" Value="Visible"/>
              </Trigger>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="row" Property="Background" Value="#16161B"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="row" Property="Background" Value="#16161B"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Border x:Name="Root" Background="#0D0D0F">
    <Grid>
      <Grid.RowDefinitions>
        <RowDefinition Height="44"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>

      <!-- Title bar: one band the whole width, thick under the name, a straight
           taper, thin under the buttons. The shape is a Path filled at run time
           from the real width of the name (Set-BandShape). -->
      <Grid x:Name="TitleBar" Grid.Row="0" Background="#101013">
        <Path x:Name="Band" Fill="#1E1E24" IsHitTestVisible="False"/>
        <StackPanel x:Name="Brand" Orientation="Horizontal" HorizontalAlignment="Left" VerticalAlignment="Stretch" Margin="0">
          <Border x:Name="Logo" Width="22" Height="22" CornerRadius="5" Margin="14,0,11,0" VerticalAlignment="Center" Background="#F5C542"/>
          <TextBlock VerticalAlignment="Center" FontFamily="Abadi, Segoe UI Variable Display, Segoe UI" FontSize="14.5" Foreground="#C8C8D0" Margin="0,0,16,0">
            <Run Text="IMTOP" FontWeight="SemiBold" Foreground="#E6E6EC"/><Run Text="  Installer"/>
          </TextBlock>
        </StackPanel>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Top">
          <Button x:Name="BtnMin" Style="{StaticResource WinBtn}" ToolTip="Minimise">
            <Path Data="M2,6 L10,6" Stroke="{Binding Foreground, RelativeSource={RelativeSource AncestorType=Button}}" StrokeThickness="1.35" StrokeStartLineCap="Round" StrokeEndLineCap="Round" Width="12" Height="12"/>
          </Button>
          <Button x:Name="BtnClose" Style="{StaticResource WinClose}" ToolTip="Close">
            <Path Data="M3,3 L9,9 M9,3 L3,9" Stroke="{Binding Foreground, RelativeSource={RelativeSource AncestorType=Button}}" StrokeThickness="1.35" StrokeStartLineCap="Round" StrokeEndLineCap="Round" Width="12" Height="12"/>
          </Button>
        </StackPanel>
      </Grid>

      <!-- Content -->
      <Grid Grid.Row="1" Margin="24,20,24,16">

        <!-- Ready -->
        <StackPanel x:Name="ViewReady">
          <TextBlock Text="Install IMTOP" FontSize="22" FontWeight="Light" Foreground="#E6E6EC" Margin="0,0,0,10"/>
          <TextBlock Foreground="#8A8A94" FontSize="11.5" Text="INTO THIS FOLDER" Margin="0,0,0,4"/>
          <Border Background="#16161B" CornerRadius="6" Padding="10,7,10,7" Margin="0,0,0,14" HorizontalAlignment="Left">
            <TextBlock x:Name="ReadyPath" FontFamily="Consolas" FontSize="12" Foreground="#C8C8D0" TextWrapping="NoWrap" TextTrimming="CharacterEllipsis"/>
          </Border>
          <StackPanel x:Name="ReadyList" Margin="0,0,0,12"/>
          <CheckBox x:Name="ChkCuda" Style="{StaticResource Tick}" Visibility="Collapsed" Margin="0,0,0,8">
            <TextBlock x:Name="ChkCudaText" Foreground="#C8C8D0"/>
          </CheckBox>
          <TextBlock x:Name="ReadyNote" Foreground="#8A8A94" FontSize="11.5" Visibility="Collapsed"/>
        </StackPanel>

        <!-- Working -->
        <Grid x:Name="ViewWork" Visibility="Collapsed">
          <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
          </Grid.RowDefinitions>
          <StackPanel x:Name="Steps" Orientation="Horizontal" Grid.Row="0" Margin="0,0,0,26"/>
          <StackPanel Grid.Row="1" VerticalAlignment="Top">
            <TextBlock x:Name="WorkTitle" Foreground="#8A8A94" FontWeight="Medium" Text="Python 3.11" Margin="0,0,0,6"/>
            <StackPanel Orientation="Horizontal" Margin="0,0,0,14">
              <TextBlock x:Name="WorkPct" Text="0" FontSize="52" FontWeight="Light" Foreground="#E6E6EC" LineHeight="52" LineStackingStrategy="BlockLineHeight"/>
              <TextBlock Text="%" FontSize="20" FontWeight="Light" Foreground="#8A8A94" Margin="3,7,0,0" VerticalAlignment="Top"/>
            </StackPanel>
            <Border x:Name="BarTrack" Height="4" CornerRadius="99" Background="#1A1A20" Margin="0,0,0,12">
              <Border x:Name="BarFill" HorizontalAlignment="Left" Width="0" CornerRadius="99">
                <Border.Background>
                  <LinearGradientBrush StartPoint="0,0" EndPoint="1,0">
                    <GradientStop Color="#C9A033" Offset="0"/>
                    <GradientStop Color="#F5C542" Offset="1"/>
                  </LinearGradientBrush>
                </Border.Background>
              </Border>
            </Border>
            <TextBlock x:Name="WorkDetail" Foreground="#8A8A94" FontSize="11.5" TextWrapping="NoWrap" TextTrimming="CharacterEllipsis" MinHeight="16" Margin="0,0,0,18"/>
            <TextBlock x:Name="WorkQuip" Foreground="#C8C8D0" MinHeight="18"/>
          </StackPanel>
        </Grid>

        <!-- Done -->
        <StackPanel x:Name="ViewDone" Visibility="Collapsed">
          <TextBlock Text="Installed" FontSize="22" FontWeight="Light" Foreground="#E6E6EC" Margin="0,0,0,10"/>
          <TextBlock x:Name="DoneText" Foreground="#C8C8D0" Margin="0,0,0,14"/>
          <StackPanel x:Name="DoneList" Margin="0,0,0,14"/>
          <TextBlock x:Name="DoneQuip" Foreground="#8A8A94" FontSize="11.5"/>
        </StackPanel>

        <!-- Error -->
        <StackPanel x:Name="ViewError" Visibility="Collapsed">
          <TextBlock Text="That did not work" FontSize="22" FontWeight="Light" Foreground="#E6E6EC" Margin="0,0,0,10"/>
          <Border Background="#2C1F1E" CornerRadius="6" Padding="14,11,14,11" Margin="0,0,0,12">
            <TextBlock x:Name="ErrorText" Foreground="#E8705B"/>
          </Border>
          <TextBlock x:Name="ErrorHint" Foreground="#8A8A94" FontSize="11.5"/>
        </StackPanel>
      </Grid>

      <!-- Footer: a filled band, the actions on the right -->
      <Grid Grid.Row="2" Background="#101013" Height="58">
        <TextBlock x:Name="FootLeft" Foreground="#6E6E78" FontSize="11.5" FontFamily="Consolas" VerticalAlignment="Center" Margin="24,0,0,0"/>
        <StackPanel x:Name="FootBtns" Orientation="Horizontal" HorizontalAlignment="Right" VerticalAlignment="Center" Margin="0,0,24,0"/>
      </Grid>
    </Grid>
  </Border>
</Window>
'@

$w = [System.Windows.Markup.XamlReader]::Load((New-Object System.Xml.XmlNodeReader ([xml]$xaml)))
$el = @{}
foreach ($name in 'Root', 'TitleBar', 'Band', 'Brand', 'Logo', 'BtnMin', 'BtnClose', 'ViewReady', 'ReadyPath', 'ReadyList', 'ChkCuda', 'ChkCudaText', 'ReadyNote',
                  'ViewWork', 'Steps', 'WorkTitle', 'WorkPct', 'BarTrack', 'BarFill', 'WorkDetail', 'WorkQuip',
                  'ViewDone', 'DoneText', 'DoneList', 'DoneQuip', 'ViewError', 'ErrorText', 'ErrorHint', 'FootLeft', 'FootBtns') {
    $el[$name] = $w.FindName($name)
}

function New-Brush([string]$hex) { return (New-Object System.Windows.Media.BrushConverter).ConvertFromString($hex) }

# -- chrome -----------------------------------------------------------------
if (Test-Path -LiteralPath $IconPath) {
    try { $w.Icon = [System.Windows.Media.Imaging.BitmapFrame]::Create((New-Object Uri($IconPath)), 'None', 'OnLoad') } catch { }
}
if (Test-Path -LiteralPath $LogoPath) {
    try {
        $img = New-Object System.Windows.Media.ImageBrush
        $img.ImageSource = [System.Windows.Media.Imaging.BitmapFrame]::Create((New-Object Uri($LogoPath)), 'None', 'OnLoad')
        $img.Stretch = 'UniformToFill'
        $el.Logo.Background = $img
    } catch { }
}

$w.add_SourceInitialized({
    # The same dressing as the app's window (imtop/app.py): a real Win32 frame
    # behind the frameless window, so minimise and close animate; rounded
    # corners, which a frameless window has to ask for; and no DWM border,
    # because nothing in this family of windows draws an outline.
    try {
        $h = (New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle
        try { [Imtop.Native]::Frame($h) } catch { }
        $round = 2
        [void][Imtop.Native]::DwmSetWindowAttribute($h, 33, [ref]$round, 4)
        $dark = 1
        [void][Imtop.Native]::DwmSetWindowAttribute($h, 20, [ref]$dark, 4)
        $noBorder = -2
        [void][Imtop.Native]::DwmSetWindowAttribute($h, 34, [ref]$noBorder, 4)
    } catch { }
})

# The band: same profile and the same rounded joints as the app's title bar
# (imtop/ui/js/titlebar.js roundedPolyPath). 44 px under the name, a 38 px
# taper, 28 px to the right edge, both joints rounded by 8 px. The path
# string is built with the invariant culture: Geometry.Parse wants "x,y"
# with a dot decimal, whatever the machine's locale.
function Set-BandShape {
    $ci = [System.Globalization.CultureInfo]::InvariantCulture
    $W = $el.TitleBar.ActualWidth
    if ($W -le 0) { return }
    $h1 = 44.0; $h2 = 28.0; $slant = 38.0; $join = 8.0
    $x1 = [math]::Round($el.Brand.ActualWidth)
    $x2 = [math]::Min($W, $x1 + $slant)
    $pts = @(
        @{ x = 0.0; y = 0.0; r = 0.0 }, @{ x = $W; y = 0.0; r = 0.0 }, @{ x = $W; y = $h2; r = 0.0 },
        @{ x = [double]$x2; y = $h2; r = $join }, @{ x = [double]$x1; y = $h1; r = $join }, @{ x = 0.0; y = $h1; r = 0.0 }
    )
    $n = $pts.Count
    $sb = New-Object System.Text.StringBuilder
    for ($i = 0; $i -lt $n; $i++) {
        $prev = $pts[($i - 1 + $n) % $n]; $cur = $pts[$i]; $next = $pts[($i + 1) % $n]
        $cmd = if ($i -eq 0) { 'M' } else { 'L' }
        if ($cur.r -le 0) {
            [void]$sb.Append($cmd).Append($cur.x.ToString('0.##', $ci)).Append(',').Append($cur.y.ToString('0.##', $ci)).Append(' ')
            continue
        }
        $v1x = $prev.x - $cur.x; $v1y = $prev.y - $cur.y
        $v2x = $next.x - $cur.x; $v2y = $next.y - $cur.y
        $l1 = [math]::Sqrt($v1x * $v1x + $v1y * $v1y); if ($l1 -eq 0) { $l1 = 1.0 }
        $l2 = [math]::Sqrt($v2x * $v2x + $v2y * $v2y); if ($l2 -eq 0) { $l2 = 1.0 }
        $a = [math]::Min($cur.r, $l1 / 2); $b = [math]::Min($cur.r, $l2 / 2)
        $p1x = $cur.x + $v1x / $l1 * $a; $p1y = $cur.y + $v1y / $l1 * $a
        $p2x = $cur.x + $v2x / $l2 * $b; $p2y = $cur.y + $v2y / $l2 * $b
        [void]$sb.Append($cmd).Append($p1x.ToString('0.##', $ci)).Append(',').Append($p1y.ToString('0.##', $ci)).Append(' ')
        [void]$sb.Append('Q').Append($cur.x.ToString('0.##', $ci)).Append(',').Append($cur.y.ToString('0.##', $ci)).Append(' ')
        [void]$sb.Append($p2x.ToString('0.##', $ci)).Append(',').Append($p2y.ToString('0.##', $ci)).Append(' ')
    }
    [void]$sb.Append('Z')
    $el.Band.Data = [System.Windows.Media.Geometry]::Parse($sb.ToString())
}
$el.TitleBar.add_SizeChanged({ try { Set-BandShape } catch { } })
$w.add_ContentRendered({ try { Set-BandShape } catch { } })

$el.TitleBar.add_MouseLeftButtonDown({
    param($src, $e)
    try { if ($e.ButtonState -eq 'Pressed') { $w.DragMove() } } catch { }
})
$el.BtnMin.add_Click({ $w.WindowState = 'Minimized' })
$el.BtnClose.add_Click({ $w.Close() })

# -- state ------------------------------------------------------------------
$S = [hashtable]::Synchronized(@{
    Step = 'python'; StepIndex = -1; Title = ''; Detail = ''; Frac = 0.0
    Done = $false; Error = ''; Cancel = $false
})
$Steps = @(
    @{ Key = 'python';   Label = 'Python';    Weight = 12 },
    @{ Key = 'venv';     Label = 'Env';       Weight = 5 },
    @{ Key = 'pip';      Label = 'pip';       Weight = 4 },
    @{ Key = 'torch';    Label = 'PyTorch';   Weight = 47 },
    @{ Key = 'deps';     Label = 'Packages';  Weight = 24 },
    @{ Key = 'check';    Label = 'Check';     Weight = 4 },
    @{ Key = 'shortcut'; Label = 'Shortcuts'; Weight = 4 }
)
$script:ps = $null
$script:rs = $null
$script:handle = $null
$script:started = $null
$script:phase = 'ready'
$script:lastQuip = ''
$script:quipStep = ''

# Quips: "step|text" lines, UTF-8. Missing file: silence, not an error.
$Quips = @{}
if (Test-Path -LiteralPath $QuipsPath) {
    foreach ($line in (Get-Content -LiteralPath $QuipsPath -Encoding UTF8)) {
        $t = "$line".Trim()
        if (-not $t -or $t.StartsWith('#')) { continue }
        $i = $t.IndexOf('|')
        if ($i -lt 1) { continue }
        $k = $t.Substring(0, $i).Trim().ToLowerInvariant()
        if (-not $Quips.ContainsKey($k)) { $Quips[$k] = New-Object System.Collections.ArrayList }
        [void]$Quips[$k].Add($t.Substring($i + 1).Trim())
    }
}
$Rng = New-Object System.Random
function Get-Quip([string]$step) {
    $pool = New-Object System.Collections.ArrayList
    if ($Quips.ContainsKey($step)) { $pool.AddRange($Quips[$step]) }
    if ($step -ne 'done' -and $Quips.ContainsKey('any')) { $pool.AddRange($Quips['any']) }
    if ($pool.Count -eq 0) { return '' }
    for ($tries = 0; $tries -lt 6; $tries++) {
        $q = $pool[$Rng.Next($pool.Count)]
        if ($q -ne $script:lastQuip -or $pool.Count -eq 1) { break }
    }
    $script:lastQuip = $q
    return $q
}

# -- widgets built in code --------------------------------------------------
function New-Text([string]$text, [string]$color, [double]$size = 12.5) {
    $t = New-Object System.Windows.Controls.TextBlock
    $t.Text = $text; $t.Foreground = New-Brush $color; $t.FontSize = $size; $t.TextWrapping = 'Wrap'
    return $t
}
function Add-Bullet($panel, [string]$text) {
    # A gold dot and a line: the app's metric-group marker, not a hairline.
    $g = New-Object System.Windows.Controls.Grid
    $g.Margin = New-Object System.Windows.Thickness(0, 0, 0, 6)
    $c0 = New-Object System.Windows.Controls.ColumnDefinition; $c0.Width = New-Object System.Windows.GridLength(16)
    $c1 = New-Object System.Windows.Controls.ColumnDefinition
    [void]$g.ColumnDefinitions.Add($c0); [void]$g.ColumnDefinitions.Add($c1)
    $dot = New-Object System.Windows.Shapes.Ellipse
    $dot.Width = 6; $dot.Height = 6; $dot.Fill = New-Brush $P.Accent
    $dot.VerticalAlignment = 'Top'; $dot.HorizontalAlignment = 'Left'; $dot.Margin = New-Object System.Windows.Thickness(1, 6, 0, 0)
    $t = New-Text $text $P.TextSoft
    [System.Windows.Controls.Grid]::SetColumn($t, 1)
    [void]$g.Children.Add($dot); [void]$g.Children.Add($t)
    [void]$panel.Children.Add($g)
}
function New-Button([string]$text, [string]$style, [scriptblock]$onClick) {
    $b = New-Object System.Windows.Controls.Button
    $b.Content = $text
    $b.Style = $w.FindResource($style)
    $b.add_Click($onClick)
    return $b
}
function Set-Footer([array]$buttons, [string]$left = '') {
    $el.FootBtns.Children.Clear()
    foreach ($b in $buttons) { [void]$el.FootBtns.Children.Add($b) }
    $el.FootLeft.Text = $left
}
function Show-View([string]$name) {
    foreach ($v in 'ViewReady', 'ViewWork', 'ViewDone', 'ViewError') {
        $el[$v].Visibility = if ($v -eq $name) { 'Visible' } else { 'Collapsed' }
    }
}

# The seven step chips: equal width, left-aligned, a gold pill for the current
# one, a tick for the ones carried through (the app's step tabs, in miniature).
$Chips = @()
foreach ($st in $Steps) {
    $b = New-Object System.Windows.Controls.Border
    $b.Width = 80; $b.Height = 26; $b.CornerRadius = New-Object System.Windows.CornerRadius(5)
    $b.Margin = New-Object System.Windows.Thickness(0, 0, 3, 0)
    $b.Background = New-Brush $P.Panel2
    $t = New-Object System.Windows.Controls.TextBlock
    $t.Text = $st.Label; $t.FontSize = 11.5; $t.HorizontalAlignment = 'Center'; $t.VerticalAlignment = 'Center'
    $t.Foreground = New-Brush $P.TextFaint
    $b.Child = $t
    [void]$el.Steps.Children.Add($b)
    $Chips += $b
}
function Update-Chips([int]$active, [bool]$allDone) {
    for ($i = 0; $i -lt $Chips.Count; $i++) {
        $b = $Chips[$i]; $t = $b.Child
        if ($allDone -or $i -lt $active) {
            $b.Background = New-Brush $P.Panel3; $t.Foreground = New-Brush $P.TextDim
            $t.Text = [string][char]0x2713 + ' ' + $Steps[$i].Label; $t.FontWeight = 'Normal'
        } elseif ($i -eq $active) {
            $b.Background = New-Brush $P.Accent; $t.Foreground = New-Brush $P.Ink
            $t.Text = $Steps[$i].Label; $t.FontWeight = 'SemiBold'
        } else {
            $b.Background = New-Brush $P.Panel2; $t.Foreground = New-Brush $P.TextFaint
            $t.Text = $Steps[$i].Label; $t.FontWeight = 'Normal'
        }
    }
}

# -- the ready screen -------------------------------------------------------
$el.ReadyPath.Text = $Root
$el.ReadyList.Children.Clear()
Add-Bullet $el.ReadyList "Finds Python 3.11 on this PC, or installs it for you. No admin rights needed."
$sizeLine = "Creates a private environment (.venv) and downloads $SizeCpu of packages."
if ($Gpu) { $sizeLine = "Creates a private environment (.venv) and downloads $SizeCpu of packages ($SizeCuda with the GPU build)." }
Add-Bullet $el.ReadyList $sizeLine
Add-Bullet $el.ReadyList "Puts IMTOP on the Desktop and in the Start Menu."
Add-Bullet $el.ReadyList "The SAM model (375 MB) is fetched later, on first use, from inside the app."
if ($Gpu) {
    $el.ChkCuda.Visibility = 'Visible'
    $el.ChkCudaText.Text = "Use the NVIDIA GPU ($Gpu): CUDA build of PyTorch, a much larger download"
    $el.ChkCuda.IsChecked = ($DefaultBuild -eq 'cu124')
}
if ($Root -match '(?i)\\OneDrive') {
    $el.ReadyNote.Visibility = 'Visible'
    $el.ReadyNote.Text = "This folder is synced by OneDrive. The environment is thousands of small files, and OneDrive will try to upload every one of them. A plain folder such as C:\IMTOP is a better home."
}

# A PNG of the window as it is now, for the smoke test and unattended runs.
# RenderTargetBitmap draws the visual tree, so it works off screen too.
$ShotsDir = $SmokeShots
if ($ShotsDir) { New-Item -ItemType Directory -Path $ShotsDir -Force | Out-Null }
function Save-Shot([string]$name) {
    if (-not $ShotsDir) { return }
    try {
        $w.UpdateLayout()
        $root = $el.Root
        $rtb = New-Object System.Windows.Media.Imaging.RenderTargetBitmap([int]$root.ActualWidth, [int]$root.ActualHeight, 96, 96, [System.Windows.Media.PixelFormats]::Pbgra32)
        $rtb.Render($root)
        $enc = New-Object System.Windows.Media.Imaging.PngBitmapEncoder
        $enc.Frames.Add([System.Windows.Media.Imaging.BitmapFrame]::Create($rtb))
        $fs = [System.IO.File]::Open((Join-Path $ShotsDir "$name.png"), 'Create')
        try { $enc.Save($fs) } finally { $fs.Close() }
    } catch {
        Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("screenshot failed: " + $_.Exception.Message)
    }
}

# -- run --------------------------------------------------------------------
function Start-Install([bool]$simulate) {
    $build = 'cpu'
    if ($el.ChkCuda.Visibility -eq 'Visible' -and $el.ChkCuda.IsChecked) { $build = 'cu124' }
    elseif ($Cuda) { $build = 'cu124' }
    $Cfg = @{
        Root = $Root; VenvDir = $VenvDir; VenvPy = $VenvPy; VenvPyw = $VenvPyw; ReqFile = $ReqFile
        LogPath = $LogPath; IconPath = $IconPath; Aumid = $Aumid; ShortcutDir = $ShortcutDir
        PyMajorMinor = $PyMajorMinor; PyFullVersion = $PyFullVersion; PyExeUrl = $PyExeUrl; PyExeBytes = $PyExeBytes; WingetId = $WingetId
        TorchVersion = $TorchVersion; TorchSpec = $TorchSpec; TorchIndex = $TorchIndex; TorchBuild = $build
        Simulate = $simulate
    }
    $S.Step = 'python'; $S.StepIndex = -1; $S.Title = 'Starting'; $S.Detail = ''; $S.Frac = 0.0
    $S.Done = $false; $S.Error = ''; $S.Cancel = $false
    $script:started = Get-Date
    $script:phase = 'work'
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("torch build: " + $build + "  gpu: " + $(if ($Gpu) { $Gpu } else { 'none' }))

    $script:rs = [runspacefactory]::CreateRunspace()
    $script:rs.ApartmentState = 'STA'
    $script:rs.Open()
    $script:ps = [powershell]::Create()
    $script:ps.Runspace = $script:rs
    # param() has to be the first statement, so it goes in front of the shared functions.
    [void]$script:ps.AddScript("param(`$S, `$Cfg)`r`n" + $Shared + "`r`n" + $WorkerBody).AddArgument($S).AddArgument($Cfg)
    $script:handle = $script:ps.BeginInvoke()

    Show-View 'ViewWork'
    Update-Chips 0 $false
    $el.WorkTitle.Text = 'Starting'
    $el.WorkDetail.Text = ''
    $el.WorkQuip.Text = Get-Quip 'any'
    $script:quipStep = 'any'
    Set-Footer @((New-Button 'Cancel' 'Muted' { Request-Cancel })) '00:00'
    $uiTimer.Start(); $quipTimer.Start()
}

function Request-Cancel {
    $S.Cancel = $true
    $el.WorkDetail.Text = 'Stopping after this step'
}

function Stop-Worker {
    # Kill whatever the venv is running (pip), then the runspace.
    try {
        Get-Process -Name python, pythonw, pip -ErrorAction SilentlyContinue | Where-Object {
            try { $_.Path -and $_.Path.StartsWith($VenvDir, [StringComparison]::OrdinalIgnoreCase) } catch { $false }
        } | ForEach-Object { try { $_.Kill() } catch { } }
    } catch { }
    try { if ($script:ps) { $script:ps.Stop() } } catch { }
    try { if ($script:rs) { $script:rs.Close() } } catch { }
}

function Show-Done {
    $script:phase = 'done'
    $uiTimer.Stop(); $quipTimer.Stop()
    Update-Chips 7 $true
    Show-View 'ViewDone'
    $dirs = Get-ShortcutDirs $ShortcutDir
    $el.DoneText.Text = "IMTOP is installed in this folder and has a shortcut on the Desktop and in the Start Menu. The same double-click on install.cmd updates it later."
    $el.DoneList.Children.Clear()
    foreach ($d in $dirs) { Add-Bullet $el.DoneList (Join-Path $d 'IMTOP.lnk') }
    $el.DoneQuip.Text = Get-Quip 'done'
    $btns = @((New-Button 'Close' 'Muted' { $w.Close() }))
    if (-not $NoLaunch) {
        $btns += New-Button 'Launch IMTOP' 'Gold' {
            try { Start-Process -FilePath $VenvPyw -ArgumentList '-m', 'imtop' -WorkingDirectory $Root } catch { }
            $w.Close()
        }
    }
    Set-Footer $btns ''
    if ($Unattended) { Save-Shot 'done'; $w.Close() }
}

function Show-Error([string]$msg) {
    $script:phase = 'error'
    $uiTimer.Stop(); $quipTimer.Stop()
    Show-View 'ViewError'
    $el.ErrorText.Text = $msg
    $el.ErrorHint.Text = "The full log is in " + $LogPath + ". Nothing outside this folder was changed, except a Python 3.11 install if one was needed. Fix the cause and press Try again: the steps already done are skipped."
    Set-Footer @(
        (New-Button 'Open log' 'Muted' { try { Start-Process notepad.exe -ArgumentList ('"' + $LogPath + '"') } catch { } }),
        (New-Button 'Close' 'Muted' { $w.Close() }),
        (New-Button 'Try again' 'Gold' { Start-Install $false })
    ) ''
    if ($Unattended) { Save-Shot 'error'; $w.Close() }
}

$uiTimer = New-Object System.Windows.Threading.DispatcherTimer
$uiTimer.Interval = [TimeSpan]::FromMilliseconds(100)
$uiTimer.add_Tick({
    try {
        $idx = [int]$S.StepIndex
        $doneW = 0; $total = 0
        for ($i = 0; $i -lt $Steps.Count; $i++) {
            $total += $Steps[$i].Weight
            if ($i -lt $idx) { $doneW += $Steps[$i].Weight }
        }
        $frac = [double]$S.Frac
        if ($idx -ge 0 -and $idx -lt $Steps.Count) { $doneW += $Steps[$idx].Weight * [math]::Max(0.0, [math]::Min(1.0, $frac)) }
        $pct = [math]::Floor(100.0 * $doneW / $total)
        if ($S.Done) { $pct = 100 }
        $el.WorkPct.Text = [string][int]$pct
        $el.BarFill.Width = [math]::Max(0.0, $el.BarTrack.ActualWidth * $pct / 100.0)
        if ($S.Title) { $el.WorkTitle.Text = $S.Title }
        if (-not $S.Cancel) { $el.WorkDetail.Text = "$($S.Detail)" }
        if ($idx -ge 0) { Update-Chips $idx $false }
        if ($script:started) { $el.FootLeft.Text = ((Get-Date) - $script:started).ToString('mm\:ss') }
        if ($S.Step -ne $script:quipStep -and $idx -ge 0) {
            $script:quipStep = $S.Step
            $el.WorkQuip.Text = Get-Quip $S.Step
            $quipTimer.Stop(); $quipTimer.Start()
        }
        if ($S.Done) { Show-Done; return }
        if ($S.Error) { Show-Error $S.Error; return }
        if ($script:handle -and $script:handle.IsCompleted -and -not $S.Done -and -not $S.Error) {
            # The worker ended without saying why: surface whatever it threw.
            $why = 'The installer stopped unexpectedly. See the log.'
            try { $script:ps.EndInvoke($script:handle) | Out-Null } catch { $why = $_.Exception.Message }
            try {
                if ($script:ps.Streams.Error.Count -gt 0) { $why = "$($script:ps.Streams.Error[0])" }
                elseif ($script:ps.InvocationStateInfo.Reason) { $why = $script:ps.InvocationStateInfo.Reason.Message }
            } catch { }
            Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("WORKER ENDED WITHOUT A VERDICT: " + $why)
            Show-Error $why
        }
    } catch { }
})
$quipTimer = New-Object System.Windows.Threading.DispatcherTimer
$quipTimer.Interval = [TimeSpan]::FromSeconds(7)
$quipTimer.add_Tick({ try { $el.WorkQuip.Text = Get-Quip $S.Step } catch { } })

Set-Footer @(
    (New-Button 'Cancel' 'Muted' { $w.Close() }),
    (New-Button 'Install' 'Gold' { Start-Install $false })
) ''

$w.add_Closing({
    if ($script:phase -eq 'work') { Stop-Worker }
})

if ($Unattended -and -not $SmokeTest) {
    $w.add_ContentRendered({ if ($script:phase -eq 'ready') { Start-Install $false } })
}

# -- smoke test: off screen, simulated pipeline, screenshots ----------------
if ($SmokeTest) {
    if (-not $ShotsDir) { $ShotsDir = Join-Path $DataDir 'smoke'; New-Item -ItemType Directory -Path $ShotsDir -Force | Out-Null }
    $w.WindowStartupLocation = 'Manual'
    $w.Left = -4000; $w.Top = -4000
    $w.ShowInTaskbar = $false
    $smokeTimer = New-Object System.Windows.Threading.DispatcherTimer
    $smokeTimer.Interval = [TimeSpan]::FromMilliseconds(150)
    $script:smokeState = 'boot'
    $script:smokeT0 = Get-Date
    $smokeTimer.add_Tick({
        try {
            $age = ((Get-Date) - $script:smokeT0).TotalMilliseconds
            switch ($script:smokeState) {
                'boot' { if ($age -gt 600) { Save-Shot 'ready'; Start-Install $true; $script:smokeState = 'work' } }
                'work' {
                    if ($S.StepIndex -ge 3 -and $S.Frac -gt 0.3 -and $script:phase -eq 'work') { Save-Shot 'working'; $script:smokeState = 'wait' }
                    elseif ($script:phase -eq 'error') { Save-Shot 'error'; $script:smokeState = 'exit' }
                }
                'wait' {
                    if ($script:phase -eq 'done') { Save-Shot 'done'; Show-Error 'pip could not install PyTorch (exit 1). Check the internet connection, then see the log.'; $script:smokeState = 'err' }
                    elseif ($script:phase -eq 'error') { Save-Shot 'error'; $script:smokeState = 'exit' }
                }
                'err' { Save-Shot 'error'; $script:smokeState = 'exit' }
                'exit' {
                    $smokeTimer.Stop()
                    # Write-Output inside a WPF event handler goes nowhere; the console does.
                    [Console]::Out.WriteLine("shots=" + $ShotsDir)
                    try { [Console]::Out.WriteLine("frame " + [Imtop.Native]::Describe((New-Object System.Windows.Interop.WindowInteropHelper($w)).Handle)) } catch { }
                    $w.Close()
                }
            }
        } catch {
            $smokeTimer.Stop()
            [Console]::Out.WriteLine("smoke error: " + $_.Exception.Message)
            $w.Close()
        }
    })
    $smokeTimer.Start()
}

try {
    [void]$w.ShowDialog()
} catch {
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value ("FATAL: " + $_.Exception.Message)
    try { [System.Windows.MessageBox]::Show("The installer window failed:`r`n" + $_.Exception.Message + "`r`n`r`nLog: " + $LogPath, "IMTOP Installer") | Out-Null } catch { }
    exit 1
}
# The smoke test ends on a simulated error screen on purpose: that is not a failure.
if ($script:phase -eq 'error' -and -not $SmokeTest) { exit 1 }
exit 0
