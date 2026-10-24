# build.ps1 - builds dist\EPMTaskDeployer (PyInstaller one-folder, windowed) and dist\EPMTaskDeployer-<ver>-win64.zip
#
# Windows PowerShell 5.1 compatible. Run from anywhere:
#   powershell -NoProfile -ExecutionPolicy Bypass -File P:\work\epm_taskmanagerauto_create\build.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -SkipTests
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -SignCertThumbprint <sha1 thumbprint>
#
# Steps: .venv -> install requirements -> pytest (fails the build) -> icon -> optional user guide
#        -> clean build/ dist/ -> PyInstaller -> optional signing -> READ ME -> self-check -> zip -> SHA-256.
#
# Code signing (optional): when -SignCertThumbprint is given, EPMTaskDeployer.exe is signed with signtool
# (SHA-256, RFC 3161 timestamp) using the certificate from the CurrentUser or LocalMachine store.
# Without the parameter the signing step is skipped silently.
# SmartScreen: an unsigned or newly signed exe can show "Windows protected your PC" until enough users run it
# or the certificate has reputation; a publicly trusted code-signing certificate reduces this. Signing does not
# remove antivirus heuristics completely.

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$SignCertThumbprint = '',
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$Version = '1.0.0'
)

$ErrorActionPreference = 'Stop'
$overall = [System.Diagnostics.Stopwatch]::StartNew()

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

$venvDir = Join-Path $root '.venv'
$py = Join-Path $venvDir 'Scripts\python.exe'
$distDir = Join-Path $root 'dist'
$appDir = Join-Path $distDir 'EPMTaskDeployer'
$exePath = Join-Path $appDir 'EPMTaskDeployer.exe'
$zipPath = Join-Path $distDir ("EPMTaskDeployer-{0}-win64.zip" -f $Version)

function Invoke-Step {
    param([string]$Title, [scriptblock]$Action)
    Write-Host ''
    Write-Host ("==> {0}" -f $Title) -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
        throw ("{0} failed (exit code {1})." -f $Title, $LASTEXITCODE)
    }
}

function Get-FolderSize {
    param([string]$Path)
    return (Get-ChildItem -LiteralPath $Path -Recurse -File | Measure-Object -Property Length -Sum).Sum
}

function Format-Size {
    param([long]$Bytes)
    if ($Bytes -ge 1MB) { return ("{0:N1} MB ({1:N0} bytes)" -f ($Bytes / 1MB), $Bytes) }
    return ("{0:N0} bytes" -f $Bytes)
}

# 1. Virtual environment ------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $py)) {
    Invoke-Step 'Create .venv (Python 3.12)' {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv $venvDir
            if ($LASTEXITCODE -ne 0) { & python -m venv $venvDir }
        }
        else {
            & python -m venv $venvDir
        }
    }
}
if (-not (Test-Path -LiteralPath $py)) { throw "Python venv not found at $py. Install Python 3.12 and retry." }
$pyVersion = (& $py -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($pyVersion -ne '3.12') { Write-Warning "The venv uses Python $pyVersion; the project is tested with 3.12." }

# 2. Dependencies -------------------------------------------------------------------------------
Invoke-Step 'Install requirements and dev requirements' {
    & $py -m pip install --disable-pip-version-check -r (Join-Path $root 'requirements.txt') -r (Join-Path $root 'requirements-dev.txt')
}

# 3. Tests (a failure stops the build) ----------------------------------------------------------
if ($SkipTests) {
    Write-Warning 'Tests skipped (-SkipTests).'
}
else {
    Invoke-Step 'Run pytest' { & $py -m pytest -q }
}

# 4. Icon ---------------------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath (Join-Path $root 'assets\app.ico'))) {
    Invoke-Step 'Generate assets\app.ico' { & $py (Join-Path $root 'tools\make_icon.py') }
}

# 5. User guide (optional) ----------------------------------------------------------------------
$buildGuide = Join-Path $root 'tools\build_user_guide.py'
if (Test-Path -LiteralPath $buildGuide) {
    Invoke-Step 'Build user guide (tools\build_user_guide.py)' { & $py $buildGuide }
}
else {
    Write-Host 'No tools\build_user_guide.py; user guide step skipped.' -ForegroundColor DarkGray
}
$guide = Join-Path $root 'docs\user-guide\USER_GUIDE.html'
if (-not (Test-Path -LiteralPath $guide)) {
    Write-Warning 'docs\user-guide\USER_GUIDE.html not found: the executable will be built without the user guide.'
}

