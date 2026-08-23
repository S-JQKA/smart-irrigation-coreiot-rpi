# SmartFarm Gateway Simulator

Laptop này đang giả lập vai trò Raspberry Pi gateway trong giai đoạn P0.

## Chạy thử

```powershell
cd C:\Users\voles\Documents\RAS\implementation\smartfarm-gateway-sim
py gateway_sim.py
```

Nếu muốn chạy số vòng cố định:

```powershell
py gateway_sim.py --ticks 20
```

## Nó đang làm gì

- Gửi telemetry cho `SI Soil Moisture 1`: `moisture`, `battery`, `gatewayMode`, `quality`.
- Nếu đất khô hơn `minMoisture`, simulator bật tưới giả lập.
- Khi tưới, gửi telemetry cho `SI Smart Valve 1`: `valveState`.
- Khi tưới, tăng `pulseCounter` cho `SI Water Meter 1`.
- Khi đủ ẩm, dừng tưới.
- Nếu quá ẩm, tạo trạng thái `WaterloggingRisk` và khóa tưới giả lập.

## File chính

- `.env`: host CoreIoT và access token thiết bị.
- `config/devices.json`: mapping zone/device/threshold.
- `gateway_sim.py`: script simulator.
- `logs/gateway_sim.log`: log quyết định sau khi chạy.

## Lưu ý

`.env` đang chứa token thật nên đã được đưa vào `.gitignore`.
