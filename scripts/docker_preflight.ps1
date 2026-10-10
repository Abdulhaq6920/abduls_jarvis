# Checks each stage of the Docker setup and tells you what to do next.
# Safe to run any time, as often as you like: it only reads, it changes nothing.
#
#   powershell -ExecutionPolicy Bypass -File scripts\docker_preflight.ps1

$ok = $true
function Check($name, $pass, $fix) {
    if ($pass) { Write-Host ("  [ OK ] " + $name) -ForegroundColor Green }
    else { Write-Host ("  [FAIL] " + $name) -ForegroundColor Red; Write-Host ("         -> " + $fix) -ForegroundColor Yellow; $script:ok = $false }
}

Write-Host "`nJARVIS Docker preflight`n"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
Write-Host ("  RAM: {0} GB   Admin shell: {1}" -f $ram, $admin)
if ($ram -lt 6) { Write-Host "  NOTE: under 6 GB, use docker-compose.lowmem.yml and docker\wslconfig.example" -ForegroundColor Yellow }
Write-Host ""

$wslOut = (wsl --status 2>&1 | Out-String) -replace "`0", ""
Check "WSL is installed" ($wslOut -notmatch "not installed") "Admin PowerShell:  wsl --install --no-distribution   then RESTART Windows"

$docker = Get-Command docker -ErrorAction SilentlyContinue
Check "docker command found" ([bool]$docker) "Admin PowerShell:  winget install -e --id Docker.DockerDesktop   then restart and open Docker Desktop once"

if ($docker) {
    $server = (docker version --format "{{.Server.Version}}" 2>$null)
    Check "Docker engine is running" ([bool]$server) "Start Docker Desktop and wait until the whale icon says 'Engine running'"
    $compose = (docker compose version --short 2>$null)
    Check "docker compose v2 available" ([bool]$compose) "Update Docker Desktop (compose v2 ships with it)"

    if ($server) {
        $memBytes = [int64](docker info --format "{{.MemTotal}}" 2>$null)
        $memGb = [math]::Round($memBytes / 1GB, 2)
        Check ("Docker VM memory is workable ({0} GB)" -f $memGb) ($memGb -ge 1.5) "Copy docker\wslconfig.example to ~\.wslconfig, run: wsl --shutdown, restart Docker Desktop"
        $hello = (docker run --rm hello-world 2>&1 | Out-String)
        Check "can pull and run a container (hello-world)" ($hello -match "Hello from Docker") "Check your internet connection / Docker Desktop login-less pull; see the error above"
    }
}

$secrets = Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) "secrets\jarvis_jwt_secret")
Check "JARVIS secret files exist (./secrets)" $secrets "Run:  python scripts\make_docker_secrets.py"
# Docker reads docker\.env.docker. The project-root .env is only for running the backend directly.
$envFile = Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) "docker\.env.docker")
Check "docker\.env.docker exists" $envFile "Run:  copy docker\env.docker.example docker\.env.docker"

Write-Host ""
if ($ok) { Write-Host "All good. Next: docker compose -f docker-compose.yml -f docker-compose.lowmem.yml up -d --build" -ForegroundColor Green }
else { Write-Host "Fix the [FAIL] items top to bottom, then run this script again." -ForegroundColor Yellow }
