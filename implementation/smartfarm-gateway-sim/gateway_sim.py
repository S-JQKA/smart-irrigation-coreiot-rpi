import argparse
import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
CONFIG_PATH = BASE_DIR / "config" / "devices.json"
LOG_DIR = BASE_DIR / "logs"
LOG_PATH = LOG_DIR / "gateway_sim.log"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def utc_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class CoreIoTClient:
    def __init__(self, env: dict[str, str]) -> None:
        scheme = env.get("COREIOT_SCHEME", "https")
        host = env.get("COREIOT_HOST", "app.coreiot.io")
        port = env.get("COREIOT_HTTP_PORT", "443")
        default_port = "443" if scheme == "https" else "80"
        host_part = host if port == default_port else f"{host}:{port}"
        self.base_url = f"{scheme}://{host_part}"

    def post_telemetry(self, token: str, payload: dict) -> tuple[bool, str]:
        url = f"{self.base_url}/api/v1/{token}/telemetry"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=10) as resp:
                return 200 <= resp.status < 300, f"HTTP {resp.status}"
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            return False, f"HTTP {exc.code}: {detail}"
        except error.URLError as exc:
            return False, f"URL error: {exc.reason}"
        except TimeoutError:
            return False, "timeout"


class ZoneSimulator:
    def __init__(self, zone_config: dict, env: dict[str, str]) -> None:
        self.zone_id = zone_config["zone_id"]
        self.field_name = zone_config["field_name"]
        self.soil = zone_config["soil_device"]
        self.valve = zone_config["valve_device"]
        self.water_meter = zone_config["water_meter_device"]
        self.thresholds = zone_config["thresholds"]
        self.water = zone_config["water"]
        self.soil_token = env[self.soil["token_env"]]
        self.valve_token = env[self.valve["token_env"]]
        self.water_token = env[self.water_meter["token_env"]]
        self.moisture = 42.0
        self.battery = 94
        self.valve_on = False
        self.pulse_counter = int(self.water.get("pulseCounterStart", 0))
        self.waterlogging_lock = False

    def update_moisture(self, scenario: str) -> None:
        if scenario == "dry":
            self.moisture -= random.uniform(1.5, 3.0)
        elif scenario == "wet":
            self.moisture += random.uniform(1.5, 3.0)
        elif scenario == "flood":
            self.moisture += random.uniform(4.0, 7.0)
        else:
            if self.valve_on:
                self.moisture += random.uniform(3.5, 6.0)
            else:
                self.moisture -= random.uniform(0.8, 2.2)

        self.moisture = max(5.0, min(95.0, self.moisture))

    def decide(self) -> str:
        min_moisture = self.thresholds["minMoisture"]
        target_moisture = self.thresholds["targetMoisture"]
        max_moisture = self.thresholds["maxMoisture"]
        flood_moisture = self.thresholds["floodMoisture"]

        if self.moisture >= flood_moisture:
            self.valve_on = False
            self.waterlogging_lock = True
            return "WATERLOGGING_LOCK"

        if self.moisture <= max_moisture:
            self.waterlogging_lock = False

        if self.waterlogging_lock:
            self.valve_on = False
            return "LOCKED"

        if self.valve_on and self.moisture >= target_moisture:
            self.valve_on = False
            return "STOP_IRRIGATION"

        if not self.valve_on and self.moisture < min_moisture:
            self.valve_on = True
            return "START_IRRIGATION"

        return "KEEP_IRRIGATING" if self.valve_on else "IDLE"

    def build_payloads(self, decision: str) -> list[tuple[str, str, dict]]:
        timestamp = utc_ms()
        quality = "OK" if 0 <= self.moisture <= 100 else "OUT_OF_RANGE"
        risk = "WaterloggingRisk" if decision == "WATERLOGGING_LOCK" else "NONE"

        soil_payload = {
            "ts": timestamp,
            "values": {
                "moisture": round(self.moisture, 2),
                "battery": self.battery,
                "gatewayMode": "SIM",
                "quality": quality,
                "zoneId": self.zone_id,
                "safetyRisk": risk,
            },
        }

        valve_payload = {
            "ts": timestamp,
            "values": {
                "valveState": "ON" if self.valve_on else "OFF",
                "battery": 88,
                "gatewayMode": "SIM",
                "zoneId": self.zone_id,
                "lastDecision": decision,
            },
        }

        if self.valve_on:
            self.pulse_counter += int(self.water.get("pulsesPerTick", 35))

        water_payload = {
            "ts": timestamp,
            "values": {
                "pulseCounter": self.pulse_counter,
                "battery": 90,
                "gatewayMode": "SIM",
                "zoneId": self.zone_id,
            },
        }

        return [
            (self.soil["name"], self.soil_token, soil_payload),
            (self.valve["name"], self.valve_token, valve_payload),
            (self.water_meter["name"], self.water_token, water_payload),
        ]


