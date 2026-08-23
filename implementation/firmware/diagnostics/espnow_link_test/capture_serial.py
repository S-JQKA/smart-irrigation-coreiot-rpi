"""Stream timestamped serial lines from one ESP32 diagnostic port."""

from __future__ import annotations

import argparse
import time

import serial


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    device = serial.Serial()
    device.port = args.port
    device.baudrate = args.baud
    device.timeout = 0.2
    device.dtr = False
    device.rts = False
    device.open()

    started_at = time.monotonic()
    try:
        while time.monotonic() - started_at < args.seconds:
            line = device.readline()
            if not line:
                continue
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            print(f"{elapsed_ms:06d}ms {args.port} {text}", flush=True)
    finally:
        device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

