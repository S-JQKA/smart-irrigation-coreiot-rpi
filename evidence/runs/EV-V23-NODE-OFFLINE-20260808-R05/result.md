# Result

## PASS

1. Device Profile alarm đọc server attribute `active` và tạo `NodeOffline`
   Major sau duration 300 giây.
2. Gateway graceful shutdown chờ đủ 16 QoS 1 PUBACK; tenant nhận đúng đường
   downstream disconnect.
3. Alarm tạo đúng trên cả relation Field và Site.
4. Reconnect đưa device về Active và clear toàn bộ `NodeOffline` đang active.

## Lỗi phát hiện và sửa

- Lần thử đầu: simulator enqueue disconnect rồi dừng network loop, tenant tiếp
  tục giữ device Active. Gateway client được sửa để chờ PUBACK và gửi MQTT
  disconnect trước khi dừng loop.
- Nhóm SI legacy có `inactivityTimeout=1576800000` ms, làm trạng thái Active
  kéo dài khoảng 18,25 ngày. Override legacy được loại bỏ trên tenant.

## Giới hạn

- Live verification dùng logical devices và simulator, không phải lỗi vật lý.
- Tenant dùng Device State inactivity timeout trước khi alarm duration bắt đầu;
  thời gian tổng có thể lớn hơn 300 giây.
