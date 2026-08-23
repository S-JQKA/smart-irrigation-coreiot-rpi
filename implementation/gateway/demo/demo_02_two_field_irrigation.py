"""Demo 02: automatic sequential irrigation for two Fields."""

from __future__ import annotations

import argparse

from demo_common import add_common_arguments, load_base_config, run_generated_config, simulation


def build_config() -> dict:
    config = load_base_config()
    config["schemaVersion"] = "2.3-demo-02-two-field"
    sim = simulation(config)
    sim["maxConcurrentZones"] = 1
    sim["startMoisture"] = {"field-1": 29.0, "field-2": 27.0}
    sim["controlModes"] = {"field-1": "AUTO", "field-2": "AUTO"}
    sim["thresholds"]["startDebounceSamples"] = 2
    sim["thresholdsByZone"] = {
        "field-1": {"minMoistureThreshold": 30, "targetMoisture": 55},
        "field-2": {"minMoistureThreshold": 32, "targetMoisture": 58},
    }
    config["localSchedules"] = []
    sim["faultInjections"] = []
    return config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, default_ticks=32, default_interval=3.5)
    return parser.parse_args()


def main() -> int:
    return run_generated_config(
        config=build_config(),
        args=parse_args(),
        title="DEMO 02 - TWO-FIELD AUTOMATIC IRRIGATION",
        purpose="Show local AUTO decisions, one active Field at a time, and hand-off after reaching target moisture.",
        notes=[
            "maxConcurrentZones=1, so two valves must never be ON simultaneously.",
            "Targets use the accepted 55%/58% values; default pacing keeps Field-1 ON about 46s and Field-2 about 56s.",
            "Capture moisture increase, valve/pump state, and the Field-1 to Field-2 hand-off.",
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
