#Requires -Version 7.0
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$files = @((Get-Item (Join-Path $root 'sandbox.ps1')))
$files += Get-ChildItem (Join-Path $root 'src') -Filter '*.ps1' -Recurse
$files += Get-ChildItem $PSScriptRoot -Filter '*.ps1' -Recurse
if (Test-Path (Join-Path $root 'packaging')) {
    $files += Get-ChildItem (Join-Path $root 'packaging') -Filter '*.ps1' -Recurse
}
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
Write-Output 'PowerShell syntax: OK'
