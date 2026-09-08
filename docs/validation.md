# Phạm vi kiểm chứng

| Phạm vi | Kết luận sử dụng được | Giới hạn |
|---|---|---|
| Python unit test | Logic Gateway/parser/safety/analytics với đầu vào kiểm soát | Không xác nhận dây nối/cảm biến |
| CoreIoT regression | Cấu trúc JSON, graph, ownership, checksum | Không xác nhận import/live tenant |
| Firmware build | Bốn target biên dịch được | Không xác nhận chạy trên board |
| CoreIoT lịch sử | Telemetry/dashboard trên platform thật, dữ liệu tổng hợp | Không phải cảm biến thật |
| HIL bốn board lịch sử | ESP-NOW, arbitration và ACK/GPIO | Laptop, firmware HIL cũ, không tải tưới thật |
| Firmware vật lý hiện hành | Có driver ADC, SHT31, BH1750, flow/phao, GPIO | Chưa flash/nghiệm thu cảm biến, tải hoặc Pi trong đợt tách source |

[Lệnh test](../tests/README.md) chạy độc lập với thư mục phát triển.
[Minh chứng](../evidence/README.md) ghi điều kiện/thời điểm; không chuyển
kết quả HIL cũ thành bằng chứng cho firmware vật lý mới.

Giới hạn còn lại: tổng nước/buffer MQTT trong RAM, origin không xác thực mật
mã, analog chưa phát hiện mọi kiểu đứt dây, flow cần hiệu chuẩn, dashboard
chưa có export độc lập tenant. Báo cáo cần cập nhật file map, pin map và quyền
sở hữu flow/phao cho khớp source hiện hành.

Giữ finalHardwareVerified=false tới khi nghiệm thu phần cứng đầy đủ. PDF
báo cáo chưa được sửa hoặc nghiệm thu trình bày bởi đợt sắp xếp repository này.
