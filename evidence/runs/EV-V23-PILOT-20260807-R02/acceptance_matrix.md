# Core acceptance matrix — v2.3 pilot R02

Quy ước: `SIM` là hành vi chạy bằng simulator/unit test; `PLATFORM` là quan
sát trên tenant CoreIoT. Static graph checks được thống kê riêng và không thay
thế acceptance case.

| TC | Scope | Hành vi cần chứng minh | Kết quả | Evidence |
|---|---|---|---|---|
| TC-01 | SIM | Đủ debounce mới bắt đầu tưới | PASS | `test_vertical_slice_starts_then_stops_with_one_command_per_transition` |
| TC-02 | SIM | Field 1 đất khô làm valve và pump cùng ON | PASS | `test_vertical_slice_starts_then_stops_with_one_command_per_transition` |
| TC-03 | SIM | Không phát lại command ID khi trạng thái không đổi | PASS | `test_vertical_slice_starts_then_stops_with_one_command_per_transition` |
| TC-04 | SIM | Đạt target moisture thì valve và pump OFF | PASS | `test_active_irrigation_stops_at_target` |
| TC-05 | SIM | Pulse được đổi sang lít đúng một lần | PASS | `test_pulses_are_converted_to_liters` |
| TC-06 | SIM | Môi trường thay đổi priority nhưng moisture vẫn là gate chính | PASS | `test_environment_changes_priority_but_moisture_remains_primary_gate` |
| TC-07 | SIM | Critical moisture bỏ qua start debounce | PASS | `test_critical_moisture_bypasses_start_debounce_but_not_hard_safety` |
| TC-08 | SIM | Tank low chặn manual TURN_ON | PASS | `test_manual_on_is_rejected_by_tank_low_hard_interlock` |
| TC-09 | SIM | Manual OFF latch ngăn auto bật lại ngay | PASS | `test_manual_off_latch_prevents_local_auto_restart` |
| TC-10 | SIM | `COREIOT_REQUEST` không tự bật tưới | PASS | `test_coreiot_request_mode_does_not_auto_start` |
| TC-11 | SIM | Pilot dùng profile/authority hỗn hợp đúng cấu hình | PASS | `test_pilot_config_mixes_profiles_and_authority_by_zone` |
| TC-12 | SIM | Chỉ Field 1 chạy local decision | PASS | `test_only_field1_runs_local_decision` |
| TC-13 | SIM | Field 2 giữ RPC state và chiếm đúng scheduler slot | PASS | `test_coreiot_controlled_field2_preserves_rpc_state_and_occupies_scheduler_slot` |
| TC-14 | SIM | TURN_OFF rỗng được chuẩn hóa fail-safe | PASS | `test_parameterless_turn_off_gets_safe_legacy_widget_metadata` |
| TC-15 | SIM | TURN_ON rỗng luôn bị từ chối | PASS | `test_parameterless_turn_on_never_uses_legacy_widget_fallback` |
| TC-16 | SIM | RPC hết hạn và trùng lặp bị từ chối | PASS | `test_guard_rejects_expired_and_duplicate` |
| TC-17 | PLATFORM | Telemetry Field/Valve/Pump/Water Meter hiện trên CoreIoT | PASS | `screenshots/01` đến `04` |
| TC-18 | PLATFORM | Chu trình live có bật tưới rồi tự dừng | PARTIAL | Người vận hành xác nhận chu trình; ảnh `01`–`03` chỉ giữ trạng thái cuối OFF |
| TC-19 | PLATFORM | Water meter: `5146 / 450 = 11.436 L` | PASS | `screenshots/04-water-meter-result.png` |
| TC-20 | PLATFORM | Sau khi đạt target, recommendation trở về `NONE/NO_CHANGE` | PASS | `screenshots/05` và `06` |

Tổng: **19 PASS, 1 PARTIAL, 0 FAIL**. Floor 15 acceptance case đã đạt.

## Không nằm trong gate

Dashboard Command Button manual RPC được ghi `DEFERRED`, không tính vào 20 case
trên. Lý do: widget có lúc gửi RPC rỗng tới Gateway và OFF được thực thi, nhưng
có lúc request timeout trước khi Gateway nhận được; không đủ ổn định để dùng
làm acceptance gate.

