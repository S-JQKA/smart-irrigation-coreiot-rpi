import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GATEWAY_DIR = ROOT / "implementation" / "gateway"
sys.path.insert(0, str(GATEWAY_DIR))

from coreiot.gateway_protocol import RpcCommand  # noqa: E402
from simulator_v22 import SimulationModel  # noqa: E402


class GatewaySimulatorV22Test(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads((GATEWAY_DIR / "config" / "devices.example.json").read_text(encoding="utf-8"))
        self.model = SimulationModel.from_config(config)

    def test_full_topology_contains_16_downstream_devices(self) -> None:
        payload = self.model.tick(timestamp=1_800_000_000_000)
        self.assertEqual(16, len(payload))
        self.assertEqual(2, len(self.model.zones))
        self.assertEqual(8, sum(len(zone.soil_devices) for zone in self.model.zones.values()))

    def test_initial_dry_zone_starts_irrigation_and_pump(self) -> None:
        payload = self.model.tick(timestamp=1_800_000_000_000)
        zone = self.model.zones["field-1"]
        self.assertEqual("ON", zone.valve_state)
        self.assertEqual("BELOW_MIN_MOISTURE", zone.decision_reason)
        self.assertEqual("ON", self.model.site.pump_state)
        self.assertGreater(payload[zone.water_meter_device][0]["values"]["flowRate"], 0)

    def test_tank_low_rejects_rpc_and_forces_safe_idle(self) -> None:
        zone = self.model.zones["field-1"]
        self.model.site.tank_low_switch = True
        command = RpcCommand(
            device=zone.valve_device,
            request_id=10,
            method="TURN_ON",
            params={"commandId": "cmd-10", "requestedAt": 1_800_000_000_000, "ttlSeconds": 300},
        )
        success, state, reason = self.model.apply_rpc(command)
        self.assertFalse(success)
        self.assertEqual("OFF", state)
        self.assertEqual("SAFETY_BLOCK", reason)
        self.assertEqual("TANK_LOW", zone.safety_block_reason)

    def test_waterlogging_rejects_valve_on(self) -> None:
        zone = self.model.zones["field-1"]
        zone.moisture = 90
        command = RpcCommand(
            device=zone.valve_device,
            request_id=11,
            method="TURN_ON",
            params={"commandId": "cmd-11", "requestedAt": 1_800_000_000_000, "ttlSeconds": 300},
        )
        success, state, reason = self.model.apply_rpc(command)
        self.assertEqual((False, "OFF", "SAFETY_BLOCK"), (success, state, reason))
        self.assertEqual("WATERLOGGING_RISK", zone.safety_block_reason)

    def test_live_rpc_mode_does_not_reopen_valve_after_turn_off(self) -> None:
        zone = self.model.zones["field-1"]
        self.model.tick(timestamp=1_800_000_000_000, local_decision=False)
        self.assertEqual("OFF", zone.valve_state)

        turn_on = RpcCommand(
            device=zone.valve_device,
            request_id=12,
            method="TURN_ON",
            params={"commandId": "cmd-12", "requestedAt": 1_800_000_000_000, "ttlSeconds": 300},
        )
        self.assertTrue(self.model.apply_rpc(turn_on)[0])
        self.model.tick(timestamp=1_800_000_005_000, local_decision=False)
        self.assertEqual("ON", zone.valve_state)
        self.assertEqual("ON", self.model.site.pump_state)

        turn_off = RpcCommand(
            device=zone.valve_device,
            request_id=13,
            method="TURN_OFF",
            params={"commandId": "cmd-13", "requestedAt": 1_800_000_005_000, "ttlSeconds": 300},
        )
        self.assertTrue(self.model.apply_rpc(turn_off)[0])
        self.model.tick(timestamp=1_800_000_010_000, local_decision=False)
        self.assertEqual("OFF", zone.valve_state)
        self.assertEqual("OFF", self.model.site.pump_state)


if __name__ == "__main__":
    unittest.main()
