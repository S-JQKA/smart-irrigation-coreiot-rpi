# CoreIoT v2.2 correction package

This area stores reviewable CoreIoT configuration artifacts. It does not prove
that the artifacts have been imported or executed on the tenant.

## Current scope after the 04/08 review

- `baseline/2026-08-03/exports/`: the 11 owner-supplied exports, unchanged and
  retained for comparison/rollback.
- `v2.2/rule_chains/`: seven active candidates and two experimental references.
- `v2.2/profiles/`: nine local profile candidates.
- `v2.2/dashboards/irrigation_management_v2_2_staging.json`: deferred layout
  reference only; do not import it.
- `v2.2/manifests/migration_manifest.json`: the authoritative active/deferred/
  experimental classification and import order.

### Active rule-chain candidates

1. `SI Count Alarms v2.2`
2. `SI Field v2.2`
3. `SI Field Multivar Control v2.2`
4. `SI Soil Moisture v2.2`
5. `SI Water Meter v2.2`
6. `SI Smart Valve v2.2`
7. `SI Environment v2.2`

`SI Field v2.2` preserves the template orchestration, removes its two internal
generators, and delegates multivariable evaluation exactly once. `SI Environment
v2.2` is a minimal telemetry/VPD/Field-routing chain and contains no actuator
RPC nodes.

### Experimental references — do not import

- `EXPERIMENTAL - SI Pump Control v2.2`
- `EXPERIMENTAL - SI Safety v2.2`

Pump arbitration, `maxConcurrentZones`, hard tank interlock, watchdog, dry-run,
manual TTL/latch and NORMAL/DEGRADED/SAFE-IDLE are authoritative at Raspberry
Pi / Central-Manifold. CoreIoT profiles may display the resulting telemetry and
raise alarms; they are not the final safety authority.

## Dashboard decision

The generated seven-state dashboard failed tenant import because its widgets,
aliases and datasource definitions are not portable enough. It is now marked
`DEFERRED_REFERENCE_ONLY`. Build the real dashboard incrementally from the
tenant's widget library after entities and telemetry are stable.

## Local validation boundary

```powershell
python implementation/coreiot/scripts/generate_v22_artifacts.py
python implementation/coreiot/scripts/validate_v22_artifacts.py
python -m unittest discover -s tests/regression -v
```

Expected local result after correction:

- 23 JSON parsed;
- 7 active rule chains, 2 experimental rule chains, 9 profiles;
- dashboard deferred;
- 0 structural errors;
- 8 post-import bindings intentionally unresolved;
- 20/20 **static checks** pass.

The 20 checks are `SC`, not SRS acceptance `TC`. They do not prove CoreIoT
execution, alarm timing, RPC delivery or dashboard behavior.

## Import safety

1. Never import the two `EXPERIMENTAL` chains.
2. Never import the deferred dashboard JSON.
3. Import only into `[TEST] SmartFarm v2.2`.
4. Resolve the eight rule-chain references by exact v2.2 name after import.
5. Keep Field/Site in `DISABLED` + `SAFE-IDLE` until attributes and relations
   have been checked.
6. Re-export and validate each small import batch before continuing.
7. Never store device tokens, Gateway credentials or Edge secrets in the repo.
