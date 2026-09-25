# NuGet extraction does not run installers; invoke this command explicitly.
[CmdletBinding()]
param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'Programs/sandboxed-agents'),
    [switch]$NoPathUpdate
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'package-functions.ps1')
try {
    Assert-PackagePathUpdatePlatform -NoPathUpdate:$NoPathUpdate -Operation 'this installer'
    $artifact = $script:PackageArtifact
    $expected = Get-PackageChecksum -Directory $PSScriptRoot
    $source = Join-Path $PSScriptRoot $artifact
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $expected) {
        throw "Checksum mismatch for $artifact"
    }
    $directory = [IO.Path]::GetFullPath($InstallDirectory)
    [IO.Directory]::CreateDirectory($directory) | Out-Null
    $temporary = Join-Path $directory ([IO.Path]::GetRandomFileName())
    try {
        Copy-Item -LiteralPath $source -Destination $temporary
        if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $expected) {
            throw "Checksum mismatch for copied $artifact"
        }
        Move-Item -LiteralPath $temporary -Destination (Join-Path $directory 'sandboxed-agents.exe') -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
    if (-not $NoPathUpdate) {
        Update-PackageUserPath -Directory $directory
        if (($env:Path -split ';') -notcontains $directory) { $env:Path += ";$directory" }
    }
    Write-Output "Installed sandboxed-agents.exe in $directory. Open a new terminal to refresh PATH."
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
