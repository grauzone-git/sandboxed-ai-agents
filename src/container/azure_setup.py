"""Stage fresh sandbox Azure authentication before replacing the active session."""
import configparser
import fcntl
import io
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import queue
import threading
import time
import sys
import tempfile

from azure_options import CLOUDS, parse
from azure_process import run

SESSION_FILES = ('azureProfile.json', 'msal_token_cache.json', 'msal_token_cache.bin',
                 'msal_http_cache.bin', 'service_principal_entries.json',
                 'service_principal_entries.bin', 'accessTokens.json', 'clouds.config', 'config')
# Login never writes these; keep the user's copy instead of deleting it.
PRESERVED_FILES = ('clouds.config',)


class SetupError(ValueError):
    """A failure whose category the host maps to its own text, never echoing remote output."""
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


def failure(detail):
    detail = detail.lower()
    if any(value in detail for value in ('interaction_required', 'interactionrequired',
                                        'aadsts50076', 'aadsts50079', 'aadsts50158',
                                        'aadsts70043', 'aadsts700082', 'aadsts700084',
                                        'aadsts50058', 'invalid_grant')):
        return SetupError('interaction', 'Azure interaction required. Rerun explicit Azure setup, then decide whether to retry your command.')
    if any(value in detail for value in ('authorizationfailed', 'forbidden', 'permission', '403')):
        return SetupError('permission', 'Azure permission denied. Check tenant, subscription, and role assignments before retrying setup.')
    if any(value in detail for value in ('connection', 'timeout', 'resolve', 'network', 'ssl')):
        return SetupError('network', 'Azure network request failed. Check connectivity and retry explicit setup.')
    return SetupError('setup', 'Azure sign-in or context selection failed. Check your selection and retry explicit setup.')


def cancelled_error():
    return SetupError('cancelled', 'Azure setup cancelled or timed out. Retry explicit setup.')


def snapshot(directory):
    result = {}
    for name in SESSION_FILES:
        file = directory / name
        if file.is_symlink() or (file.exists() and not file.is_file()):
            raise ValueError('Remove unexpected symlinks or file types in the Azure session before setup.')
        result[name] = file.read_bytes() if file.exists() else None
    return result


def publish(active, pending, before):
    if snapshot(active) != before:
        raise ValueError('Azure state changed during sign-in. Stop concurrent Azure commands and retry setup.')
    # Keep non-Azure config sections, including native Azure DevOps defaults.
    config = configparser.ConfigParser(interpolation=None)
    config.read_string((before['config'] or b'').decode('utf-8-sig'))
    selected = configparser.ConfigParser(interpolation=None)
    selected.read(pending / 'config')
    if not config.has_section('cloud'):
        config.add_section('cloud')
    config.set('cloud', 'name', selected.get('cloud', 'name'))
    content = io.StringIO()
    config.write(content)
    (pending / 'config').write_text(content.getvalue())
    changed = []
    try:
        for name in SESSION_FILES:
            changed.append(name)
            source = pending / name
            if source.exists():
                source.chmod(0o600)
                os.replace(source, active / name)
            elif name not in PRESERVED_FILES:
                (active / name).unlink(missing_ok=True)
    except BaseException:
        for name in changed:
            if before[name] is None:
                (active / name).unlink(missing_ok=True)
            else:
                restore = pending / 'restore'
                restore.write_bytes(before[name])
                restore.chmod(0o600)
                os.replace(restore, active / name)
        raise


class Session:
    def __init__(self, hosted):
        self.hosted = hosted
        self.cloud = None
        self.answers = queue.Queue()
        self.disconnected = threading.Event()
        self.deadline = time.monotonic() + 600
        if hosted:
            threading.Thread(target=self.read_answers, daemon=True).start()

    def read_answers(self):
        try:
            for line in sys.stdin:
                if len(line) > 65536:
                    break
                self.answers.put(json.loads(line))
        except (ValueError, OSError):
            pass
        finally:
            self.disconnected.set()

    def cancelled(self):
        return self.disconnected.is_set() or time.monotonic() >= self.deadline

    def notify(self, event, value=None):
        if self.hosted:
            print(json.dumps({'event': event, 'value': value, 'cloud': self.cloud}), flush=True)
        elif event == 'device':
            print(value, flush=True)

    def answer(self):
        while not self.cancelled():
            try:
                return self.answers.get(timeout=0.1)
            except queue.Empty:
                continue
        raise cancelled_error()

    def prompt(self, kind):
        if self.hosted:
            self.notify('prompt', kind)
            value = self.answer().get('answer', '')
        else:
            value = input(f'Azure {kind} ID or name: ')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Select an explicit Azure tenant and subscription (or --tenant-only).')
        return value.strip()


