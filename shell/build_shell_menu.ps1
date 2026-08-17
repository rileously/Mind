# Builds the Windows 11 context menu handler: the DLL, its logos, and the sparse
# package that gives Mind the package identity Explorer's compact menu insists on.
#
# Three separate toolchains are involved (MSVC, the Windows SDK, and Mind's own
# Python for the logos), so each is located rather than assumed to be on PATH.
#
#   .\shell\build_shell_menu.ps1                     # build and sign for this PC
#   .\shell\build_shell_menu.ps1 -Publisher "CN=..."  # sign with a real certificate
#
# Without -CertificateThumbprint a development certificate is created and used.
# That certificate has to be trusted on the machine before Windows will register
# the package, which needs administrator rights once; the script prints the
# command rather than elevating behind the user's back.

[CmdletBinding()]
param(
    # Must match the signing certificate's subject exactly, or Windows rejects
    # the package with a mismatched-publisher error that names neither side.
    [string]$Publisher = "CN=Mind",
    [string]$CertificateThumbprint = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"

$shellDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $shellDir
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectDir "artifacts\shell"
}

function Find-Newest {
    param([string[]]$Roots, [string]$Leaf)
    foreach ($root in $Roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $found = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName $Leaf } |
            Where-Object { Test-Path -LiteralPath $_ } |
            Select-Object -First 1
        if ($found) { return $found }
    }
    return $null
}

$vcvars = Find-Newest -Roots @(
    "C:\Program Files\Microsoft Visual Studio",
    "C:\Program Files (x86)\Microsoft Visual Studio"
) -Leaf "BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if (-not $vcvars) {
    $vcvars = Find-Newest -Roots @(
        "C:\Program Files\Microsoft Visual Studio",
        "C:\Program Files (x86)\Microsoft Visual Studio"
    ) -Leaf "Community\VC\Auxiliary\Build\vcvars64.bat"
}
if (-not $vcvars) {
    throw "No MSVC build environment found. Install the Visual Studio Build Tools with the C++ workload."
}

$sdkBin = Find-Newest -Roots @("C:\Program Files (x86)\Windows Kits\10\bin") -Leaf "x64\makeappx.exe"
if (-not $sdkBin) {
    throw "makeappx.exe was not found. Install the Windows SDK."
}
$makeappx = $sdkBin
$signtool = Join-Path (Split-Path -Parent $sdkBin) "signtool.exe"
if (-not (Test-Path -LiteralPath $signtool)) {
    throw "signtool.exe was not found next to makeappx.exe."
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$stageDir = Join-Path $OutputDirectory "package"
$resourceDir = Join-Path $OutputDirectory "resources"
Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stageDir "resources") -Force | Out-Null
New-Item -ItemType Directory -Path $resourceDir -Force | Out-Null

Write-Host "Compiling MindShellMenu.dll..." -ForegroundColor Cyan
$source = Join-Path $shellDir "mind_shell_menu.cpp"
$exports = Join-Path $shellDir "mind_shell_menu.def"
$dll = Join-Path $OutputDirectory "MindShellMenu.dll"
$objDir = Join-Path $OutputDirectory "obj"
New-Item -ItemType Directory -Path $objDir -Force | Out-Null

