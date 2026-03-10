import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kryzln_vrc_logger.log_parser import LogParser


class LogParserAvatarSwitchTests(unittest.TestCase):
    def test_extract_avatar_switches_pairs_nearby_avatar_ids(self):
        parser = LogParser()
        content = "\n".join(
            [
                "2026.03.08 12:00:00 Log        -  [Behaviour] Switching Alice to avatar Spring Fox",
                "2026.03.08 12:00:05 Log        -  [Behaviour] Saving Avatar Data:avtr_11111111-1111-1111-1111-111111111111",
                "2026.03.08 12:00:06 Log        -  [Behaviour] Switching Bob to avatar Neon Wolf",
                "2026.03.08 12:00:09 Log        -  [Behaviour] Loading Avatar Data:avtr_22222222-2222-2222-2222-222222222222",
                "2026.03.08 12:00:30 Log        -  [Behaviour] Saving Avatar Data:avtr_33333333-3333-3333-3333-333333333333",
            ]
        )

        events = parser._extract_avatar_switches_with_ids(content)

        self.assertEqual(
            events,
            [
                (
                    datetime(2026, 3, 8, 12, 0, 0),
                    "Alice",
                    "Spring Fox",
                    "avtr_11111111-1111-1111-1111-111111111111",
                ),
                (
                    datetime(2026, 3, 8, 12, 0, 6),
                    "Bob",
                    "Neon Wolf",
                    "avtr_22222222-2222-2222-2222-222222222222",
                ),
            ],
        )

    def test_extract_avatar_switches_respects_active_filter_and_self_filter(self):
        parser = LogParser()
        parser.my_username = "MyUser"
        content = "\n".join(
            [
                "2026.03.08 12:00:00 Log        -  [Behaviour] Switching MyUser to avatar My Avatar",
                "2026.03.08 12:00:01 Log        -  [Behaviour] Switching ActiveFriend to avatar Shared Avatar",
                "2026.03.08 12:00:05 Log        -  [Behaviour] Loading Avatar Data:avtr_aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "2026.03.08 12:00:06 Log        -  [Behaviour] Switching InactiveFriend to avatar Hidden Avatar",
                "2026.03.08 12:00:10 Log        -  [Behaviour] Loading Avatar Data:avtr_bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            ]
        )

        events = parser._extract_avatar_switches_with_ids(content, {"ActiveFriend"})

        self.assertEqual(
            events,
            [
                (
                    datetime(2026, 3, 8, 12, 0, 1),
                    "ActiveFriend",
                    "Shared Avatar",
                    "avtr_aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
