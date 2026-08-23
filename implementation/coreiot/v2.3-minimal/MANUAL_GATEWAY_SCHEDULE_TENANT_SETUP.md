# Manual CoreIoT setup — Gateway-local schedules

Use this checklist for the current tenant. The target ownership is:

- Gateway: schedule timing, arbitration, safety and actuator execution.
- CoreIoT: telemetry, alarms, task history and schedule-status display.

## 1. Back up before editing

1. Export `Root Rule Chain`.
2. Export the current Irrigation Management dashboard.
3. Do not delete the old v2.3 or v2.3.1 chains; keep them as rollback copies.

## 2. Remove the failed CoreIoT Scheduler route

Open `Root Rule Chain` in edit mode:

1. Delete `Is START_IRRIGATION?`.
2. Delete `To Field Scheduler`.
3. Delete the added `Device Profile Node --Failure--> Message Type Switch` connection.
4. Restore `Message Type Switch --Other--> Log Other`.
5. Apply changes.

Open Asset profile `SI Field` and set its Default rule chain back
to the older `SI Field`. Do not select
`SI Field v2.3.1 Scheduler`.

In Advanced features > Scheduler, delete the test events `Scheduler MVP 1 min`,
`Scheduler test 2` and `test RPC`. Keep the four legacy Morning/Evening events
disabled, or delete them after the dashboard backup is confirmed.

## 3. Install the task/schedule telemetry sync chain

1. Go to Rule chains and choose Import rule chain.
2. Select
   `implementation/coreiot/v2.3-minimal/rule_chains/si_smart_valve_v2_3_minimal.json`.
3. Confirm the imported chain name is
   `SI Smart Valve`.
4. Open the chain, edit `Count Alarms`, and select
   `SI Count Alarms` as its target chain.
5. Apply changes.
6. Open Device profile `SI Smart Valve` and set Default rule chain
   to `SI Smart Valve`.

Do not import the generated Field chain into the tenant: the older tenant chain
`SI Field` already provides the required storage/threshold behavior.

## 4. Replace the non-working Scheduler widget

1. Open Irrigation Management and export one dashboard backup.
2. Enter Edit mode and open the Field-detail state.
3. Keep `Irrigation Tasks` unchanged.
4. Remove or hide the existing `Irrigation Schedule` widget because it lists
   CoreIoT Scheduler events that do not execute on this tenant.
5. Add a `Latest values` or `Entities table` widget and name it
   `Gateway Schedules`.
6. Use the current Field/state entity alias as the datasource. The Task Sync
   chain mirrors the following telemetry keys from the related Smart Valve to
   the Field:

| Telemetry key | Column label |
|---|---|
| `gatewayScheduleId` | Schedule |
| `gatewayScheduleSource` | Source |
| `gatewayScheduleEnabled` | Enabled |
| `gatewayScheduleStatus` | Status |
| `gatewayScheduleNextRunTs` | Next run |
| `gatewayScheduleDurationSec` | Duration (s) |
| `gatewayScheduleRepeatEverySec` | Repeat (s) |
| `gatewayScheduleLastRunTs` | Last run |
| `gatewayScheduleLastResultReason` | Result |
| `gatewayScheduleConfigAck` | Config ACK |
| `gatewayScheduleConfigReason` | Config result |

For timestamp columns, select Date/Time formatting if the widget offers it;
otherwise leave the millisecond value during the first validation pass.

If the Field table is blank, verify these keys appear first under the related
Smart Valve's Latest telemetry, then under the Field's Latest telemetry. A value
on the valve but not on the Field means the Task Sync profile binding or
`FieldToSmartValve` relation is wrong.

## 5. Enable dashboard-edited schedules (durable shared attribute)

Do not send schedule definitions through server-side RPC. On this tenant the
lightweight RPC endpoint returns HTTP 504 when CoreIoT does not consider the
downstream Smart Valve session active, even while Gateway telemetry is flowing.
A schedule is durable configuration, so the dashboard stores it as the Smart
Valve shared attribute `localScheduleConfig`. The Gateway subscribes to live
updates and requests the current value again after every reconnect.

1. In dashboard edit mode, create an entity alias named `Current valve`.
2. Alias filter: **Relations query**; root entity is the current dashboard state
   entity, direction `From`, relation type `FieldToSmartValve`, entity type
   `Device`, max level `1`.
3. Duplicate the `Gateway Schedules` widget as a rollback copy.
4. On the working widget, change datasource from `Current field` to
   `Current valve`. Keep the same telemetry columns.
5. Open **Actions**, edit the existing row action `Set local schedule`, keep
   **Custom action**, and replace the complete function with:

