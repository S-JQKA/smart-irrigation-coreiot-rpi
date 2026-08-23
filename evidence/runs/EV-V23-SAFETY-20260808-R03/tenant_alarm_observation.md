# Tenant alarm observation

Quan sát trực tiếp tại trang `Alarms`, bộ lọc `Cleared`, tenant
`app.coreiot.io` sau live run:

| Created time | Originator | Alarm | Severity | Trạng thái |
|---|---|---|---|---|
| 2026-08-08 14:16:46 | SF Manifold 1 | TankLow | Critical | Cleared / Unacknowledged |
| 2026-08-08 14:17:36 | SI Water Meter 2 | ZoneFlowLow | Major | Cleared / Unacknowledged |
| 2026-08-08 14:17:36 | SF Main Pump 1 | PumpDryRun | Critical | Cleared / Unacknowledged |
| 2026-08-08 14:19:12 | SmartFarm Pi Gateway | GatewayDegraded | Major | Cleared / Unacknowledged |

`GatewayDegraded` chỉ có thể đến tenant sau reconnect; buffered degraded
telemetry được replay rồi mẫu `NORMAL` hiện tại làm alarm clear.
