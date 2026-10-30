# One-command setup for Arete on Windows:
#   1. checks for Python 3.13
#   2. creates .venv and installs the pinned Python packages (requirements.txt)
#   3. adds Arete shortcuts to the Start menu and the desktop
#
# Run from the project folder, in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Safe to run again (e.g. after pulling updates).
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$AppDir = (Get-Location).Path

function Say($text)  { Write-Host "`n$text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n$text" -ForegroundColor Red; exit 1 }

# --- 1. Python -----------------------------------------------------------------
Say "1/3  Checking Python..."
$Py = $null
# The "py" launcher (installed with Python from python.org) can pick 3.13 exactly.
foreach ($candidate in @(@("py", "-3.13"), @("python"), @("python3"))) {
    $exe = $candidate[0]; $extra = @($candidate | Select-Object -Skip 1)
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        & $exe @extra -c "import sys; sys.exit(sys.version_info[:2] != (3, 13))" 2>$null
        if ($LASTEXITCODE -eq 0) { $Py = $candidate; break }
    }
}
if (-not $Py) {
    Fail ("Arete needs Python 3.13 (the version its packages are tested with).`n" +
          "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run setup.ps1 again.")
}
$PyExe = $Py[0]; $PyArgs = @($Py | Select-Object -Skip 1)
Write-Host ("Using " + (& $PyExe @PyArgs --version))

# --- 2. Packages ---------------------------------------------------------------
Say "2/3  Installing packages (a few minutes the first time)..."
$VenvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
# Reuse an existing .venv only if it's Python 3.13; otherwise make a fresh one.
if (Test-Path $VenvPy) {
    & $VenvPy -c "import sys; sys.exit(sys.version_info[:2] != (3, 13))" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "Replacing .venv (it isn't Python 3.13)"; Remove-Item -Recurse -Force .venv }
}
if (-not (Test-Path $VenvPy)) { & $PyExe @PyArgs -m venv .venv }
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "Installing the packages failed (see the messages above)." }
Write-Host "Packages installed."

# --- 3. Shortcuts --------------------------------------------------------------
Say "3/3  Adding Arete to the Start menu and the desktop..."
# pythonw.exe runs the app without a console window.
$Target = Join-Path $AppDir ".venv\Scripts\pythonw.exe"
$Icon = Join-Path $AppDir "assets\icon.ico"
$Shell = New-Object -ComObject WScript.Shell
$Places = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Arete.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Arete.lnk")
)
foreach ($place in $Places) {
    $link = $Shell.CreateShortcut($place)
    $link.TargetPath = $Target
    $link.Arguments = "`"$(Join-Path $AppDir 'desktop.py')`""
    $link.WorkingDirectory = $AppDir
    $link.IconLocation = $Icon
    $link.Description = "Arete: a personal life tracker"
    $link.Save()
    Write-Host "Made $place"
}

Say "All done!"
Write-Host "Open Arete from the Start menu or the desktop shortcut."
Write-Host "  - A short setup guide runs on first launch (Claude, Outlook calendar, microphone)."
