"""Live demo: receive and execute a local schedule configured from the Dashboard."""

from __future__ import annotations

import argparse

from demo_common import add_common_arguments, load_base_config, run_generated_config, simulation


def build_config() -> dict:
    config = load_base_config()
    config["schemaVersion"] = "2.3-demo-dashboard-schedule"
    config["localSchedules"] = []

    sim = simulation(config)
    sim["startMoisture"] = {"field-1": 45.0, "field-2": 48.0}
    sim["controlModes"] = {"field-1": "MANUAL", "field-2": "MANUAL"}
    sim["startEnvironment"] = {
        "field-1": {"airTemp": 27.0, "airHumidity": 65.0, "lightLux": 12000},
        "field-2": {"airTemp": 26.5, "airHumidity": 68.0, "lightLux": 10000},
    }
    sim["faultInjections"] = []
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(
        parser,
        default_ticks=None,
        default_interval=1.0,
        allow_offline=False,
    )
    return parser.parse_args()


def main() -> int:
    return run_generated_config(
        config=build_config(),
        args=parse_args(),
        title="DASHBOARD SCHEDULE DEMO",
        purpose="Keep both Fields idle, receive localScheduleConfig from CoreIoT, and execute it at the requested time.",
        notes=[
            "Both Fields start at normal moisture and MANUAL mode prevents an AUTO start.",
            "No schedule is preloaded; use the Dashboard action Set local schedule on the target Smart Valve.",
            "MANUAL still accepts source=SCHEDULER; DISABLED would reject the schedule.",
            "Expected: config ACK ACCEPTED, then SCHEDULED -> RUNNING -> COMPLETED.",
            "This runner stays online until Ctrl+C.",
        ],
        allow_offline=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())

