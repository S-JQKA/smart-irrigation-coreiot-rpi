# SmartFarm ESP32 firmware

Một project PlatformIO, bốn target: `sensor_field1`, `sensor_field2`, `bridge`,
`central_controller`. Không tự sinh dữ liệu khi thiếu cảm biến.

```powershell
pio run -d implementation/firmware/smartfarm
```

Lệnh chỉ build. Trước khi nạp phải xác định đúng board/COM/MAC và flash/PSRAM.
Hai Sensor target tham chiếu N16R8; pin map dựa trên ESP32-S3 DevKitC-1.
Điền `include/peer_config.h` bằng MAC Bridge/Central; toàn số 0 mặc định chặn
gửi radio. Wi-Fi channel 1, serial 115200 baud.

## Pin map và phần cứng tham chiếu

| Board | Tín hiệu | GPIO |
|---|---|---|
| Mỗi Sensor | Bốn soil điện dung analog | 1, 2, 4, 5 |
| Mỗi Sensor | SHT31-D 0x44, BH1750 0x23: SDA/SCL | 8 / 9 |
| Central | YF-S201 Field 1 / Field 2 | 6 / 7 |
| Central | Phao cạn tiếp điểm khô | 10 |
| Central | Van 1 / van 2 / bơm qua driver | 11 / 12 / 13 |

Pin và hệ số flow trong `include/board_config.h`. SHT31 dùng single-shot có
CRC; BH1750 dùng one-time high-resolution. Soil đọc ADC1 millivolt, median
chín mẫu. Tín hiệu vào ESP32 phải phù hợp 3,3 V; ngõ xung flow cấp nguồn 5 V
cần mạch chuyển mức phù hợp với kiểu ngõ ra thực tế.

Phao đóng xuống GND khi đủ nước; mở/đứt dây được hiểu là cạn. Không suy ra
phần trăm mức nước từ phao. Driver tham chiếu active HIGH, cần điện trở kéo
xuống bên ngoài để OFF khi reset. Đổi polarity phải đổi cả cấu hình và bias.
Chọn driver nhận logic 3,3 V, đủ định mức DC, có bảo vệ tải cảm; không nối tải
vào GPIO. Van/bơm tham chiếu DC 12 V; model tải/nguồn còn cần chọn theo áp và
lưu lượng thực tế. Các chân này khác pin LED/GPIO của firmware HIL cũ.

## Hiệu chuẩn soil

Mỗi board lưu bốn cặp mốc vào NVS. Chưa có mốc thì soil gửi null. Serial
Monitor 115200, newline, đặt đầu đo ở điều kiện tham chiếu trước mỗi lệnh:

```text
CAL SHOW
CAL DRY 1
CAL WET 1
```

Lặp lại cho kênh 2–4. Lệnh lấy số đo ADC thực và lưu NVS. Hai mốc cách nhau ít
nhất 100 mV; số đo gần rail bị loại. Không làm ướt phần mạch điện. Thang 0–100
là tương đối sau hiệu chuẩn, chưa phải VWC được kiểm định theo loại đất.
Driver không phát hiện được mọi trường hợp đứt dây analog có đầu vào trôi;
đấu nối, bias và hiệu chuẩn cần được kiểm tra bằng phần cứng.

## Flow và đầu ra

Central đếm interrupt riêng hai nhánh, gửi counter/flow mỗi hai giây.
`kPulsesPerLiter` phải khớp `control.pulsesPerLiterByZone` ở Gateway; 450 là
mốc danh định YF-S201, cần hiệu chuẩn bằng thể tích đo được. Chọn ngưỡng flow
trong dải đo cảm biến, không mặc định khả năng phát hiện dòng rò rất nhỏ.

Central khởi tạo OFF trước serial/radio, vẫn chạy interlock/lease nếu radio
khởi tạo lỗi. Lease tối đa 15 giây. Bơm chỉ giữ ON khi còn van hoạt động.
ACK xác nhận GPIO, chưa xác nhận vị trí van hay nước chảy.

## Wire protocol

JSON tối đa 250 byte ESP-NOW, JSONL UART. CRC16-CCITT (0x1021, init 0xFFFF)
tính trên JSON sắp key theo alphabet, không gồm crc16. Mỗi packet có type và
version=1; số đo dùng số nguyên để MCU/Python có cùng biểu diễn CRC.

- SENSOR: nodeId, zoneId, sequence, boot, origin=P, soil (bốn giá trị 0–10000
  hoặc null), tC (0,01 °C), rh (0,01 %RH), lightLux.
- FLOW: nodeId=central, zoneId, sequence, boot, origin=P, pulseCounter,
  flowMilliLpm (0,001 lít/phút).
- PEER: peerId=central, role=CENTRAL, online, tankLow, origin=P.
- SET_ZONE/ACK: giữ commandId, sequence, lease và trạng thái output.

Thiếu môi trường bỏ key; thiếu soil giữ null đúng vị trí. Sensor vật lý không
gửi flow/pulse. origin=P tránh nhầm profile, không xác thực mật mã.

## Phạm vi kiểm chứng

Firmware mới chưa được nạp lên board hoặc thử với cảm biến/tải thật trong đợt
refactor này. Evidence HIL cũ vẫn thuộc firmware nội bộ cũ.

Tài liệu driver: [Sensirion SHT3x](https://admin.sensirion.com/media/documents/213E6A3B/63A5A569/Datasheet_SHT3x_DIS.pdf),
[ROHM BH1750](https://www.mouser.com/datasheet/2/348/bh1750fvi-e-186247.pdf),
[DFRobot soil calibration](https://wiki.dfrobot.com/sen0193),
[Seeed flow](https://wiki.seeedstudio.com/Water-Flow-Sensor/).
