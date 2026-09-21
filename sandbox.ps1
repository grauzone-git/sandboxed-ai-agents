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
    # Interactive programs need the console handles for prompts and TTY detection.
    # Other commands must emit through PowerShell streams for assignment/pipelines.
    $interactive = $args.Count -gt 0 -and $args[0] -in @('shell', 'run', 'tool', 'copilot', 'claude', 'codex', 'hermes', 'opencode', 'deepseek', 't3')
    if ($args.Count -gt 2 -and $args[0] -in @('agents', 'tools') -and $args[2] -in @('login', 'setup')) {
        $interactive = $true
    }
    if (-not $interactive) {
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $start.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    }
    foreach ($argument in ($prefix + @('-B', '-u', '-X', 'utf8', (Join-Path $PSScriptRoot 'src/host/windows_cli.py')))) {
        $start.ArgumentList.Add($argument)
    }
    foreach ($argument in $args) { $start.ArgumentList.Add([string]$argument) }
    $process = [System.Diagnostics.Process]::Start($start)
    if (-not $interactive) {
        # Drain both pipes concurrently so a full stderr pipe cannot block stdout.
        # An anonymous emitter keeps the native error ID intact; a file-backed
        # Write-Error appends its script name and triggers PowerShell context formatting.
        $emitDiagnostic = [scriptblock]::Create('param($record) Write-Error -ErrorRecord $record -ErrorAction Continue')
        $outputRead = $process.StandardOutput.ReadLineAsync()
        $errorRead = $process.StandardError.ReadLineAsync()
        while ($null -ne $outputRead -or $null -ne $errorRead) {
            if ($null -ne $outputRead -and $outputRead.IsCompleted) {
                $line = $outputRead.GetAwaiter().GetResult()
                if ($null -eq $line) { $outputRead = $null } else {
                    Write-Output $line
                    $outputRead = $process.StandardOutput.ReadLineAsync()
                }
            }
            if ($null -ne $errorRead -and $errorRead.IsCompleted) {
                $line = $errorRead.GetAwaiter().GetResult()
                if ($null -eq $line) { $errorRead = $null } else {
                    # Native stderr includes progress, not just errors. Preserve
                    # stream redirection without adding PowerShell source context.
                    $record = [System.Management.Automation.ErrorRecord]::new(
                        [System.Management.Automation.RemoteException]::new($line),
                        'NativeCommandError', [System.Management.Automation.ErrorCategory]::NotSpecified, $null)
                    & $emitDiagnostic $record
                    $errorRead = $process.StandardError.ReadLineAsync()
                }
            }
            if (($null -eq $outputRead -or -not $outputRead.IsCompleted) -and
                ($null -eq $errorRead -or -not $errorRead.IsCompleted)) {
                Start-Sleep -Milliseconds 10
            }
        }
    }
    $process.WaitForExit()
    $result = $process.ExitCode
    $process.Dispose()
    exit $result
} catch {
    [Console]::Error.WriteLine("Error: $($_.Exception.Message)")
    exit 1
}
