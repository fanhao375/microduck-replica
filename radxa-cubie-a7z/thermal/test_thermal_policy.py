import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import thermal_policy as policy


class ThermalPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.thermal = self.root / "thermal"
        self.cpufreq = self.root / "cpufreq"
        self.backup = self.root / "state/original.json"
        for index, (name, cpu) in enumerate(policy.ZONES.items()):
            zone = self.thermal / f"thermal_zone{index + 4}"
            values = {"type": name, "mode": "enabled", "policy": "power_allocator",
                      "available_policies": "power_allocator step_wise", "temp": "32000"}
            for i, trip in enumerate(policy.EXPECTED_TRIPS):
                values.update({f"trip_point_{i}_{k}": str(v) for k, v in trip.items()})
            for field, value in values.items():
                self.put(zone / field, value)
            self.put(zone / "cdev0/type", f"cpufreq-cpu{cpu}")
            self.put(zone / "cdev0/cur_state", "0")
            for field in ("scaling_max_freq", "cpuinfo_max_freq"):
                self.put(self.cpufreq / f"policy{cpu}" / field, "2002000")

    @staticmethod
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def apply(self):
        return policy.apply(self.thermal, self.cpufreq, self.backup)

    def test_apply_identifies_zones_by_type_preserves_protection_and_original(self):
        before = {p: p.read_bytes() for p in self.thermal.rglob("trip_point_*")}
        self.assertEqual(set(self.apply().values()), {"step_wise"})
        self.apply()
        self.assertEqual(set(policy.saved_policies(self.backup).values()), {"power_allocator"})
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual({policy.read(z["path"] / "mode") for z in policy.inspect(self.thermal).values()}, {"enabled"})

    def test_changed_or_disabled_protection_is_rejected_before_any_write(self):
        for field, value in (("trip_point_2_temp", "120000"), ("mode", "disabled")):
            path = self.thermal / "thermal_zone4" / field
            original = path.read_text()
            path.write_text(value)
            with self.assertRaisesRegex(RuntimeError, "protection"):
                self.apply()
            self.assertFalse(self.backup.exists())
            path.write_text(original)

    def test_missing_cpu_zone_or_cooling_binding_is_rejected(self):
        self.put(self.thermal / "thermal_zone4/cdev0/type", "pwm-fan")
        with self.assertRaisesRegex(RuntimeError, "binding"):
            self.apply()
        self.put(self.thermal / "thermal_zone4/type", "unrelated_zone")
        with self.assertRaisesRegex(RuntimeError, "both CPU"):
            self.apply()

    def test_unavailable_governor_is_rejected(self):
        self.put(self.thermal / "thermal_zone4/available_policies", "power_allocator")
        with self.assertRaisesRegex(RuntimeError, "governor"):
            self.apply()

    def test_second_write_failure_restores_first_zone(self):
        zones = policy.inspect(self.thermal)
        write = Path.write_text
        second = zones["cpub_thermal_zone"]["path"] / "policy"

        def fail_second(path, value, *args, **kwargs):
            if path == second and value.strip() == "step_wise":
                raise OSError("injected sysfs failure")
            return write(path, value, *args, **kwargs)

        with patch.object(Path, "write_text", fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                self.apply()
        self.assertEqual({policy.read(z["path"] / "policy") for z in zones.values()}, {"power_allocator"})

    def test_release_verification_failure_restores_both_zones(self):
        with patch.object(policy, "wait_for_release", side_effect=RuntimeError("cap stayed")):
            with self.assertRaisesRegex(RuntimeError, "cap stayed"):
                self.apply()
        self.assertEqual({z["policy"] for z in policy.inspect(self.thermal).values()}, {"power_allocator"})

    def test_low_temperature_stale_cap_fails_but_hot_cooling_is_preserved(self):
        zone = self.thermal / "thermal_zone4"
        self.put(zone / "cdev0/cur_state", "8")
        zones = policy.inspect(self.thermal)
        with self.assertRaisesRegex(RuntimeError, "not released"):
            policy.wait_for_release(zones, self.cpufreq, timeout=0)
        self.put(zone / "temp", "65000")
        policy.wait_for_release(zones, self.cpufreq, timeout=0)
        self.assertEqual(policy.read(zone / "cdev0/cur_state"), "8")

    def test_invalid_backup_cannot_change_policies(self):
        self.put(self.backup, json.dumps({"kernel": policy.KERNEL,
                                       "policies": dict.fromkeys(policy.ZONES, "user_space")}))
        with self.assertRaisesRegex(RuntimeError, "invalid governor"):
            self.apply()
        self.assertEqual({z["policy"] for z in policy.inspect(self.thermal).values()}, {"power_allocator"})

    def test_saved_original_policies_can_be_restored(self):
        self.apply()
        zones = policy.inspect(self.thermal)
        policy.set_policies(zones, policy.saved_policies(self.backup))
        self.assertEqual({z["policy"] for z in policy.inspect(self.thermal).values()}, {"power_allocator"})


if __name__ == "__main__":
    unittest.main()
