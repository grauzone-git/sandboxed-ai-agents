#!/bin/bash
# Save commit identity in the persistent sandbox home after GitHub authentication.
set -euo pipefail

read_identity() {
    local prompt=$1 value
    while true; do
        printf '%s' "$prompt" >&2
        if ! IFS= read -r value; then
            printf '\nGit identity setup cancelled; name and email were not changed.\n' >&2
            return 1
        fi
        if [[ $value == *[![:space:]]* ]]; then
            REPLY=$value
            return
        fi
        printf 'Enter a nonempty value.\n' >&2
    done
}

printf 'Set the Git commit identity for all repositories in this sandbox.\n'
read_identity 'Git user name: '
git_name=$REPLY
read_identity 'Git email (your GitHub noreply address is also accepted): '
git_email=$REPLY

# Collect both values before changing existing settings, including on EOF.
git config --global user.name "$git_name"
git config --global user.email "$git_email"
printf 'Git commit name and email saved for this sandbox user.\n'
