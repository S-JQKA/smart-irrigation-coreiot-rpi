# SmartFarm tests

Chạy từ repository root, không cần local_dev:

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
Regression đóng gói xác nhận HTML/CSS/JS và test CJS có trong danh sách bản nộp.

Bộ test dùng mô hình tưới và HIL cũ nằm trên máy ở local_dev/tests, chạy bằng `py -3 local_dev/run_tests.py`. Không suy diễn test/build thành nghiệm thu cảm biến, tải nước hoặc Pi.
