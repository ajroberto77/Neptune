<#
  Neptune launcher (PowerShell). Run with:  .\start.ps1
  Uses the project's .venv directly — no activation required — so PowerShell's
  execution policy never gets in the way. Bootstraps the venv + deps on first run.
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "[setup] creating .venv ..." -ForegroundColor Cyan
    python -m venv .venv
}

# Install deps only if a core import is missing (fast no-op on later runs).
& $py -c "import fastapi, sqlalchemy, uvicorn, numpy, cvxpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[setup] installing dependencies (first run) ..." -ForegroundColor Cyan
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt
}

Write-Host "[run] Neptune -> UI http://localhost:5173   API http://localhost:8000" -ForegroundColor Green
& $py run.py
