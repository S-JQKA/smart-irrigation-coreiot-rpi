# EV-COREIOT-V22-20260803-R01

> **Historical/superseded local run.** Review on 04/08 found architecture and
> claim issues in the generated rule chains and dashboard. Do not use this run
> as current SRS-alignment evidence. See `EV-COREIOT-V22-20260804-R02`.

Kết quả hiện tại là `STATIC+SIM`, chưa phải `SIM+PLATFORM` và không phải bằng chứng Raspberry Pi/phần cứng.

## Kết quả

- Đã lưu raw archive của 11 JSON ban đầu.
- Đã sinh 9 rule chain, 9 profile và dashboard staging gồm 7 state, 23 widget, 13 alias.
- Static validator: 23 JSON, 0 lỗi, 9 cảnh báo binding ID sau import.
- Unit test Gateway/simulator: 9/9 đạt.
- Regression giữa kỳ: 20/20 TC đạt.
- Simulator tạo một batch đủ 16 downstream device, xử lý target moisture, ACK, expired/duplicate và safety reject.

## Evidence thô

- `raw/artifact_validation.log`: kết quả kiểm tra cấu trúc JSON và secret scan.
- `raw/unit_9tests.log`: test payload/RPC/Gateway/simulator.
- `raw/regression_20tc.log`: 20 TC phạm vi CoreIoT v2.2.
- `raw/baseline_to_v22_diff.json`: hash và structural diff giữa baseline với v2.2.

## Chặn platform

CoreIoT đã mở đúng tenant và dialog import rule chain, nhưng Chrome extension chưa có quyền đọc file cục bộ. Chưa có JSON nào được import và cấu hình production hiện tại chưa bị thay đổi.

Sau khi bật quyền, thực hiện import theo `implementation/coreiot/v2.2/manifests/migration_manifest.json`, resolve 9 binding ID, rồi mới chạy test `SIM+PLATFORM` và chụp evidence tenant.
