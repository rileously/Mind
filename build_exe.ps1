$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Create the virtual environment first: python -m venv .venv"
}

$artifactDir = Join-Path $projectDir "artifacts"
New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null

Push-Location $projectDir
try {
    & $pythonExe -m pip install -r (Join-Path $projectDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Installing runtime requirements failed." }
    & $pythonExe -m pip install -r (Join-Path $projectDir "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "Installing build requirements failed." }
    & $pythonExe (Join-Path $projectDir "tools\export_app_icon.py") (Join-Path $artifactDir "Mind.ico")
    if ($LASTEXITCODE -ne 0) { throw "Creating the Mind icon failed." }
    & $pythonExe -m PyInstaller --noconfirm --clean (Join-Path $projectDir "Mind.spec")
    if ($LASTEXITCODE -ne 0) { throw "Building Mind.exe failed." }

    $builtExe = Join-Path $projectDir "dist\Mind.exe"
    if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
        throw "The Mind executable was not created."
    }
    $artifactExe = Join-Path $artifactDir "Mind.exe"
    Copy-Item -LiteralPath $builtExe -Destination $artifactExe -Force
    $artifactHash = (Get-FileHash -LiteralPath $artifactExe -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath (Join-Path $artifactDir "Mind.exe.sha256") -Value "$artifactHash  Mind.exe" -Encoding ASCII

    Write-Host ""
    Write-Host "Mind.exe is ready:" -ForegroundColor Green
    Write-Host $artifactExe
} finally {
    Pop-Location
}
