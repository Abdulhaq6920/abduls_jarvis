# Installs the secret-scanning pre-commit hook into THIS clone (hooks are local; git does not
# share them). Run once per clone:   powershell -ExecutionPolicy Bypass -File scripts\security\install_git_hooks.ps1
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dest = Join-Path $root ".git\hooks\pre-commit"
if (-not (Test-Path (Join-Path $root ".git"))) { throw "Not a git clone: $root" }
# LF line endings are required: Git for Windows runs hooks with sh, which chokes on CRLF.
$text = (Get-Content (Join-Path $PSScriptRoot "git-hooks\pre-commit") -Raw) -replace "`r`n", "`n"
[IO.File]::WriteAllText($dest, $text, (New-Object Text.UTF8Encoding $false))
Write-Host "Installed pre-commit hook -> $dest"
