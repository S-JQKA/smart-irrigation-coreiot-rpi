# Tenant alarm observation

Quan sát trực tiếp trên tenant `app.coreiot.io` ngày 2026-08-08:

| Mốc | Quan sát | Trạng thái |
|---|---|---|
| Gateway shutdown | `requested=16 acknowledged=16` trước MQTT disconnect | PASS |
| Site devices inactive | Gateway, Main Pump, Manifold và Env hiển thị `Inactive` | PASS |
| Field device create | Notification `NodeOffline`, Major, originator `SI Soil Moisture 1` | PASS |
| Site device create | Notification `NodeOffline` trên Gateway, Main Pump và Manifold | PASS |
| Reconnect clear | Trang Alarms lọc `Active`, `For all time` trả `No alarms found` | PASS |
| Clear notifications | CoreIoT báo `NodeOffline - cleared` trên Soil, Water Meter, Env và các device khác | PASS |

Các SI device cũ ban đầu không chuyển Inactive vì server attribute legacy
`inactivityTimeout=1576800000` ms (khoảng 18,25 ngày). Sau khi loại bỏ override
legacy, chúng dùng Device State timeout của tenant và alarm hoạt động.

Ảnh chỉ ghi nhận những dòng hiển thị được trong viewport; verdict toàn tenant
dựa thêm vào trang Active/All time không còn alarm sau reconnect.
