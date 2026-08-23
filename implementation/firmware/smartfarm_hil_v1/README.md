# SmartFarm HIL firmware v1

Đây là firmware HIL, chưa phải bằng chứng tưới vật lý final. Ba environment dùng
chung protocol v1:

- `sensor_node`: sinh một mẫu moisture/environment synthetic cho Field 1.
- `bridge_usb`: chuyển JSONL có CRC giữa USB serial và ESP-NOW; không quyết định tưới.
- `central_led`: thực hiện valve-before-pump, pump-before-valve khi dừng, dedup
  `commandId` và lease tối đa 15 giây.

## Chuẩn bị

1. Nạp từng board một lần và ghi `STA_MAC` trên Serial Monitor.
2. Điền MAC Bridge/Central vào `include/peer_config.h`.
3. Board có nút reset hỏng chỉ dùng cho `sensor_node`.
4. Kiểm tra lại `SMARTFARM_VALVE_PIN` và `SMARTFARM_PUMP_PIN` trước khi nạp
   Central. Cấu hình mặc định chỉ dành cho LED/output không tải.
5. Không nối relay, bơm hoặc van thật ở giai đoạn HIL.

```powershell
pio run -d implementation/firmware/smartfarm_hil_v1 -e sensor_node
pio run -d implementation/firmware/smartfarm_hil_v1 -e bridge_usb
pio run -d implementation/firmware/smartfarm_hil_v1 -e central_led
```

Pi/laptop chạy `gateway_runtime.py` với
`config/devices.v23.hil-field1.json`. Đặt `SMARTFARM_SERIAL_PORT` nếu cổng không
phải `COM5`. CoreIoT chỉ được connect sáu device được ánh xạ của Field 1; Field 2
giữ `DISABLED` và offline.

`actualValveState` trong HIL chỉ có nghĩa output LED/GPIO đã được Central ACK,
không phải vị trí cơ khí của valve.