# 6. Clean --------------------------------------------------------------------------------------
foreach ($dir in @((Join-Path $root 'build'), $distDir)) {
    if (Test-Path -LiteralPath $dir) {
        Write-Host ("Removing {0}" -f $dir)
        Remove-Item -LiteralPath $dir -Recurse -Force
    }
}

# 7. PyInstaller --------------------------------------------------------------------------------
$pyiSw = [System.Diagnostics.Stopwatch]::StartNew()
Invoke-Step 'PyInstaller (EPMTaskDeployer.spec)' {
    & $py -m PyInstaller --noconfirm --clean (Join-Path $root 'EPMTaskDeployer.spec')
}
$pyiSw.Stop()
if (-not (Test-Path -LiteralPath $exePath)) { throw "Build did not produce $exePath" }

# 8. Optional signing ---------------------------------------------------------------------------
if ($SignCertThumbprint) {
    $signtool = $null
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { $signtool = $cmd.Source }
    if (-not $signtool) {
        $kits = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending
        if ($kits) { $signtool = @($kits)[0].FullName }
    }
    if (-not $signtool) { throw 'signtool.exe not found. Install the Windows SDK or omit -SignCertThumbprint.' }
    $thumb = ($SignCertThumbprint -replace '\s', '').ToUpperInvariant()
    Invoke-Step 'Sign EPMTaskDeployer.exe' {
        & $signtool sign /sha1 $thumb /fd SHA256 /td SHA256 /tr $TimestampUrl /d 'Oracle EPM Task Manager - Schedule Deployer' $exePath
    }
    Invoke-Step 'Verify signature' { & $signtool verify /pa /v $exePath }
}

# 9. User README --------------------------------------------------------------------------------
$readme = Join-Path $root 'packaging\READ_ME_FIRST.txt'
Copy-Item -LiteralPath $readme -Destination (Join-Path $appDir 'READ ME FIRST.txt') -Force

# 10. Self-check on the built exe (windowed exe has no console: read the report file) -----------
Write-Host ''
Write-Host '==> Self-check of the built executable' -ForegroundColor Cyan
$proc = Start-Process -FilePath $exePath -ArgumentList '--self-check' -PassThru -Wait
$selfCheckExit = $proc.ExitCode
$logDir = if ($env:EPMTD_HOME) { Join-Path $env:EPMTD_HOME 'logs' } else { Join-Path $env:APPDATA 'EPMTaskDeployer\logs' }
$reportFile = Join-Path $logDir 'self_check.txt'
if (Test-Path -LiteralPath $reportFile) {
    Write-Host ("----- {0} -----" -f $reportFile) -ForegroundColor DarkGray
    Get-Content -LiteralPath $reportFile | ForEach-Object { Write-Host $_ }
    Write-Host '-----' -ForegroundColor DarkGray
}
else {
    Write-Warning "Self-check report not found at $reportFile"
}
if ($selfCheckExit -ne 0) { throw "Self-check failed (exit code $selfCheckExit). See $reportFile" }

# 11. Zip ---------------------------------------------------------------------------------------
Write-Host ''
Write-Host '==> Create zip' -ForegroundColor Cyan
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -LiteralPath $appDir -DestinationPath $zipPath -CompressionLevel Optimal

# 12. Report ------------------------------------------------------------------------------------
$overall.Stop()
$exeHash = (Get-FileHash -LiteralPath $exePath -Algorithm SHA256).Hash
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
Write-Host ''
Write-Host 'Build summary' -ForegroundColor Green
Write-Host ("  Executable      : {0}" -f $exePath)
Write-Host ("  Exe size        : {0}" -f (Format-Size (Get-Item -LiteralPath $exePath).Length))
Write-Host ("  Folder size     : {0}" -f (Format-Size (Get-FolderSize $appDir)))
Write-Host ("  Zip             : {0}" -f $zipPath)
Write-Host ("  Zip size        : {0}" -f (Format-Size (Get-Item -LiteralPath $zipPath).Length))
Write-Host ("  PyInstaller time: {0:N1} s" -f $pyiSw.Elapsed.TotalSeconds)
Write-Host ("  Total build time: {0:N1} s" -f $overall.Elapsed.TotalSeconds)
Write-Host ("  SHA-256 exe     : {0}" -f $exeHash)
Write-Host ("  SHA-256 zip     : {0}" -f $zipHash)
if ($SignCertThumbprint) { Write-Host '  Signed          : yes' } else { Write-Host '  Signed          : no (unsigned build)' }
