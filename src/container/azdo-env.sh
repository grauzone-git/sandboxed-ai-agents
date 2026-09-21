# Load data, never shell code, from the sandbox's opt-in environment settings.
if [ "${AZURE_DEVOPS_EXT_PAT+x}" != x ] && [ -r "$HOME/.config/sandbox-azdo/environment" ]; then
    IFS= read -r AZURE_DEVOPS_EXT_PAT < "$HOME/.config/sandbox-azdo/environment" || true
    export AZURE_DEVOPS_EXT_PAT
fi
