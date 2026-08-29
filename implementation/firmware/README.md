# Firmware workspace

Firmware HIL v1 hiện nằm trong `smartfarm_hil_v1/` và dùng các PlatformIO
environment trên cùng codebase:

- `sensor_node`: alias Field 1 tương thích cũ;
- `sensor_field1_n16r8` và `sensor_field2_n16r8`: hai Sensor Node synthetic có
  node/zone ID, sequence và CRC độc lập;
- `bridge_usb`: chuyển frame USB serial sang ESP-NOW và ngược lại, không quyết
  định tưới;
- `central_led`: Central hai valve, pump dùng chung, command dedup, ACK và lease
  tối đa 15 giây riêng từng Field; hard cap concurrency là hai.

Đây là firmware HIL đã build cục bộ, chưa phải bằng chứng phần cứng. Trước khi
flash phải điền MAC thật trong `smartfarm_hil_v1/include/peer_config.h` và kiểm
tra lại pin LED/relay theo đúng board. Cấu hình MAC bằng 0 được giữ cố ý để bản
build mặc định không thể vô tình điều khiển peer.
