# CoreIoT v2.3 Minimal — two-Field runtime

This package is a parallel replacement candidate. It must not overwrite the
current v2.2 chains during the first validation pass.

## Control boundary

- CoreIoT stores telemetry/configuration, raises evidence-backed alarms, accepts
  manual requests, displays Gateway schedule/task state and calculates a
  multivariable irrigation recommendation.
- The Raspberry Pi Gateway owns multi-zone arbitration, safety, scheduled/manual
  valve execution and all actual command state.
- `SI Field Recommendation` never sends an automatic RPC and never
  writes `actualValveState`, `pumpState` or `commandStatus`.

## Import order

1. Import profiles without assigning them to production devices.
2. Import `SI Count Alarms` and `SI Field Recommendation`.
3. Import the remaining five rule chains.
4. Bind every `Count Alarms` input node to the v2.3 counter chain.
5. Bind `To Field Recommendation` only in the soil chain to the v2.3 recommendation chain.
6. Keep Field 1 as the verified pilot. Bind Field 2 only after the local two-Field
   runtime suite passes; do not create a synthetic Field 3.

The runtime now supports the two existing Fields, independent water accounting,
scheduler concurrency, watchdog/quota/flow safety and delayed DEGRADED mode.
CoreIoT remains the telemetry/configuration/alarm layer; these additions do not
move actuator authority back into rule chains.

## Device-offline alarm

Every production Device Profile has a `NodeOffline` alarm. It reads CoreIoT's
server-side `active` attribute, creates a `MAJOR` alarm only after
`active=false` for 300 seconds, and clears when `active=true`. The alarm is
propagated through the existing Field/Site relation for visibility.

This is a Device Profile change only. Do not import a replacement rule chain.
Apply the alarm manually to the seven existing tenant profiles using
`MANUAL_NODE_OFFLINE_TENANT_SETUP.md`, then live-test disconnect and reconnect.

The existing tenant Root Rule Chain is retained. Default rule-chain IDs are
intentionally cleared because exported tenant UUIDs are not portable.

Duration alarm specs use the tenant-compatible shape
`spec.predicate.defaultValue`. Do not restore the nested
`spec.predicate.value.defaultValue` form from the v2.2 sources: the current
CoreIoT tenant accepts the import but silently clears that duration value.

## Gateway-local duration schedule MVP

Tenant validation on 2026-08-07 showed that neither the custom
`START_IRRIGATION` event nor the platform's built-in scheduled RPC produced a
message/RPC at its configured time. Root Rule Chain debug contained only normal
device telemetry. Therefore CoreIoT Scheduler execution is **not live-verified**
and is not the demo's authoritative trigger in the current tenant. The obsolete
`START_IRRIGATION` path has been removed from the generated Field chain.

The Gateway bounds the duration again, applies every existing interlock and
publishes one `irrigationTask` history point at the task start timestamp. The
same point is updated from `RUNNING` to `COMPLETED`, `REJECTED`, `CANCELLED` or
`SAFETY_STOPPED`, so the existing Irrigation Tasks widget can render it.

For the tenant upgrade, bind the Field asset profile back to
`SI Field`. Import only
`rule_chains/si_smart_valve_v2_3_minimal.json` as the uniquely named
`SI Smart Valve`, resolve its `Count Alarms` input and
bind the Smart Valve device profile to it. This chain mirrors Gateway-reported
task and schedule telemetry to the related Field; it never starts irrigation.
Do not enable the four legacy 2022 schedules.

## Gateway validation before import

```powershell
py -3 implementation\gateway\simulator_v23.py --offline --ticks 40 --interval 0
py -3 -m unittest tests.unit.test_gateway_control_v23 tests.regression.test_coreiot_v23_minimal -v
```

For live CoreIoT telemetry with local control, omit `--offline`; the config keeps
`transportMode=GATEWAY` and `controlAuthority=LOCAL` as independent settings.

For the working duration demo, use the dedicated Gateway-local schedule config.
Local AUTO remains disabled so the configured schedule is the only start source:

```powershell
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.scheduler-demo.json --ticks 120
```

This fires Field 1 after 30 seconds and Field 2 after 120 seconds, each for a
bounded 60-second run, and publishes `irrigationTask` plus `gatewaySchedule*`
state through CoreIoT. The dashboard's CoreIoT Scheduler table
may display configured tenant events, but those events are not execution evidence.
The Gateway-local path was live-validated on 2026-08-07: the command was accepted,
Field 1/pump stayed ON for approximately 60 seconds, returned OFF, and the existing
Irrigation Tasks widget displayed progress and consumption.

## Rollback

The source ZIP is frozen by SHA-256 in
`manifests/baseline_si_sf_2026_08_07.json`. Rebind Field 1 to its previous
profiles/chains if the live vertical slice does not pass. Do not delete or
overwrite v2.2 during this stage.