def write_log(line: str, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(
    ticks: int | None,
    once: bool,
    dry_run: bool,
    seed: int,
    run_id: str,
    log_path: Path,
    interval_seconds: float | None,
) -> None:
    env = load_env(ENV_PATH)
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    random.seed(seed)

    client = CoreIoTClient(env)
    zones = [ZoneSimulator(zone, env) for zone in config["zones"]]
    interval = interval_seconds if interval_seconds is not None else float(env.get("SIM_INTERVAL_SECONDS", "5"))
    scenario = env.get("SIM_SCENARIO", "auto")
    verification = "SIM" if dry_run else "SIM+PLATFORM"
    max_ticks = 1 if once else ticks
    tick = 0

    print(f"Gateway SIM -> {client.base_url}")
    print(
        f"Run={run_id}, verification={verification}, seed={seed}, "
        f"scenario={scenario}, interval={interval}s, dry_run={dry_run}"
    )
    print(f"Log={log_path}")
    print("Press Ctrl+C to stop.\n")

    try:
        while max_ticks is None or tick < max_ticks:
            tick += 1
            for zone in zones:
                zone.update_moisture(scenario)
                decision = zone.decide()
                payloads = zone.build_payloads(decision)

                statuses = []
                for device_name, token, payload in payloads:
                    if dry_run:
                        ok, status = True, "DRY_RUN"
                    else:
                        ok, status = client.post_telemetry(token, payload)
                    statuses.append(f"{device_name}:{status}")
                    if not ok:
                        print(f"[WARN] {device_name} post failed: {status}")

                log_line = (
                    f"{datetime.now().isoformat(timespec='seconds')} "
                    f"run_id={run_id} verification={verification} seed={seed} "
                    f"tick={tick} zone={zone.zone_id} moisture={zone.moisture:.2f} "
                    f"decision={decision} valve={'ON' if zone.valve_on else 'OFF'} "
                    f"pulseCounter={zone.pulse_counter} statuses={'; '.join(statuses)}"
                )
                print(log_line)
                write_log(log_line, log_path)

            if max_ticks is not None and tick >= max_ticks:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartFarm CoreIoT gateway simulator")
    parser.add_argument("--ticks", type=int, default=None, help="number of loop ticks to run")
    parser.add_argument("--once", action="store_true", help="run only one loop tick")
    parser.add_argument("--dry-run", action="store_true", help="print/log without posting to CoreIoT")
    parser.add_argument("--seed", type=int, default=20260803, help="random seed for reproducible runs")
    parser.add_argument("--run-id", default="DEV-RUN", help="evidence run identifier written to every log line")
    parser.add_argument("--log-path", type=Path, default=LOG_PATH, help="isolated log file for this run")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=None,
        help="override SIM_INTERVAL_SECONDS for a bounded evidence run",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        ticks=args.ticks,
        once=args.once,
        dry_run=args.dry_run,
        seed=args.seed,
        run_id=args.run_id,
        log_path=args.log_path,
        interval_seconds=args.interval_seconds,
    )
