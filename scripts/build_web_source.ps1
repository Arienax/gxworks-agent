param(
    [switch]$FrontendOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$web = Join-Path $root "web"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$requirements = Join-Path $root "requirements\web.txt"
$log = Join-Path $root "build-web.log"

function Write-Step([string]$Text) {
    Write-Host $Text -ForegroundColor Cyan
}

$transcriptStarted = $false
try {
    try {
        Start-Transcript -Path $log -Force | Out-Null
        $transcriptStarted = $true
    } catch {
        # The visible console remains authoritative if transcription is unavailable.
        Write-Host ("[WARN] Could not start build transcript: " + $_.Exception.Message) -ForegroundColor Yellow
    }

    Write-Host "=============================================="
    Write-Host "  GXWorks Agent - Web source build"
    Write-Host "=============================================="
    Write-Host ("Root: " + $root)

    if (-not $FrontendOnly) {
        if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
            throw "Missing requirements\web.txt."
        }

        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            Write-Step "[1/4] Creating Python Web environment in .venv ..."
            $bootstrapExe = $null
            $bootstrapPrefix = @()

            $py = Get-Command py.exe -ErrorAction SilentlyContinue
            if ($py) {
                & $py.Source -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
                if ($LASTEXITCODE -eq 0) {
                    $bootstrapExe = $py.Source
                    $bootstrapPrefix = @("-3")
                }
            }
            if (-not $bootstrapExe) {
                $python = Get-Command python.exe -ErrorAction SilentlyContinue
                if ($python) {
                    & $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
                    if ($LASTEXITCODE -eq 0) {
                        $bootstrapExe = $python.Source
                        $bootstrapPrefix = @()
                    }
                }
            }
            if (-not $bootstrapExe) {
                throw "Python 3.10+ was not found in PATH. Install Python 3.10 or newer and rerun build-web.bat."
            }

            if ($bootstrapPrefix.Count -gt 0) {
                & $bootstrapExe @bootstrapPrefix -m venv (Join-Path $root ".venv")
            } else {
                & $bootstrapExe -m venv (Join-Path $root ".venv")
            }
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
                throw "Failed to create .venv."
            }
        } else {
            Write-Step "[1/4] Using existing Python Web environment: .venv"
        }

        & $venvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)"
        if ($LASTEXITCODE -ne 0) {
            throw "Existing .venv uses Python older than 3.10. Remove .venv and rerun build-web.bat."
        }

        Write-Host "      Installing/updating Web and MCP runtime dependencies ..."
        & $venvPython -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) { throw "Failed to install requirements\web.txt into .venv." }

        & $venvPython -c "import fastapi, uvicorn, openai, numpy, mcp"
        if ($LASTEXITCODE -ne 0) { throw "Web runtime dependency verification failed." }
        $previousPythonPath = $env:PYTHONPATH
        try {
            $env:PYTHONPATH = Join-Path $root "src"
            & $venvPython (Join-Path $root "scripts\web_entry.py") --self-test-knowledge
            if ($LASTEXITCODE -ne 0) { throw "Knowledge runtime verification failed in .venv. Check requirements\context.txt and bundled SQLite resources." }
        } finally { $env:PYTHONPATH = $previousPythonPath }
    } else {
        Write-Step "[1/4] Skipping Python setup (--frontend-only)."
    }

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node) { throw "Node.js was not found in PATH." }
    if (-not $npm) { throw "npm.cmd was not found in PATH." }
    if (-not (Test-Path -LiteralPath (Join-Path $web "package-lock.json") -PathType Leaf)) {
        throw "Missing web\package-lock.json."
    }
    Write-Host ("Node: " + (& $node.Source --version))

    Push-Location $web
    try {
        Write-Step "[2/4] Installing locked frontend dependencies ..."
        & $npm.Source ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }

        Write-Step "[3/4] Regenerating TypeScript API types from web\openapi.json ..."
        & $npm.Source run types
        if ($LASTEXITCODE -ne 0) { throw "npm run types failed." }

        Write-Step "[4/4] Type-checking and building the Vite frontend ..."
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed." }
    } finally {
        Pop-Location
    }

    if (-not (Test-Path -LiteralPath (Join-Path $web "dist\index.html") -PathType Leaf)) {
        throw "Frontend build reported success but web\dist\index.html is missing."
    }
    if (-not $FrontendOnly -and -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "Source runtime setup finished without .venv\Scripts\python.exe."
    }

    Write-Host ""
    Write-Host "[OK] Frontend build completed: web\dist" -ForegroundColor Green
    if (-not $FrontendOnly) {
        Write-Host "[OK] Source Web runtime is ready. You can now run start-web.cmd." -ForegroundColor Green
    }
    $exitCode = 0
} catch {
    Write-Host ""
    Write-Host ("[ERROR] " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ("Detailed log: " + $log) -ForegroundColor Yellow
    $exitCode = 1
} finally {
    if ($transcriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
}
exit $exitCode
