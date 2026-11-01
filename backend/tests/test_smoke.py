import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gitsync


class SmokeTest(unittest.TestCase):
    def test_version(self):
        self.assertTrue(gitsync.__version__)


if __name__ == "__main__":
    unittest.main()
