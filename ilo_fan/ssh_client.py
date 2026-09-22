"""SSH transport for iLO4's fan-control CLI.

Two things make this different from a typical paramiko client:

1. iLO4's SSH server only offers legacy key-exchange and host-key algorithms
   (diffie-hellman-group14-sha1, ssh-rsa/ssh-dss) that modern OpenSSH clients
   and recent paramiko releases (4.0+/5.0+) have dropped. Pin paramiko to the
   3.x line (see requirements.txt) and put these algorithms first when
   negotiating.
2. iLO4's SSH server doesn't support one-shot "exec" channels (running
   `ssh host 'fan info'` just echoes the command back); it only understands
   an interactive PTY shell, same as the upstream project's example scripts
   which drive it via `screen`/`stuff`. We open a single interactive shell
   channel and write commands to it, reading until the output goes quiet.
"""
from __future__ import annotations

import socket
import time
from typing import List, Optional

import paramiko

from .config import IloConfig

_LEGACY_KEX = ("diffie-hellman-group14-sha1", "diffie-hellman-group1-sha1")
_LEGACY_HOST_KEYS = ("ssh-rsa", "ssh-dss")


class IloSSHClient:
    def __init__(self, cfg: IloConfig):
        self.cfg = cfg
        self._sock: Optional[socket.socket] = None
        self._transport: Optional[paramiko.Transport] = None
        self._channel: Optional[paramiko.Channel] = None

    def __enter__(self) -> "IloSSHClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=self.cfg.connect_timeout)
        transport = paramiko.Transport(sock)

        opts = transport.get_security_options()
        opts.kex = _LEGACY_KEX + tuple(k for k in opts.kex if k not in _LEGACY_KEX)
        opts.key_types = _LEGACY_HOST_KEYS + tuple(k for k in opts.key_types if k not in _LEGACY_HOST_KEYS)

        transport.start_client(timeout=self.cfg.connect_timeout)

        if self.cfg.key_path:
            pkey = self._load_private_key()
            transport.auth_publickey(self.cfg.username, pkey)
        else:
            transport.auth_password(self.cfg.username, self.cfg.password)

        channel = transport.open_session(timeout=self.cfg.connect_timeout)
        channel.get_pty(term="vt100", width=200, height=50)
        channel.invoke_shell()
        channel.settimeout(self.cfg.connect_timeout)

        self._sock = sock
        self._transport = transport
        self._channel = channel
        self._read_until_quiet(quiet_seconds=1.0, max_seconds=self.cfg.connect_timeout)  # drain login banner

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def _load_private_key(self) -> paramiko.PKey:
        assert self.cfg.key_path is not None
        last_error: Optional[Exception] = None
        for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
            try:
                return key_cls.from_private_key_file(self.cfg.key_path, password=self.cfg.key_passphrase)
            except paramiko.SSHException as exc:
                last_error = exc
                continue
        raise SystemExit(f"Could not load SSH private key from {self.cfg.key_path}: {last_error}")

    def _read_until_quiet(self, quiet_seconds: float, max_seconds: float) -> str:
        assert self._channel is not None
        chunks: List[bytes] = []
        start = time.monotonic()
        last_data = start
        while True:
            got_data = False
            while self._channel.recv_ready():
                chunks.append(self._channel.recv(4096))
                got_data = True
                last_data = time.monotonic()
            now = time.monotonic()
            if not got_data and (now - last_data) >= quiet_seconds:
                break
            if (now - start) >= max_seconds:
                break
            time.sleep(0.05)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def run(self, command: str, timeout: float = 10.0, check: bool = True) -> str:
        """Send a single fan-CLI command to the interactive shell and return its output."""
        if self._channel is None:
            raise RuntimeError("Not connected; use IloSSHClient as a context manager or call connect() first.")

        self._channel.send(command + "\n")
        output = self._read_until_quiet(quiet_seconds=0.5, max_seconds=timeout)

        lines = output.splitlines()
        if lines and lines[0].strip() == command.strip():
            lines = lines[1:]
        return "\n".join(lines)

    def run_many(self, commands: List[str], timeout: float = 10.0, check: bool = True) -> List[str]:
        """Run a sequence of commands, sleeping cfg.command_delay between each (iLO4's CLI is slow)."""
        outputs = []
        for i, command in enumerate(commands):
            outputs.append(self.run(command, timeout=timeout, check=check))
            if i != len(commands) - 1:
                time.sleep(self.cfg.command_delay)
        return outputs
