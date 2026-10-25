"""The viewer port: the settings page's choice, an older install's saved port, else 18443."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import viewer_port


class ViewerPortTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative, value):
        path = self.state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_default(self):
        self.assertEqual(viewer_port.resolve(self.state), 18443)

    def test_options_choice(self):
        self.write("options.json", {"viewer_port": 19000})
        self.assertEqual(viewer_port.resolve(self.state), 19000)

    def test_saved_receiver_port_survives_an_update(self):
        # A port changed by hand before the option existed is still used when the option is absent.
        self.write("receiver/config.json", {"httpPort": 19500})
        self.assertEqual(viewer_port.resolve(self.state), 19500)

    def test_options_win_over_the_saved_port(self):
        self.write("receiver/config.json", {"httpPort": 19500})
        self.write("options.json", {"viewer_port": 19000})
        self.assertEqual(viewer_port.resolve(self.state), 19000)

    def test_invalid_choice_falls_back(self):
        self.write("options.json", {"viewer_port": 7000})
        self.write("receiver/config.json", {"httpPort": 19500})
        self.assertEqual(viewer_port.resolve(self.state), 19500)

    def test_valid_range_and_reserved(self):
        for port in (1024, 18443, 65535):
            self.assertTrue(viewer_port.valid(port))
        for port in (80, 443, 7000, 1023, 65536, "x", None):
            self.assertFalse(viewer_port.valid(port))


if __name__ == "__main__":
    unittest.main()
