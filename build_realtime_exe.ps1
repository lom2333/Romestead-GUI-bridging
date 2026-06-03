$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$cecilDll = Join-Path $root "tools\Mono.Cecil.0.11.6\lib\net40\Mono.Cecil.dll"
if (-not (Test-Path $cecilDll)) {
    Write-Host "Mono.Cecil.dll is missing. Downloading Mono.Cecil 0.11.6 from NuGet..."
    $toolsDir = Join-Path $root "tools"
    New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
    $nupkg = Join-Path $toolsDir "Mono.Cecil.0.11.6.nupkg"
    $zip = Join-Path $toolsDir "Mono.Cecil.0.11.6.zip"
    $extractDir = Join-Path $toolsDir "Mono.Cecil.0.11.6"
    Invoke-WebRequest -Uri "https://www.nuget.org/api/v2/package/Mono.Cecil/0.11.6" -OutFile $nupkg
    Copy-Item -LiteralPath $nupkg -Destination $zip -Force
    if (Test-Path -LiteralPath $extractDir) {
        Remove-Item -LiteralPath $extractDir -Recurse -Force
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $extractDir -Force
}

if (-not (Test-Path (Join-Path $root "items_catalog.json"))) {
    Write-Host "items_catalog.json is missing. Creating an empty catalog. Replace it before building if you want full item names."
    Set-Content -LiteralPath (Join-Path $root "items_catalog.json") -Value "[]" -Encoding UTF8
}

Write-Host "Building RomesteadLiveBridge.dll..."
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "build_live_bridge.ps1")

Write-Host "Checking PyInstaller..."
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
python -m PyInstaller --version *> $null
$pyinstallerCheck = $LASTEXITCODE
$ErrorActionPreference = $oldErrorActionPreference
if ($pyinstallerCheck -ne 0) {
    Write-Host "PyInstaller is missing. Installing with pip..."
    python -m pip install --user pyinstaller
}

$addData = @(
    "items_catalog.json;.",
    "RomesteadLiveBridge.dll;.",
    "patch_romestead_bridge.ps1;.",
    "tools\Mono.Cecil.0.11.6\lib\net40\Mono.Cecil.dll;tools\Mono.Cecil.0.11.6\lib\net40"
)

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "RomesteadRealtimeInventory"
)

foreach ($item in $addData) {
    $args += "--add-data"
    $args += $item
}

$args += "romestead_live_inventory_gui.py"

Write-Host "Running PyInstaller..."
python @args

$exe = Join-Path $root "dist\RomesteadRealtimeInventory.exe"
if (-not (Test-Path $exe)) {
    throw "Build finished but EXE was not found: $exe"
}

Write-Host "Built: $exe"
Get-Item -LiteralPath $exe | Select-Object FullName,Length,LastWriteTime | Format-List