# cl.exe needs the environment vcvars64.bat sets up, and that only survives
# inside a single cmd invocation.
$compile = @"
call "$vcvars" >nul || exit /b 1
rem /IMPLIB keeps the import library and its .exp out of the repository root,
rem which is where the linker would otherwise drop them.
cl.exe /nologo /LD /EHsc /W4 /WX /O2 /MT /DUNICODE /D_UNICODE /Fo"$objDir\\" "$source" /link /DLL /DEF:"$exports" /IMPLIB:"$objDir\mind_shell_menu.lib" /OUT:"$dll" || exit /b 1
"@
$compileScript = Join-Path $OutputDirectory "compile.cmd"
Set-Content -LiteralPath $compileScript -Value $compile -Encoding ASCII
& cmd.exe /c "`"$compileScript`""
if ($LASTEXITCODE -ne 0) { throw "Compiling MindShellMenu.dll failed." }
if (-not (Test-Path -LiteralPath $dll)) { throw "MindShellMenu.dll was not produced." }

Write-Host "Rendering package logos..." -ForegroundColor Cyan
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Create the virtual environment first: python -m venv .venv"
}
& $python (Join-Path $projectDir "tools\export_package_logos.py") $resourceDir
if ($LASTEXITCODE -ne 0) { throw "Rendering the package logos failed." }

# The manifest is the only thing inside the package. Everything it names is read
# from the external location at registration time, exactly as Zed and VS Code do.
$manifestText = Get-Content -LiteralPath (Join-Path $shellDir "AppxManifest.xml") -Raw
# -creplace throughout: a case-insensitive match would rewrite the version in the
# XML declaration and leave a manifest MakeAppx refuses to read.
$manifestText = $manifestText -creplace 'Publisher="[^"]*"', ('Publisher="' + $Publisher + '"')

# Mind's version is the one source of truth: shell_menu.py works out the package
# name it expects from it, so a manifest left behind would look like a foreign
# package and be replaced on every launch.
$versionLine = Select-String -LiteralPath (Join-Path $projectDir "mind\__init__.py") -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $versionLine) { throw "Could not read __version__ from mind\__init__.py." }
$packageVersion = $versionLine.Matches[0].Groups[1].Value + ".0"
$manifestText = $manifestText -creplace 'Version="[0-9][^"]*"', ('Version="' + $packageVersion + '"')
Write-Host "Package version: $packageVersion" -ForegroundColor Cyan
$stagedManifest = Join-Path $stageDir "AppxManifest.xml"
Set-Content -LiteralPath $stagedManifest -Value $manifestText -Encoding UTF8
Copy-Item -LiteralPath (Join-Path $resourceDir "logo_150x150.png") -Destination (Join-Path $stageDir "resources") -Force
Copy-Item -LiteralPath (Join-Path $resourceDir "logo_44x44.png") -Destination (Join-Path $stageDir "resources") -Force

Write-Host "Packing MindShellMenu.msix..." -ForegroundColor Cyan
$msix = Join-Path $OutputDirectory "MindShellMenu.msix"
Remove-Item -LiteralPath $msix -Force -ErrorAction SilentlyContinue
& $makeappx pack /d $stageDir /p $msix /o /nv
if ($LASTEXITCODE -ne 0) { throw "Packing the sparse package failed." }

if (-not $CertificateThumbprint) {
    $existing = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $Publisher -and $_.NotAfter -gt (Get-Date) } |
        Select-Object -First 1
    if (-not $existing) {
        Write-Host "Creating a development certificate for $Publisher..." -ForegroundColor Cyan
        $existing = New-SelfSignedCertificate -Type Custom -Subject $Publisher `
            -KeyUsage DigitalSignature -FriendlyName "Mind shell menu (development)" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    }
    $CertificateThumbprint = $existing.Thumbprint
    $exported = Join-Path $OutputDirectory "MindShellMenu.cer"
    Export-Certificate -Cert $existing -FilePath $exported -Force | Out-Null
}

Write-Host "Signing MindShellMenu.msix..." -ForegroundColor Cyan
& $signtool sign /fd SHA256 /sha1 $CertificateThumbprint $msix
if ($LASTEXITCODE -ne 0) { throw "Signing the sparse package failed." }

Write-Host ""
Write-Host "Context menu handler is ready:" -ForegroundColor Green
Write-Host "  $dll"
Write-Host "  $msix"
Write-Host "  $resourceDir\logo_150x150.png, logo_44x44.png"
if (Test-Path -LiteralPath (Join-Path $OutputDirectory "MindShellMenu.cer")) {
    Write-Host ""
    Write-Host "Signed with a development certificate. Windows will refuse the package" -ForegroundColor Yellow
    Write-Host "until that certificate is trusted, which needs one elevated command:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Import-Certificate -FilePath '$OutputDirectory\MindShellMenu.cer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
    Write-Host ""
    Write-Host "Shipping to other people needs a real code signing certificate instead." -ForegroundColor Yellow
}
