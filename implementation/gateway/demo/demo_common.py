"""Shared helpers for the short SmartFarm/CoreIoT demo scenarios."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEMO_DIR = Path(__file__).resolve().parent
GATEWAY_DIR = DEMO_DIR.parent
REPO_ROOT = GATEWAY_DIR.parents[1]
BASE_CONFIG = GATEWAY_DIR / "config" / "devices.v23.example.json"
SIMULATOR = GATEWAY_DIR / "simulator_v23.py"


def load_base_config() -> dict[str, Any]:
    """Load a fresh copy of the accepted two-Field v2.3 configuration."""

    return json.loads(BASE_CONFIG.read_text(encoding="utf-8"))


def simulation(config: dict[str, Any]) -> dict[str, Any]:
    return config["simulation"]


def add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_ticks: int | None,
    default_interval: float,
    allow_offline: bool = True,
) -> None:
    parser.add_argument(
        "--ticks",
        type=int,
        default=default_ticks,
        help="Number of simulator ticks; omit in continuous scenarios to run until Ctrl+C",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=default_interval,
        help="Seconds between ticks; scenario timings are tuned for the default",
    )
    if allow_offline:
        parser.add_argument(
            "--offline",
            action="store_true",
            help="Rehearse locally without publishing to CoreIoT",
        )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the generated config without running any ticks or MQTT connection",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])


def validate_cli_values(args: argparse.Namespace) -> None:
    if args.ticks is not None and args.ticks < 1:
        raise SystemExit("--ticks must be at least 1")
    if args.interval <= 0:
        raise SystemExit("--interval must be greater than 0")


def require_live_credentials(config: dict[str, Any]) -> None:
    gateway = config["gateway"]
    missing = [
        env_name
        for env_name in (gateway["hostEnv"], gateway["tokenEnv"])
        if not os.getenv(env_name)
    ]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(
            f"Missing live CoreIoT environment variable(s): {joined}. "
            "Set them first or use --offline when that scenario allows it."
        )


def print_banner(title: str, purpose: str, notes: list[str]) -> None:
    print("=" * 72)
    print(title)
    print(purpose)
    for note in notes:
        print(f"- {note}")
    print("=" * 72, flush=True)


def run_generated_config(
    *,
    config: dict[str, Any],
    args: argparse.Namespace,
    title: str,
    purpose: str,
    notes: list[str],
    allow_offline: bool = True,
) -> int:
    """Write an isolated config and invoke the authoritative v2.3 simulator."""

    validate_cli_values(args)
    offline = bool(getattr(args, "offline", False))
    if offline and not allow_offline:
        raise SystemExit("This scenario requires a live CoreIoT MQTT connection.")
    if not args.validate_only and not offline:
        require_live_credentials(config)

    mode = "CONFIG VALIDATION" if args.validate_only else ("OFFLINE REHEARSAL" if offline else "LIVE COREIOT")
    print_banner(title, purpose, [f"Run mode: {mode}", *notes])

    if args.validate_only:
        # Validate in memory so preflight never creates runtime/persistence files.
        sys.path.insert(0, str(GATEWAY_DIR))
        from simulator_v23 import (  # pylint: disable=import-outside-toplevel
            LocalScheduleRunner,
            SimulationModelV23,
            fault_injections_from_config,
        )

        model = SimulationModelV23.from_config(config)
        LocalScheduleRunner.from_config(config, model, int(time.time() * 1000))
        fault_injections_from_config(config, model)
        print("Configuration validation: PASS", flush=True)
        return 0

    # Keep runtime files inside the workspace. This also avoids Windows Store/
    # sandbox ACL differences sometimes present in the user TEMP directory.
    with tempfile.TemporaryDirectory(prefix=".runtime-", dir=DEMO_DIR) as temp_dir:
        config_path = Path(temp_dir) / "scenario.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        command = [
            sys.executable,
            str(SIMULATOR),
            "--config",
            str(config_path),
        ]
        if args.ticks is not None:
            command.extend(["--ticks", str(args.ticks)])
        command.extend(["--interval", str(args.interval), "--log-level", args.log_level])
        if offline:
            command.append("--offline")

        try:
            completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
            return completed.returncode
        except KeyboardInterrupt:
            print("Demo stopped by operator.", flush=True)
            return 0
