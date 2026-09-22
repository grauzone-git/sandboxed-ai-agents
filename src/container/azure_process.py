"""Run Azure CLI with bounded, redacted output and cancellable child processes."""
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time


def read_lines(stream, output, source):
    try:
        for line in iter(stream.readline, ''):
            output.put((source, line))
    finally:
        output.put((source, None))


def run(command, env, notify, cancelled, timeout=600):
    browser = command[0] == 'login' and '--use-device-code' not in command
    executable = [sys.executable, '-B', str(Path(__file__).with_name('azure_browser.py'))] if browser else ['az']
    child = subprocess.Popen([*executable, *command], env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    output = queue.Queue()
    for source, stream in (('out', child.stdout), ('err', child.stderr)):
        threading.Thread(target=read_lines, args=(stream, output, source), daemon=True).start()
    end = time.monotonic() + timeout
    closed = 0
    stdout, stderr = '', ''
    try:
        while closed < 2:
            if cancelled():
                raise ValueError('Azure setup cancelled. Retry explicit setup when ready.')
            if time.monotonic() >= end:
                raise ValueError('Azure setup timed out. Retry explicit setup.')
            try:
                source, line = output.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                closed += 1
                continue
            if source == 'out' and line.startswith('SANDBOX_AZURE_BROWSER '):
                notify('browser', json.loads(line.split(' ', 1)[1]))
            elif source == 'out':
                stdout += line
                if len(stdout) > 4 * 1024 * 1024:
                    raise ValueError('Azure returned too much output. Retry with an explicit tenant.')
            else:
                stderr = (stderr + line)[-65536:]
                # Only the documented device-code instruction reaches the terminal.
                # All other CLI diagnostics can contain authorization URLs or tokens.
                clean = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
                if not browser and command[0] == 'login' and re.fullmatch(
                        r'To sign in, use a web browser to open the page https://[a-zA-Z0-9./-]+ and enter the code [A-Z0-9-]+ to authenticate\.', clean):
                    notify('device', clean)
        return child.wait(timeout=5), stdout, stderr
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        child.stdout.close()
        child.stderr.close()
