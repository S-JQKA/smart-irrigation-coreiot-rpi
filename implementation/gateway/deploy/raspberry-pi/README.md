# Raspberry Pi deployment

Hệ điều hành tham chiếu: Raspberry Pi OS Lite 64-bit. Gateway là ứng dụng
Python của dự án, không sử dụng ThingsBoard/CoreIoT Edge.

## Cài đặt

1. Chép workspace vào `/opt/smartfarm-gateway`.
2. Tạo user hệ thống `smartfarm-gateway`, thêm vào group `dialout`.
3. Tạo virtualenv và cài `implementation/gateway/requirements.txt`.
4. Chép `implementation/gateway/config/hardware.example.json` thành
   `/etc/smartfarm-gateway/devices.json`, sau đó thay serial path và mapping vật lý.
5. Chép `gateway.env.example` thành `/etc/smartfarm-gateway/gateway.env`, điền
   token thật và đặt permission `0600`.
6. Tạo `/var/lib/smartfarm-gateway`, owner `smartfarm-gateway`.
7. Cài `smartfarm-gateway.service`, daemon-reload rồi enable service.

Các lệnh kiểm tra sau khi cài:

```bash
systemctl status smartfarm-gateway
journalctl -u smartfarm-gateway -f
timedatectl show -p NTPSynchronized --value
```

Gateway yêu cầu đồng bộ thời gian bằng NTP. Khi đồng hồ chưa sẵn sàng, Gateway vẫn nhận OFF và
thực hiện safety nhưng từ chối Manual ON/lịch có timestamp. State nhỏ nằm dưới
`/var/lib/smartfarm-gateway`; MQTT telemetry buffer vẫn chỉ ở RAM.

Service có thời gian dừng 25 giây để Gateway reassert OFF. Central vẫn là lớp
fail-safe cuối cùng và phải tự OFF khi lease 15 giây hết hạn.
