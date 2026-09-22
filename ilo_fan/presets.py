"""Load and apply named fan-control presets (raw iLO4 `fan` CLI command lists)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .ssh_client import IloSSHClient

DEFAULT_PRESETS_PATH = Path(__file__).resolve().parent.parent / "config" / "presets.yaml"


@dataclass
class Preset:
    name: str
    description: str
    commands: List[str]


def load_presets(path: Path = DEFAULT_PRESETS_PATH) -> Dict[str, Preset]:
    if not path.exists():
        raise SystemExit(f"Presets file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    presets = {}
    for name, body in (data.get("presets") or {}).items():
        commands = body.get("commands") or []
        if not commands:
            continue
        presets[name] = Preset(
            name=name,
            description=body.get("description", ""),
            commands=list(commands),
        )
    return presets


def get_preset(name: str, path: Path = DEFAULT_PRESETS_PATH) -> Preset:
    presets = load_presets(path)
    if name not in presets:
        available = ", ".join(sorted(presets)) or "(none defined)"
        raise SystemExit(f"Unknown preset {name!r}. Available presets: {available}")
    return presets[name]


def apply_preset(client: IloSSHClient, preset: Preset, dry_run: bool = False) -> Optional[List[str]]:
    if dry_run:
        return list(preset.commands)
    client.run_many(preset.commands, check=False)
    return None
