# Thiết lập thủ công alarm NodeOffline trên CoreIoT

Không import lại Device Profile hoặc Rule Chain. Thực hiện bằng giao diện trên
bảy Device Profile hiện có.

## Cấu hình chung

- Alarm type: `NodeOffline`
- Severity: `Major`
- Create condition key type: `Attribute`
- Attribute scope: `Server attribute`
- Key: `active`
- Value type: `Boolean`
- Operation: `Equal`
- Value: `false`
- Condition type: `Duration`
- Duration: `300 seconds`
- Clear condition: cùng server attribute `active`, Boolean `Equal true`, kiểu `Simple`
- Propagate alarm: bật

## Relation cần chọn theo profile

| Device Profile | Relation type |
|---|---|
| `SI Soil Moisture Sensor` | `FieldToMoistureSensor` |
| `SI Smart Valve` | `FieldToSmartValve` |
| `SI Water Meter` | `FieldToWaterMeter` |
| `SF Env Sensor` | `FieldToEnvSensor` |
| `SF Pump Controller` | `SiteToPump` |
| `SF Manifold Controller` | `SiteToManifold` |
| `SF Gateway` | `SiteToGateway` |

Không bật propagate to owner/tenant. Nếu giao diện không cho chọn relation type
không tồn tại, lưu alarm không propagate trước và kiểm tra lại Relations của
entity; không tự tạo relation tên mới.

## Kiểm thử tenant

1. Chạy simulator và xác nhận thiết bị có server attribute `active=true`.
2. Dừng simulator sạch; Gateway gửi disconnect cho downstream devices.
   Terminal phải có `CoreIoT downstream disconnect requested=16 acknowledged=16`
   trước dòng MQTT disconnected. Nếu không đủ 16 ACK, chưa bắt đầu tính 300 giây.
3. Đợi hơn 300 giây, mở trang Alarms với bộ lọc Active/Any.
4. Xác nhận `NodeOffline` Major xuất hiện trên đúng originator.
5. Chạy lại simulator; xác nhận `active=true` và alarm chuyển sang Cleared.

Lần đầu chỉ cần live-verify một thiết bị đại diện, khuyến nghị
`SI Soil Moisture 1`, rồi kiểm tra thêm `SF Main Pump 1` và `SF Manifold 1` để
chứng minh cả relation Field và Site. Việc generator/static test PASS chỉ là
`local-tested`, không thay cho bằng chứng tenant.

Lần thử đầu ngày 08/08 cho thấy tenant vẫn giữ 17 device ở trạng thái Active vì
simulator dừng MQTT ngay sau khi enqueue 16 disconnect QoS 1 mà chưa chờ PUBACK.
Gateway client đã được sửa để chờ ACK theo deadline rồi mới ngắt transport.

Live retest `EV-V23-NODE-OFFLINE-20260808-R05` PASS create và clear. Nhóm SI cũ
từng có server attribute `inactivityTimeout=1576800000` ms (khoảng 18,25 ngày);
override legacy này phải được xóa để Device State dùng timeout của tenant.
