# ESP-NOW two-board link test

Physical diagnostic for the two borrowed ESP32-S3 boards:

- `sender_uart`: Muse Lab nanoESP32-S3 v1.0 over CH340/UART;
- `receiver_usb`: ESP32-S3-A over native USB-Serial/JTAG;
- `receiver_uart`: the same receiver role over its CH343/UART port.

The sender transmits 20 versioned telemetry packets on fixed channel 1. Each
packet contains a sequence number and CRC16-CCITT. The receiver validates the
packet and returns an application ACK. The sender retries at most three times
with backoff and reports application-level round-trip time.

This is an unencrypted PHY/HIL diagnostic. PMK/LMK encryption, Pi UART framing,
range testing and production retry/buffering remain separate work.
