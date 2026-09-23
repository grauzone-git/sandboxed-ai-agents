"""Coordinate sandbox-owned Azure login and temporary pinned SSH forwarding."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'container'))
from azure_options import CLOUDS, parse


def callback(url, cloud):
    error = 'Azure returned an unexpected authorization endpoint or callback. Retry explicit setup after updating the image.'
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, strict_parsing=True)
        if (cloud not in CLOUDS or parsed.scheme != 'https' or parsed.netloc != CLOUDS[cloud]
                or not re.fullmatch(r'/[a-zA-Z0-9.-]+/oauth2/(v2\.0/)?authorize', parsed.path)
                or parsed.fragment or any(len(value) != 1 for value in query.values())
                or query.get('response_type') != ['code'] or not query.get('state')):
            raise ValueError(error)
        redirect = urlsplit(query['redirect_uri'][0])
        if (redirect.scheme != 'http' or redirect.hostname not in ('localhost', '127.0.0.1')
                or redirect.username or redirect.password or redirect.query or redirect.fragment
                or redirect.path not in ('', '/') or not redirect.port or not 1024 <= redirect.port <= 65535):
            raise ValueError(error)
        return redirect.port
    except (KeyError, TypeError, ValueError):
        raise ValueError(error) from None


def stop(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def launch_browser(url):
    if os.name == 'nt':
        os.startfile(url)
    else:
        opener = shutil.which('xdg-open')
        if not opener:
            raise ValueError('Install xdg-open and configure a default browser, then retry --interactive.')
        result = subprocess.run([opener, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=15)
        if result.returncode:
            raise ValueError('Host browser launch failed. Configure your default browser and retry --interactive.')


class BrowserRedirect:
    def __init__(self, url):
        route = '/' + secrets.token_urlsafe(24)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != route:
                    self.send_error(404)
                    return
                self.send_response(302)
                self.send_header('Location', url)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}{route}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def verify_forward(port, forward, deadline):
    readiness = queue.Queue()
    threading.Thread(target=lambda: readiness.put(forward.stdout.readline(64)), daemon=True).start()
    try:
        marker = readiness.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        marker = ''
    if marker != 'SANDBOX_AZURE_FORWARD\n':
        raise ValueError('Azure callback forwarding failed. Check SSH access and retry --interactive.')
    while time.monotonic() < deadline:
        if forward.poll() is not None:
            break
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
        try:
            # MSAL's callback handler has no HEAD operation. A 501 verifies the
            # whole tunnel without supplying an OAuth response or consuming login.
            connection.request('HEAD', '/')
            if connection.getresponse().status == 501 and forward.poll() is None:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.1)
    raise ValueError('Azure callback forwarding failed. Check managed SSH access and local port availability, then retry --interactive.')


def prompt(kind, deadline):
    answers = queue.Queue()
    def read():
        try:
            answers.put(input(f'Azure {kind} ID or name: ').strip())
        except EOFError:
            answers.put(None)
    threading.Thread(target=read, daemon=True).start()
    try:
        answer = answers.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise ValueError('Azure setup timed out. Retry explicit setup.') from None
    if not answer:
        raise ValueError('Azure setup cancelled. Supply a tenant and subscription (or --tenant-only) and retry.')
    return answer


def interactive(name, config, arguments, *, popen=subprocess.Popen, browser=launch_browser, timeout=600):
    options = parse(arguments)
    if not options.interactive:
        raise ValueError('Host browser setup requires --interactive.')
    ssh = shutil.which('ssh')
    if not ssh:
        raise ValueError('Install OpenSSH, then retry Azure setup.')
    launcher = './sandbox.ps1' if os.name == 'nt' else './sandbox'
    config = Path(config)
    if not all(path.is_file() for path in (config, config.parent / 'known_hosts', config.parent / 'id_ed25519')):
        raise ValueError(f'Configure SSH first: {launcher} ssh-config {name} --install')
    common = [ssh, '-F', str(config), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
              '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'IdentityAgent=none',
              '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ConnectTimeout=10']
    remote = shlex.join(['/opt/az/bin/python3', '-B', '/usr/local/lib/sandbox-agents/azure_setup.py',
                         '--host-protocol', *arguments])
    login = forward = redirect = None
    events = queue.Queue()
    deadline = time.monotonic() + timeout
    handlers = {}
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    def read_events():
        try:
            while True:
                line = login.stdout.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    events.put(None)
                    return
                events.put(line)
        finally:
            events.put(None)

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, 'SIGHUP') else [])):
                handlers[signum] = signal.signal(signum, interrupt)
        login = popen([*common, '-T', name, remote], stdin=subprocess.PIPE,
                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        threading.Thread(target=read_events, daemon=True).start()
        complete = False
        while not complete:
            if time.monotonic() >= deadline:
                raise ValueError('Azure setup timed out. Retry explicit setup.')
            if forward is not None and forward.poll() is not None:
                raise ValueError('Azure callback forwarding stopped. Check SSH access and retry --interactive.')
            try:
                line = events.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                raise ValueError('Sandbox Azure setup ended before completion. Check SSH access, update the image, and retry setup.')
            try:
                event = json.loads(line)
                kind = event['event']
            except (ValueError, KeyError, TypeError):
                raise ValueError('Unexpected Azure setup response. Update the sandbox image and retry.') from None
            if kind == 'browser':
                if forward is not None:
                    raise ValueError('Unexpected second browser request. Retry explicit setup.')
                if options.cloud and event.get('cloud') != options.cloud:
                    raise ValueError('The sandbox returned a different cloud. Update the image and retry explicit setup.')
                port = callback(event['value'], event['cloud'])
                with socket.socket() as probe:
                    if os.name == 'nt':
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind(('127.0.0.1', port))
                # The remote marker is emitted only after SSH has bound the
                # local forward. A pre-existing HTTP listener cannot stand in
                # for a tunnel that is still starting or about to fail binding.
                forward = popen([*common, '-o', 'ExitOnForwardFailure=yes', '-T', '-L',
                                 f'127.0.0.1:{port}:127.0.0.1:{port}', name,
                                 "printf 'SANDBOX_AZURE_FORWARD\\n'; exec cat"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding='utf-8')
                verify_forward(port, forward, min(deadline, time.monotonic() + 15))
                redirect = BrowserRedirect(event['value'])
                browser(redirect.url)
                print('Complete Azure sign-in in your host browser. Keep this command running.', flush=True)
            elif kind == 'prompt':
                # Prompts are a closed protocol; never echo arbitrary remote text.
                if event.get('value') == 'tenant':
                    answer = prompt('tenant', deadline)
                elif event.get('value') == 'subscription':
                    print('Select a subscription from the sign-in account (ID or name).')
                    answer = prompt('subscription', deadline)
                else:
                    raise ValueError('Unexpected Azure setup prompt.')
                login.stdin.write(json.dumps({'answer': answer}) + '\n')
                login.stdin.flush()
            elif kind == 'ready':
                login.stdin.write('{"commit":true}\n')
                login.stdin.flush()
            elif kind == 'complete':
                complete = True
            elif kind == 'error':
                messages = {'interaction': 'Azure interaction required. Retry explicit setup.',
                            'permission': 'Azure permission denied. Check tenant, subscription, and roles.',
                            'network': 'Azure network request failed. Check connectivity and retry setup.'}
                raise ValueError(messages.get(event.get('value'), 'Azure setup failed; the preceding session was retained. Check your selection and retry setup.'))
            else:
                raise ValueError('Unexpected Azure setup response. Update the image and retry.')
        if login.wait(timeout=5):
            raise ValueError('Azure setup did not exit cleanly. Check the sandbox account context before retrying.')
        print('Azure setup completed. The callback tunnel is closing.')
    finally:
        if login is not None and login.stdin is not None:
            try:
                login.stdin.close()
            except OSError:
                pass
        stop(login)
        stop(forward)
        if forward is not None:
            forward.stdin.close()
            forward.stdout.close()
        if redirect is not None:
            redirect.close()
        if login is not None and login.stdout is not None:
            login.stdout.close()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


def main(args):
    if args and args[0] == '--validate':
        parse(args[1:])
        return 0
    if len(args) < 2 or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*', args[0]):
        raise ValueError('Use tools NAME setup azure [--interactive] [OPTIONS].')
    name = args[0]
    interactive(name, Path.home() / '.ssh/sanboxed-agents' / name / f'{name}.conf', args[1:])
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except ValueError as error:
        sys.exit(f'Error: {error}')
    except (KeyboardInterrupt, EOFError):
        print('Error: Azure setup cancelled. Retry explicit setup when ready.', file=sys.stderr)
        sys.exit(130)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        sys.exit('Error: Azure browser setup failed. Check SSH, local port availability, and your default browser, then retry --interactive.')
