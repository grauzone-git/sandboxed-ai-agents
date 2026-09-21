# Match the Bash profile while preserving a PAT explicitly set by the caller.
$sandboxAzdoSettings = Join-Path $HOME '.config/sandbox-azdo/environment'
if (-not (Test-Path Env:AZURE_DEVOPS_EXT_PAT) -and (Test-Path -LiteralPath $sandboxAzdoSettings)) {
    $env:AZURE_DEVOPS_EXT_PAT = [System.IO.File]::ReadLines($sandboxAzdoSettings) | Select-Object -First 1
}
Remove-Variable sandboxAzdoSettings
