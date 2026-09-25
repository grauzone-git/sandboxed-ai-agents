# Shared checksum and raw registry PATH handling for package installation/removal.
$script:PackageArtifact = 'sandboxed-agents-windows-amd64.exe'

function Assert-PackagePathUpdatePlatform {
    param([switch]$NoPathUpdate, [string]$Operation)
    if (-not $NoPathUpdate -and [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "Use -NoPathUpdate when testing $Operation outside Windows."
    }
}

function Get-PackageChecksum {
    param([string]$Directory)
    $artifact = $script:PackageArtifact
    $entries = @(Get-Content -LiteralPath (Join-Path $Directory 'SHA256SUMS') |
        Where-Object { $_ -match ('^[a-f0-9]{64}  ' + [regex]::Escape($artifact) + '$') })
    if ($entries.Count -ne 1) { throw "Missing or invalid checksum for $artifact" }
    return $entries[0].Substring(0, 64)
}

function Update-PackageUserPath {
    param([string]$Directory, [switch]$Remove,
          [Microsoft.Win32.RegistryKey]$RegistryKey)
    $ownedKey = $null -eq $RegistryKey
    if ($ownedKey) {
        $RegistryKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
        if ($null -eq $RegistryKey) {
            if ($Remove) { return }
            $RegistryKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
        }
    }
    try {
        $raw = $RegistryKey.GetValue('Path', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
        if ($null -ne $raw) { $kind = $RegistryKey.GetValueKind('Path') }
        $parts = @([string]$raw -split ';')
        $present = $parts -contains $Directory
        if ($Remove) {
            if (-not $present) { return }
            $updated = (@($parts | Where-Object { $_ -ne $Directory }) -join ';')
        } else {
            if ($present) { return }
            $separator = if ($raw) { ';' } else { '' }
            $updated = [string]$raw + $separator + $Directory
        }
        $RegistryKey.SetValue('Path', $updated, $kind)
        if ($ownedKey) {
            if (-not ('SandboxedAgentsEnvironmentNotify' -as [type])) {
                Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class SandboxedAgentsEnvironmentNotify {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(IntPtr window, uint message,
        UIntPtr parameter, string text, uint flags, uint timeout, out UIntPtr result);
}
'@
            }
            $result = [UIntPtr]::Zero
            [SandboxedAgentsEnvironmentNotify]::SendMessageTimeout([IntPtr]0xffff, 0x001a,
                [UIntPtr]::Zero, 'Environment', 2, 1000, [ref]$result) | Out-Null
        }
    } finally {
        if ($ownedKey) { $RegistryKey.Dispose() }
    }
}
