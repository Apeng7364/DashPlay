"""F50/MU300 Bluetooth rfkill: unblock the matching node after hci0, never a fixed index."""
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import board_zte_f50


class RfkillTests(unittest.TestCase):
    def make(self, soft='1', hard='0', name='rfkill5'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        radio = root / name
        radio.mkdir()
        (radio / 'type').write_text('bluetooth\n')
        (radio / 'soft').write_text(soft + '\n')
        (radio / 'hard').write_text(hard + '\n')
        return root, radio

    def test_matches_the_bluetooth_node_and_unblocks(self):
        root, radio = self.make()
        # Another rfkill (Wi-Fi) with a lower index must not be picked.
        wifi = root / 'rfkill0'
        wifi.mkdir()
        (wifi / 'type').write_text('wlan\n')
        (wifi / 'soft').write_text('1\n')
        (wifi / 'hard').write_text('0\n')
        self.assertEqual(board_zte_f50.unblock_rfkill(root), radio)
        self.assertEqual((radio / 'soft').read_text().strip(), '0')

    def test_hard_block_is_reported(self):
        root, radio = self.make(soft='0', hard='1')
        with self.assertRaises(SystemExit) as caught:
            board_zte_f50.unblock_rfkill(root)
        self.assertIn('hard=1', str(caught.exception))

    def test_missing_bluetooth_rfkill_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        wifi = root / 'rfkill0'
        wifi.mkdir()
        (wifi / 'type').write_text('wlan\n')
        with self.assertRaises(SystemExit) as caught:
            board_zte_f50.unblock_rfkill(root)
        self.assertIn('not found', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
