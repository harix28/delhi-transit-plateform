param([switch]$RealData, [int]$Port = 8000, [string]$HostName = "0.0.0.0")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$transitPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $transitPython)) {
    throw 'Create a Python virtual environment and install requirements.txt first. See README.md.'
}
if ($RealData) {
    $env:TRANSIT_CONFIG = Join-Path $PSScriptRoot 'config.yaml'
} else {
    if (!(Test-Path -LiteralPath 'data\demo\processed\crowding.parquet')) {
        & $transitPython scripts/create_demo.py
        if ($LASTEXITCODE -ne 0) { throw 'Demo setup failed.' }
    }
    $env:TRANSIT_CONFIG = Join-Path $PSScriptRoot 'config.demo.yaml'
}
& $transitPython -m uvicorn src.api.main:app --host $HostName --port $Port
