# SmartFarm HIL firmware v1

Đây là firmware HIL, chưa phải bằng chứng tưới vật lý final. Các environment
dùng chung protocol v1:

- `sensor_node`: alias tương thích cũ cho Sensor Field 1.
- `sensor_field1_n16r8`: sinh bốn moisture value và environment/flow synthetic
  cho Field 1 trên board N16R8.
- `sensor_field2_n16r8`: tương tự cho Field 2 với `nodeId`/`zoneId` độc lập.
- `bridge_usb`: chuyển JSONL có CRC giữa USB serial và ESP-NOW; không quyết định tưới.
- `central_led`: thực hiện valve-before-pump, pump-before-valve khi dừng, dedup
  `commandId`, lease riêng từng Field và tối đa hai valve đồng thời.

## Chuẩn bị

1. Nạp từng board một lần và ghi `STA_MAC` trên Serial Monitor.
   Các `board = ...` trong `platformio.ini` là baseline compile; kiểm tra đúng
   biến thể flash/PSRAM của board mượn trước khi upload.
2. Điền MAC Bridge/Central vào `include/peer_config.h`.
3. Nếu nút reset hỏng nhưng USB native còn hoạt động, nạp/monitor qua cổng
   `USB` thay cho UART bridge; vẫn phải xác minh OFF-at-boot trước khi dùng làm
   Central.
4. Kiểm tra lại `SMARTFARM_VALVE1_PIN`, `SMARTFARM_VALVE2_PIN` và
   `SMARTFARM_PUMP_PIN` trước khi nạp Central. Baseline hiện dùng GPIO48,
   GPIO4 và GPIO47; tất cả chỉ dành cho LED/GPIO không tải. Tránh GPIO33–37
   trên N16R8 vì Octal PSRAM sử dụng nhóm chân này.
5. Không nối relay, bơm hoặc van thật ở giai đoạn HIL.

```powershell
pio run -d implementation/firmware/smartfarm_hil_v1 -e sensor_node
pio run -d implementation/firmware/smartfarm_hil_v1 -e sensor_field1_n16r8
pio run -d implementation/firmware/smartfarm_hil_v1 -e sensor_field2_n16r8
pio run -d implementation/firmware/smartfarm_hil_v1 -e bridge_usb
pio run -d implementation/firmware/smartfarm_hil_v1 -e central_led
```

Laptop chạy `gateway_runtime.py` với
`config/devices.v23.hil-two-field-4board.json`. Đặt `SMARTFARM_SERIAL_PORT` nếu
cổng không phải `COM5`. Hai Sensor Node phát bốn giá trị soil synthetic cho mỗi
Field để ánh xạ đủ topology CoreIoT; đây không phải bốn cảm biến vật lý.
Sensor dùng các key protocol v1 ngắn mà Gateway đã hỗ trợ (`airTemp`,
`airHumidity`, `flowRateLpm`, `soilMoisture`) để frame bốn soil value luôn nằm
trong giới hạn ESP-NOW v1 250 byte. Firmware đo kích thước JSON trước khi gửi và
từ chối frame quá cỡ thay vì truyền dữ liệu bị cắt.

`actualValveState` trong HIL chỉ có nghĩa output LED/GPIO đã được Central ACK,
không phải vị trí cơ khí của valve.

Gateway config chấp nhận `maxConcurrentZones=1|2`; file HIL bốn board mặc định
là `2`. Central được build với hard cap hai valve. Mỗi zone có lease/command
riêng: dừng một zone giữ pump ON nếu zone kia còn ON; dừng zone cuối cùng sẽ tắt
pump trước valve. Tank-low, pump-without-valve hoặc valve-without-pump vẫn
`ALL_OFF`.

Không đổi `finalHardwareVerified=true`: HIL này dùng sensor synthetic và
LED/GPIO, không phải relay, flow/tank sensor, van hoặc bơm thật.
