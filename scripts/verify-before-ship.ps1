# Pre-ship verification gate.
#
# Runs the full validation pipeline locally before a webapp-touching
# change is declared "done": byte-compile, the static ratchet and the other ratchets, the non-e2e pytest suite,
# then the Playwright e2e suite (Chromium + WebKit/iPhone projections)
# against a disposable webapp the script boots itself on a free port. The e2e
# leg is routed on the branch diff by scripts/classify_e2e.py (skip / static
# slice / full; any doubt runs full) -- see the e2e routing block below.
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

    # Static ratchet: fails on a duplicate block or unused import/variable that
    # is not in .static-ratchet-baseline.json. The wrapper maps every non-zero
    # exit, exit 3 (a detector could not run) included, to a failure -- never a
    # skip. It reads the whole tree, so it is not path-routed.
    Write-Host "==> static ratchet..." -ForegroundColor Cyan
    & $python -m scripts.static_ratchet_gate
    if ($LASTEXITCODE -ne 0) { Fail "static ratchet failed (new finding, or a detector could not run)." }

    # Docs ratchet: fails when the root README grows or a README/docs page gains an
    # issue reference, dated heading or changelog heading beyond
    # .docs-ratchet-baseline.json. The wrapper maps every non-zero exit, exit 3 (a doc
    # could not be read) included, to a failure -- never a skip. It counts the whole
    # tree, so it is not path-routed.
    Write-Host "==> docs ratchet..." -ForegroundColor Cyan
    & $python -m scripts.docs_ratchet_gate
    if ($LASTEXITCODE -ne 0) { Fail "docs ratchet failed (README grew, a new history marker, or a doc could not be read)." }

    # Structure ratchet: fails on a file, function or directory over its limit, a
    # src/ module importing app/, or a root folder outside the declared shape, beyond
    # .structure-ratchet-baseline.json. Exit 3 (a file could not be measured, or an
    # unknown shape) is its own failure, never a pass or a skip. It reads the whole
    # tree, so it is not path-routed.
    Write-Host "==> structure ratchet..." -ForegroundColor Cyan
    & $python "scripts/structure_ratchet.py"
    $structureExit = $LASTEXITCODE
    if ($structureExit -eq 3) { Fail "structure ratchet NOT CONFIRMED (a file could not be measured, or the shape is unknown)." }
    if ($structureExit -ne 0) { Fail "structure ratchet failed (a new or grown offender, a root folder outside the shape, or a bad table or baseline)." }

    # Conventions ratchet: fails when a file's hit count for an enabled rule (no-window,
    # logging-not-print, no-drive-path) rises above .conventions-ratchet-baseline.json.
    # Exit 3 (git unusable, or a .py file that cannot be read or parsed) is its own
    # failure, never a pass or a skip. It reads the whole tree, so it is not path-routed.
    Write-Host "==> conventions ratchet..." -ForegroundColor Cyan
    & $python "scripts/conventions_ratchet.py"
    $conventionsExit = $LASTEXITCODE
    if ($conventionsExit -eq 3) { Fail "conventions ratchet NOT CONFIRMED (git unusable, or a .py file could not be read or parsed)." }
    if ($conventionsExit -ne 0) { Fail "conventions ratchet failed (a rule count rose, or a bad table or baseline)." }

    # Comment ratchet: fails when a file's count of #NNN issue references inside
    # product-code comments rises above .comment-ratchet-baseline.json. Exit 3 (git
    # unusable, or a file that cannot be scanned) is its own failure, never a pass or a
    # skip. It reads the whole tree, so it is not path-routed.
    Write-Host "==> comment ratchet..." -ForegroundColor Cyan
    & $python "scripts/comment_ratchet.py"
    $commentExit = $LASTEXITCODE
    if ($commentExit -eq 3) { Fail "comment ratchet NOT CONFIRMED (git unusable, or a file could not be scanned)." }
    if ($commentExit -ne 0) { Fail "comment ratchet failed (an issue reference was added to a comment, or a bad table or baseline)." }

    # Instruction ratchet: fails when a file's count for a lint-tier prompt-audit rule
    # rises above .instruction-ratchet-baseline.json, or an instruction file passes its
    # size cap. Exit 3 (git unusable, or a file that cannot be read) is its own failure,
    # never a pass or a skip. A reader of CLAUDE.md depends on AGENTS.md, so it is not
    # path-routed.
    Write-Host "==> instruction ratchet..." -ForegroundColor Cyan
    & $python "scripts/instruction_ratchet.py"
    $instructionExit = $LASTEXITCODE
    if ($instructionExit -eq 3) { Fail "instruction ratchet NOT CONFIRMED (git unusable, or an instruction file could not be read)." }
    if ($instructionExit -ne 0) { Fail "instruction ratchet failed (a rule count rose, a file passed its size cap, or a bad table or baseline)." }

    # Doc refs ratchet: fails on a dangling repo path, python -m module, link or loopback
    # port in README.md, CLAUDE.md or docs/ that .doc-refs-ratchet-baseline.json does not
    # record. Exit 3 (git unusable, a doc that is not UTF-8, an unclosed fence) is its own
    # failure, never a pass or a skip. A code change can strand a doc reference without
    # touching the doc, so it is not path-routed.
    Write-Host "==> doc refs ratchet..." -ForegroundColor Cyan
    & $python "scripts/doc_refs_ratchet.py"
    $docRefsExit = $LASTEXITCODE
    if ($docRefsExit -eq 3) { Fail "doc refs ratchet NOT CONFIRMED (git unusable, or a doc could not be read)." }
    if ($docRefsExit -ne 0) { Fail "doc refs ratchet failed (a doc names a path, module or port that does not exist, or a bad table or baseline)." }

    Write-Host "==> pytest (non-e2e)..." -ForegroundColor Cyan
    & $python -m pytest -q --ignore=tests/e2e
    if ($LASTEXITCODE -ne 0) { Fail "non-e2e pytest suite failed." }

    # ------------------------------------------------------------ e2e routing
    # Diff-proportionate e2e: scripts/classify_e2e.py (vendored, rules
    # in .fleet.toml [e2e]) maps this branch's changed files vs origin/main to
    # a tier. skip -> no browser suite, with the tier and reason printed;
    # static -> the declared smoke slice; full -> the whole suite, as before.
    # Anything the gate cannot positively read as skip/static/full -- a
    # classifier that errors or prints no tier, an unknown tier -- runs the
    # full suite and says why: uncertainty never narrows. On CI the full
    # suite always runs.
    $tier = "full"
    $e2eTarget = "tests/e2e"
    $e2eBrowsers = @("chromium", "webkit")
    $routeNote = ""
    $reason = ""
    if ($env:CI -eq "true") {
        $routeNote = "CI always runs the full e2e suite"
    }
    else {
        $kv = @{}
        $classifyError = ""
        try {
            $classifyOut = & $python (Join-Path $repoRoot "scripts\classify_e2e.py")
            if ($LASTEXITCODE -ne 0) {
                $classifyError = "classify_e2e.py exited $LASTEXITCODE"
            }
            else {
                foreach ($line in $classifyOut) {
                    if ($line -match '^(E2E_[A-Z_]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2] }
                }
            }
        }
        catch {
            $classifyError = "classify_e2e.py could not run: $($_.Exception.Message)"
        }
        if ($classifyError) {
            $routeNote = "e2e classifier error ($classifyError) -- running the full suite (fail-safe)"
        }
        elseif (-not $kv.ContainsKey("E2E_TIER") -or -not $kv["E2E_TIER"]) {
            $routeNote = "e2e classifier printed no E2E_TIER -- running the full suite (fail-safe)"
        }
        else {
            $reason = $kv["E2E_REASON"]
            switch ($kv["E2E_TIER"]) {
                "skip" { $tier = "skip" }
                "full" { }
                "static" {
                    if ($kv["E2E_PYTEST_TARGET"]) {
                        $tier = "static"
                        $e2eTarget = $kv["E2E_PYTEST_TARGET"]
                        $slice = @($kv["E2E_BROWSERS"] -split "," | Where-Object { $_ })
                        if ($slice.Count -gt 0) { $e2eBrowsers = $slice }
                    }
                    else {
                        $routeNote = "e2e classifier gave a static tier with no target -- running the full suite (fail-safe)"
                    }
                }
                default {
                    $routeNote = "e2e classifier returned unknown tier '$($kv["E2E_TIER"])' -- running the full suite (fail-safe)"
                }
            }
        }
    }

    if ($tier -eq "skip") {
        Write-Host "==> pytest e2e: SKIPPED (tier=skip) -- no browser surface in this diff" -ForegroundColor Yellow
        Write-Host "    reason: $reason" -ForegroundColor DarkGray
    }
    else {
        $label = if ($tier -eq "static") { "static slice $e2eTarget" } else { "full suite" }
        Write-Host "==> pytest e2e ($label; $($e2eBrowsers -join ' + '), auto-booted)..." -ForegroundColor Cyan
        if ($routeNote) { Write-Host "    $routeNote" -ForegroundColor Yellow }
        elseif ($reason) { Write-Host "    tier=$tier reason: $reason" -ForegroundColor DarkGray }
        $e2eArgs = @($e2eTarget -split "\s+" | Where-Object { $_ })
        foreach ($b in $e2eBrowsers) { $e2eArgs += @("--browser", $b) }
        $e2eArgs += @("-q", "--junitxml=webapp/e2e-junit.xml")
        $env:PHOTO_OCR_E2E_AUTOBOOT = "1"
        try {
            & $python -m pytest @e2eArgs
            $e2eExit = $LASTEXITCODE
        }
        finally {
            Remove-Item Env:\PHOTO_OCR_E2E_AUTOBOOT -ErrorAction SilentlyContinue
        }
        if ($e2eExit -ne 0) { Fail "Playwright e2e suite failed." }
    }
}
finally {
    Pop-Location
}

$sw.Stop()
Write-Host ""
Write-Host ("[OK] Ready to ship -- all checks passed in {0:n1}s." -f $sw.Elapsed.TotalSeconds) -ForegroundColor Green
exit 0
