# Thiết lập CoreIoT

## Entity và binding

Sao lưu tenant trước khi triển khai. Tenant mới dùng Smart Irrigation Template
làm nền. Tenant đã dùng cần đối chiếu theo tên, tránh tạo profile/chain trùng.
Các bước dưới đây cần được thực hiện trên tenant triển khai.

1. Đọc [migration manifest](../manifests/migration_manifest.json), import
   7 chain theo importOrder. Giữ Root Rule Chain có sẵn của nền tảng.
2. Resolve node gọi chain theo postImportBindings; UUID không portable.
   Kiểm tra cả chain-to-chain và default chain của profile.
3. Tạo/import 7 device profile và 2 asset profile trong profiles/.
   Default rule-chain ID để trống có chủ đích, cần bind bằng tên.
4. Hai Field dùng asset profile SI Field. Soil, Water Meter, Smart Valve,
   Environment dùng chain tương ứng. Gateway/Pump/Manifold cần đường lưu
   telemetry và xử lý profile của tenant; không thêm control tự động.
5. Tạo Site, Field 1 và Field 2; gắn thiết bị/quan hệ theo data contract và
   [mapping Gateway](../../gateway/config/hardware.example.json). Có 16
   downstream logical device: 8 soil, 2 environment, 2 water meter, 2 valve,
   pump, manifold; Gateway là thiết bị kết nối riêng.
6. Giữ Field DISABLED tới khi mapping, threshold, dữ liệu và OFF được xác nhận.

Recommendation là thông tin tham khảo. Không thêm auto-RPC tới van/bơm.
Trạng thái actual và ACK phải đến từ Gateway/Central.

## Lịch và dashboard

Gateway thực thi lịch từ localScheduleConfig; CoreIoT lưu shared attribute và
hiển thị gatewaySchedule*, irrigationTask. Action mẫu:
[set_local_schedule_shared_attribute.js](../dashboard_actions/set_local_schedule_shared_attribute.js).
Gắn action vào đúng entity và kiểm tra ACK trước khi coi lịch đã được nhận.
Lịch CoreIoT cũ không phải nguồn trigger được kiểm chứng của hệ này.

Dựng dashboard từ template với hai Field, soil/môi trường/lưu lượng,
valve/pump, alarm, Irrigation Tasks và Gateway Schedules. Bộ source chưa có
export dashboard hoàn chỉnh có thể import sang mọi tenant. Smart Irrigation
Template là điều kiện đầu vào trên tenant, không được phân phối trong repository.
Nếu tenant chưa có template này, cần chuẩn bị dashboard và các entity tương ứng
trước khi gắn widget; bản clone chỉ cung cấp mã widget và cấu hình bên dưới.

Widget Cấu hình tưới hiện hành có source HTML/CSS/JavaScript tại
[field_selector](../widgets/field_selector/README.md). Hướng dẫn này liệt kê
hai datasource Smart Valve và các key phản hồi bắt buộc. Gateway phát
`fieldConfig*` để xác nhận cấu hình đã nhận và giá trị đang có hiệu lực.

Manual RPC cần commandId duy nhất, source=MANUAL, requestedAt là thời điểm
hiện tại, ttlSeconds và runDurationSeconds cho ON; xem
[hợp đồng RPC](../manifests/data_contract.json). Không dùng timestamp 0 để gửi
lệnh thật. Không bật bơm trực tiếp; yêu cầu đi qua van và safety.

## Alarm và kiểm tra sau cấu hình

NodeOffline dùng server attribute active=false liên tục 300 giây, clear khi
active=true. Thời lượng alarm dùng spec.predicate.defaultValue. Analytics mặc
định tắt; chỉ cấu hình SensorAnomaly/ValveLeak khi có telemetry tin cậy.
Analytics chỉ cảnh báo, không tham gia quyền điều khiển.

Kiểm tra telemetry/quan hệ, shared-attribute ACK, task sync, alarm create/clear
và disconnect/reconnect trên tenant triển khai. Ghi rõ đầu vào vật lý hay tổng
hợp và mức xác nhận output. Kết quả lịch sử không nghiệm thu tenant mới.
