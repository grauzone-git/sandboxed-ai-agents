# Remove only this package's binary and its user PATH entry.
[CmdletBinding()]
param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs/sandboxed-agents'),
    [switch]$NoPathUpdate
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'package-functions.ps1')
try {
    Assert-PackagePathUpdatePlatform -NoPathUpdate:$NoPathUpdate -Operation 'command removal'
    $directory = [IO.Path]::GetFullPath($InstallDirectory)
    $binary = Join-Path $directory 'sandboxed-agents.exe'
    if (Test-Path -LiteralPath $binary) {
        $expected = Get-PackageChecksum -Directory $PSScriptRoot
        if ((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash -ne $expected) {
            throw 'Installed command differs from this package. Use the matching package to remove it.'
        }
        Remove-Item -LiteralPath $binary
    }
    if (-not $NoPathUpdate) {
        Update-PackageUserPath -Directory $directory -Remove
        $env:Path = (@($env:Path -split ';' | Where-Object { $_ -ne $directory }) -join ';')
    }
    Write-Output 'Removed the package command. Sandbox data and SSH configuration were kept.'
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
