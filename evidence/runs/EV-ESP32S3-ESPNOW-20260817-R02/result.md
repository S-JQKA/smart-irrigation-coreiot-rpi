# Result

## PASS — ESP-NOW uplink; ACK có giới hạn quan sát

Mức kiểm chứng: `PHY` — hai board ESP32-S3 thật, không dùng Wi-Fi AP.

- Sender Muse Lab MAC `7C:DF:A1:FC:70:74` truyền tới receiver ESP32-S3-A MAC
  `F4:12:FA:E6:F9:48` trên ESP-NOW channel 1.
- Receiver nhận đủ 20/20 packet, sequence liên tục `1..20`, không thiếu và không
  trùng packet trong log quan sát.
- CRC16-CCITT PASS 20/20; payload telemetry nhị phân 18 byte, dưới giới hạn
  ESP-NOW 250 byte.
- Receiver enqueue ACK ứng dụng thành công 20/20 với `err=0`.
- Không xuất hiện retry cùng sequence. Theo hành vi firmware, đây là bằng chứng
  gián tiếp sender đã nhận ACK ở lần đầu, nhưng không thay thế log sender trực
  tiếp.

## Giới hạn

- Windows không nhận CH340/COM3 của sender trong run đồng thời, nên chưa thu được
  log sender để xác nhận trực tiếp `ACK`, số attempt và round-trip time.
- Chưa kiểm tra khoảng cách, packet loss có chủ đích, PMK/LMK encryption, sleep,
  UART framing tới Raspberry Pi hoặc replay/buffering.
- Sensor values là dữ liệu mô phỏng trong firmware, không phải cảm biến thật.

