# Registers jarvis-local-agent to start with Windows for the current user.
# Creates a shortcut in the user's Startup folder that launches the runtime
# minimized. Reversible: run install_autostart.ps1 -Remove to undo.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
#   powershell -ExecutionPolicy Bypass -File install_autostart.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "JARVIS.lnk"

if ($Remove) {
    if (Test-Path $shortcut) { Remove-Item $shortcut; Write-Host "Removed JARVIS autostart." }
    else { Write-Host "No JARVIS autostart shortcut found." }
    return
}

# Resolve the python / console-script that runs the runtime.
$exe = (Get-Command jarvis-local-agent -ErrorAction SilentlyContinue).Source
if (-not $exe) {
    $exe = (Get-Command python -ErrorAction Stop).Source
    $args = "-m jarvis_local_agent"
} else {
    $args = ""
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = $exe
if ($args) { $lnk.Arguments = $args }
$lnk.WindowStyle = 7   # minimized
$lnk.Description = "JARVIS persistent voice runtime"
$lnk.Save()

Write-Host "Installed JARVIS autostart ->" $shortcut
Write-Host "Target:" $exe $args
