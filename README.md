# SmartFarm — Smart Irrigation with CoreIoT and Raspberry Pi

SmartFarm là hệ thống tưới hai vùng dùng Raspberry Pi Gateway để điều phối,
ESP32-S3/ESP-NOW để thu thập dữ liệu và thực thi đầu ra, CoreIoT để quản lý
cấu hình, telemetry, lịch sử, dashboard và cảnh báo.

Repository chứa mã triển khai đọc cảm biến vật lý. **Bản firmware này chưa
được nghiệm thu với cảm biến, van/bơm thật hoặc triển khai đầy đủ trên Pi.**
Các thử nghiệm CoreIoT và HIL trước đây sử dụng dữ liệu tổng hợp; xem
[phạm vi kiểm chứng](docs/validation.md).

## Kiến trúc

```text
2 Sensor Node ── ESP-NOW ── Bridge ── USB/UART ── Raspberry Pi Gateway
                              │                         │
                           ESP-NOW                    MQTT
                              │                         │
                           Central                   CoreIoT
                     2 van + 1 bơm, flow/phao
```

Gateway quyết định lịch tưới, phân bổ vùng và kiểm tra điều kiện an toàn.
Central thực thi GPIO, kiểm tra phao và tự tắt khi hết thời hạn duy trì lệnh
(lease). CoreIoT gửi yêu cầu điều khiển thủ công, lưu dữ liệu và hiển thị
trạng thái. Các khuyến nghị trên CoreIoT chỉ dùng để tham khảo.
Gateway là ứng dụng Python của dự án, không sử dụng ThingsBoard/CoreIoT Edge.

## Thành phần

| Đường dẫn | Nội dung |
|---|---|
| [implementation/gateway](implementation/gateway/README.md) | Python runtime, điều khiển, lịch, MQTT và UART |
| [implementation/firmware/smartfarm](implementation/firmware/smartfarm/README.md) | Một project PlatformIO cho hai Sensor, Bridge và Central |
| [implementation/coreiot](implementation/coreiot/README.md) | Profile, rule chain, dashboard action và thiết lập |
| [docs](docs/README.md) | Kiến trúc, phần cứng, triển khai, giao thức và kiểm chứng |
| [tests](tests/README.md) | Unit test và kiểm tra cấu trúc CoreIoT |
| [evidence](evidence/README.md) | Minh chứng lịch sử chọn lọc |
| [tools/release](tools/release/build_submission.py) | Đóng gói mã nguồn |

## Bắt đầu

Cần Python 3.10 trở lên. Để biên dịch firmware, cài PlatformIO CLI hoặc
extension PlatformIO IDE trong VS Code. Chạy các lệnh từ thư mục gốc repository.

Trên Windows (PowerShell):

```powershell
py -3 -m pip install -r implementation/gateway/requirements.txt
py -3 implementation/gateway/gateway_runtime.py --help
py -3 -m unittest discover -s tests/unit -v
py -3 -m unittest discover -s tests/regression -v
```

Trên Linux/Raspberry Pi, tạo và kích hoạt môi trường Python riêng:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r implementation/gateway/requirements.txt
python implementation/gateway/gateway_runtime.py --help
python -m unittest discover -s tests/unit -v
python -m unittest discover -s tests/regression -v
```

Biên dịch firmware trên máy đã cài PlatformIO:

```text
pio run -d implementation/firmware/smartfarm
```

Kiểm thử widget cần Node.js; xem [hướng dẫn kiểm thử](tests/README.md).

Lệnh PlatformIO chỉ build. Trước khi chạy phần cứng, thực hiện
[cấu hình và triển khai](docs/deployment.md), điền MAC/chân nối và hiệu chuẩn
theo [hướng dẫn firmware](implementation/firmware/smartfarm/README.md).
`--offline` vẫn cần cảm biến và Central; chỉ ngắt kết nối MQTT.

## Phạm vi mã nguồn công bố

Repository gồm Gateway, firmware, cấu hình CoreIoT, tài liệu triển khai,
kiểm thử và minh chứng chọn lọc. Bộ công cụ mô phỏng/HIL và log thô không đi
kèm phiên bản hiện tại; một số commit cũ có mã mô phỏng. Các đối tượng giả lập
trong unit test chỉ phục vụ kiểm thử, không tạo dữ liệu cho Gateway khi vận hành.

Cấu hình CoreIoT cần được gắn với các entity trong tenant triển khai.
Repository chưa cung cấp dashboard JSON hoàn chỉnh để import trực tiếp;
xem [hướng dẫn CoreIoT](implementation/coreiot/setup/README.md).

Tạo gói mã nguồn bằng `py -3 tools/release/build_submission.py` trên Windows
hoặc `python tools/release/build_submission.py` trong môi trường Linux ở trên.
Kết quả nằm ở `deliverables/source/smartfarm-source.zip`. Báo cáo PDF và slide
không nằm trong gói mã nguồn. Token, file `.env` và cấu hình thiết bị riêng
cần được lưu ngoài repository.

## Bản quyền

Repository phục vụ đồ án tốt nghiệp. Chưa cấp giấy phép mã nguồn mở;
mọi quyền được bảo lưu nếu không có thỏa thuận khác.
