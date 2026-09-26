# SmartFarm tests

Chạy từ thư mục gốc repository sau khi cài dependency Python. Các lệnh dưới
đây dùng Python Launcher trên Windows; trên Linux, thay `py -3` bằng `python`
trong môi trường ảo đã kích hoạt:

```powershell
py -3 -m unittest discover -s tests/unit -v
py -3 -m unittest discover -s tests/regression -v
node tests/widget_field_selector.test.cjs
```

Unit test kiểm tra MQTT/RPC, analytics, nguồn/CRC, mất mẫu, đồng thuận, slot tưới, tank-low, ACK timeout, flow stale và counter reset. Test double chỉ phục vụ kiểm thử. Regression CoreIoT kiểm cấu trúc artifact, không xác nhận live tenant.

Các test cấu hình kiểm tra phản hồi APPLIED/REJECTED theo Field, cấu hình
hiệu lực, quota và khôi phục cache. Test telemetry dùng broker double trả
ACK muộn/mất ACK và lỗi transport, kiểm tra runtime không chờ PUBACK.
Test widget cần Node.js, dùng DOM và dịch vụ CoreIoT giả lập; không gọi tenant.
Regression đóng gói xác nhận HTML/CSS/JS và test CJS có trong gói mã nguồn.

Các kiểm thử trên xác nhận hành vi phần mềm với đầu vào được kiểm soát.
Kết quả test và build không thay thế kiểm chứng với cảm biến, tải tưới và
cấu hình Raspberry Pi thực tế; xem [phạm vi kiểm chứng](../docs/validation.md).
