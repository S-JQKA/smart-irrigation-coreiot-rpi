"""Demo 03: TankLow safety interlock stops irrigation and later clears."""

from __future__ import annotations

import argparse

from demo_common import add_common_arguments, load_base_config, run_generated_config, simulation


def build_config() -> dict:
    config = load_base_config()
    config["schemaVersion"] = "2.3-demo-03-tank-low"
    sim = simulation(config)
    sim["startMoisture"] = {"field-1": 20.0, "field-2": 45.0}
    sim["controlModes"] = {"field-1": "MANUAL", "field-2": "DISABLED"}
    config["localSchedules"] = [
        {
            "id": "demo-tank-low-field-1",
            "enabled": True,
            "zoneId": "field-1",
            "startAfterSeconds": 2,
            "durationSeconds": 30,
        }
    ]
    sim["faultInjections"] = [
        {"atIteration": 6, "action": "SET_TANK_LEVEL_PCT", "value": 5},
        {"atIteration": 11, "action": "SET_TANK_LEVEL_PCT", "value": 70},
    ]
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, default_ticks=16, default_interval=1.0)
    return parser.parse_args()


def main() -> int:
    return run_generated_config(
        config=build_config(),
        args=parse_args(),
        title="DEMO 03 - TANKLOW SAFETY INTERLOCK",
        purpose="Start Field-1 from a local schedule, inject low tank level, and verify fail-safe shutdown.",
        notes=[
            "Field-1 schedule starts after 2 seconds; TankLow is injected at tick 6 and restored at tick 11.",
            "Expected result: valve and pump go OFF immediately; CoreIoT shows alarm create then clear.",
            "Fault injection is explicit simulator input, not a claim of physical sensor validation.",
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
