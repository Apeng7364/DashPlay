from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/onecloud"))
import board_zte_f50


class StopChildrenTests(unittest.TestCase):
    def test_all_children_go_within_one_deadline(self):
        # One child after the other, 8 s each, outlived the supervisor's 8 s for the helper: the helper was
        # killed and bluetoothd/btattach stayed, so the next start found hci0 and gave up (review 2026-10-12).
        stubborn = [("c%d" % i, subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 30"], start_new_session=True))
                    for i in range(3)]
        time.sleep(0.2)
        started = time.monotonic()
        board_zte_f50.stop_children(stubborn, timeout=1)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertTrue(all(child.poll() is not None for _, child in stubborn))


if __name__ == "__main__":
    unittest.main()
