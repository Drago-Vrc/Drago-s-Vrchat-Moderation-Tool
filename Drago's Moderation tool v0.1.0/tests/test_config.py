import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kryzln_vrc_logger.config import env_flag


class ConfigEnvFlagTests(unittest.TestCase):
    def test_env_flag_uses_default_when_missing_or_unknown(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(env_flag("KRYZLN_TEST_FLAG"))
            self.assertTrue(env_flag("KRYZLN_TEST_FLAG", True))

        with patch.dict(os.environ, {"KRYZLN_TEST_FLAG": "maybe"}, clear=True):
            self.assertFalse(env_flag("KRYZLN_TEST_FLAG"))
            self.assertTrue(env_flag("KRYZLN_TEST_FLAG", True))

    def test_env_flag_handles_common_true_and_false_values(self):
        with patch.dict(os.environ, {"KRYZLN_TEST_FLAG": " YES "}, clear=True):
            self.assertTrue(env_flag("KRYZLN_TEST_FLAG"))

        with patch.dict(os.environ, {"KRYZLN_TEST_FLAG": "off"}, clear=True):
            self.assertFalse(env_flag("KRYZLN_TEST_FLAG", True))


if __name__ == "__main__":
    unittest.main()
