import sys
from pathlib import Path
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
from status_light import StatusLight


class StatusLightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for color in ('green', 'blue', 'red'):
            led = self.root / f'onecloud:{color}:alive'; led.mkdir()
            (led / 'trigger').write_text('[mmc1]\n'); (led / 'brightness').write_text('0\n')

    def read(self, color, name):
        return (self.root / f'onecloud:{color}:alive' / name).read_text().strip()

    def test_states_replace_activity_flashing(self):
        light = StatusLight(self.root)
        light.show('starting')
        self.assertEqual(self.read('green', 'trigger'), 'timer')
        self.assertEqual(self.read('green', 'delay_on'), '500')
        self.assertEqual((self.read('blue', 'trigger'), self.read('blue', 'brightness')), ('none', '0'))
        light.show('live')
        self.assertEqual((self.read('green', 'trigger'), self.read('green', 'brightness')), ('none', '1'))
        light.show('waiting')
        self.assertEqual((self.read('blue', 'brightness'), self.read('green', 'brightness')), ('1', '0'))
        light.show('fallback')
        self.assertEqual(self.read('blue', 'trigger'), 'timer')
        self.assertEqual(self.read('red', 'trigger'), '[mmc1]')   # the power light is left alone

    def test_repeated_state_is_not_rewritten_and_missing_leds_are_ignored(self):
        light = StatusLight(self.root)
        light.show('waiting')
        (self.root / 'onecloud:blue:alive' / 'brightness').write_text('0\n')
        light.show('waiting')
        self.assertEqual(self.read('blue', 'brightness'), '0')
        StatusLight(self.root / 'none').show('live')
        light.show('unknown')

    def test_single_white_led_of_the_phicomm_n1_shows_the_state_by_rhythm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            led = root / 'n1:white:status'; led.mkdir()
            (led / 'trigger').write_text('[none]\n'); (led / 'brightness').write_text('1\n')
            light = StatusLight(root)
            light.show('waiting')
            self.assertEqual(((led / 'trigger').read_text().strip(), (led / 'delay_on').read_text().strip(),
                              (led / 'delay_off').read_text().strip()), ('timer', '100', '1900'))
            light.show('fallback')
            self.assertEqual((led / 'delay_on').read_text().strip(), '150')
            light.show('live')
            self.assertEqual(((led / 'trigger').read_text().strip(), (led / 'brightness').read_text().strip()), ('none', '1'))


if __name__ == '__main__':
    unittest.main()
