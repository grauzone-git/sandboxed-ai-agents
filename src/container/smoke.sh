#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 1000 ]]
[[ $HOME == /home/agent ]]
test -w /workspace
test -w /home/agent
test ! -w /usr/share/dotnet
test ! -r /var/lib/agent-sshd/ssh_host_ed25519_key
test ! -S /var/run/docker.sock
test -z "${SSH_AUTH_SOCK:-}"
dotnet --list-sdks | grep -E '^9\.'
dotnet --list-sdks | grep -E '^10\.'
node --version
npm --version
git --version
python3 --version
pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
az version
test -n "$(az extension show --name azure-devops --query version --output tsv)"
az devops --help >/dev/null
sandbox-agents check
sandbox-tools check
playwright --version
test "$(npm config get prefix)" = /home/agent/.local
printf 'Toolchain, non-root identity, writable workspace/home, and credential separation: OK\n'

# Run via `agent-smoke --full` to exercise restore, builds, npm, Python venvs, and browsers.
if [[ ${1:-} == --full ]]; then
    check_dir=$(mktemp -d /tmp/agent-smoke.XXXXXXXX)
    trap 'rm -rf -- "$check_dir"' EXIT
    for major in 9 10; do
        sdk=$(dotnet --list-sdks | awk -v prefix="$major." 'index($1,prefix)==1 {print $1}' | sort -V | tail -1)
        mkdir "$check_dir/net$major"
        cd "$check_dir/net$major"
        printf '{"sdk":{"version":"%s","rollForward":"disable"}}\n' "$sdk" > global.json
        dotnet new console --framework "net$major.0"
        dotnet run
    done
    python3 -m venv "$check_dir/python"
    "$check_dir/python/bin/pip" install --disable-pip-version-check --quiet six
    "$check_dir/python/bin/python" -c "import six; print('Python venv and pip install: OK')"
    mkdir "$check_dir/browser"
    cd "$check_dir/browser"
    npm init -y >/dev/null
    npm install --save-dev playwright
    npx playwright install --only-shell chromium firefox
    node <<'JS'
const { chromium, firefox } = require('playwright');
const fs = require('node:fs');
(async () => {
  const targets = [['Chromium', chromium, {}], ['Firefox', firefox, {}]];
  if (fs.existsSync('/opt/microsoft/msedge/msedge')) {
    targets.push(['Microsoft Edge', chromium, { channel: 'msedge' }]);
  } else {
    console.log('Microsoft Edge: skipped (image built without Edge)');
  }
  for (const [name, engine, options] of targets) {
    const browser = await engine.launch({ headless: true, ...options });
    try {
      const page = await browser.newPage();
      await page.setContent('<title>sandbox-smoke</title><h1>OK</h1>');
      if (await page.title() !== 'sandbox-smoke') throw new Error(`${name}: wrong page title`);
      console.log(`${name}: headless launch and page check OK`);
    } finally {
      await browser.close();
    }
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
JS
fi
