import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kryzln_vrc_logger.stability_monitor import StabilityMonitor


class StabilityMonitorTests(unittest.TestCase):
    def test_detect_mass_leave_prunes_old_events_and_correlates_recent_switches(self):
        monitor = StabilityMonitor()

        monitor.record_avatar_switch(
            username="RecentSwitcher",
            user_id="usr_recent",
            created_at="2026-03-08 12:00:00",
            avatar_id="avtr_recent0000-0000-0000-0000-000000000000",
            avatar_name="Recent Crash Avatar",
            creator_id="usr_creator_recent",
        )
        monitor.record_avatar_switch(
            username="OldSwitcher",
            user_id="usr_old",
            created_at="2026-03-08 11:59:40",
            avatar_id="avtr_old00000000-0000-0000-0000-000000000000",
            avatar_name="Old Avatar",
            creator_id="usr_creator_old",
        )

        monitor.record_leave(
            username="TooOld",
            user_id="usr_too_old",
            leave_time=datetime(2026, 3, 8, 11, 59, 40),
            players_before_leave=8,
            avatar_id="avtr_leave00000-0000-0000-0000-000000000001",
        )
        monitor.record_leave(
            username="Alice",
            user_id="usr_alice",
            leave_time=datetime(2026, 3, 8, 11, 59, 56),
            players_before_leave=8,
            avatar_id="avtr_leave00000-0000-0000-0000-000000000002",
        )
        monitor.record_leave(
            username="Bob",
            user_id="usr_bob",
            leave_time=datetime(2026, 3, 8, 11, 59, 58),
            players_before_leave=8,
            avatar_id="avtr_leave00000-0000-0000-0000-000000000003",
        )
        monitor.record_leave(
            username="Cara",
            user_id="usr_cara",
            leave_time=datetime(2026, 3, 8, 12, 0, 0),
            players_before_leave=8,
            avatar_id="avtr_leave00000-0000-0000-0000-000000000004",
        )

        incident = monitor.record_leave(
            username="Drew",
            user_id="usr_drew",
            leave_time=datetime(2026, 3, 8, 12, 0, 3),
            players_before_leave=8,
            avatar_id="avtr_leave00000-0000-0000-0000-000000000005",
        )

        self.assertIsNotNone(incident)
        assert incident is not None
        self.assertEqual(incident.leave_count, 4)
        self.assertEqual(incident.baseline, 8)
        self.assertAlmostEqual(incident.leave_ratio, 0.5)
        self.assertIn("RecentSwitcher", monitor.user_risk["usr_recent"].username)
        self.assertEqual(monitor.user_risk["usr_recent"].switches_before_mass_leave, 1)
        self.assertEqual(monitor.user_risk["usr_old"].switches_before_mass_leave, 0)
        self.assertIn("avtr_recent0000-0000-0000-0000-000000000000", incident.affected_avatar_ids)
        self.assertNotIn("avtr_old00000000-0000-0000-0000-000000000000", incident.affected_avatar_ids)
        self.assertEqual(monitor.mass_leave_incidents, 1)

    def test_detect_mass_leave_suppresses_duplicate_incidents_during_cooldown(self):
        monitor = StabilityMonitor()

        for offset, username in enumerate(("Alice", "Bob", "Cara", "Drew")):
            incident = monitor.record_leave(
                username=username,
                user_id=f"usr_{username.casefold()}",
                leave_time=datetime(2026, 3, 8, 12, 0, offset),
                players_before_leave=4,
            )

        self.assertIsNotNone(incident)
        duplicate = monitor.detect_mass_leave(datetime(2026, 3, 8, 12, 0, 4).timestamp())

        self.assertIsNone(duplicate)
        self.assertEqual(monitor.mass_leave_incidents, 1)


if __name__ == "__main__":
    unittest.main()
