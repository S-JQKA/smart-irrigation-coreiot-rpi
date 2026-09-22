# SmartFarm Gateway

Gateway điều khiển hai Field, nhận số đo vật lý qua USB/UART–ESP-NOW và đồng bộ telemetry, cấu hình, lịch sử với CoreIoT. Central thực thi đầu ra và trả ACK.

## Chạy bản phần cứng

```powershell
py -3 -m pip install -r implementation/gateway/requirements.txt
py -3 implementation/gateway/gateway_runtime.py --help
py -3 implementation/gateway/gateway_runtime.py --config implementation/gateway/config/hardware.example.json
```

`hardware.example.json` là cấu hình mặc định. Chép thành cấu hình triển khai riêng và điền serial path. Chỉ hỗ trợ HARDWARE_TWO_FIELD, hai Field, 16 logical device. `--offline` chỉ ngắt MQTT, vẫn cần phần cứng; không sinh số đo hoặc ACK.

Đặt SMARTFARM_SERIAL_PORT và COREIOT_MQTT_HOST, COREIOT_MQTT_PORT, COREIOT_MQTT_TLS, COREIOT_GATEWAY_TOKEN trong process. Không lưu credential trong repository.

## Module

| File | Trách nhiệm |
|---|---|
| gateway_runtime.py | Vòng chạy, kết nối, khởi động OFF, shutdown, telemetry |
| irrigation_controller.py | Điều phối Field, lịch, cấu hình nguyên tử, quota và safety |
| scheduler.py | Lịch cục bộ, recurrence và trạng thái lịch |
| control_engine.py | Quyết định từ đầu vào đã kiểm tra |
| controller_state.py | Topology và trạng thái được xác nhận |
| configuration.py | Cấu hình và biến môi trường |
| field_configuration.py | Phản hồi cấu hình từng Field: yêu cầu, hiệu lực, trạng thái và thời điểm |
| hardware_adapter.py | Kiểu sự kiện, interface phần cứng |
| uart_espnow_adapter.py | JSONL/CRC, nguồn dữ liệu, sequence và ACK |
| runtime_state.py | Persistence cho lịch, cấu hình và command ledger |
| coreiot/ | MQTT Gateway API và RPC |
| analytics_hook.py, anomaly_detector.py | Analytics advisory tùy chọn |

## Đầu vào và safety

Sensor gửi bốn vị trí soil cùng SHT31/BH1750. Vị trí lỗi giữ null, không dồn số đo sang tên khác. Central sở hữu FLOW từng nhánh và phao cạn trong PEER. Profile vật lý từ chối flow/pulse từ Sensor Node.

Packet số đo có origin=P để tránh dùng nhầm firmware HIL; đây không phải xác thực mật mã. Không xuất tankLevelPct vì cấu hình cơ sở chỉ có phao. Số đo chưa có được bỏ khỏi telemetry, không thay bằng hằng số.

Cần ít nhất ba mẫu soil đồng thuận, môi trường hợp lệ, flow còn mới và trạng thái phao xác định. Gateway khởi động DISABLED, yêu cầu Central ALL_OFF và đợi cấu hình Field đầy đủ từ cache/CoreIoT. Cấu hình đầu tiên gồm controlMode và năm ngưỡng độ ẩm. NTP chưa sẵn sàng thì ON/lịch có timestamp bị chặn; OFF vẫn có hiệu lực.

Central thực hiện valve-before-pump khi bật, pump-before-valve khi dừng Field cuối, lease tối đa 15 giây. ACK xác nhận GPIO, chưa xác nhận vị trí van hay nước chảy.

pulsesPerLiterByZone và minimumFlowRateByZone cấu hình riêng từng nhánh. Mẫu flow đầu sau Central reboot thiết lập mốc, không cộng bộ đếm cũ vào chu kỳ mới. Tổng nước trong Gateway là RAM; Gateway restart và khoảng mất liên lạc có thể làm thiếu thống kê. Chưa có bộ đếm nước bền qua mất nguồn.

Analytics mặc định tắt, khi bật chỉ bổ sung telemetry/alarm, không điều khiển. MQTT buffer ở RAM, TTL 900 giây. Lịch/cấu hình/command ledger được lưu nguyên tử dưới SMARTFARM_STATE_DIR.

## Cấu hình dashboard và truyền telemetry

MQTT callback đưa Shared attributes vào hàng đợi tối đa 128 bản tin. Runtime
áp dụng trong luồng điều khiển và phát `fieldConfigStatus`, `fieldConfigReason`,
`fieldConfigRequested`, `fieldConfigEffective`, `fieldConfigReceivedAt` và
`fieldConfigAppliedAt` trên Smart Valve tương ứng ở mỗi tick. Cập nhật không
hợp lệ giữ cấu hình hiệu lực và thời điểm áp dụng thành công trước đó.
Khởi động từ cache báo `LOCAL_ONLY`; chưa có cấu hình hợp lệ báo `WAITING`.
`APPLIED` chỉ xuất hiện sau một yêu cầu cloud hợp lệ trong phiên hiện tại.

Runtime dùng `submit_telemetry`: đưa bản tin QoS 1 vào hàng đợi mà không chờ
PUBACK. Mỗi tick quan sát xác nhận bằng `poll_telemetry_delivery`; quá hạn
5 giây hoặc hàng đợi theo dõi đầy được tính vào `telemetryDeliveryFailures`,
không ném timeout vào vòng điều khiển. Hàng đợi theo dõi và MQTT có giới hạn
theo `buffer_capacity`. MQTT mất kết nối/lỗi gửi được đệm theo TTL để replay.
`telemetryPublishCount` đếm lần submit, `telemetryPubackCount` đếm xác nhận
đã quan sát, `lastTelemetryPubackMs` ghi mốc xác nhận gần nhất. Quá hạn theo
dõi không khẳng định bản tin sẽ không được broker nhận muộn hơn.

`publish_telemetry` có chờ PUBACK được giữ cho runner chẩn đoán hữu hạn;
không gọi API này từ vòng điều khiển hoặc callback reconnect sản phẩm.
PUBACK chỉ xác nhận MQTT; cần timestamp/giá trị mới trên CoreIoT để xác nhận
lưu dữ liệu. Cách gắn widget: [hướng dẫn Field selector](../coreiot/widgets/field_selector/README.md).

## Kiểm chứng

Xem tests/README.md, deploy/raspberry-pi/README.md và implementation/firmware/smartfarm/README.md. SIM/HIL ở local_dev trên máy phát triển, sử dụng lõi controller sản phẩm; sản phẩm không import local_dev. Giữ finalHardwareVerified=false cho tới khi nghiệm thu phần cứng đầy đủ. Chưa flash hoặc xác nhận hoạt động với cảm biến/relay thật trong đợt refactor này.
