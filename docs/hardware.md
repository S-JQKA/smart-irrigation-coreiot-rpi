# Phần cứng tham chiếu

Đây là cấu hình triển khai tham chiếu, chưa phải BOM đã mua/nghiệm thu.

| Thiết bị | Số lượng | Vai trò |
|---|---:|---|
| Raspberry Pi chạy OS Lite 64-bit | 1 | Gateway mục tiêu |
| ESP32-S3 | 4 | Hai Sensor, Bridge, Central |
| Soil điện dung analog v1.2/v2.0 | 8 | Bốn đầu đo mỗi Field |
| SHT31-D | 2 | Nhiệt độ/độ ẩm không khí |
| BH1750 | 2 | Độ rọi |
| YF-S201 | 2 | Xung/lưu lượng mỗi nhánh |
| Phao tiếp điểm khô | 1 | Nhận biết cạn, không đo phần trăm bồn |
| Driver/relay logic 3,3 V | 3 kênh | Hai van và bơm |

Model Pi, tải van/bơm DC 12 V, nguồn/driver cụ thể cần chọn theo lưu lượng,
áp lực và dòng tải. Soil là thang tương đối sau hiệu chuẩn, chưa phải VWC.
450 pulse/lít là mốc danh định cho flow; cần hiệu chuẩn thực nghiệm.

[README firmware](../implementation/firmware/smartfarm/README.md) là nguồn
pin map và quy trình CAL DRY/CAL WET. Sensor soil: GPIO 1/2/4/5, I2C 8/9;
Central flow: 6/7, phao: 10, van: 11/12, bơm: 13. Không dùng pin LED HIL cũ.

Phao đóng GND khi đủ nước; mở/đứt dây hiểu là cạn. Driver active HIGH cần bias
OFF ngoài khi reset. Tín hiệu vào ESP32 phải phù hợp 3,3 V; ngõ xung flow và
tải cảm cần mạch giao tiếp/bảo vệ phù hợp.
