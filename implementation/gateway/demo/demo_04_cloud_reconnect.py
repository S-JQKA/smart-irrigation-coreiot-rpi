"""Demo 04: MQTT loss, local degraded operation, reconnect, and bounded replay."""

from __future__ import annotations

import argparse

from demo_common import add_common_arguments, load_base_config, run_generated_config, simulation


def build_config(interval: float) -> tuple[dict, int]:
    config = load_base_config()
    config["schemaVersion"] = "2.3-demo-04-cloud-reconnect"
    sim = simulation(config)
    demo_timeout = max(3, int(interval * 6))
    sim["cloudLossTimeoutSeconds"] = demo_timeout
    sim["maxConcurrentZones"] = 1
    sim["startMoisture"] = {"field-1": 25.0, "field-2": 24.0}
    sim["controlModes"] = {"field-1": "AUTO", "field-2": "AUTO"}
    config["localSchedules"] = []
    sim["faultInjections"] = [
        {"atIteration": 5, "action": "DISCONNECT_CLOUD"},
        {"atIteration": 16, "action": "RECONNECT_CLOUD"},
    ]
    return config, demo_timeout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_arguments(parser, default_ticks=22, default_interval=1.0, allow_offline=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config, demo_timeout = build_config(args.interval)
    return run_generated_config(
        config=config,
        args=args,
        title="DEMO 04 - CLOUD LOSS AND RECONNECT",
        purpose="Pause MQTT while local control continues, enter DEGRADED, reconnect, and replay buffered telemetry.",
        notes=[
            "MQTT pauses at tick 5 and reconnects at tick 16; this scenario requires live CoreIoT.",
            f"DEGRADED timeout is accelerated to {demo_timeout}s; the accepted base config remains 60s.",
            "Capture continued local irrigation, DEGRADED mode, reconnect, and replay/buffer logs.",
        ],
        allow_offline=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