def main(args, session=None):
    session = session or Session(False)
    options = parse(args)
    if options.interactive and not session.hosted:
        # The container hostname is the sandbox name.
        command = f'{socket.gethostname()} tools setup azure {shlex.join(args)}'
        raise ValueError(f'Run on the host: ./sandbox {command} (native Windows: ./sandbox.ps1 {command}).')
    if not options.tenant:
        options.tenant = session.prompt('tenant')
        parse(['--tenant', options.tenant])
    home = Path.home()
    active = home / '.azure'
    if os.environ.get('AZURE_CONFIG_DIR') not in (None, str(active)):
        raise ValueError('Unset AZURE_CONFIG_DIR for setup; this configures the normal sandbox Azure session.')
    if active.is_symlink():
        raise ValueError('Azure setup requires a normal ~/.azure directory, not a symlink.')
    active.mkdir(mode=0o700, exist_ok=True)
    active.chmod(0o700)
    before = snapshot(active)
    config = configparser.ConfigParser(interpolation=None)
    config.read_string((before['config'] or b'').decode('utf-8-sig'))
    cloud = options.cloud or config.get('cloud', 'name', fallback='AzureCloud')
    if cloud not in CLOUDS:
        raise ValueError('Unsupported Azure cloud. Select --cloud AzureCloud or --cloud AzureChinaCloud explicitly.')
    session.cloud = cloud
    with tempfile.TemporaryDirectory(prefix='.azure-login-', dir=home) as directory:
        pending = Path(directory)
        env = {**{key: value for key, value in os.environ.items() if not key.startswith('AZURE_')},
               'AZURE_CONFIG_DIR': directory,
               'AZURE_LOGGING_ENABLE_LOG_FILE': 'false',
               'AZURE_CORE_COLLECT_TELEMETRY': 'false',
               'AZURE_CORE_LOGIN_EXPERIENCE_V2': 'off',
               'AZURE_CORE_ENABLE_BROKER_ON_WINDOWS': 'false'}
        def az(command, output='none'):
            try:
                code, stdout, stderr = run([*command, '--output', output], env,
                                           session.notify, session.cancelled,
                                           timeout=max(0, session.deadline - time.monotonic()))
            except ValueError:
                if session.cancelled():
                    raise cancelled_error() from None
                raise
            if code:
                raise failure(stderr)
            return stdout
        az(['cloud', 'set', '--name', cloud])
        az(['login', *([] if options.interactive else ['--use-device-code']), '--tenant', options.tenant,
            *(['--allow-no-subscriptions', '--skip-subscription-discovery'] if options.tenant_only else [])])
        if not options.tenant_only:
            if not options.subscription:
                accounts = json.loads(az(['account', 'list'], 'json'))
                if not session.hosted:
                    for account in accounts:
                        print(f'{account.get("id", "")}: {account.get("name", "")}')
                options.subscription = session.prompt('subscription')
                if not options.subscription:
                    raise ValueError('Select a subscription, or retry with --tenant-only.')
            az(['account', 'set', '--subscription', options.subscription])
        az(['account', 'show'], 'json')
        if session.hosted:
            session.notify('ready')
            if session.answer().get('commit') is not True:
                raise cancelled_error()
        if session.cancelled():
            raise cancelled_error()
        publish(active, pending, before)
    if session.hosted:
        session.notify('complete')
    else:
        print('Azure setup completed.')
    return 0


if __name__ == '__main__':
    args = sys.argv[1:]
    hosted = bool(args and args[0] == '--host-protocol')
    session = Session(hosted)
    if hosted:
        args.pop(0)
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
        signal.signal(signum, interrupt)
    signal.alarm(600)
    try:
        lock_directory = Path.home() / '.local/state/sandbox-agents'
        lock_directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        with (lock_directory / 'azure-setup.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SetupError('busy', 'Another Azure setup is running in this sandbox. Wait for it to finish, then retry.') from None
            sys.exit(main(args, session))
    except ValueError as error:
        if hosted:
            session.notify('error', getattr(error, 'category', 'setup'))
            sys.exit(1)
        sys.exit(f'Error: {error} The preceding session was retained.')
    except (KeyboardInterrupt, EOFError):
        sys.exit('Error: Azure setup cancelled. The preceding session was retained; retry explicit setup.')
    except (OSError, RuntimeError, configparser.Error):
        if hosted:
            session.notify('error', 'setup')
            sys.exit(1)
        sys.exit('Error: Unable to complete Azure setup. The preceding session was retained; retry explicit setup.')
