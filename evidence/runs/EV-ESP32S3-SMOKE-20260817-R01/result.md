# Result

## PASS — cả hai board ESP32-S3

Mức kiểm chứng: `PHY` — firmware chạy trực tiếp trên hai board ESP32-S3 thật.

### Board 1 — Muse Lab nanoESP32-S3 v1.0

- CH340/UART COM3, bootloader và auto-reset hoạt động.
- ESP32-S3 240 MHz, 2 core; flash 8 MB được ghi và verify thành công.
- OPI PSRAM 8 MB; mẫu dữ liệu 1 MB ghi/đọc PASS.
- Wi-Fi scan PASS, phát hiện 37 mạng; BLE stack init/deinit PASS.
- Serial heartbeat ổn định. LED nguồn không sáng nhưng không ảnh hưởng các bài
  kiểm tra trên.
- Kết quả firmware: `RESULT=PASS`.

### Board 2 — ESP32-S3-A

- USB-Serial/JTAG COM8, bootloader và auto-reset hoạt động.
- Flash 8 MB được ghi và verify thành công.
- OPI PSRAM 8 MB; mẫu dữ liệu 1 MB ghi/đọc PASS.
- Wi-Fi scan PASS, phát hiện 52 mạng; BLE stack init/deinit PASS.
- Serial heartbeat ổn định qua bảy chu kỳ quan sát.
- Kết quả firmware: `RESULT=PASS`.

## Giới hạn

- Chưa kiểm tra GPIO, ADC, I2C, SPI, nút BOOT hoặc LED RGB vì chưa có pinout
  carrier-board đáng tin cậy.
- Nút RESET cơ của board 2 bị hỏng, nhưng auto-reset qua USB hoạt động.
- Đây là smoke test phần cứng độc lập, chưa phải firmware ESP-NOW/SmartFarm và
  chưa chứng minh luồng end-to-end với Raspberry Pi/CoreIoT.
