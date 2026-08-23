"""Run the short SmartFarm demo scenarios in their recommended order."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parent
CORE_SCRIPTS = [
    "demo_01_coreiot_connection.py",
    "demo_02_two_field_irrigation.py",
    "demo_03_tank_low_safety.py",
]
CLOUD_SCRIPT = "demo_04_cloud_reconnect.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="Run all four scenarios against CoreIoT")
    mode.add_argument("--offline", action="store_true", help="Rehearse the first three scenarios locally")
    mode.add_argument("--validate-only", action="store_true", help="Validate all four generated configs")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scripts = [*CORE_SCRIPTS]
    child_args: list[str] = []
    if args.live:
        scripts.append(CLOUD_SCRIPT)
    elif args.offline:
        child_args.append("--offline")
        print("Offline rehearsal skips demo 04 because MQTT reconnect requires a live transport.", flush=True)
    else:
        scripts.append(CLOUD_SCRIPT)
        child_args.append("--validate-only")

    for script in scripts:
        print(f"\n>>> Starting {script}", flush=True)
        completed = subprocess.run(
            [sys.executable, str(DEMO_DIR / script), *child_args],
            cwd=DEMO_DIR,
            check=False,
        )
        if completed.returncode != 0:
            print(f"Sequence stopped: {script} returned {completed.returncode}.", flush=True)
            return completed.returncode

    print("\nDemo sequence completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

