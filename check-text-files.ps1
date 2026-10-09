<#
.SYNOPSIS
Recursively inspect text encodings, line endings, and invisible characters.
.EXAMPLE
.\check-text-files.ps1 -Path 'C:\work' -Output 'C:\reports\check.html'
.EXAMPLE
.\check-text-files.ps1 -Path 'C:\work' -Encoding cp932 -Force
.NOTES
Windows PowerShell 5.1 / PowerShell 7. No Python or third-party modules required.
Exit codes: 0 = clean, 1 = review needed, 2 = errors / incomplete checks.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Path,
    [string]$Output = 'text-check-report.html',
    [ValidateSet('auto', 'utf-8', 'utf-16le', 'utf-16be', 'utf-32le', 'utf-32be',
                 'cp932', 'euc-jp', 'iso-2022-jp', 'windows-1252', 'latin-1')]
    [string]$Encoding = 'auto',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
try {
    if (-not ('ControlCharChecker.Runner' -as [type])) {
        $sourcePath = Join-Path $PSScriptRoot 'lib\TextChecker.cs'
        $source = [System.IO.File]::ReadAllText($sourcePath, [System.Text.Encoding]::UTF8)
        Add-Type -TypeDefinition $source -Language CSharp
    }
    $rootPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
    $outputPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Output)
    $result = [ControlCharChecker.Runner]::Run(
        $rootPath, $outputPath, $Encoding.ToLowerInvariant(), $Force.IsPresent, $PSScriptRoot)
    exit $result
}
catch {
    [Console]::Error.WriteLine('ERROR: ' + $_.Exception.Message)
    exit 2
}
