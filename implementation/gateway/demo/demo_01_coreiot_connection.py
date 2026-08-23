"""Demo 01: connect the Gateway and publish the complete device topology."""

from __future__ import annotations

import argparse

from demo_common import add_common_arguments, load_base_config, run_generated_config, simulation


def build_config() -> dict:
    config = load_base_config()
    config["schemaVersion"] = "2.3-demo-01-connection"
    sim = simulation(config)
    sim["controlModes"] = {"field-1": "DISABLED", "field-2": "DISABLED"}
    config["localSchedules"] = []
    sim["faultInjections"] = []
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, default_ticks=8, default_interval=1.0)
    return parser.parse_args()


def main() -> int:
    return run_generated_config(
        config=build_config(),
        args=parse_args(),
        title="DEMO 01 - COREIOT CONNECTION AND TELEMETRY",
        purpose="Show one Gateway session publishing the accepted 16-device, two-Field topology.",
        notes=[
            "Both Fields are DISABLED so this clip focuses only on connectivity and telemetry.",
            "Capture the terminal connection logs, then refresh the CoreIoT device/dashboard view.",
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
