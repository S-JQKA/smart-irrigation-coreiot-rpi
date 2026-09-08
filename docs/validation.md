# Phạm vi kiểm chứng

| Phạm vi | Kết luận sử dụng được|
|---|---|---|
| Python unit test | Logic Gateway/parser/safety/analytics với đầu vào kiểm soát | 
| CoreIoT regression | Cấu trúc JSON, graph, ownership, checksum | 
| Firmware build | Bốn target biên dịch được | 
| CoreIoT lịch sử | Telemetry/dashboard trên platform thật, dữ liệu tổng hợp |
| HIL bốn board lịch sử | ESP-NOW, arbitration và ACK/GPIO | 
| Firmware vật lý hiện hành | Có driver ADC, SHT31, BH1750, flow/phao, GPIO |

[Lệnh test](../tests/README.md) chạy độc lập với thư mục phát triển.
[Minh chứng](../evidence/README.md) ghi điều kiện/thời điểm; không chuyển
kết quả HIL cũ thành bằng chứng cho firmware vật lý mới.

Giới hạn còn lại: tổng nước/buffer MQTT trong RAM, origin không xác thực mật
mã, analog chưa phát hiện mọi kiểu đứt dây, flow cần hiệu chuẩn, dashboard
chưa có export độc lập tenant. 
Giữ finalHardwareVerified=false tới khi nghiệm thu phần cứng đầy đủ. 
