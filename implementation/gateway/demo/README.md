# SmartFarm/CoreIoT video demo runners

These scripts generate isolated temporary configurations and call the accepted
`simulator_v23.py`. They do not overwrite the base configuration or persisted
Gateway schedule state.

## Preflight

From the repository root:

```powershell
py -3 implementation/gateway/demo/validate_all_demo_configs.py
```

Equivalent combined runner modes are:

```powershell
py -3 implementation/gateway/demo/run_demo_sequence.py --validate-only
py -3 implementation/gateway/demo/run_demo_sequence.py --offline
py -3 implementation/gateway/demo/run_demo_sequence.py --live
```

`--live` is explicit because it runs all four scenarios against the tenant,
including intentional MQTT loss/reconnect and TankLow alarm activity.

For a live run, set the same CoreIoT environment variables used by the Gateway:
`COREIOT_MQTT_HOST`, `COREIOT_GATEWAY_TOKEN`, and optionally
`COREIOT_MQTT_PORT`/`COREIOT_MQTT_TLS`. Never show the token in the recording.

## Recommended recording order

```powershell
py -3 implementation/gateway/demo/demo_01_coreiot_connection.py
py -3 implementation/gateway/demo/demo_02_two_field_irrigation.py
py -3 implementation/gateway/demo/demo_03_tank_low_safety.py
py -3 implementation/gateway/demo/demo_04_cloud_reconnect.py
```

The first three can be rehearsed without CoreIoT by adding `--offline`.
The reconnect scenario is live-only because it intentionally pauses and resumes
the MQTT transport.

## Dashboard schedule demo

This live-only runner starts both Fields at normal moisture in `MANUAL`, with no
preloaded schedule or fault injection. It stays connected until `Ctrl+C` and
waits for the Dashboard action `Set local schedule` to write
`localScheduleConfig` to the selected Smart Valve:

```powershell
py -3 implementation/gateway/demo/demo_dashboard_schedule.py
```

When the terminal is already in `implementation\gateway\demo`, use:

```powershell
py -3 .\demo_dashboard_schedule.py
```

Choose a future start time, a short duration such as `30` seconds, and repeat
`0`. Expected state is `SCHEDULED -> RUNNING -> COMPLETED`. `MANUAL` prevents an
automatic moisture-based start but still permits the explicit `SCHEDULER`
request; `DISABLED` would block it.

Each runner prints the expected observation before starting. Demo-specific short
thresholds/timeouts are clearly labeled and exist only in its temporary config;
they must not be presented as production settings. NodeOffline is not automated
here because the tenant-side inactivity window makes it unsuitable for a short
recording; show the already captured create/clear evidence instead.
