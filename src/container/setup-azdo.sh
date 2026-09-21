#!/bin/bash
# Choose native Azure DevOps login or explicit environment persistence.
set -euo pipefail
case "${1:-}" in
    --clear)
        [[ $# -eq 1 ]] || exit 1
        exec sandbox-azdo --clear-pat
        ;;
    --persist) [[ $# -eq 1 ]] || exit 1 ;;
    '') [[ $# -eq 0 ]] || exit 1 ;;
    *) printf 'Use setup azdo [--persist|--clear].\n' >&2; exit 1 ;;
esac
if [[ ${1:-} == --persist ]]; then
    printf 'Save AZURE_DEVOPS_EXT_PAT in this sandbox home for new shells and agents.\n'
    printf 'No az login or az devops login will run. Other agents in this sandbox can use the PAT.\n'
else
    printf 'Use native az devops login to store credentials in this sandbox.\n'
fi
printf 'Default Azure DevOps organization URL: ' >&2
IFS= read -r organization || { printf '\nSetup cancelled.\n' >&2; exit 1; }
[[ $organization == https://* && $organization != *[[:space:]]* ]] || { printf 'Supply an HTTPS organization URL.\n' >&2; exit 1; }
if [[ $# -eq 0 ]]; then
    az devops login --organization "$organization"
    az devops configure --defaults "organization=$organization"
    sandbox-azdo --clear-pat
    printf 'Native Azure DevOps login completed; saved environment PAT removed.\n'
    exit
fi
printf 'PAT (input hidden): ' >&2
IFS= read -r -s pat || { printf '\nSetup cancelled.\n' >&2; exit 1; }
printf '\n' >&2
printf '%s' "$pat" | sandbox-azdo --save-pat "$organization"
