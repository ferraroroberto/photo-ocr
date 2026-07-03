# Pre-ship verification gate (issue #8).
#
# Runs the full validation pipeline locally before a webapp-touching
# change is declared "done": byte-compile, the non-e2e pytest suite,
# then the Playwright e2e suite (Chromium + WebKit/iPhone projections)
# against a disposable webapp the script boots itself on a free port.
#
# Usage:
#   powershell.exe -File scripts/verify-before-ship.ps1   # Windows PowerShell 5.1 (agent-facing default)
#   pwsh -File scripts/verify-before-ship.ps1              # PowerShell 7, if installed; do not spawn from an agent (PATH alias can fail non-interactively)
#
# A tray on :8444 may be running or not — autoboot picks a free port for
# its own disposable webapp and never touches the running tray. The
# disposable server is torn down by the e2e fixture, so the script is
# re-runnable with no manual cleanup. Exits non-zero on the first
# failure with the offending output left visible.

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$sw = [System.Diagnostics.Stopwatch]::StartNew()

function Fail($message) {
    Write-Host ""
    Write-Host "[X] $message" -ForegroundColor Red
    Write-Host ("Failed after {0:n1}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $python)) {
    Fail ".venv missing -- run setup.bat first."
}

Push-Location $repoRoot
try {
    Write-Host "==> byte-compile (app, src, tests)..." -ForegroundColor Cyan
    & $python -m compileall -q app src tests
    if ($LASTEXITCODE -ne 0) { Fail "byte-compile failed." }

    Write-Host "==> pytest (non-e2e)..." -ForegroundColor Cyan
    & $python -m pytest -q --ignore=tests/e2e
    if ($LASTEXITCODE -ne 0) { Fail "non-e2e pytest suite failed." }

    Write-Host "==> pytest e2e (Chromium + WebKit/iPhone, auto-booted)..." -ForegroundColor Cyan
    $env:PHOTO_OCR_E2E_AUTOBOOT = "1"
    try {
        & $python -m pytest tests/e2e -q --browser chromium --browser webkit
        $e2eExit = $LASTEXITCODE
    }
    finally {
        Remove-Item Env:\PHOTO_OCR_E2E_AUTOBOOT -ErrorAction SilentlyContinue
    }
    if ($e2eExit -ne 0) { Fail "Playwright e2e suite failed." }
}
finally {
    Pop-Location
}

$sw.Stop()
Write-Host ""
Write-Host ("[OK] Ready to ship -- all checks passed in {0:n1}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
exit 0
