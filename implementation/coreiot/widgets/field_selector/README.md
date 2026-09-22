# Widget Cấu hình tưới

Một form chọn Field 1/2, lưu chín Shared attributes và gửi RPC Tưới/Dừng tưới.
Source gồm `template.html`, `style.css`, `controller.js`; runtime sản phẩm
`implementation/gateway/gateway_runtime.py` cung cấp phản hồi cấu hình.

## Gắn vào dashboard

1. Trong trình sửa widget của tenant, tạo custom widget loại Latest values.
   Chép HTML, CSS và JavaScript từ ba file source vào các vùng tương ứng.
   Source không có token; widget dùng attributeService/deviceService của phiên đăng nhập.
2. Thêm widget vào dashboard và khai báo đúng hai datasource kiểu Device:
   `SI Smart Valve 1` và `SI Smart Valve 2`. Mỗi datasource resolve một thiết bị
   khác nhau. Không chọn asset Field thay cho Smart Valve.
3. Thêm các key sau cho **cả hai datasource**, loại Timeseries/Latest:

   | Key | Vai trò |
   |---|---|
   | fieldConfigStatus | WAITING, LOCAL_ONLY, APPLIED hoặc REJECTED |
   | fieldConfigReason | Mã lý do, hiển thị trong Chi tiết |
   | fieldConfigRequested | JSON chứa thuộc tính của yêu cầu gần nhất |
   | fieldConfigEffective | JSON chứa chín giá trị Gateway đang sử dụng |
   | fieldConfigReceivedAt | Epoch milliseconds lúc nhận yêu cầu |
   | fieldConfigAppliedAt | Epoch milliseconds lần áp dụng thành công |

4. Form tự đọc Shared attributes qua dịch vụ nền tảng. Không khai báo các key
   phản hồi trên thành Attributes. Bật nhận dữ liệu mới; timestamp của
   `fieldConfigEffective` phải được cập nhật khi runtime publish mỗi tick.
5. Điền đủ chín thuộc tính: `controlMode`, `criticalMoisture`,
   `minMoistureThreshold`, `targetMoisture`, `maxMoistureThreshold`,
   `floodMoistureThreshold`, `maxWaterPerCycle`, `maxWaterPerDay`, `maxDurationSec`.
   Bắt đầu với DISABLED; chọn ngưỡng/hạn mức phù hợp từng Field.

Tên menu có thể khác giữa các tenant; ba file source là nội dung để dán vào
custom widget, không phải một dashboard JSON để import trực tiếp.

## Xác nhận và điều khiển

- Lưu cấu hình chỉ ghi vào Smart Valve đang chọn. Chờ Đã đồng bộ và kiểm tra
  Chi tiết/giờ áp dụng. Khi runtime chỉ phục hồi cache, LOCAL_ONLY không phải
  xác nhận cloud mới. Bản sai giữ cấu hình hiệu lực trước đó.
- Nút Tưới cần MANUAL đã lưu, phản hồi APPLIED khớp form, không còn thay đổi
  chưa lưu hoặc yêu cầu đang chờ và dữ liệu hiệu lực mới trong khoảng 30 giây.
  Thời lượng là số nguyên từ 1 đến min(300, maxDurationSec) giây.
- Dừng tưới không cần cấu hình MANUAL đã đồng bộ; cả hai nút bị khóa khi đang
  chờ RPC. Gateway vẫn giữ kiểm tra quyền, safety và phân bổ bơm.
- RPC dùng commandId riêng, source=MANUAL, requestedAt hiện tại, TTL 15 giây,
  và runDurationSeconds cho ON. Thành công phải khớp commandId, EXECUTED và
  trạng thái yêu cầu. Timeout không tự gửi lại ON; kiểm tra trạng thái van.
- Nếu luôn Chưa có phản hồi, kiểm tra key/datasource, phiên runtime và việc
  CoreIoT thực sự lưu telemetry mới. MQTT PUBACK không chứng minh lưu dữ liệu.

## Kiểm tra trước khi dùng

Chạy `node tests/widget_field_selector.test.cjs` từ repository root. Trên
tenant, đối chiếu lưu riêng Field 1/2, từ chối giá trị sai, phản hồi mới,
MANUAL có thời lượng, dừng sớm và trạng thái Field còn lại. Test local không
thay thế việc kiểm tra trên tenant/Pi.

`tools/release/update_field_selector_sources.py` chỉ cập nhật một widget-type
export đã có tại `deliverables/coreiot/irrigation_widgets/field_selector_widget_type.json`;
nó không tự tạo export khi clone mới. Có thể dùng cách dán source ở trên hoặc:

```powershell
py -3 tools/release/update_field_selector_sources.py --input-export widget_type.json --output updated_widget_type.json
```

Lệnh giữ metadata của export và chỉ thay HTML/CSS/JavaScript. Bỏ `--output`
sẽ cập nhật trực tiếp file đầu vào. `--help` hoạt động cả trên clone mới.
`build_irrigation_widgets.py` là công cụ cho các form/table xuất từ tenant,
cần hai file đầu vào qua `--input-export` và `--table-export`; không cần dùng
công cụ đó để tạo custom Field selector này.
