param(
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [string]$Notes = "Mind $Version update"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$artifactExe = Join-Path $projectDir "artifacts\Mind.exe"
$artifactHash = Join-Path $projectDir "artifacts\Mind.exe.sha256"
$versionFile = Join-Path $projectDir "mind\__init__.py"
$repository = "rileously/Mind"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "Install GitHub CLI first: https://cli.github.com/"
}
& gh auth status
if ($LASTEXITCODE -ne 0) { throw "Sign in first with: gh auth login" }
if (-not (Test-Path -LiteralPath $artifactExe -PathType Leaf)) { throw "Build Mind.exe first." }
if (-not (Test-Path -LiteralPath $artifactHash -PathType Leaf)) { throw "Build the checksum first." }
$versionSource = Get-Content -Raw -LiteralPath $versionFile
if ($versionSource -notmatch ('__version__\s*=\s*"' + [regex]::Escape($Version) + '"')) {
    throw "mind\__init__.py does not match version $Version."
}

& gh release create "v$Version" "$artifactExe#Mind.exe" "$artifactHash#Mind.exe.sha256" `
    --repo $repository --title "Mind $Version" --notes $Notes
if ($LASTEXITCODE -ne 0) { throw "Publishing the GitHub release failed." }

Write-Host "Mind $Version is published. Existing installations will detect it automatically." -ForegroundColor Green
