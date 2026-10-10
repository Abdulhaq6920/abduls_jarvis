# Starts the backend against a throwaway demo data directory (never touches ~/.jarvis).
# Used by scripts/ui_smoke.py. Creates the demo admin on first run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:JARVIS_DATA_DIR = Join-Path $env:TEMP "jarvis-demo"
$env:JARVIS_LOG_TO_FILE = "false"
$env:JARVIS_PORT = "8077"
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "scripts\seed_demo.py")
& $py -m jarvis_backend serve
