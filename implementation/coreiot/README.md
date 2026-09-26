# SmartFarm CoreIoT configuration

Bộ artifact hiện hành gồm 7 rule chain, 7 device profile, 2 asset profile và
action cấu hình lịch Gateway. Các file JSON có thể được chỉnh sửa và kiểm tra
bằng bộ regression test đi kèm. Bộ cấu hình cần được gắn với entity và rule
chain của tenant triển khai; không bao gồm bản export đầy đủ của tenant.

| Thư mục | Vai trò |
|---|---|
| profiles/ | Profile cho Field, Site và thiết bị |
| rule_chains/ | Lưu/chuyển telemetry, recommendation và đồng bộ task |
| dashboard_actions/ | Action ghi shared attribute lịch Gateway |
| [widgets/field_selector](widgets/field_selector/README.md) | Form cấu hình riêng từng Field, phản hồi Gateway và Tưới/Dừng tưới |
| manifests/data_contract.json | Schema, telemetry, RPC và quyền điều khiển |
| manifests/migration_manifest.json | Thứ tự import và binding |
| manifests/artifact_checksums.json | SHA-256 để kiểm tra tính toàn vẹn của artifact |
| [setup](setup/README.md) | Entity, binding, dashboard và alarm |

Schema, tên entity và key telemetry được mô tả trong data contract.
Các nhãn SIM/HIL trong đó mô tả nguồn dữ liệu thử nghiệm; Gateway vận hành
không tự sinh dữ liệu mô phỏng.

Khi chỉnh JSON, kiểm tra thay đổi và cập nhật checksum bằng
`py -3 tools/release/update_artifact_checksums.py`, rồi chạy
`py -3 -m unittest discover -s tests/regression -v`.

Checksum kiểm tra tính toàn vẹn bộ file, không chứng minh logic đúng hoặc đã
import thành công. Repository chưa có dashboard JSON hoàn chỉnh độc lập tenant;
dashboard cần dựng từ Smart Irrigation Template theo hướng dẫn setup.
