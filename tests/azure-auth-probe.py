"""Run read-only Azure probes and return allowlisted evidence over managed SSH."""
import datetime
import configparser
from pathlib import Path
import json
import os
import subprocess
import sys


def command(args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True, timeout=120)


def az(*args):
    result = command(['az', *args])
    if result.returncode:
        raise ValueError('Azure CLI probe failed; inspect connectivity and permissions in the disposable sandbox.')
    return result.stdout.strip()


def main():
    request = json.load(sys.stdin)
    action = request['action']
    evidence = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if action == 'azure':
        cloud = json.loads(az('cloud', 'show', '--output', 'json'))
        if cloud['name'] != request['cloud']:
            raise ValueError('Active cloud does not match the requested cloud.')
        endpoint = cloud['endpoints']['resourceManager']
        expected = {'AzureCloud': 'https://management.azure.com',
                    'AzureChinaCloud': 'https://management.chinacloudapi.cn'}[request['cloud']]
        if endpoint.rstrip('/') != expected:
            raise ValueError('ARM endpoint does not match the selected cloud.')
        kind = az('account', 'show', '--query', 'user.type', '--output', 'tsv')
        if kind != 'user':
            raise ValueError('Expected a user session.')
        token = json.loads(az('account', 'get-access-token', '--output', 'json'))
        expiry = int(token['expires_on'])
        del token
        if expiry <= datetime.datetime.now(datetime.timezone.utc).timestamp():
            raise ValueError('Azure returned an expired token.')
        az('rest', '--method', 'get', '--url', endpoint.rstrip('/') + '/tenants?api-version=2020-01-01',
           '--output', 'none')
        evidence.update(cloud=cloud['name'], endpoint=endpoint, user_type=kind,
                        expires_on=expiry, arm_exit_code=0)
    elif action == 'versions':
        import msal
        versions = json.loads(az('version', '--output', 'json'))
        evidence.update(azure_cli=versions['azure-cli'], msal=msal.__version__,
                        azure_devops=versions.get('extensions', {}).get('azure-devops'))
    elif action == 'azdo_setup':
        mode = request['mode']
        args = ['sandbox-tools', 'setup', 'azdo']
        if mode == 'saved':
            args.append('--persist')
        result = command(args, request['organization'] + '\n' + request['pat'] + '\n')
        if result.returncode:
            raise ValueError('DevOps setup failed.')
        evidence.update(configured=True, mode=mode)
    elif action == 'azdo':
        defaults = configparser.ConfigParser()
        defaults.read(Path.home() / '.azure/azuredevops/config')
        if defaults.get('defaults', 'organization', fallback='') != request['organization']:
            raise ValueError('DevOps organization default changed.')
        saved = request['mode'] == 'saved'
        if bool(os.environ.get('AZURE_DEVOPS_EXT_PAT')) != saved:
            raise ValueError('DevOps PAT environment does not match the selected credential mode.')
        count = int(az('devops', 'project', 'list', '--detect', 'false', '--query', 'length(value)', '--output', 'tsv'))
        evidence.update(default_organization_preserved=True, native_projects=count,
                        pat_environment_present=saved)
        if saved:
            result = command(['sandbox-azdo', 'devops', 'project', 'list', '--detect', 'false',
                              '--query', 'length(value)', '--output', 'tsv'])
            if result.returncode:
                raise ValueError('Saved-PAT helper request failed.')
            evidence['helper_projects'] = int(result.stdout.strip())
    elif action == 'logout':
        az('logout')
        result = command(['az', 'account', 'show', '--query', 'user.type', '--output', 'tsv'])
        if result.returncode != 1:
            raise ValueError('Azure account remained available or logout could not be verified.')
        evidence.update(logged_out=True, account_exit_code=result.returncode)
    else:
        raise ValueError('Unknown probe action.')
    print(json.dumps(evidence))


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError):
        print('Error: Azure validation probe failed; no provider output was retained.', file=sys.stderr)
        sys.exit(1)
