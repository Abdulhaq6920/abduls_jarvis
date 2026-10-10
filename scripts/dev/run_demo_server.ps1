# Starts the backend against a throwaway demo data directory (never touches ~/.jarvis).
# Used by scripts/dev/ui_smoke.py. Creates the demo admin on first run.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$env:JARVIS_DATA_DIR = Join-Path $env:TEMP "jarvis-demo"
$env:JARVIS_LOG_TO_FILE = "false"
$env:JARVIS_PORT = "8077"
# Empty values override .env, so the demo can never touch a real database or Redis.
$env:JARVIS_ENV = "dev"
$env:JARVIS_DATABASE_URL = ""
$env:JARVIS_REDIS_URL = ""
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "scripts\dev\seed_demo.py")
& $py -m jarvis_backend serve