```javascript
let injector = widgetContext.$scope.$injector;
let attributeService = injector.get(
  widgetContext.servicesMap.get('attributeService')
);

let defaultStart = new Date(Date.now() + 2 * 60 * 1000);
defaultStart.setMinutes(
  defaultStart.getMinutes() - defaultStart.getTimezoneOffset()
);
let startText = window.prompt(
  'Start time (YYYY-MM-DDTHH:mm)',
  defaultStart.toISOString().slice(0, 16)
);
if (!startText) return;
let durationText = window.prompt('Duration in seconds', '60');
if (!durationText) return;
let repeatText = window.prompt('Repeat every seconds; 0 = once', '0');
if (repeatText === null) return;

let startAtMs = new Date(startText).getTime();
let durationSeconds = Number(durationText);
let repeatEverySeconds = Number(repeatText);

if (
  !Number.isFinite(startAtMs) ||
  startAtMs <= Date.now() ||
  startAtMs > Date.now() + 7 * 24 * 60 * 60 * 1000 ||
  !Number.isInteger(durationSeconds) ||
  durationSeconds < 1 ||
  !Number.isInteger(repeatEverySeconds) ||
  repeatEverySeconds < 0 ||
  (repeatEverySeconds > 0 && repeatEverySeconds < durationSeconds)
) {
  window.alert(
    'Invalid input: start must be within 7 days; duration >= 1; ' +
    'repeat must be 0 or >= duration.'
  );
  return;
}

let now = Date.now();
let safeEntityName = String(entityName || 'smart-valve')
  .replace(/\s+/g, '-')
  .toLowerCase();
let config = {
  schemaVersion: 1,
  configId: 'dashboard-schedule-' + safeEntityName + '-' + now,
  scheduleId: safeEntityName + '-operator',
  enabled: true,
  startAtMs: startAtMs,
  durationSeconds: durationSeconds,
  repeatEverySeconds: repeatEverySeconds
};

attributeService.saveEntityAttributes(
  entityId,
  'SHARED_SCOPE',
  [{key: 'localScheduleConfig', value: config}]
).subscribe(
  function() {
    console.log('localScheduleConfig saved', config);
    window.alert('Schedule saved; wait for Gateway Config ACK');
    widgetContext.updateAliases();
  },
  function(error) {
    console.warn('localScheduleConfig save failed', error);
    window.alert(
      'Attribute save failed: ' +
      (error.message || JSON.stringify(error))
    );
  }
);
```

6. Save the dashboard. Keep the simulator running continuously, click
   `Set local schedule`, choose a time 2 minutes in the future, duration `60`,
   repeat `0`.

Start the simulator without `--ticks` so it remains online to receive the
shared-attribute update and execute the schedule:

```powershell
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.scheduler-demo.json
```

7. Expected Gateway log first contains
   `schedule attribute device=SI Smart Valve ... accepted=True ... EXECUTED`,
   then at the chosen time
   `local schedule fired ... accepted=True ack=EXECUTED`.
8. The widget must change to `SCHEDULED`, then `RUNNING`, then `COMPLETED`.
   `Config ACK` must be `ACCEPTED` after the updated v2.3.3 chain is bound.

The Gateway persists accepted schedules in
`<selected-config>.runtime-schedules.json`. A one-shot schedule that has already
expired is not restored; a repeating schedule advances to its next future run.

The old `SET_LOCAL_SCHEDULE` RPC handler remains only for compatibility tests;
the dashboard must not call `/api/rpc/oneway` or `/api/plugins/rpc/oneway`.

## 6. Fix Field water totals and Statistics

The exported `Statistics` widget reads `waterConsumptionLiters` from the
`Current field` alias. That key originally existed only on the Water Meter, so
the Field card/chart could show `0.0 L` while Irrigation Tasks showed real
consumption. The v2.3.3 Smart Valve chain mirrors Gateway-authoritative
`waterConsumptionLiters`, `cycleWaterLiters`, and `dailyWaterLiters` to Field.

After binding v2.3.3, keep the Statistics datasource as `Current field` and the
key as `waterConsumptionLiters`. Restart the simulator and confirm the Field's
Latest telemetry contains a value greater than zero during irrigation.

## 7. Live validation

Run from the workspace root:

```powershell
py -3 implementation\gateway\simulator_v23.py --config implementation\gateway\config\devices.v23.scheduler-demo.json --ticks 60
```

Expected timeline:

- About +30 seconds: `field-1-demo` accepted; Field 1 and pump ON.
- About +90 seconds: Field 1 and pump OFF; task COMPLETED.
- About +120 seconds: `field-2-demo` accepted; Field 2 and pump ON.
- About +180 seconds: Field 2 and pump OFF; task COMPLETED.
- `activeZones` never exceeds 1.
- Both Field detail dashboards show schedule state and Irrigation Tasks.

## Rollback

If task/schedule mirroring fails, bind Device profile
`SI Smart Valve` back to `SI Smart Valve v2.3.1 Scheduler`. This
does not change Gateway execution. Restore the exported dashboard only if the
replacement widget cannot be corrected quickly.
