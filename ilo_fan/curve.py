"""Fan-curve engine: maps observed temperatures to PWM percentages.

Applying a curve issues `fan p <channel> max <percent>` commands (percent is
a float 0-100, matching the units used by iLO4's `fan p` CLI subcommand).

NOTE on temperature parsing: iLO4's `fan info t` text output format isn't
officially documented. `parse_temperatures()` below is a best-effort heuristic.
Before relying on `curve monitor`, run `ilofan debug temps` to confirm the
parsed sensor indices/values match reality, and adjust the regexes or
curve.yaml's `temperature_sensors` list as needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

from .ssh_client import IloSSHClient

DEFAULT_CURVE_PATH = Path(__file__).resolve().parent.parent / "config" / "curve.yaml"

_IDX_RE = re.compile(r"^\s*(?P<idx>\d+)\D")
_LABELLED_VALUE_RE = re.compile(r"(?:cur\w*|read\w*)[:=]?\s*(?P<temp>-?\d+(?:\.\d+)?)", re.IGNORECASE)
_ANY_NUMBER_RE = re.compile(r"(?P<temp>-?\d+(?:\.\d+)?)")


@dataclass
class CurvePoint:
    temp_c: float
    percent: float


@dataclass
class FanCurveConfig:
    poll_interval_seconds: float
    temperature_sensors: List[int]
    pwm_channels: List[int]
    min_percent: float
    max_percent: float
    points: List[CurvePoint]


def load_curve(path: Path = DEFAULT_CURVE_PATH) -> FanCurveConfig:
    if not path.exists():
        raise SystemExit(f"Curve file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    points = [CurvePoint(float(t), float(p)) for t, p in data.get("points", [])]
    points.sort(key=lambda pt: pt.temp_c)
    if not points:
        raise SystemExit("Curve file must define at least one [temp_c, percent] point.")

    return FanCurveConfig(
        poll_interval_seconds=float(data.get("poll_interval_seconds", 30)),
        temperature_sensors=[int(x) for x in data.get("temperature_sensors", [])],
        pwm_channels=[int(x) for x in data.get("pwm_channels", [])],
        min_percent=float(data.get("min_pwm_percent", 20)),
        max_percent=float(data.get("max_pwm_percent", 100)),
        points=points,
    )


def parse_temperatures(raw_text: str) -> Dict[int, float]:
    """Best-effort parse of `fan info t` output into {sensor_index: celsius}.

    Prefers a value following a "Cur"/"Read" label anywhere on the line
    (e.g. "35 (CPU 1) Cur:55 Caut:70"); falls back to the first number found
    after the sensor index if no such label is present.
    """
    readings: Dict[int, float] = {}
    for line in raw_text.splitlines():
        idx_match = _IDX_RE.match(line)
        if not idx_match:
            continue
        rest = line[idx_match.end():]

        value_match = _LABELLED_VALUE_RE.search(rest) or _ANY_NUMBER_RE.search(rest)
        if not value_match:
            continue
        readings[int(idx_match.group("idx"))] = float(value_match.group("temp"))
    return readings


def percent_for_temp(curve: FanCurveConfig, temp_c: float) -> float:
    points = curve.points
    if temp_c <= points[0].temp_c:
        percent = points[0].percent
    elif temp_c >= points[-1].temp_c:
        percent = points[-1].percent
    else:
        percent = points[-1].percent
        for lo, hi in zip(points, points[1:]):
            if lo.temp_c <= temp_c <= hi.temp_c:
                span = hi.temp_c - lo.temp_c
                ratio = 0.0 if span == 0 else (temp_c - lo.temp_c) / span
                percent = lo.percent + ratio * (hi.percent - lo.percent)
                break
    return max(curve.min_percent, min(curve.max_percent, percent))


def build_apply_commands(curve: FanCurveConfig, percent: float) -> List[str]:
    percent_str = f"{percent:.1f}"
    return [f"fan p {channel} max {percent_str}" for channel in curve.pwm_channels]


def read_highest_temp(client: IloSSHClient, curve: FanCurveConfig) -> Tuple[float, Dict[int, float]]:
    raw = client.run("fan info t", check=False)
    readings = parse_temperatures(raw)
    watched = {
        idx: t
        for idx, t in readings.items()
        if not curve.temperature_sensors or idx in curve.temperature_sensors
    }
    if not watched:
        raise RuntimeError(
            "Could not parse any temperature readings from `fan info t` output "
            f"(raw response: {raw!r}). This can mean the parser needs tuning, OR you're hitting "
            "a known unfixed ilo4_unlock firmware bug where `fan` subcommands return empty output "
            "over SSH (see github.com/kendallgoto/ilo4_unlock issues #50/#51) even though the "
            "command was accepted. Run `ilofan debug temps` to inspect raw output, and "
            "`ilofan debug session` to confirm the SSH session itself is healthy. If `fan` output "
            "is reliably empty, `curve monitor` cannot work on this firmware -- use `preset apply` "
            "instead, since 'set' commands (e.g. `fan p N min X`) take effect even without visible output."
        )
    return max(watched.values()), watched


def apply_curve_once(client: IloSSHClient, curve: FanCurveConfig, dry_run: bool = False) -> Tuple[float, List[str]]:
    highest_temp, _ = read_highest_temp(client, curve)
    percent = percent_for_temp(curve, highest_temp)
    commands = build_apply_commands(curve, percent)
    if not dry_run:
        client.run_many(commands, check=False)
    return highest_temp, commands
