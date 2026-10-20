param([string]$OutputDirectory = "")
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) "PLC AI Studio\native-active"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The active-document adapter must be built outside the project directory."
}
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) { throw "A .NET Framework C# compiler was not found." }
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$executable = Join-Path $resolvedOutput "PlcAi.ActiveDocument.exe"
& $compiler /nologo /target:exe /platform:x86 /optimize+ "/out:$executable" /reference:System.Web.Extensions.dll `
    (Join-Path $repositoryRoot "native_adapters\ActiveDocument.cs") `
    (Join-Path $repositoryRoot "native_adapters\NativeUiLease.cs")
if ($LASTEXITCODE -ne 0) { throw "Active-document adapter compilation failed with exit code $LASTEXITCODE." }
Write-Output $executable
