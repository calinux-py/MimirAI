param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

if ($Clean) {
    Remove-Item -LiteralPath "$root\build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "$root\dist" -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
& $python -m pip install pyinstaller
& $python -m PyInstaller .\Mimir.spec --noconfirm

Write-Host "Built dist\Mimir.exe"
