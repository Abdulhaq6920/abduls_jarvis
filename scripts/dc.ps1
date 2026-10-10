# Thin wrapper so you never have to remember the long docker compose command line.
#
#   scripts\dc.ps1 up -d --build            start everything (low-memory profile)
#   scripts\dc.ps1 ps                        what is running and healthy
#   scripts\dc.ps1 logs -f api               follow API logs
#   scripts\dc.ps1 run --rm api create-admin create the first dashboard user
#   scripts\dc.ps1 down                      stop (data is kept)
#   scripts\dc.ps1 -Full up -d --build       big-machine profile (2 API replicas, bigger limits)
#
# It always passes  --env-file docker/.env.docker  and, unless -Full, the low-memory profile.
param([switch]$Full, [Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$envFile = Join-Path $root "docker\.env.docker"
if (-not (Test-Path $envFile)) {
    Write-Host "Missing docker\.env.docker. Create it with:" -ForegroundColor Yellow
    Write-Host "    copy docker\env.docker.example docker\.env.docker" -ForegroundColor Yellow
    exit 2
}
if (-not (Test-Path (Join-Path $root "secrets\jarvis_jwt_secret"))) {
    Write-Host "Missing ./secrets. Create it with:  python scripts\make_docker_secrets.py" -ForegroundColor Yellow
    exit 2
}

$files = @("-f", "docker-compose.yml")
if (-not $Full) { $files += @("-f", "docker-compose.lowmem.yml") }

& docker compose --env-file $envFile @files @ComposeArgs
exit $LASTEXITCODE
