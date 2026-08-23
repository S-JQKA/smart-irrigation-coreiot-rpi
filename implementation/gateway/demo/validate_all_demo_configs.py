"""Validate every generated demo configuration without MQTT or simulator ticks."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


DEMO_DIR = Path(__file__).resolve().parent
SCRIPTS = [
    "demo_01_coreiot_connection.py",
    "demo_02_two_field_irrigation.py",
    "demo_03_tank_low_safety.py",
    "demo_04_cloud_reconnect.py",
    "demo_dashboard_schedule.py",
]


def main() -> int:
    for script in SCRIPTS:
        print(f"Validating {script} ...", flush=True)
        completed = subprocess.run(
            [sys.executable, str(DEMO_DIR / script), "--validate-only"],
            cwd=DEMO_DIR,
            check=False,
        )
        if completed.returncode != 0:
            return completed.returncode
    print("All demo configurations: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
