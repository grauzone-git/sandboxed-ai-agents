# PowerShell stderr investigation

Issue [#18](https://github.com/grauzone-git/sandboxed-ai-agents/issues/18)
reported a blank diagnostic line missing when the entry-point test ran from a
temporary checkout. Investigation for the #38 prerequisite reproduced the loss
on Linux with PowerShell 7.6.6 from `/workspace`, `/tmp`, and `/home/agent`.
The launcher and test were identical to the base commit `b9ee4a0`.

## Cause and test correction

The launcher reads the empty stderr line and emits an empty native
`ErrorRecord`. PowerShell's direct error rendering omits that record in the
tested environment. Capturing the PowerShell error stream with `2>&1` preserves
it. A probe without the launcher, Python, or Podman reproduces the distinction:

```powershell
$emit = [scriptblock]::Create('param($r) Write-Error -ErrorRecord $r -ErrorAction Continue')
foreach ($line in @('first', '', 'last')) {
    $record = [System.Management.Automation.ErrorRecord]::new(
        [System.Management.Automation.RemoteException]::new($line),
        'NativeCommandError',
        [System.Management.Automation.ErrorCategory]::NotSpecified,
        $null)
    & $emit $record
}
```

Save this as `renderer.ps1`. Direct execution prints `first` and `last` without
the intervening blank line in the observed environment. Capture it with:

```powershell
@(& ./renderer.ps1 2>&1 | ForEach-Object { $_.ToString() }) | ConvertTo-Json -Compress
```

The captured result is exactly `["first","","last"]` in all three tested
directories. Thus the original directory correlation does not reproduce here;
it does not establish a directory-dependent launcher defect.

The entry-point regression now accepts either rendering of the one empty
record, while still requiring exact diagnostic text, no PowerShell error
decoration, empty stdout, and the original exit status. It runs from both the
repository and temporary fixture directories. The redirected-stream regression
requires the empty record in order between its surrounding diagnostics and
preserves stdout capture and the native failure status. No launcher behavior
changed, and the test does not promise byte-for-byte terminal rendering of empty
PowerShell error records.

## Validation limits

These are offline Linux observations with PowerShell 7.6.6. Native Windows
execution was not available for this investigation. Run
`python -B tests/test-powershell.py` on Windows to check the same assertions.
Earlier Windows results in #18 remain historical evidence, not validation of
this test correction. No containers, credentials, or host settings were used.
