# ESP32-S3 basic smoke test

Diagnostic firmware for an ESP32-S3-WROOM-1 N8R8 board. It checks:

- ESP32-S3 chip identity and CPU startup;
- 8 MB flash detection;
- 1 MB write/read pattern in external PSRAM;
- Wi-Fi scan without connecting to a network;
- BLE stack initialization;
- serial heartbeat and runtime stability.

The test intentionally does not drive GPIO or an onboard RGB LED because the
borrowed boards use different carrier-board pinouts.

PlatformIO environments:

- `esp32_s3_n8r8`: native USB-Serial/JTAG output;
- `esp32_s3_n8r8_uart`: UART0 output through a CH340 bridge.
