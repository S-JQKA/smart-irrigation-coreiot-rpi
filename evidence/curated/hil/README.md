# HIL bốn ESP32-S3 — 29/08/2026

Tóm tắt từ EV-ESP32S3-HIL4-20260829-R01. Hai Sensor phát dữ liệu synthetic qua
ESP-NOW, Bridge nối USB với Gateway laptop, Central trả ACK và điều khiển GPIO/LED.

Kết quả lịch sử: concurrency=2 cho hai Field ON; concurrency=1 giữ Field còn
lại WAITING_FOR_SCHEDULER_SLOT; shutdown đưa hai Field/bơm OFF. Quan sát cùng
phiên gồm CRC reject, dedup, lease expiry, ACK timeout và khôi phục OFF.

Đây là tóm tắt, không phải toàn bộ log để tái phân tích. Log gốc giữ nội bộ.
Không kết nối CoreIoT trong run này, không Pi, không cảm biến/relay/van/bơm
thật. Kết quả thuộc firmware HIL cũ, không nghiệm thu firmware hiện hành.
