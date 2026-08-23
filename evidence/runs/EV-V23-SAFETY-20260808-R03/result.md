# Result

## PASS

1. Tank-low cắt ngay Field 1 và pump, đưa site về `SAFE-IDLE`; sau khi phục hồi
   tank level, site trở lại `NORMAL`.
2. Flow fault ở Field 2 đạt grace 15 giây, dừng van/pump, phát cả
   `ZoneFlowLow` và `PumpDryRun`, rồi clear sau thao tác clear fault.
3. MQTT bị ngắt 80 giây. Runtime chỉ chuyển `DEGRADED` sau timeout 60 giây,
   sau đó reconnect về `NORMAL`.
4. Reconnect chạy đúng đường production: re-announce 16 downstream device,
   đăng ký lại 2 shared-attribute watch và replay 32 buffered message.
5. CoreIoT tạo và clear đủ bốn alarm live tương ứng.

## Giới hạn còn lại

- Trong live run R03, existing `localScheduleConfig` được trả lại ở startup và
  reconnect nhưng bị Gateway từ chối `INVALID_COMMAND`; batch safety dùng schedule
  từ config nên không bị ảnh hưởng. Gateway đã được sửa local sau run này để bỏ
  qua response không có value, không chạy trễ one-shot đã hết hạn, roll-forward
  recurring và giữ `configId` qua restart. Unit 77/77 và regression 33/33 PASS;
  Đã live-verify lại startup/reconnect sau fix trong
  `EV-V23-SCHEDULE-20260808-R04`: startup trả `STALE_ONE_SHOT_IGNORED`, reconnect
  trả `DUPLICATE`, không còn `INVALID_COMMAND`.
- Buffer hiện nằm trong RAM; batch này không chứng minh replay qua process reboot.
- Đây là SIM+PLATFORM, không phải lỗi vật lý từ ESP32/van/bơm thật.
