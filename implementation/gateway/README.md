# SmartFarm Gateway v2.2 / v2.3

The production architecture uses the CoreIoT Gateway API. Direct Device API remains a SIM/diagnostic fallback in the legacy `smartfarm-gateway-sim` package.

## Runtime contract

- One MQTT connection authenticates as `SmartFarm Pi Gateway`.
- Downstream devices are pre-created in CoreIoT and connected by exact name/profile.
- Telemetry is batched through `v1/gateway/telemetry`.
- Startup waits for the MQTT CONNACK before downstream `connect` messages are sent, so the first devices in the topology are not lost during the connection race.
- Shared attributes and downstream RPC use the gateway topics.
- RPC methods are restricted to `TURN_ON`, `TURN_OFF` and the compatibility
  schedule-config path; command ID, delivery TTL, duration and duplicate checks
  run before dispatch.
- Credentials come from environment variables and are never stored in the repository.

## Install

```powershell
python -m pip install -r implementation\gateway\requirements.txt
```

## Reproducible dry run

```powershell
python implementation\gateway\simulator_v22.py --dry-run --ticks 5 --interval 0
```

After the staging entities are created:

```powershell
python implementation\gateway\simulator_v22.py --config implementation\gateway\config\devices.staging.json --ticks 20
```

The default topology contains 16 downstream logical devices: eight soil sensors,
two valves, two water meters, two per-field environment clusters, one shared
main pump and one shared manifold. The Gateway entity itself is not included in
that count.

Each Field is one irrigation zone: four soil sampling points, one valve, one
branch water meter and one environment cluster. The two zones share the pump and
manifold. `tankLevelPct` and `tankLowSwitch` have one owner: the site manifold;
they are not duplicated on both environment clusters. The manifold publishes
`controllerState=ONLINE|OFFLINE|FAULT` separately from
`systemMode=NORMAL|DEGRADED|SAFE-IDLE`.

The simulator enforces `maxConcurrentZones` and prioritizes the driest waiting
zone. This is only a deterministic edge-model check. The Pi runtime and
Central firmware are implemented below, but remain local-tested/build-tested
until they pass HIL on the borrowed boards.

Live mode requires `COREIOT_MQTT_HOST`, `COREIOT_MQTT_PORT`,
`COREIOT_MQTT_TLS` and `COREIOT_GATEWAY_TOKEN` in the process environment.

Control authority is intentionally split by runtime mode:

- `--dry-run` uses the deterministic local scheduler to verify topology,
  priority and `maxConcurrentZones` without CoreIoT.
- Live mode leaves irrigation start/stop decisions to CoreIoT RPC. The local
  model preserves the last acknowledged valve state and only applies runtime
  safety guards, so a later tick cannot reopen a valve after `TURN_OFF`.

## Current validation boundary

Payload encoding, RPC validation, deduplication, buffering, full-topology telemetry and safety rejection have unit tests. Live MQTT validation requires a staging Gateway entity and `COREIOT_GATEWAY_TOKEN`.

## v2.3 Minimal edge-authoritative path

`simulator_v23.py` is the parallel replacement candidate. It keeps transport
and control authority independent, so the Pi can publish to CoreIoT while the
local scheduler remains authoritative:

```powershell
# Deterministic two-Field offline proof
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.example.json --offline --ticks 40 --interval 0

# Live CoreIoT telemetry plus local control (requires environment credentials)
py -3 implementation\gateway\simulator_v23.py --control-authority LOCAL

# CoreIoT may send manual/automation requests; edge safety still has final authority
py -3 implementation\gateway\simulator_v23.py --control-authority COREIOT_REQUEST

# Field 1 pilot: local v2.3 control on Field 1, legacy CoreIoT requests on Field 2
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.field1-pilot.json
```

The v2.3 runtime contains only the two existing Fields and 16 downstream
devices; it does not create a synthetic Field 3. The decision engine uses average moisture as the primary gate and uses
temperature, VPD and light as SRS-aligned priority modifiers. Critical moisture
can bypass start debounce but never bypasses tank-low, waterlogging, quota,
duration or other hard safety blocks. In `SIM_TWO_FIELD`, valve and pump state
come from the explicitly synthetic `FakeAdapter` boundary; they are not
hardware evidence.

Field 1 and Field 2 have independent cycle, daily and cumulative water
counters. The scheduler supports `maxConcurrentZones=1|2`, priority ordering
and slot handoff. Runtime guards cover tank-low, irrigation watchdog,
cycle/daily quota, stale data, waterlogging, per-zone flow-low and pump dry-run.
Manual ON requires `runDurationSeconds=1..300`; `ttlSeconds` remains only the
request-delivery window. Direct pump ON is always rejected.
An explicit `SCHEDULER` TURN_ON request must carry `runDurationSeconds`; it uses
the same bounded hold mechanism and emits `irrigationTask` progress/history.
`devices.v23.scheduler-demo.json` also defines two Gateway-local one-shot schedules:
Field 1 fires 30 seconds after startup and Field 2 after 120 seconds, each for at
most 60 seconds. This fallback
uses the same `SCHEDULER` command path, arbitration and safety interlocks; it does
not bypass actuator admission. Optional `repeatEverySeconds` enables recurrence
and must be at least the configured duration.

