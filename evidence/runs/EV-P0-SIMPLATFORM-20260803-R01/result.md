# Result — EV-P0-SIMPLATFORM-20260803-R01

## Trạng thái

`PASS` — run 20 tick hoàn tất, 60/60 lượt gửi telemetry trả HTTP 200 và ba thiết bị đã được xác nhận trên giao diện CoreIoT lúc 21:57:06 ngày 03/08/2026.

## Kết quả nền đã biết

Run hiện tại dùng seed `20260803`. Tại tick 11, moisture giảm xuống `24.86`, quyết định `START_IRRIGATION`, valve ON và `pulseCounter=35`. Tại tick 17, moisture đạt `55.96`, quyết định `STOP_IRRIGATION`, valve OFF và `pulseCounter=210`. Từ tick 17 đến tick 20, pulseCounter giữ nguyên ở 210.

## Checklist hoàn tất

- [x] Chạy đúng lệnh trong `command.txt`.
- [x] Raw log có đúng `run_id`, seed và 20 tick.
- [x] 60/60 device post trả HTTP 200.
- [x] Có `START_IRRIGATION` và `STOP_IRRIGATION`.
- [x] `pulseCounter` chỉ tăng khi valve ON.
- [x] Chụp CoreIoT latest telemetry của Soil Moisture, Smart Valve và Water Meter.
- [x] Kiểm tra bundle không chứa `.env`, token hoặc secret.
- [x] Cập nhật `run.json` và kết luận cuối thành `PASS`.

## Evidence dự kiến cho báo cáo giữa kỳ

1. Raw log thể hiện chu trình bật tưới tại tick 11 và dừng tưới tại tick 17.
2. Ảnh CoreIoT Soil Moisture: `moisture=49.97`, `gatewayMode=SIM`, `quality=OK`.
3. Ảnh CoreIoT Smart Valve: `valveState=OFF`, `lastDecision=IDLE`, `zoneId=zone_1`.
4. Ảnh CoreIoT Water Meter: `pulseCounter=210`, `zoneId=zone_1`.

## Kết luận

BL-001 đạt Definition of Done ở mức `SIM+PLATFORM`. Các cảm biến, valve, water meter và vai trò Raspberry Pi vẫn là mô phỏng; CoreIoT tenant/API là thành phần thật.
