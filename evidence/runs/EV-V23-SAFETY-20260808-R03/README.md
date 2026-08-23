# EV-V23-SAFETY-20260808-R03

Bundle này ghi lại batch live safety/degraded của SmartFarm v2.3 trên tenant
CoreIoT ngày 08/08/2026.

## Kết luận

- `TankLow`: PASS local runtime và PASS alarm tenant.
- `ZoneFlowLow` + `PumpDryRun`: PASS local runtime và PASS alarm tenant.
- `NORMAL -> DEGRADED -> NORMAL`: PASS live.
- Reconnect: PASS; Gateway re-announce 16 downstream device, phục hồi 2 shared
  attribute watch và replay 32 message còn hạn.
- Kết thúc an toàn: cả hai van OFF, pump OFF, mode NORMAL.

Không có CoreIoT profile, rule chain hoặc dashboard artifact nào được sửa/import
trong batch này. Fault được inject tại Gateway simulator.

## File

- `run.json`: metadata và kết quả máy đọc được.
- `runtime_excerpt.log`: các mốc runtime chính xác từ live process.
- `tenant_alarm_observation.md`: alarm quan sát trực tiếp trên tenant.
- `result.md`: đánh giá PASS và giới hạn còn lại.