Run the live fallback with:

```powershell
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.scheduler-demo.json --ticks 120
```

Expected evidence is `local schedule fired ... accepted=True`, followed by
`active=['field-1'] pump=ON`, a later Field 2 run and `irrigationTask` points on
both Smart Valves. Each valve also publishes `gatewaySchedule*` state for the
replacement dashboard table.
The live tenant run on 2026-08-07 passed: Field 1 and the pump turned ON at
20:34:42 and both returned OFF at 20:35:43, while the dashboard rendered the
task duration, water volume and progress.

RPC admission is authority-aware. A `LOCAL` zone rejects parameter-less RPC and
cloud `AUTO|SAFETY` commands; only explicit, metadata-complete `MANUAL` and
bounded `SCHEDULER` requests can enter the local actuator path. The legacy parameter-less
`TURN_OFF` compatibility fallback is available only to a
`COREIOT_REQUEST` zone. This prevents an old tenant chain from interrupting the
edge scheduler while preserving the rollback path.

Cloud disconnection enters `DEGRADED` only after the configured 60-second
timeout. Local control continues, telemetry is buffered for at most 15 minutes,
and reconnect re-announces downstream devices before replaying valid buffered
samples.

## HIL and Raspberry Pi runtime

`gateway_runtime.py` is the non-simulated entry point. It accepts
`HIL_FIELD1_3BOARD`, `HIL_TWO_FIELD_4BOARD` or `HARDWARE_TWO_FIELD`, uses `UartEspNowAdapter`, and
changes actual valve/pump output only after a CRC-valid Central ACK. Three
attempts fit inside a three-second ACK deadline; failure is `REJECTED` with
detail `ACK_TIMEOUT`. Central remains responsible for valve-before-pump start,
pump-before-valve stop and the 15-second lease.

```powershell
# Three-board HIL: one synthetic Sensor Node, one USB Bridge, one LED Central
$env:SMARTFARM_SERIAL_PORT = "COM5"
py -3 implementation\gateway\gateway_runtime.py `
  --config implementation\gateway\config\devices.v23.hil-field1.json

# Four-board, two-Field HIL: two synthetic Sensors, Bridge and shared LED Central
py -3 implementation\gateway\gateway_runtime.py `
  --config implementation\gateway\config\devices.v23.hil-two-field-4board.json
```

The legacy three-board HIL connects only mapped Field 1/site devices. The
four-board HIL requires two Sensor Nodes and maps all 16 logical devices while
keeping sensor values synthetic and actuator outputs LED/GPIO. Runtime diagnostics publish
`runtimeMode`, `sensorDataOrigin`, `actuatorBackend` and `evidenceClass` so SIM,
HIL and final hardware evidence cannot be silently mixed.

`HARDWARE_TWO_FIELD` starts with `evidenceClass=HARDWARE-UNVERIFIED`.
`finalHardwareVerified=true` is a commissioning latch, not a development
shortcut; set it only after the complete two-Field physical acceptance suite
passes.

Small control state is atomically persisted under `SMARTFARM_STATE_DIR` (Pi
default `/var/lib/smartfarm-gateway`): schedules, last-known-valid Field config
and a bounded command ledger. MQTT telemetry remains RAM-only with a 900-second
TTL and is not durable across Pi power loss.

Gateway watches `controlMode`, the five ordered moisture thresholds and
`localScheduleConfig` on each mapped Smart Valve. Invalid updates are rejected
as a whole. Hardware runtime starts with outputs OFF and Field modes DISABLED
until a validated persisted/cloud config exists. With NTP required, timestamped
ON/schedule requests remain blocked until the clock is synchronized; OFF
remains available.

The legacy SIM Field 1 pilot config still uses `controlAuthority=MIXED` for
rollback testing only. All HIL/hardware profiles require `LOCAL` authority for
every mapped Field; the legacy HIL profile maps only Field 1 and the final template
maps both Fields. `HIL_TWO_FIELD_4BOARD` also maps both Fields without making a
physical sensor/relay claim.

Two-Field Gateway configs accept `maxConcurrentZones=1|2`. The four-board HIL
defaults to `2`, while `1` remains the arbitration/handoff mode. Central has a
hard cap of two, per-zone leases and keeps the shared pump ON when one valve
stops while the other remains active.
