"""Configuration and process environment for SmartFarm Gateway."""

import json
import os
import time
from pathlib import Path
from typing import Any
from coreiot.gateway_client import GatewaySettings


def utc_ms() -> int:
    return int(time.time() * 1000)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def settings_from_env(config: dict[str, Any]) -> GatewaySettings:
    gateway = config["gateway"]
    host = os.getenv(gateway["hostEnv"], "")
    token = os.getenv(gateway["tokenEnv"], "")
    port = int(os.getenv(gateway["portEnv"], "1883"))
    tls = os.getenv(gateway["tlsEnv"], "false").lower() in {"1", "true", "yes"}
    return GatewaySettings(host=host, port=port, access_token=token, tls=tls)
