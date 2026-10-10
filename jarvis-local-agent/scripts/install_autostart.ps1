# Start the JARVIS voice runtime automatically when you sign in to Windows.
#
#   powershell -ExecutionPolicy Bypass -File jarvis-local-agent\scripts\install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File jarvis-local-agent\scripts\install_autostart.ps1 -Remove
#
# What it does: puts a shortcut in YOUR Startup folder (no admin rights, no registry, no service)
# that runs `pythonw -m jarvis_local_agent.voice_app` from the project's virtual environment.
# pythonw has no console window, so JARVIS lives in the tray; logs go to ~\.jarvis\logs\voice.log.
# Only one instance can run (a named mutex), so autostart plus a manual launch is harmless.
#
# Prerequisites: the device is registered and its secret stored with
#   python -m jarvis_local_agent.credentials set
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "JARVIS.lnk"

if ($Remove) {
    if (Test-Path $shortcut) { Remove-Item $shortcut; Write-Host "Removed JARVIS autostart." }
    else { Write-Host "No JARVIS autostart shortcut found." }
    return
}

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    throw "Cannot find $pythonw. Create the virtual environment first (see README.md)."
}

$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "-m jarvis_local_agent.voice_app"
# Start in the agent folder so its .env (allowed paths, backend URL) is found.
$lnk.WorkingDirectory = Join-Path $root "jarvis-local-agent"
$lnk.WindowStyle = 7
$lnk.Description = "JARVIS voice runtime"
$lnk.Save()

Write-Host "Installed: $shortcut"
Write-Host "Starts at next sign-in:  $pythonw -m jarvis_local_agent.voice_app"
