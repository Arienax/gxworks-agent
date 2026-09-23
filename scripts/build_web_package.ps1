param(
    [string]$Python = "",
    [string]$NpmCommand = "npm.cmd",
    [string]$GatewayDirectory = "",
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')]
    [string]$StageName = "",
    [switch]$AllowWithoutGateway,
    [switch]$SkipInstall,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$distRoot = Join-Path $repositoryRoot "dist"
$buildRoot = Join-Path $repositoryRoot "build"
if (-not [string]::IsNullOrWhiteSpace($StageName)) {
    $distRoot = [IO.Path]::GetFullPath((Join-Path $distRoot ("staging\" + $StageName)))
    $buildRoot = [IO.Path]::GetFullPath((Join-Path $buildRoot ("staging\" + $StageName)))
    # PyInstaller may remove its COLLECT target. Keep staging inside this
    # repository and reject junctions/symlinks instead of following them.
    foreach ($buildPath in @($distRoot, $buildRoot)) {
        if (-not $buildPath.StartsWith($repositoryRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "The staging destination must stay inside this repository."
        }
        $ancestor = $buildPath
        while ($ancestor -and $ancestor -ne $repositoryRoot) {
            if (Test-Path -LiteralPath $ancestor) {
                if (((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "The staging destination must not traverse a junction or symbolic link."
                }
            }
            $ancestor = [IO.Path]::GetDirectoryName($ancestor)
        }
    }
    if (Test-Path -LiteralPath (Join-Path $distRoot "GXWorks-Agent-Web")) {
        throw "This staging package already exists. Choose a new StageName; existing packages are not replaced in staging mode."
    }
}
$packageDirectory = [IO.Path]::GetFullPath((Join-Path $distRoot "GXWorks-Agent-Web"))
$packageExecutable = [IO.Path]::GetFullPath((Join-Path $packageDirectory "GXWorks-Agent-Web.exe"))
$mcpExecutable = [IO.Path]::GetFullPath((Join-Path $packageDirectory "gxworks-agent-mcp.exe"))
if (-not $ValidateOnly) {
    $runningPackage = @(Get-Process -Name "GXWorks-Agent-Web" -ErrorAction SilentlyContinue | Where-Object {
        $_.Path -and [string]::Equals($_.Path, $packageExecutable, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($runningPackage.Count -gt 0) {
        throw "The destination Web executable is running. Use -StageName to build an isolated update; this script never stops a running service."
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $Python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Provide -Python with a Python 3.10+ environment containing requirements/web.txt and PyInstaller."
}
& $Python -c "import sys; assert sys.version_info >= (3, 10); import PyInstaller, fastapi, uvicorn, openai, numpy, mcp; import pythoncom, pywinauto"
if ($LASTEXITCODE -ne 0) { throw "Web/MCP packaging dependencies are missing from the selected environment." }

$resolvedGateway = ""
if (-not [string]::IsNullOrWhiteSpace($GatewayDirectory)) {
    $resolvedGateway = (Resolve-Path -LiteralPath $GatewayDirectory).Path
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedGateway "PlcAi.GxSimulator2Gateway.exe") -PathType Leaf)) {
        throw "GatewayDirectory must contain PlcAi.GxSimulator2Gateway.exe. The packaging script never downloads or starts the gateway."
    }
} elseif (-not $AllowWithoutGateway) {
    throw "Provide -GatewayDirectory for a complete package, or explicitly use -AllowWithoutGateway for a package without simulator support."
}

$requiredResources = @(
    "resources\config.default.json", "resources\pattern_library.json", "resources\plc_models.json",
    "resources\knowledge\fx3u_knowledge.sqlite", "resources\knowledge\fx3u_dense_lsa.npz",
    "resources\knowledge\manifest.json", "resources\knowledge\THIRD_PARTY_NOTICES.md",
    "resources\locales\en.json", "resources\locales\ja.json", "resources\app.ico", "LICENSE",
    "docs\integrations\web.md", "docs\integrations\mcp.md", "docs\architecture\web-migration-checklist.md",
    "start-web.cmd", "gxworks-agent-mcp.cmd", "scripts\start_web.ps1", "scripts\mcp_entry.py",
    "packaging\pyinstaller\mcp.spec"
)
foreach ($resource in $requiredResources) {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot $resource) -PathType Leaf)) {
        throw "Missing required release resource: $resource"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot "resources\instructions\mitsubishi") -PathType Container)) {
    throw "Missing Mitsubishi instruction resources."
}

Push-Location (Join-Path $repositoryRoot "web")
try {
    if (-not $ValidateOnly) {
        if (-not (Test-Path -LiteralPath "package-lock.json" -PathType Leaf)) {
            throw "Commit the front-end package-lock.json before making a reproducible release."
        }
        if (-not $SkipInstall) {
            & $NpmCommand ci
            if ($LASTEXITCODE -ne 0) { throw "Front-end dependency installation failed." }
        }
        & $NpmCommand run build
        if ($LASTEXITCODE -ne 0) { throw "Front-end build failed." }
    }
    if (-not (Test-Path -LiteralPath "dist\index.html" -PathType Leaf)) {
        throw "Built frontend is missing: web/dist/index.html."
    }
} finally {
    Pop-Location
}
if ($ValidateOnly) {
    Write-Output "Web release resources, MCP launcher resources and selected packaging dependencies are present. No executable was built or started."
    Write-Output ("Web package destination: " + $packageExecutable)
    Write-Output ("MCP launcher destination: " + $mcpExecutable)
    Write-Output ("Archive destination: " + (Join-Path $buildRoot "web\PYZ-00.pyz"))
    return
}

$previousGateway = [Environment]::GetEnvironmentVariable("GX_WEB_PACKAGE_GATEWAY_DIR", "Process")
$previousWithoutGateway = [Environment]::GetEnvironmentVariable("GX_WEB_PACKAGE_ALLOW_WITHOUT_GATEWAY", "Process")
try {
    $env:GX_WEB_PACKAGE_GATEWAY_DIR = $resolvedGateway
    $env:GX_WEB_PACKAGE_ALLOW_WITHOUT_GATEWAY = $(if ($AllowWithoutGateway) { "1" } else { "0" })
    Push-Location $repositoryRoot
    try {
        $webSpec = Join-Path $repositoryRoot "packaging\pyinstaller\web.spec"
        & $Python -m PyInstaller --noconfirm --distpath $distRoot --workpath $buildRoot $webSpec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller Web package build failed." }
        $mcpSpec = Join-Path $repositoryRoot "packaging\pyinstaller\mcp.spec"
        $mcpWork = Join-Path $buildRoot "mcp"
        & $Python -m PyInstaller --noconfirm --distpath $packageDirectory --workpath $mcpWork $mcpSpec
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller MCP launcher build failed." }
    } finally {
        Pop-Location
    }
} finally {
    [Environment]::SetEnvironmentVariable("GX_WEB_PACKAGE_GATEWAY_DIR", $previousGateway, "Process")
    [Environment]::SetEnvironmentVariable("GX_WEB_PACKAGE_ALLOW_WITHOUT_GATEWAY", $previousWithoutGateway, "Process")
}
if (-not (Test-Path -LiteralPath $mcpExecutable -PathType Leaf)) {
    throw "MCP product launcher was not produced: $mcpExecutable"
}
Copy-Item -LiteralPath (Join-Path $repositoryRoot "gxworks-agent-mcp.cmd") -Destination (Join-Path $packageDirectory "gxworks-agent-mcp.cmd") -Force
# Stage the same source version's documentation and inert reference copies.
& $Python (Join-Path $repositoryRoot "scripts/package_documentation.py") --root $repositoryRoot --destination $packageDirectory
if ($LASTEXITCODE -ne 0) { throw "Documentation staging failed." }
Write-Output $packageExecutable
Write-Output $mcpExecutable
if ($AllowWithoutGateway -and [string]::IsNullOrWhiteSpace($resolvedGateway)) {
    Write-Warning "This package does not include the Simulator2 gateway; real simulator acceptance has not been performed."
}
