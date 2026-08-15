param(
    [Parameter(Mandatory = $true)][int]$MindProcessId,
    [int]$MindParentProcessId = 0,
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

function Clear-PyInstallerEnvironment() {
    Remove-Item env:_MEIPASS2 -ErrorAction SilentlyContinue
    Remove-Item env:_MEIPASS -ErrorAction SilentlyContinue
    Remove-Item env:PYTHONHOME -ErrorAction SilentlyContinue
    Remove-Item env:PYTHONPATH -ErrorAction SilentlyContinue
    if ($env:PATH) {
        $env:PATH = ($env:PATH -split ';' | Where-Object { $_ -notmatch '[\\/]_MEI[0-9A-Za-z]+' }) -join ';'
    }
}

Clear-PyInstallerEnvironment

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

    # A PyInstaller one-file application has a bootloader parent and a Python
    # child. Both must exit before the executable is replaced and restarted.
    $mindProcessIds = @($MindProcessId, $MindParentProcessId) |
        Where-Object { $_ -gt 0 } |
        Select-Object -Unique
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        $runningMindProcesses = @(
            $mindProcessIds | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }
        )
        if ($runningMindProcesses.Count -eq 0) { break }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($runningMindProcesses.Count -gt 0) {
        throw "Mind did not close before the update timeout."
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

    Clear-PyInstallerEnvironment
    # Avoid volatile system temp cleanup and antivirus races while the new
    # one-file build extracts its bundled Python runtime on first launch.
    $runtimeRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "Mind\Runtime"))
    New-Item -ItemType Directory -Path $runtimeRoot -Force -ErrorAction Stop | Out-Null
    $runtimeItem = Get-Item -LiteralPath $runtimeRoot -Force -ErrorAction Stop
    if (($runtimeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The Mind runtime directory cannot be a link or junction."
    }
    $env:TEMP = $runtimeRoot
    $env:TMP = $runtimeRoot

    try {
        $startedProcess = Start-Process -FilePath $targetPath `
            -WorkingDirectory $targetItem.DirectoryName -PassThru -ErrorAction Stop
        Start-Sleep -Seconds 6
        $startedProcess.Refresh()
        if ($startedProcess.HasExited) {
            throw "The updated application exited during its startup check (code $($startedProcess.ExitCode))."
        }
    } catch {
        $startupError = $_.Exception.Message
        Copy-Item -LiteralPath $backupPath -Destination $targetPath -Force -ErrorAction Stop
        Clear-PyInstallerEnvironment
        Start-Process -FilePath $targetPath -WorkingDirectory $targetItem.DirectoryName -ErrorAction Stop
        throw "The update could not start, so the previous version was restored. $startupError"
    }

    Remove-Item -LiteralPath $sourcePath -Force -ErrorAction SilentlyContinue
    Write-UpdateLog "Installed update and passed the startup check. Previous build: $backupPath"
} catch {
    Write-UpdateLog "Update failed: $($_.Exception.Message)"
    exit 1
}
