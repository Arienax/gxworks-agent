param(
    [string]$Workspace = "",
    [int]$Port = 0,
    [switch]$ReadOnly,
    [switch]$NoBrowser,
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$applicationRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$interactiveSelection = [string]::IsNullOrWhiteSpace($Workspace)

try {
    Write-Host "GXWorks Agent · 本地 Web 工作台" -ForegroundColor Cyan
    Write-Host "启动工作台不会启动 GX Works2、仿真网关或操作 PLC。"
    if ($interactiveSelection) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            $folder = New-Object System.Windows.Forms.FolderBrowserDialog
            try {
                $folder.Description = "选择工作区外层文件夹（已有工作区含 index.json 和 projects；新工作区可选空文件夹）"
                $folder.ShowNewFolderButton = $true
                if ($folder.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
                    Write-Host "已取消，未启动工作台。"
                    exit 0
                }
                $Workspace = $folder.SelectedPath
            } finally {
                $folder.Dispose()
            }
        } catch {
            $Workspace = Read-Host "请输入工作区文件夹完整路径（直接回车取消）"
            if ([string]::IsNullOrWhiteSpace($Workspace)) { exit 0 }
        }

    }

    $resolvedWorkspace = [IO.Path]::GetFullPath($Workspace.Trim().Trim('"'))
    if ((Test-Path -LiteralPath $resolvedWorkspace) -and -not (Test-Path -LiteralPath $resolvedWorkspace -PathType Container)) {
        throw "工作区必须是文件夹，不能选择单个文件。"
    }
    if (Test-Path -LiteralPath (Join-Path $resolvedWorkspace "project.json") -PathType Leaf) {
        throw "选中了单个项目目录。请重新选择含 index.json 和 projects 的工作区外层目录。"
    }
    if ($Port -ne 0 -and ($Port -lt 1024 -or $Port -gt 65535)) {
        throw "端口必须在 1024 到 65535 之间。"
    }
    $requestedPort = $(if ($Port -eq 0) { 8765 } else { $Port })
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $requestedPort)
    try {
        try {
            $listener.Start()
        } catch {
            if ($Port -ne 0) { throw "指定端口正在使用。请关闭先前的服务，或选择另一个端口。" }
            $listener.Stop()
            $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
            $listener.Start()
        }
        $selectedPort = $listener.LocalEndpoint.Port
    } finally {
        $listener.Stop()
    }

    $releaseExecutable = Join-Path $applicationRoot "GXWorks-Agent-Web.exe"
    $sourcePython = Join-Path $applicationRoot ".venv\Scripts\python.exe"
    $sourceEntry = Join-Path $applicationRoot "src\integrations\web\__main__.py"
    $builtPackageExecutable = Join-Path $applicationRoot "dist\GXWorks-Agent-Web\GXWorks-Agent-Web.exe"
    $hasSourceRuntime = Test-Path -LiteralPath $sourcePython -PathType Leaf
    $isSourceCheckout = Test-Path -LiteralPath $sourceEntry -PathType Leaf
    $hasReleaseExecutable = Test-Path -LiteralPath $releaseExecutable -PathType Leaf
    $hasBuiltPackage = Test-Path -LiteralPath $builtPackageExecutable -PathType Leaf

    # A repository/source checkout can also contain an older packaged executable.
    # Never silently fall back to that executable: a frozen backend serves the
    # web assets bundled inside its own _MEIPASS directory, so rebuilding the
    # checkout's web/dist would otherwise appear to have no effect.
    if ($hasSourceRuntime) {
        $backend = $sourcePython
        $backendArgs = @("-m", "integrations.web")
        $backendKind = "source"
        $staticIndex = Join-Path $applicationRoot "web\dist\index.html"
        if ($hasReleaseExecutable) {
            Write-Host "[INFO] 检测到 GXWorks-Agent-Web.exe；当前目录存在源码 .venv，因此优先启动源码后端。" -ForegroundColor DarkGray
        }
    } elseif ($isSourceCheckout) {
        throw "检测到源码目录，但缺少 .venv\Scripts\python.exe。请运行根目录 build-web.bat（不要使用 --frontend-only）后再启动；不会回退到可能过期的 GXWorks-Agent-Web.exe。"
    } elseif ($hasReleaseExecutable) {
        $backend = $releaseExecutable
        $backendArgs = @()
        $backendKind = "release"
        $staticIndex = Join-Path $applicationRoot "web\dist\index.html"
    } elseif ($hasBuiltPackage) {
        $backend = $builtPackageExecutable
        $backendArgs = @()
        $backendKind = "built-package"
        $staticIndex = Join-Path (Split-Path -Parent $builtPackageExecutable) "web\dist\index.html"
    } else {
        throw "源码运行环境和本地构建包都不存在。请先运行根目录 build-web.bat；若要生成独立发布包，请运行 scripts\build_web_package.ps1。"
    }
    if (-not (Test-Path -LiteralPath $staticIndex -PathType Leaf)) {
        throw "缺少网页资源。源码模式请先运行 build-web.bat；发布包请保持完整目录结构。"
    }
    $backendArgs += @("--workspace", $resolvedWorkspace, "--port", [string]$selectedPort)
    if ($ReadOnly) { $backendArgs += "--read-only" }
    if (-not $NoBrowser) { $backendArgs += "--open-browser" }

    Write-Host ("工作区：" + $resolvedWorkspace)
    Write-Host ("后端：" + $backendKind)
    Write-Host ("模式：" + $(if ($ReadOnly) { "只读浏览" } else { "自动保存；审批模式在网页设置中调整" }))
    if ($ValidateOnly) {
        Write-Output (@{ validated = $true; backend = $backendKind; workspace = $resolvedWorkspace; port = $selectedPort; read_only = [bool]$ReadOnly; browser_requested = -not [bool]$NoBrowser; service_started = $false } | ConvertTo-Json -Compress)
        exit 0
    }
    if ($NoBrowser) {
        Write-Host "请使用下方的 Workbench link 本地链接登录。"
    } else {
        Write-Host "服务准备好后将打开浏览器。若没有自动打开，请使用下方的 Workbench link 本地链接。"
    }
    Write-Host "请保持此窗口打开；浏览器关闭后服务仍在运行。完成任务后按 Ctrl+C 正常停止。"
    Write-Host "登录链接只供本机用户使用，请勿分享。" -ForegroundColor Yellow
    $previousPythonPath = [Environment]::GetEnvironmentVariable("PYTHONPATH", "Process")
    try {
        if ($backendKind -eq "source") { $env:PYTHONPATH = Join-Path $applicationRoot "src" }
        & $backend @backendArgs
        $resultCode = $LASTEXITCODE
    } finally {
        [Environment]::SetEnvironmentVariable("PYTHONPATH", $previousPythonPath, "Process")
    }
    if ($resultCode -ne 0) {
        throw "工作台未能正常启动或已异常停止。请查看上方提示；若工作区被占用，请先正常关闭使用它的 Qt 或 Web 服务。"
    }
    Write-Host "工作台服务已停止。"
    exit 0
} catch {
    Write-Host ("无法启动：" + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
