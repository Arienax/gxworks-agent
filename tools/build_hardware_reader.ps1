param(
    [string]$OutputDirectory = "",
    [ValidateSet("x86", "x64")][string]$Platform = "x86"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $localDataRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $OutputDirectory = Join-Path $localDataRoot "PLC AI Studio\hardware-reader"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Build the hardware reader outside the engineering repository."
}
$compiler = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $compiler)) { throw "A .NET Framework C# compiler was not found." }
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$source = Join-Path $repositoryRoot "hardware_reader\Program.cs"
$contractSource = Join-Path $repositoryRoot "native_adapters\NativeRequest.cs"
$executable = Join-Path $resolvedOutput "PlcAi.HardwareReader.exe"
& $compiler /nologo /target:exe "/platform:$Platform" /optimize+ "/out:$executable" /reference:System.Web.Extensions.dll /reference:Microsoft.CSharp.dll $source $contractSource
if ($LASTEXITCODE -ne 0) { throw "Hardware reader compilation failed." }
Write-Output $executable
