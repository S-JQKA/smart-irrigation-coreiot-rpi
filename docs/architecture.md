# Kiến trúc SmartFarm

Hai Field, mỗi Field có một ESP32-S3 Sensor Node đọc bốn soil, SHT31 và BH1750.
Hai Sensor truyền ESP-NOW tới Bridge. Bridge nối USB/UART với Gateway Python
chạy trên Raspberry Pi mục tiêu. Một Central dùng chung đọc hai flow, một
phao cạn và điều khiển hai van cùng một bơm qua driver.

| Thành phần | Trách nhiệm |
|---|---|
| Sensor Node | Đọc/hiệu chuẩn cảm biến, gửi số đo và lỗi |
| Bridge | Kiểm tra frame, chuyển UART và ESP-NOW |
| Gateway | Kiểm tra dữ liệu, lịch, arbitration, quota, safety, RPC và telemetry |
| Central | GPIO sequencing, phao interlock, lease, dedup và ACK |
| CoreIoT | Entity, cấu hình, lịch sử, dashboard, alarm và yêu cầu manual |

Gateway mặc định tối đa một vùng tưới đồng thời; cấu hình hỗ trợ hai vùng.
ON cần dữ liệu hợp lệ và interlock cho phép. CoreIoT recommendation và
analytics không ghi actual hoặc tự quyết định điều khiển.

Mất cloud, Gateway dùng cấu hình cục bộ và tiếp tục safety. MQTT replay nằm
trong RAM, giới hạn TTL. Lịch/cấu hình/command ledger có persistence;
tổng nước chưa bền qua Gateway reboot.

Xem [module Gateway](../implementation/gateway/README.md) và
[giới hạn kiểm chứng](validation.md).
