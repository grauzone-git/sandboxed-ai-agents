#Requires -Version 7.0
# Run with an explicit Python executable so test dependencies remain predictable.
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'check-powershell.ps1')
foreach ($name in @('test-powershell.py', 'test-windows-cli.py', 'test-windows-update.py', 'test-windows-paths.py', 'test-capabilities.py', 'test-container-line-endings.py', 'test-live-windows-arguments.py', 'test-live-azure-auth.py', 'test-azure-host.py')) {
    & $Python -B -X utf8 (Join-Path $PSScriptRoot $name)
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
