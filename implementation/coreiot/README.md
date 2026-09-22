# SmartFarm CoreIoT configuration

Bộ artifact hiện hành gồm 7 rule chain, 7 device profile, 2 asset profile và
action cấu hình lịch Gateway. JSON trong thư mục này được review trực tiếp;
không cần template v2.2 hoặc generator nội bộ để chạy test và sử dụng bộ
artifact. Đây không phải bản export đầy đủ của tenant đang chạy.

| Thư mục | Vai trò |
|---|---|
| profiles/ | Profile cho Field, Site và thiết bị |
| rule_chains/ | Lưu/chuyển telemetry, recommendation và đồng bộ task |
| dashboard_actions/ | Action ghi shared attribute lịch Gateway |
| [widgets/field_selector](widgets/field_selector/README.md) | Form cấu hình riêng từng Field, phản hồi Gateway và Tưới/Dừng tưới |
| manifests/data_contract.json | Schema, telemetry, RPC và quyền điều khiển |
| manifests/migration_manifest.json | Thứ tự import và binding |
| manifests/artifact_checksums.json | SHA-256 của artifact đã review |
| [setup](setup/README.md) | Entity, binding, dashboard và alarm |

Tên file bỏ hậu tố phiên bản thử nghiệm. Tên entity, key telemetry, schema
version và logic bên trong profile/rule chain giữ nguyên. Nhãn SIM/HIL trong
data contract mô tả dữ liệu lịch sử; không bật mô phỏng trong runtime sản phẩm.

Generator lịch sử được lưu nội bộ cùng cây đầu vào đầy đủ. Nếu chỉnh JSON
hiện hành, review thay đổi, cập nhật checksum bằng
`py -3 tools/release/update_artifact_checksums.py`, rồi chạy
`py -3 -m unittest discover -s tests/regression -v`.

Checksum kiểm tra tính toàn vẹn bộ file, không chứng minh logic đúng hoặc đã
import thành công. Bộ nộp chưa có dashboard JSON hoàn chỉnh độc lập tenant;
dashboard cần dựng từ Smart Irrigation Template theo hướng dẫn setup.
