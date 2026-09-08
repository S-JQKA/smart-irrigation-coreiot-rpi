# Giao thức và hợp đồng dữ liệu

ESP-NOW dùng JSON tối đa 250 byte; USB/UART dùng JSONL, 115200 baud.
Version=1, CRC16-CCITT 0x1021/init 0xFFFF trên JSON canonical sắp key, bỏ
crc16. Số đo MCU dùng số nguyên để biểu diễn CRC nhất quán.

| Packet | Nguồn | Dữ liệu |
|---|---|---|
| SENSOR | Sensor của Field | Bốn soil hoặc null, tC, rh, lightLux |
| FLOW | Central | pulseCounter và flowMilliLpm theo Field |
| PEER | Central | Online và tankLow |
| SET_ZONE | Gateway qua Bridge | commandId, sequence, lease, trạng thái yêu cầu |
| ACK | Central | Kết quả và trạng thái GPIO |

Số đo vật lý có origin=P; đây là nhãn profile, không xác thực mật mã.
Sequence/boot ID giúp loại mẫu trùng và nhận biết khởi động lại. Soil giữ
null đúng vị trí lỗi; không thay dữ liệu thiếu bằng số giả. Sensor không
được gửi flow/pulse. Phao cho tankLow, không tankLevelPct.

Chi tiết scale/wire: [firmware README](../implementation/firmware/smartfarm/README.md)
và [codec](../implementation/gateway/coreiot/gateway_protocol.py).
MQTT/telemetry/RPC: [data_contract.json](../implementation/coreiot/manifests/data_contract.json).
ACK xác nhận GPIO, chưa xác nhận vị trí cơ khí van hoặc nước chảy.
