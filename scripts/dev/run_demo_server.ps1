# Starts the backend against a throwaway demo data directory (never touches ~/.jarvis).
# Used by scripts/dev/ui_smoke.py. Creates the demo admin on first run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:JARVIS_DATA_DIR = Join-Path $env:TEMP "jarvis-demo"
$env:JARVIS_LOG_TO_FILE = "false"
$env:JARVIS_PORT = "8077"
# Point explicitly at a throwaway SQLite file so a real database in .env can never be used.
# (Setting an env var to "" in PowerShell DELETES it, which would silently fall back to .env.)
$env:JARVIS_ENV = "dev"
$env:JARVIS_DATABASE_URL = "sqlite+aiosqlite:///" + ((Join-Path $env:JARVIS_DATA_DIR "demo.db") -replace '\\', '/')
New-Item -ItemType Directory -Force $env:JARVIS_DATA_DIR | Out-Null
# Settings also read ./.env from the working directory; run from the demo dir so none is found.
Set-Location $env:JARVIS_DATA_DIR
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "scripts\dev\seed_demo.py")
& $py -m jarvis_backend serve
