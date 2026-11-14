# Copyright 2026 Arienax
# Licensed under the GNU Affero General Public License v3.0. See LICENSE for the license terms.

param(
    [string]$Python = "python",
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$ResourceRoot = ""
)
$ErrorActionPreference = "Stop"
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($ResourceRoot)) { $ResourceRoot = $repositoryRoot }
& $Python (Join-Path $PSScriptRoot "build_release.py") --component full --output $OutputDirectory --resource-root $ResourceRoot
if ($LASTEXITCODE -ne 0) { throw "Executable package build failed." }
