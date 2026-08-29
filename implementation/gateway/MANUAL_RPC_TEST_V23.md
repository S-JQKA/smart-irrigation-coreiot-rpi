# Manual RPC validation — v2.3 Field 1

Run these checks only after the Field 1 automatic vertical slice has passed.
Use a new `commandId` and current Unix time in milliseconds for every request.

PowerShell timestamp helper:

```powershell
[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
```

## Test M1 — Manual OFF and OFF latch

1. Start the mixed pilot normally:

   ```powershell
   py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.field1-pilot.json
   ```

2. Wait until `SI Smart Valve 1` and the main pump are `ON`.
3. On `SI Smart Valve 1`, send a two-way RPC with method `TURN_OFF` and params:

   ```json
   {
     "commandId": "manual-field1-off-001",
     "source": "MANUAL",
     "requestedAt": 0,
     "ttlSeconds": 30,
     "latchSeconds": 30,
     "reason": "M1_OPERATOR_OFF_TEST"
   }
   ```

   Replace `requestedAt: 0` with the current Unix time in milliseconds immediately before sending.

4. Expected two-way response:

   ```json
   {
     "success": true,
     "state": "OFF",
     "reason": "EXECUTED",
     "commandId": "manual-field1-off-001"
   }
   ```

5. Expected telemetry during the next 30 seconds:

   - Valve 1: `actualValveState=OFF`
   - Valve 1: `lastCommandId=manual-field1-off-001`
   - Valve 1: `lastAck=EXECUTED`
   - Field 1: `decisionReason=MANUAL_OFF_LATCHED`
   - Pump: `pumpState=OFF` if Field 2 is not active
   - No new `local-field-1-*` command while the latch is active

6. If moisture remains below the start threshold, local AUTO may restart after the 30-second latch expires. That restart must use exactly one new local command ID.

## Test M2 — Positive Manual ON path

1. Stop the previous process.
2. Start the same topology with final `LOCAL` authority:

   ```powershell
   py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.example.json --control-authority LOCAL
   ```

3. Set the Field 1 Smart Valve shared attribute `controlMode=MANUAL`, then
   confirm Valve 1 and pump are `OFF`. This prevents AUTO from racing the
   direct Manual RPC while retaining the same final authority boundary.
4. Send method `TURN_ON` to `SI Smart Valve 1` with params:

   ```json
   {
     "commandId": "manual-field1-on-001",
     "source": "MANUAL",
     "requestedAt": 0,
     "ttlSeconds": 30,
     "runDurationSeconds": 60,
     "reason": "M2_OPERATOR_ON_TEST"
   }
   ```

5. Expected response and telemetry:

   - response `success=true`, `state=ON`, `reason=EXECUTED`
   - Valve 1 `actualValveState=ON`
   - Valve 1 `lastCommandId=manual-field1-on-001`
   - Pump `pumpState=ON`
   - Manual tiếp tục đủ 60 giây nếu chỉ đạt `targetMoisture`; hard safety như
     max/flood, tank-low, quota, flow fault và watchdog vẫn dừng sớm

6. Send a new `TURN_OFF` command to finish the test safely. Never reuse the ON command ID.

## Negative metadata checks

The Gateway must reject with `INVALID_COMMAND` when any of these are true:

- `commandId` is absent;
- `source` is absent or not `AUTO|MANUAL|SCHEDULER|SAFETY`;
- `requestedAt` is absent, invalid or over 30 seconds in the future;
- `ttlSeconds` is outside `1..3600`.
- Manual `TURN_ON` thiếu `runDurationSeconds` hoặc giá trị ngoài `1..300`.

`ttlSeconds` chỉ là hạn giao request. `runDurationSeconds` mới là thời gian tưới.
`manualTtlSeconds` cũ còn được nhận tạm thời nhưng Gateway sẽ log cảnh báo
deprecated.

An expired request returns `EXPIRED`; a repeated command ID returns `DUPLICATE`.

Tank-low and waterlogging rejection are separate safety tests. They must be run with explicit simulator fault injection rather than by editing CoreIoT state, because CoreIoT is not authoritative for the hard interlock.

## CoreIoT Command button compatibility

The deployed CoreIoT Command button may drop both Function and Constant params.
The v2.3 Gateway now applies the fallback according to the effective zone
authority:

- a `LOCAL` zone rejects parameter-less `TURN_OFF` and `TURN_ON` with
  `INVALID_COMMAND`;
- a `LOCAL` zone rejects explicit cloud `AUTO|SCHEDULER|SAFETY` RPC with
  `SAFETY_BLOCK` and leaves the actuator state unchanged;
- a metadata-complete `MANUAL` RPC remains valid on a `LOCAL` zone;
- only a `COREIOT_REQUEST` zone may normalize parameter-less `TURN_OFF` into a
  request-correlated Manual OFF with a 30-second TTL and latch;
- parameter-less `TURN_ON` is never normalized.

This makes the compatibility behavior opt-in for the legacy rollback path and
prevents an old tenant rule chain from interrupting the local scheduler.
