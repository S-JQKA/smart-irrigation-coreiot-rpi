# EV-V23-MINIMAL-20260807-R01

This run proves the v2.3 Minimal offline vertical slice and validates the
generated CoreIoT graphs statically. It does not claim a live tenant import.

Observed deterministic sequence:

1. Tick 1: Field 1 remains OFF while the start debounce is collected.
2. Tick 2: multivariable local decision opens Field 1 and starts the pump.
3. Ticks 3-15: irrigation continues with one unchanged transition command ID.
4. Tick 16: target moisture is reached; valve and pump turn OFF.

The next evidence run must capture CoreIoT telemetry and dashboard state after
binding only Field 1 to the parallel v2.3 package.
