import unittest

import sg


class TestSmoke(unittest.TestCase):
    def test_version_is_nonempty_string(self):
        self.assertIsInstance(sg.__version__, str)
        self.assertTrue(sg.__version__)


if __name__ == "__main__":
    unittest.main()
