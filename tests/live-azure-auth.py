"""Human-run Azure acceptance checks on Linux or native Windows disposable sandboxes."""
import argparse
import datetime
import getpass
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import warnings
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

PROJECT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).with_name('azure-auth-probe.py')


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class CommandFailure(RuntimeError):
    """Keep command status without retaining potentially sensitive process output."""
    def __init__(self, returncode):
        super().__init__('Command failed; raw output was not saved.')
        self.returncode = returncode


class Validation:
    def __init__(self, options):
        self.options = options
        self.operation = 'initialize'
        self.name = 'azure-auth-test-' + uuid.uuid4().hex[:12]
        self.report = Path(options.report or (self.name + '.json')).absolute()
        self.env = dict(os.environ, SANDBOX_IMAGE=options.image, SANDBOX_PYTHON=sys.executable)
        self.env.pop('AZURE_DEVOPS_EXT_PAT', None)
        if options.azdo_pat_env:
            self.env.pop(options.azdo_pat_env, None)
        self.launcher = ([shutil.which('pwsh') or 'pwsh', '-NoProfile', '-File', str(PROJECT / 'sandbox.ps1')]
                         if os.name == 'nt' else [str(PROJECT / 'sandbox')])
        self.evidence = {'started_utc': utc(), 'host': platform.platform(), 'python': platform.python_version(),
                         'cloud': options.azure_environment, 'sandbox': self.name, 'image': options.image,
                         'checks': [], 'limitations': ['WSL-shell browser integration and policy-triggered recovery are not established.',
                                                     'One run covers one host and one cloud.']}
        # Refuse an existing report, including symlinks, before any sandbox mutation.
        with self.report.open('x', encoding='utf-8') as stream:
            json.dump(self.evidence, stream, indent=2)
        print(f'Disposable sandbox: {self.name}\nResults: {self.report}', flush=True)

    def record(self, check, status, **details):
        self.evidence['checks'].append(dict(check=check, status=status, recorded_utc=utc(), **details))
        self.report.write_text(json.dumps(self.evidence, indent=2) + '\n', encoding='utf-8')
        print(f'{status.upper()}: {check}', flush=True)

    def execute(self, args, *, input=None, interactive=False, timeout=300):
        result = subprocess.run(args, cwd=PROJECT, env=self.env, input=input,
                                text=True, encoding='utf-8', capture_output=not interactive, timeout=timeout)
        if result.returncode:
            # Provider/transport output can contain credentials or authorization URLs.
            raise CommandFailure(result.returncode)
        return result.stdout or ''

    def sandbox(self, command, *args, interactive=False):
        self.operation = 'azure_setup' if command == 'tools' else command
        return self.execute([*self.launcher, self.name, command, *args], interactive=interactive, timeout=1200)

    def ssh_command(self, command):
        config = Path.home() / '.ssh/sanboxed-agents' / self.name / (self.name + '.conf')
        args = ['ssh', '-F', str(config), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'IdentityAgent=none',
                '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=10', self.name,
                command]
        return args

    def wait_for_ssh(self):
        self.operation = 'ssh_readiness'
        deadline = time.monotonic() + 60
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('SSH did not become ready within 60 seconds.')
            try:
                self.execute(self.ssh_command('true'), timeout=min(15, remaining))
                return
            except (CommandFailure, subprocess.TimeoutExpired):
                time.sleep(min(1, max(0, deadline - time.monotonic())))

    def remote(self, action, **payload):
        self.operation = 'ssh_' + action
        command = shlex.join(['/bin/bash', '-lc', shlex.join([
            '/opt/az/bin/python3', '-B', '-c', PROBE.read_text(encoding='utf-8')])])
        args = self.ssh_command(command)
        return json.loads(self.execute(args, input=json.dumps(dict(action=action, **payload))))

    def probe(self, check):
        result = self.remote('azure', cloud=self.options.azure_environment)
        self.record(check, 'passed', **result)
        return result

    def login_arguments(self, remembered=False):
        args = ['tools', 'setup', 'azure', '--interactive', '--tenant', self.options.tenant_id,
                '--tenant-only']
        if not remembered:
            args += ['--cloud', self.options.azure_environment]
        return args

    def login(self, remembered=False):
        args = self.login_arguments(remembered)
        print('Complete sign-in in the host browser. Keep this terminal open.', flush=True)
        self.sandbox(*args, interactive=True)

    def cancellation(self):
        print('Cancellation check: when the browser opens, press Ctrl+C here BEFORE completing sign-in.', flush=True)
        if input('Ready to test cancellation? [yes/no] ').strip().lower() != 'yes':
            raise ValueError('Cancellation test was not confirmed.')
        child = subprocess.Popen([*self.launcher, self.name, *self.login_arguments()], cwd=PROJECT, env=self.env,
                                 start_new_session=os.name != 'nt')
        try:
            try:
                code = child.wait(timeout=660)
            except KeyboardInterrupt:
                # Windows shares Ctrl+C with the launcher. POSIX uses a private
                # process group so cleanup cannot signal the calling terminal.
                if os.name != 'nt':
                    os.killpg(child.pid, signal.SIGINT)
                code = child.wait(timeout=30)
            if code != 130:
                raise ValueError('Cancellation did not return exit code 130.')
            if input('Cancelled before completing browser sign-in? [yes/no] ').strip().lower() != 'yes':
                raise ValueError('Cancellation before sign-in was not confirmed.')
            self.probe('cancellation')
        finally:
            if child.poll() is None:
                # Finish cleanup even if a second Ctrl+C interrupted the grace period.
                previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
                try:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                                       capture_output=True, timeout=30, env=self.env)
                    else:
                        os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        if os.name != 'nt':
                            os.killpg(child.pid, signal.SIGKILL)
                        else:
                            child.kill()
                        child.wait(timeout=10)
                finally:
                    signal.signal(signal.SIGINT, previous)

    def devops(self, pat):
        for mode in ('native', 'saved'):
            check = 'azdo_' + mode
            try:
                self.remote('azdo_setup', mode=mode, organization=self.options.azdo_organization, pat=pat)
            except (ValueError, CommandFailure):
                self.record(check + '_setup', 'failed', reason='DevOps setup failed; credential-store installation or service access may be unavailable.')
                continue
            self.record(check + '_setup', 'passed')
            for phase in ('before_replacement', 'after_replacement', 'after_logout'):
                if phase == 'after_replacement':
                    self.login(remembered=True)
                    self.probe(check + '_remembered_cloud')
                elif phase == 'after_logout':
                    self.record(check + '_logout', 'passed', **self.remote('logout'))
                result = self.remote('azdo', mode=mode, organization=self.options.azdo_organization)
                self.record(check + '_' + phase, 'passed', **result)

    def versions(self):
        versions = self.remote('versions')
        versions['git_head'] = self.execute(['git', 'rev-parse', 'HEAD']).strip()
        versions['tracked_changes'] = bool(self.execute(['git', 'status', '--porcelain', '--untracked-files=no']).strip())
        versions['podman'] = self.execute(['podman', 'version']).strip()
        versions['image_id'] = self.execute(['podman', 'image', 'inspect', '--format', '{{.Id}}', self.options.image]).strip()
        ssh = subprocess.run(['ssh', '-V'], text=True, capture_output=True, env=self.env, timeout=10)
        if ssh.returncode:
            raise ValueError('Cannot determine OpenSSH version.')
        versions['openssh'] = (ssh.stderr or ssh.stdout).strip()
        if os.name == 'nt':
            versions['powershell'] = self.execute([self.launcher[0], '-NoProfile', '-Command',
                                                  '$PSVersionTable.PSVersion.ToString()']).strip()
            versions['podman_machines'] = self.execute(['podman', 'machine', 'list']).strip()
        self.record('versions', 'passed', **versions)

    def run(self):
        stage = 'create'
        try:
            self.sandbox('up', '--ssh-port', str(self.options.ssh_port),
                         '--agents', self.options.agents, '--ssh-config')
            self.record(stage, 'passed')
            stage = 'versions'
            self.wait_for_ssh()
            self.versions()
            stage = 'initial_login'
            self.login()
            self.probe(stage)
            stage = 'restart'
            self.sandbox('stop')
            self.sandbox('start')
            self.wait_for_ssh()
            self.probe(stage)
            stage = 'recreation'
            self.sandbox('update', '--no-build')
            self.wait_for_ssh()
            baseline = self.probe(stage)
            stage = 'renewal'
            if self.options.skip_renewal:
                self.record(stage, 'skipped', reason='Requested --skip-renewal.')
            else:
                deadline = baseline['expires_on'] + 60
                print('Leave this sandbox idle; do not run Azure commands or sign in elsewhere in it.', flush=True)
                print('Waiting until ' + datetime.datetime.fromtimestamp(deadline, datetime.timezone.utc).isoformat(), flush=True)
                while time.time() < deadline:
                    time.sleep(max(0, min(30, deadline - time.time())))
                renewed = self.remote('azure', cloud=self.options.azure_environment)
                if renewed['expires_on'] <= baseline['expires_on']:
                    raise ValueError('Token expiry did not advance after the old token expired.')
                if input('No intervening Azure commands or sign-in in this sandbox? [yes/no] ').strip().lower() != 'yes':
                    raise ValueError('Uninterrupted renewal was not confirmed.')
                self.record(stage, 'passed', **renewed)
            stage = 'cancellation'
            if self.options.skip_cancellation:
                self.record(stage, 'skipped', reason='Requested --skip-cancellation.')
            else:
                self.cancellation()
            stage = 'remembered_cloud'
            self.login(remembered=True)
            self.probe(stage)
            stage = 'logout'
            result = self.remote('logout')
            self.record(stage, 'passed', **result)
            stage = 'devops'
            if self.options.pat:
                self.devops(self.options.pat)
            else:
                self.record(stage, 'skipped', reason='No DevOps PAT supplied.')
            failed = any(item['status'] == 'failed' for item in self.evidence['checks'])
            if not self.options.keep_sandbox and not failed:
                stage = 'cleanup'
                self.sandbox('remove', '--volumes')
                self.record(stage, 'passed')
            else:
                self.record('cleanup', 'skipped', reason='Sandbox retained by request or because a check failed.')
            return 1 if failed else 0
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError, KeyboardInterrupt, EOFError) as error:
            self.record(stage, 'failed', reason='Check failed or was interrupted; sandbox retained for inspection.',
                        operation=self.operation, exit_code=getattr(error, 'returncode', None),
                        failure_type=type(error).__name__)
            print('Validation stopped. Raw provider output was not retained.', file=sys.stderr)
            return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--azure-environment', choices=('AzureCloud', 'AzureChinaCloud'), default='AzureCloud')
    parser.add_argument('--tenant-id', required=True)
    parser.add_argument('--image', default=os.environ.get('SANDBOX_IMAGE', 'localhost/agent-sandbox:dev'))
    parser.add_argument('--ssh-port', type=int, default=2299)
    parser.add_argument('--agents', default='codex')
    parser.add_argument('--report', help='New JSON results file; existing files are never overwritten.')
    parser.add_argument('--skip-renewal', action='store_true', help='Record renewal as skipped instead of waiting for token expiry.')
    parser.add_argument('--skip-cancellation', action='store_true')
    parser.add_argument('--keep-sandbox', action='store_true')
    pat = parser.add_mutually_exclusive_group()
    pat.add_argument('--azdo-pat', action='store_true', help='Prompt for a PAT without echoing it; never put a PAT in arguments.')
    pat.add_argument('--azdo-pat-env', metavar='NAME', help='Read the PAT from this host environment variable.')
    parser.add_argument('--azdo-organization', help='HTTPS organization URL for optional DevOps checks.')
    options = parser.parse_args(argv)
    if not 1 <= options.ssh_port <= 65535:
        raise ValueError('Choose an SSH port between 1 and 65535.')
    requested_pat = options.azdo_pat or options.azdo_pat_env
    if bool(requested_pat) != bool(options.azdo_organization):
        raise ValueError('Supply --azdo-organization together with --azdo-pat or --azdo-pat-env.')
    if options.azdo_organization:
        url = urlsplit(options.azdo_organization)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or any(c.isspace() for c in options.azdo_organization):
            raise ValueError('Supply an HTTPS organization URL without credentials, query, or fragment.')
    options.pat = os.environ.get(options.azdo_pat_env, '') if options.azdo_pat_env else ''
    if options.azdo_pat:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                options.pat = getpass.getpass('Azure DevOps PAT: ')
        except getpass.GetPassWarning:
            raise ValueError('A hidden prompt needs a terminal; use --azdo-pat-env otherwise.') from None
    if requested_pat and (not options.pat or any(c in options.pat for c in '\r\n\0')):
        raise ValueError('Supply a nonempty single-line PAT through the hidden prompt or environment.')
    if options.pat and options.pat in options.azdo_organization:
        raise ValueError('The organization URL must not contain the PAT.')
    return Validation(options).run()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit('Error: ' + str(error))
