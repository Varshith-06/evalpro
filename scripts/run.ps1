# EvalPro - start the platform on Windows.
#
#   .\scripts\run.ps1            start the server (seeds the demo course on first run)
#   .\scripts\run.ps1 -Test      run the test suite
#   .\scripts\run.ps1 -Demo      run the narrative walkthrough
#   .\scripts\run.ps1 -Deck      regenerate the SIH presentation from the template
#   .\scripts\run.ps1 -Reset     drop the demo database so it rebuilds from scratch

param(
    [switch]$Test,
    [switch]$Demo,
    [switch]$Deck,
    [switch]$Reset,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo "backend"

Write-Host "EvalPro - Automated Programming Lab Evaluation Platform" -ForegroundColor Cyan

if ($Reset) {
    $var = Join-Path $backend "var"
    if (Test-Path $var) {
        Remove-Item -Recurse -Force $var
        Write-Host "Removed $var - the demo course will rebuild on next start."
    }
    if (-not ($Test -or $Demo -or $Deck)) { return }
}

Push-Location $backend
try {
    python -m pip install -q -r requirements.txt

    if ($Test) {
        python -m pip install -q -r requirements-dev.txt
        python -m pytest
        return
    }
    if ($Demo) {
        python (Join-Path $repo "scripts\demo.py")
        return
    }
    if ($Deck) {
        python -m pip install -q python-pptx
        python (Join-Path $repo "scripts\build_presentation.py")
        return
    }

    Write-Host "Starting on http://127.0.0.1:$Port" -ForegroundColor Green
    Write-Host "First run builds the demo course by grading ~90 submissions through the real"
    Write-Host "cascade in the real sandbox. That takes about 80 seconds and is the point."
    python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
