# EV-COREIOT-V22-20260804-R02

Correction run after the rule-chain, dashboard and topology review.

## Scope

- Reclassify rule chains into 7 active candidates and 2 experimental references.
- Remove duplicated Multivar logic from `SI Field`.
- Align Multivar conditions/defaults with SRS v2.2 without the ad-hoc
  `effectiveMin` formula.
- Replace the cloned Valve-shaped Environment chain with a minimal telemetry/VPD
  route.
- Defer dashboard JSON import.
- Assign tank telemetry to Manifold only and separate controller/system state.
- Add `maxConcurrentZones` to the deterministic simulator.
- Rename the 20 local checks as static checks (`SC`), not acceptance test cases.

## Results

- Artifact validation: 23 JSON, 7 active chains, 2 experimental chains,
  9 profiles, dashboard deferred, 0 errors, 8 post-import binding warnings.
- Gateway/simulator unit tests: 9/9 PASS.
- Static review checks: 20/20 PASS.
- Dry-run: 16 child devices, `maxConcurrentZones=1`, one active zone.

## Claim boundary

- Local artifacts: reviewed candidate, not tenant implementation.
- Gateway behavior: simulated.
- CoreIoT tenant: NOT VALIDATED.
- Dashboard: DEFERRED.
- Raspberry Pi/Central Controller/hardware: NOT VALIDATED.

No screenshot, tenant export or platform timing evidence is included in this
run. Therefore this run must not be labelled `SIM+PLATFORM`.
