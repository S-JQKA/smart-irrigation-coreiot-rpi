# Triển khai SmartFarm

1. Xác định board, flash/PSRAM và MAC. Điền peer_config.h, kiểm tra pin map,
   build bốn target theo [firmware README](../implementation/firmware/smartfarm/README.md).
   Nạp đúng target cho từng board khi triển khai; build không tự flash.
2. Đấu nối và hiệu chuẩn soil/flow. Xác nhận OFF khi boot, reset, mất radio và
   phao báo cạn trước khi nối tải tưới.
3. Cài dependency Python, chép hardware.example.json thành cấu hình riêng
   ngoài Git; cập nhật serial, mapping và hệ số flow khớp Central.
4. Cấu hình environment/token, cài systemd theo
   [Raspberry Pi README](../implementation/gateway/deploy/raspberry-pi/README.md).
5. Thiết lập entity/profile/chain theo [CoreIoT setup](../implementation/coreiot/setup/README.md).
6. Kiểm tra NTP, OFF/ACK, số đo từng Field và threshold đầy đủ trước khi
   chuyển từ DISABLED sang chế độ điều khiển.

Chạy foreground bằng gateway_runtime.py --config với đường dẫn cấu hình
riêng. Các biến môi trường: SMARTFARM_SERIAL_PORT, SMARTFARM_STATE_DIR,
COREIOT_MQTT_HOST, COREIOT_MQTT_PORT, COREIOT_MQTT_TLS, COREIOT_GATEWAY_TOKEN.
Không đưa token hoặc cấu hình triển khai riêng vào repository.

Đây là hướng dẫn mục tiêu, chưa phải biên bản nghiệm thu Pi. Không đổi
finalHardwareVerified thành true chỉ vì build/test pass.
