# Result — EV-V23-PILOT-20260807-R02

## Đã chứng minh

1. Lát cắt Field 1 chạy theo ownership v2.3: Gateway quyết định/thực thi,
   CoreIoT lưu telemetry và hiển thị trạng thái.
2. Hành vi start/continue/stop, pulse-to-liter, mixed authority, scheduler và
   các guard RPC/safety cốt lõi có test tự động.
3. Tenant live có telemetry cuối chu trình nhất quán giữa Field, Valve, Pump và
   Water Meter.
4. Floor QA 15 core acceptance case đã vượt qua: 19 PASS.

## Chưa tuyên bố

- TC-18 chưa có raw log hoặc ảnh đúng thời điểm ON nên chỉ là `PARTIAL`.
- Chưa có evidence cho chu trình mất cloud `NORMAL -> DEGRADED -> NORMAL`.
- Manual RPC qua Dashboard Command Button không ổn định và đã được tách khỏi
  critical path.
- Không dùng 30 static CoreIoT checks để thay thế platform acceptance.

## Gate tiếp theo

Ưu tiên một batch duy nhất: hoàn thiện degraded/reconnect scenario và lưu raw
log tự động. Không tiếp tục sửa dashboard trước khi report/evidence đạt tiến độ.

