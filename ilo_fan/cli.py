"""Command-line interface for ilofan: apply presets and fan curves over SSH."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import load_config
from .curve import apply_curve_once, load_curve, read_highest_temp
from .presets import apply_preset, get_preset, load_presets
from .ssh_client import IloSSHClient


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config()
    with IloSSHClient(cfg) as client:
        for command in args.command:
            print(f"$ {command}")
            print(client.run(command, check=False))
    return 0


def cmd_preset_list(args: argparse.Namespace) -> int:
    presets = load_presets(Path(args.presets_file))
    if not presets:
        print("No presets defined.")
        return 0
    for name, preset in sorted(presets.items()):
        print(f"{name}: {preset.description}")
    return 0


def cmd_preset_apply(args: argparse.Namespace) -> int:
    preset = get_preset(args.name, Path(args.presets_file))
    print(f"Preset {preset.name!r}: {preset.description}")
    if args.dry_run:
        for command in preset.commands:
            print(f"  (dry-run) {command}")
        return 0

    cfg = load_config()
    with IloSSHClient(cfg) as client:
        apply_preset(client, preset, dry_run=False)
    print(f"Applied preset {preset.name!r} ({len(preset.commands)} commands).")
    return 0


def cmd_curve_apply(args: argparse.Namespace) -> int:
    curve = load_curve(Path(args.curve_file))
    cfg = load_config()
    with IloSSHClient(cfg) as client:
        highest_temp, commands = apply_curve_once(client, curve, dry_run=args.dry_run)
    prefix = "(dry-run) " if args.dry_run else ""
    print(f"Highest watched temperature: {highest_temp:.1f}C")
    for command in commands:
        print(f"  {prefix}{command}")
    return 0


def cmd_curve_monitor(args: argparse.Namespace) -> int:
    curve = load_curve(Path(args.curve_file))
    cfg = load_config()
    interval = args.interval or curve.poll_interval_seconds

    print(f"Monitoring every {interval:.0f}s. Press Ctrl+C to stop.")
    try:
        with IloSSHClient(cfg) as client:
            while True:
                try:
                    highest_temp, commands = apply_curve_once(client, curve, dry_run=args.dry_run)
                    prefix = "(dry-run) " if args.dry_run else ""
                    print(f"[{time.strftime('%H:%M:%S')}] {highest_temp:.1f}C -> {prefix}{', '.join(commands)}")
                except RuntimeError as exc:
                    print(f"[{time.strftime('%H:%M:%S')}] ERROR: {exc}", file=sys.stderr)
                time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")
    return 0


def cmd_debug_session(args: argparse.Namespace) -> int:
    cfg = load_config()
    with IloSSHClient(cfg) as client:
        for command in ("help", "power", "fan info"):
            print(f"$ {command}")
            print(client.run(command, check=False) or "(empty output)")
            print()
    print(
        "If `help`/`power` returned data but `fan info` was empty, you are likely hitting a known "
        "unfixed ilo4_unlock firmware bug (github.com/kendallgoto/ilo4_unlock issues #50/#51) where "
        "`fan` subcommands are accepted but produce no SSH output. `preset apply` may still work "
        "(it doesn't need to read output), but `curve monitor` cannot."
    )
    return 0


def cmd_debug_temps(args: argparse.Namespace) -> int:
    curve = load_curve(Path(args.curve_file))
    cfg = load_config()
    with IloSSHClient(cfg) as client:
        raw = client.run("fan info t", check=False)
        print("--- raw `fan info t` output ---")
        print(raw)
        print("--- parsed readings (verify these against the raw output above) ---")
        try:
            highest_temp, watched = read_highest_temp(client, curve)
        except RuntimeError as exc:
            print(f"ERROR: {exc}")
            return 1
        for idx, temp in sorted(watched.items()):
            print(f"sensor {idx}: {temp}C")
        print(f"highest watched temperature: {highest_temp}C")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ilofan", description="Automate iLO4 fan presets and curves over SSH.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_run = sub.add_parser("run", help="Run one or more raw `fan` CLI commands.")
    p_run.add_argument("command", nargs="+", help="Command(s) to send, e.g. \"fan info\"")
    p_run.set_defaults(func=cmd_run)

    p_preset = sub.add_parser("preset", help="List or apply named presets.")
    preset_sub = p_preset.add_subparsers(dest="preset_command", required=True)

    p_preset_list = preset_sub.add_parser("list", help="List available presets.")
    p_preset_list.add_argument("--presets-file", default=str(Path("config") / "presets.yaml"))
    p_preset_list.set_defaults(func=cmd_preset_list)

    p_preset_apply = preset_sub.add_parser("apply", help="Apply a named preset.")
    p_preset_apply.add_argument("name")
    p_preset_apply.add_argument("--presets-file", default=str(Path("config") / "presets.yaml"))
    p_preset_apply.add_argument("--dry-run", action="store_true", help="Print commands without sending them.")
    p_preset_apply.set_defaults(func=cmd_preset_apply)

    p_curve = sub.add_parser("curve", help="Apply or monitor a temperature-based fan curve.")
    curve_sub = p_curve.add_subparsers(dest="curve_command", required=True)

    p_curve_apply = curve_sub.add_parser("apply", help="Read temperatures once and apply the resulting PWM percent.")
    p_curve_apply.add_argument("--curve-file", default=str(Path("config") / "curve.yaml"))
    p_curve_apply.add_argument("--dry-run", action="store_true")
    p_curve_apply.set_defaults(func=cmd_curve_apply)

    p_curve_monitor = curve_sub.add_parser("monitor", help="Continuously poll temperatures and adjust fan PWM.")
    p_curve_monitor.add_argument("--curve-file", default=str(Path("config") / "curve.yaml"))
    p_curve_monitor.add_argument("--interval", type=float, default=None, help="Override poll_interval_seconds.")
    p_curve_monitor.add_argument("--dry-run", action="store_true")
    p_curve_monitor.set_defaults(func=cmd_curve_monitor)

    p_debug = sub.add_parser("debug", help="Diagnostics.")
    debug_sub = p_debug.add_subparsers(dest="debug_command", required=True)
    p_debug_temps = debug_sub.add_parser("temps", help="Dump raw + parsed temperature output for calibration.")
    p_debug_temps.add_argument("--curve-file", default=str(Path("config") / "curve.yaml"))
    p_debug_temps.set_defaults(func=cmd_debug_temps)

    p_debug_session = debug_sub.add_parser(
        "session", help="Sanity-check the SSH session (help/power/fan info) to isolate firmware bugs."
    )
    p_debug_session.set_defaults(func=cmd_debug_session)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
