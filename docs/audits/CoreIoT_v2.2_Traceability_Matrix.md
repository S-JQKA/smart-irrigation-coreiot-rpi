# CoreIoT v2.2 traceability matrix

Trạng thái ở cột baseline phản ánh 11 JSON và tenant trước khi triển khai v2.2.
`Target 17/08` là **mục tiêu**, không phải trạng thái hiện tại. Sau review 04/08,
trạng thái hiện tại của bộ v2.2 vẫn là `LOCAL REVIEWED CANDIDATE`; tenant là
`NOT VALIDATED` và dashboard là `DEFERRED`.

Hai mươi kiểm tra trong `tests/regression/test_coreiot_v22_regression.py` là
static checks (`SC`), không phải 20 test case nghiệm thu SRS và không được dùng
thay cho evidence `SIM+PLATFORM`.

| Requirement | CoreIoT component | Baseline | Target 17/08 | Planned evidence |
|---|---|---|---|---|
| FR-08 | Device profiles, telemetry, dashboard | PARTIAL | IMPLEMENTED | Telemetry keys + dashboard timestamp |
| FR-09 | `SF Env Sensor Cluster`, dashboard | MISSING | SIMULATED | T/RH/Lux/tank/VPD timeline |
| FR-12 | Smart Valve RPC | PARTIAL | SIMULATED | TURN_ON/OFF + ACK/duplicate/expired |
| FR-13 | Pump profile/RPC | MISSING | SIMULATED | Pump ON/OFF + safety reject |
| FR-16 | Soil aggregation → Field | PARTIAL | IMPLEMENTED | Expected/actual average ≤5 s |
| FR-17 | Water delta → consumption | PARTIAL | IMPLEMENTED | Pulse delta calculation |
| FR-18 | Field threshold propagation | PARTIAL | IMPLEMENTED | Field/sensor attribute comparison |
| FR-19 | Dashboard history | IMPLEMENTED | REGRESSION | Seven-day widget configuration + controlled run |
| FR-20 | Low Battery alarm | PARTIAL | IMPLEMENTED | Create at 30, clear at 35 |
| FR-21 | Low/High Moisture alarm | PARTIAL | IMPLEMENTED | Create/clear timeline |
| FR-22 | Stop at moisture/target/quota | MISSING | SIMULATED | Decision + RPC TURN_OFF |
| FR-23 | High Temperature alarm | MISSING | SIMULATED | Major create/clear |
| FR-24 | Tank-low status/alarm | MISSING | SIMULATED | Critical create/clear + SAFETY_BLOCK |
| FR-25 | Node Offline | MISSING | SIMULATED | Inactivity create/telemetry clear |
| FR-28 | Irrigation timeout | MISSING | SIMULATED | Forced OFF/status + alarm |
| FR-29 | PumpDryRun/ZoneFlowLow | MISSING | SIMULATED | Injected flow fault + alarm |
| FR-40 | Gateway data quality | MISSING | SIMULATED | Invalid input absent from main key; quality present |
| FR-42 | Debounce/clear/dedup alarms | MISSING | IMPLEMENTED | Repeated event creates one active alarm |
| FR-46 | Waterlogging lock | PARTIAL | SIMULATED | Three-sample trigger, block, clear |
| FR-49 | Field/System modes | MISSING | SIMULATED | AUTO/MANUAL/DISABLED and NORMAL/DEGRADED/SAFE_IDLE |
| NFR-15 | Gateway API path | MISSING | SIMULATED | Gateway connect, batch telemetry, attribute, RPC |

## Claim boundary

- `IMPLEMENTED`: CoreIoT configuration executes and has platform evidence.
- `SIMULATED`: simulator/gateway test client provides the external behavior; no Pi/hardware claim.
- `DESIGN`: documented only.
- `NOT VALIDATED`: configuration may exist but no evidence is available.
