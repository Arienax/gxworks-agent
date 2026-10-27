# Copyright 2026 Arienax
# Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

param([string]$OutputDirectory = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) "PLC AI Studio\workspace-adapter"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The native workspace adapter must be built outside the project directory."
}
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) { throw "A .NET Framework C# compiler was not found." }
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$executable = Join-Path $resolvedOutput "PlcAi.WorkspaceSourceSave.exe"
& $compiler /nologo /target:exe /platform:x86 /optimize+ "/out:$executable" `
    "/win32manifest:$(Join-Path $repositoryRoot 'native_adapters\WorkspaceSourceSave.manifest')" /reference:System.Web.Extensions.dll `
    /reference:System.Windows.Forms.dll `
    (Join-Path $repositoryRoot "native_adapters\WorkspaceSourceSave.cs") `
    (Join-Path $repositoryRoot "native_adapters\WorkspaceValidation.cs") `
    (Join-Path $repositoryRoot "native_adapters\WorkspaceTasks.cs") (Join-Path $repositoryRoot "native_adapters\WorkspaceSfcGraph.cs") `
    (Join-Path $repositoryRoot "native_adapters\NativeRequest.cs") (Join-Path $repositoryRoot "native_adapters\WorkspaceBusinessProtocol.cs") `
    (Join-Path $repositoryRoot "native_adapters\NativeExceptionTrace.cs")
if ($LASTEXITCODE -ne 0) { throw "Native workspace adapter compilation failed with exit code $LASTEXITCODE." }
Write-Output $executable
