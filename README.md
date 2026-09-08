# SmartFarm — Smart Irrigation with CoreIoT and Raspberry Pi

SmartFarm là hệ thống tưới hai vùng dùng Raspberry Pi Gateway để điều phối,
ESP32-S3/ESP-NOW để thu thập dữ liệu và thực thi đầu ra, CoreIoT để quản lý
cấu hình, telemetry, lịch sử, dashboard và cảnh báo.

Repository chứa mã triển khai đọc cảm biến vật lý. **Bản firmware này chưa
được nghiệm thu với cảm biến, van/bơm thật hoặc triển khai đầy đủ trên Pi.**
Các thử nghiệm CoreIoT và HIL trước đây sử dụng dữ liệu tổng hợp; xem
[phạm vi kiểm chứng](docs/validation.md). Giữ `finalHardwareVerified=false`.

## Kiến trúc

```text
2 Sensor Node ── ESP-NOW ── Bridge ── USB/UART ── Raspberry Pi Gateway
                              │                         │
                           ESP-NOW                    MQTT
                              │                         │
                           Central                   CoreIoT
                     2 van + 1 bơm, flow/phao
```

Gateway quyết định lịch tưới, phân bổ vùng và safety. Central thực thi GPIO,
kiểm tra phao và tự tắt khi hết lease. CoreIoT gửi yêu cầu manual, lưu dữ liệu
và hiển thị trạng thái; recommendation không tự điều khiển bơm/van.
Đây là Gateway do đồ án xây dựng, không phải ThingsBoard/CoreIoT Edge.

## Thành phần

| Đường dẫn | Nội dung |
|---|---|
| [implementation/gateway](implementation/gateway/README.md) | Python runtime, điều khiển, lịch, MQTT và UART |
| [implementation/firmware/smartfarm](implementation/firmware/smartfarm/README.md) | Một project PlatformIO cho hai Sensor, Bridge và Central |
| [implementation/coreiot](implementation/coreiot/README.md) | Profile, rule chain, dashboard action và thiết lập |
| [docs](docs/README.md) | Kiến trúc, phần cứng, triển khai, giao thức và kiểm chứng |
| [tests](tests/README.md) | Unit test và kiểm tra cấu trúc CoreIoT |
| [evidence](evidence/README.md) | Minh chứng lịch sử chọn lọc |
| [tools/release](tools/release/build_submission.py) | Đóng gói source nộp |

## Bắt đầu

Python 3.10 trở lên; chạy từ repository root:

```powershell
py -3 -m pip install -r implementation/gateway/requirements.txt
py -3 implementation/gateway/gateway_runtime.py --help
py -3 -m unittest discover -s tests/unit -v
py -3 -m unittest discover -s tests/regression -v
pio run -d implementation/firmware/smartfarm
```

Lệnh PlatformIO chỉ build. Trước khi chạy phần cứng, thực hiện
[cấu hình và triển khai](docs/deployment.md), điền MAC/chân nối và hiệu chuẩn
theo [hướng dẫn firmware](implementation/firmware/smartfarm/README.md).
`--offline` vẫn cần cảm biến và Central; chỉ ngắt kết nối MQTT.

## Bản nộp và bản phát triển

Tên file thể hiện chức năng; phiên bản phát hành quản lý bằng Git. Phiên bản
schema/protocol vẫn được giữ trong nội dung hợp đồng dữ liệu.

SIM/HIL, generator CoreIoT cũ, tài liệu làm việc và log thô được giữ riêng trên
máy phát triển, không thuộc bản clone này. Cần sao lưu local_dev và các thư
mục nội bộ riêng khi chuyển máy. Lịch sử Git cũ vẫn chứa mã mô phỏng.
Mock trong unit test chỉ kiểm tra logic, không sinh telemetry cho runtime.

Tạo gói bằng `py -3 tools/release/build_submission.py`. Kết quả nằm ở
`deliverables/source/smartfarm-source.zip`. PDF báo cáo và slide sẽ phát hành
riêng khi chốt bản nộp. Không commit token, .env, cấu hình triển khai riêng,
cache hoặc binary.

## Bản quyền

Repository phục vụ đồ án tốt nghiệp. Chưa cấp giấy phép mã nguồn mở;
mọi quyền được bảo lưu nếu không có thỏa thuận khác.
