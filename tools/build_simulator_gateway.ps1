param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $localDataRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $OutputDirectory = Join-Path $localDataRoot "PLC AI Studio\simulator-gateway"
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
$repositoryPrefix = $repositoryRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.StartsWith($repositoryPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    $resolvedOutput.Equals($repositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "The simulator gateway must be built outside the project directory."
}

$compilerCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $compiler) {
    throw "A .NET Framework C# compiler was not found."
}

New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$sourcePath = Join-Path $repositoryRoot "simulator_gateway\Program.cs"
$contractSource = Join-Path $repositoryRoot "native_adapters\NativeRequest.cs"
$executablePath = Join-Path $resolvedOutput "PlcAi.GxSimulator2Gateway.exe"
& $compiler /nologo /target:exe /platform:x86 /optimize+ "/out:$executablePath" `
    /reference:System.Web.Extensions.dll /reference:Microsoft.CSharp.dll $sourcePath $contractSource
if ($LASTEXITCODE -ne 0) {
    throw "Simulator gateway compilation failed with exit code $LASTEXITCODE."
}

Write-Output $executablePath
