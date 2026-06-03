$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $root "RomesteadLiveBridge.cs"
$output = Join-Path $root "RomesteadLiveBridge.dll"

if (-not (Test-Path $source)) {
    throw "Missing source file: $source"
}

Remove-Item $output -ErrorAction SilentlyContinue

Add-Type `
    -Path $source `
    -OutputAssembly $output `
    -OutputType Library `
    -ReferencedAssemblies @("System.dll", "System.Core.dll")

Write-Host "Built $output"
