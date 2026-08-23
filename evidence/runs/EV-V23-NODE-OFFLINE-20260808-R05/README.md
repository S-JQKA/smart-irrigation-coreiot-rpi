# EV-V23-NODE-OFFLINE-20260808-R05

## Phạm vi

Live-verify alarm `NodeOffline` trên tenant CoreIoT cho cả nhánh Field và Site,
dùng Gateway simulator v2.3 và bảy Device Profile được chỉnh thủ công.

## Kết quả

- Gateway shutdown gửi `v1/gateway/disconnect` cho 16 downstream device và chờ
  đủ `16/16` PUBACK trước khi ngắt MQTT.
- CoreIoT tạo alarm `NodeOffline` Major cho các originator đại diện:
  `SI Soil Moisture 1`, `SF Main Pump 1`, `SF Manifold 1` và
  `SmartFarm Pi Gateway`.
- Khi simulator kết nối lại, trang Alarms lọc Active/All time trả
  `No alarms found`; notification xác nhận nhiều `NodeOffline` đã cleared.
- Không sửa hoặc import Rule Chain.

## Phân loại bằng chứng

- Runtime: `SIM+PLATFORM`.
- Gateway code/tests: `local-tested` (`79/79` unit + `34/34` regression).
- Alarm create/clear: `live-verified` trên tenant ngày 2026-08-08.
- Không phải bằng chứng phần cứng ESP32/Raspberry Pi thật.

## File

- `01_disconnect_ack_16.png`: terminal xác nhận 16/16 downstream PUBACK.
- `02_inactive_devices.png`: Gateway/Pump/Manifold/Env chuyển Inactive.
- `03_alarm_created.png`: notification `NodeOffline` trên Soil device.
- `04_alarm_cleared_list.png`: trang Active không còn alarm.
- `05_alarm_cleared_notifications.png`: notification clear sau reconnect.
- `tenant_alarm_observation.md`: quan sát tenant và boundary.
- `result.md`: verdict và nguyên nhân lỗi đã xử lý.
- `run.json`: metadata máy đọc được.
