#Requires -Version 7.0
# Use ArgumentList on every PowerShell 7 version, independent of native quoting modes.
$ErrorActionPreference = 'Stop'
try {
    if ($PWD.Provider.Name -ne 'FileSystem') { throw 'Run sandbox.ps1 from a filesystem directory.' }
    $candidates = if ($env:SANDBOX_PYTHON) { @($env:SANDBOX_PYTHON) } else { @('py', 'python3', 'python') }
    $python = $null
    $prefix = @()
    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $command) { continue }
        $candidatePrefix = if ($command.Name -match '^py(\.exe)?$') { @('-3') } else { @() }
        $probe = [System.Diagnostics.ProcessStartInfo]::new()
        $probe.FileName = $command.Source
        $probe.UseShellExecute = $false
        $probe.RedirectStandardOutput = $true
        $probe.RedirectStandardError = $true
        foreach ($argument in ($candidatePrefix + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'))) {
            $probe.ArgumentList.Add($argument)
        }
        $process = [System.Diagnostics.Process]::Start($probe)
        if (-not $process.WaitForExit(5000)) { $process.Kill(); $process.Dispose(); continue }
        $valid = $process.ExitCode -eq 0
        $process.Dispose()
        if ($valid) { $python = $command.Source; $prefix = $candidatePrefix; break }
    }
    if (-not $python) { throw 'Install Python 3.9+ or set SANDBOX_PYTHON to its executable path.' }
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $python
    $start.UseShellExecute = $false
    $start.WorkingDirectory = $PWD.ProviderPath
    foreach ($argument in ($prefix + @('-B', '-X', 'utf8', (Join-Path $PSScriptRoot 'src/host/windows_cli.py')))) {
        $start.ArgumentList.Add($argument)
    }
    foreach ($argument in $args) { $start.ArgumentList.Add([string]$argument) }
    $process = [System.Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    $result = $process.ExitCode
    $process.Dispose()
    exit $result
} catch {
    [Console]::Error.WriteLine("Error: $($_.Exception.Message)")
    exit 1
}
