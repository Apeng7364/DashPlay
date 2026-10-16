from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/onecloud"))
import board_zte_f50


class ResetControllerTests(unittest.TestCase):
    """rfkill power cycle of the Bluetooth function: the quick way first, the vendor's 1 s + 2 s waits only after a failed init."""
    def run_reset(self, results):
        import tempfile
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        state = Path(d.name) / "state"
        writes, sleeps, outcomes = [], [], list(results)
        real = Path.write_text
        def record(path, text):
            if path == state: writes.append(text.strip())
            return real(path, text)
        from unittest.mock import patch
        with patch.object(Path, "write_text", record):
            try:
                way = board_zte_f50.reset_controller(state, lambda: outcomes.pop(0), sleep=sleeps.append)
            except SystemExit as error:
                way = error
        return way, writes, sleeps

    def test_quick_way_when_the_init_takes(self):
        self.assertEqual(self.run_reset([True]), ("quick", ["0", "1"], [0.2]))

    def test_vendor_waits_after_a_failed_init(self):
        self.assertEqual(self.run_reset([False, True]), ("with the vendor's waits", ["0", "1", "0", "1"], [0.2, 1, 2]))

    def test_two_failures_stop_the_helper(self):
        way, writes, sleeps = self.run_reset([False, False])
        self.assertIsInstance(way, SystemExit)
        self.assertEqual(sleeps, [0.2, 1, 2])


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
