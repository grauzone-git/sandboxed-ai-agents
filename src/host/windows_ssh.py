"""Manage opt-in Windows SSH access without forwarding private keys or sockets."""
import base64
import errno
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from containers import LABEL
from windows_paths import local_path
from windows_runtime import native


class SshSetup:
    def __init__(self, runtime, project, name, *, require_keygen=True):
        self.runtime, self.project, self.name = runtime, project, name
        self.home = Path.home()
        self.root = self.home / '.ssh/sanboxed-agents'
        self.state = self.root / name
        self.config = self.home / '.ssh/config'
        self.owner = self.state / 'owner.json'
        self.identity = {'project': project, 'name': name}
        self.keygen = shutil.which('ssh-keygen')
        if require_keygen and not self.keygen:
            raise ValueError('Install the Windows OpenSSH client before requesting SSH setup.')
        self.pwsh = shutil.which('pwsh') if os.name == 'nt' else None
        if os.name == 'nt' and not self.pwsh:
            raise ValueError('PowerShell 7 (pwsh) must be on PATH to secure the SSH key ACL.')
        for path in (self.root, self.state):
            local_path(path)
            if any(char in path.as_posix() for char in ('"', '%', '$', '*', '?', '[', ']', '\n', '\r')):
                raise ValueError('SSH paths must not contain quotes, newlines, or SSH expansion characters.')
        # Refuse redirection of any managed location before writing credentials.
        for path in (self.home / '.ssh', self.root, self.state, self.config, self.owner,
                     self.state / 'id_ed25519', self.state / 'id_ed25519.pub',
                     self.state / 'known_hosts', self.state / f'{name}.conf'):
            if path.is_symlink() or (path.exists() and
                    getattr(path.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise ValueError('SSH setup refuses symlinks and reparse points in managed paths.')
            if path.exists() and path not in (self.home / '.ssh', self.root, self.state) and not path.is_file():
                raise ValueError(f'Unexpected managed SSH file type: {path}')
        if self.owner.exists():
            if json.loads(self.owner.read_text(encoding='utf-8')) != self.identity:
                raise ValueError('SSH state belongs to another checkout or differently cased sandbox name.')
        elif self.state.exists() and any(self.state.iterdir()):
            raise ValueError('Existing SSH state has no matching ownership record; move it aside explicitly.')
        # Read early so unsupported encodings fail before provisioning or key writes.
        self.existing_config = self.config.read_text(encoding='utf-8-sig') if self.config.exists() else ''

    def secure(self, path):
        if os.name == 'nt':
            native([self.pwsh, '-NoProfile', '-File',
                    Path(__file__).with_name('windows-acl.ps1'), '-Path', path], runner=self.runtime.runner)
        else:
            path.chmod(0o700 if path.is_dir() else 0o600)

    def write(self, path, content):
        descriptor, temporary = tempfile.mkstemp(prefix='.sandbox-ssh-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(content)
            self.secure(Path(temporary))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def install(self):
        info = self.runtime.json('inspect', self.name)[0]
        if (info.get('Config', {}).get('Labels') or {}).get(LABEL) != self.project:
            raise ValueError('Container is not owned by this checkout; SSH files were not changed.')
        mapping = self.runtime.run('port', self.name, '2222/tcp').stdout.strip()
        if not re.fullmatch(r'127\.0\.0\.1:[0-9]+', mapping):
            raise ValueError('Expected one loopback-only SSH port mapping.')
        port = int(mapping.rsplit(':', 1)[1])
        if not 1024 <= port <= 65535:
            raise ValueError('Unexpected SSH port mapping.')
        hostkey = self.runtime.run('exec', '--user', '0', self.name, '/bin/cat',
                                   '/var/lib/agent-sshd/ssh_host_ed25519_key.pub').stdout.split()
        if len(hostkey) < 2 or hostkey[0] != 'ssh-ed25519':
            raise ValueError('The sandbox did not return an Ed25519 SSH host key.')
        base64.b64decode(hostkey[1], validate=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(exist_ok=True)
        self.secure(self.state)
        self.write(self.owner, json.dumps(self.identity) + '\n')
        key = self.state / 'id_ed25519'
        public = self.state / 'id_ed25519.pub'
        if not key.exists():
            native([self.keygen, '-q', '-t', 'ed25519', '-N', '', '-C',
                    f'{self.name} sandbox access', '-f', key], runner=self.runtime.runner)
        self.secure(key)
        # Derive the public key from the retained private key, so a partial setup
        # can recover without replacing the identity or trusting a stale .pub.
        public_key = native([self.keygen, '-y', '-P', '', '-f', key], runner=self.runtime.runner).stdout.strip()
        self.write(public, public_key + '\n')
        self.runtime.run('exec', '-i', '--user', '0', self.name, '/bin/sh', '-c',
                         '/bin/cat > /var/lib/agent-sshd/authorized_keys && '
                         '/bin/chmod 0644 /var/lib/agent-sshd/authorized_keys', input=public_key + '\n')
        known = self.state / 'known_hosts'
        self.write(known, f'[127.0.0.1]:{port} {hostkey[0]} {hostkey[1]}\n')
        target = self.state / f'{self.name}.conf'
        self.write(target, f'''Host {self.name}
    HostName 127.0.0.1
    Port {port}
    User agent
    IdentityFile "{key.as_posix()}"
    IdentitiesOnly yes
    IdentityAgent none
    ForwardAgent no
    ForwardX11 no
    UserKnownHostsFile "{known.as_posix()}"
    StrictHostKeyChecking yes
    ServerAliveInterval 30
''')
        include = f'Include "{target.as_posix()}"'
        # Move only our exact managed Include to global scope; retain all other
        # lines verbatim, including existing Host and Match blocks.
        lines = [line for line in self.existing_config.splitlines(keepends=True)
                 if line.strip() != include]
        self.write(self.config, include + '\n' + ''.join(lines))
        print(f'SSH configured: ssh {self.name}')

    def remove(self):
        # Only the exact Include generated by install belongs to this sandbox.
        # Do not normalize or deduplicate unrelated user SSH configuration.
        include = f'Include "{(self.state / (self.name + ".conf")).as_posix()}"'
        content = self.config.read_text(encoding='utf-8-sig') if self.config.exists() else ''
        updated = ''.join(line for line in content.splitlines(keepends=True) if line.strip() != include)
        if updated != content:
            self.write(self.config, updated)
        for filename in (f'{self.name}.conf', 'id_ed25519', 'id_ed25519.pub', 'known_hosts', 'owner.json'):
            (self.state / filename).unlink(missing_ok=True)
        try:
            self.state.rmdir()
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTEMPTY, errno.EEXIST):
                raise
