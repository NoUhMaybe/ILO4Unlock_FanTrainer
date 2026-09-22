"""Environment-variable based configuration for iLO4 SSH access."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class IloConfig:
    host: str
    port: int
    username: str
    password: Optional[str]
    key_path: Optional[str]
    key_passphrase: Optional[str]
    command_delay: float
    connect_timeout: float


def load_config() -> IloConfig:
    """Load iLO SSH connection settings from environment variables (.env supported)."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    host = os.environ.get("ILO_HOST")
    username = os.environ.get("ILO_USERNAME")
    if not host or not username:
        raise SystemExit(
            "ILO_HOST and ILO_USERNAME must be set (copy .env.example to .env and edit it)."
        )

    password = os.environ.get("ILO_PASSWORD") or None
    key_path = os.environ.get("ILO_SSH_KEY_PATH") or None
    if not password and not key_path:
        raise SystemExit("Set either ILO_PASSWORD or ILO_SSH_KEY_PATH in the environment.")

    return IloConfig(
        host=host,
        port=int(os.environ.get("ILO_PORT", "22")),
        username=username,
        password=password,
        key_path=key_path,
        key_passphrase=os.environ.get("ILO_SSH_KEY_PASSPHRASE") or None,
        command_delay=float(os.environ.get("ILO_COMMAND_DELAY", "0.3")),
        connect_timeout=float(os.environ.get("ILO_CONNECT_TIMEOUT", "15")),
    )
