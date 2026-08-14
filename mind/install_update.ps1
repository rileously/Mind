param(
    [Parameter(Mandatory = $true)][int]$MindProcessId,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target
)

$ErrorActionPreference = "Stop"
$updatesRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "Mind\Updates"))
$sourcePath = [IO.Path]::GetFullPath($Source)
$targetPath = [IO.Path]::GetFullPath($Target)
$logPath = Join-Path $updatesRoot "install.log"

function Write-UpdateLog([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$stamp] $Message" -Encoding UTF8
}

try {
    if (-not $sourcePath.StartsWith($updatesRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "The source is outside the Mind updates directory."
    }
    if ([IO.Path]::GetExtension($sourcePath) -ne ".exe" -or [IO.Path]::GetExtension($targetPath) -ne ".exe") {
        throw "The updater only accepts executable files."
    }
    $sourceItem = Get-Item -LiteralPath $sourcePath -ErrorAction Stop
    $targetItem = Get-Item -LiteralPath $targetPath -ErrorAction Stop
    if ($sourceItem.Length -lt 1MB -or $sourcePath -eq $targetPath) {
        throw "The update files did not pass validation."
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ((Get-Process -Id $MindProcessId -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }

    $backupPath = Join-Path $targetItem.DirectoryName "Mind.previous.exe"
    Copy-Item -LiteralPath $targetPath -Destination $backupPath -Force -ErrorAction Stop
    $installed = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        try {
            Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force -ErrorAction Stop
            $installed = (Get-Item -LiteralPath $targetPath -ErrorAction Stop).Length -eq $sourceItem.Length
            if ($installed) { break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $installed) {
        Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force -ErrorAction SilentlyContinue
        throw "Windows could not replace the running application."
    }

    Start-Process -FilePath $targetPath -ErrorAction Stop
    Remove-Item -LiteralPath $sourcePath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Installed update successfully. Previous build: $backupPath"
} catch {
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    exit 1
}
