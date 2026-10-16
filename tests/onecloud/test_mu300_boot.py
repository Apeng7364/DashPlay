"""mu300_boot.py: the F50 system's boot script changes, on copies shaped like the originals."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/onecloud"))
import mu300_boot

PREINIT = "pi_suppress_stderr=\"y\"\nfs_failsafe_wait_timeout=4\npi_init_path=\"/usr/sbin:/usr/bin:/sbin:/bin\"\n"
HW = """#!/bin/sh /etc/rc.common
START=12

hw_bringup() {
\t/opt/mu300/bin/extra-modules >/tmp/mu300-extra-modules.log 2>&1
\t/opt/mu300/bin/zram-start >/dev/null 2>&1
\t/opt/mu300/bin/wifi-start >/tmp/mu300-wifi-start.log 2>&1
\tcountry=$(uci -q get wireless.radio0.country)
\t# a reload does not restart a hostapd interface that went AP-DISABLED while the world regulatory domain
\t# was still active; one down/up right after the country is live does
\twifi down >>/tmp/mu300-wifi-start.log 2>&1
\twifi up >>/tmp/mu300-wifi-start.log 2>&1
}
"""


class Mu300BootTests(unittest.TestCase):
    def files(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        pre, hw = Path(d.name) / "00_preinit.conf", Path(d.name) / "mu300-hw"
        pre.write_text(PREINIT); hw.write_text(HW); hw.chmod(0o755)
        self.originals = Path(d.name) / "state" / "mu300-originals"
        return pre, hw

    def test_wifi_first_and_the_down_up_only_when_the_access_point_is_not_enabled(self):
        new = mu300_boot.hw(HW)
        lines = [l.strip() for l in new.splitlines()]
        wifi, modules = lines.index("/opt/mu300/bin/wifi-start >/tmp/mu300-wifi-start.log 2>&1"), \
            lines.index("/opt/mu300/bin/extra-modules >/tmp/mu300-extra-modules.log 2>&1")
        self.assertLess(wifi, modules)
        self.assertLess(lines.index('if [ "$ap" = ENABLED ]; then'), lines.index("wifi down >>/tmp/mu300-wifi-start.log 2>&1"))
        self.assertEqual(new.count("zram-start"), 1)
        self.assertEqual(subprocess.run(["sh", "-n"], input=new, text=True).returncode, 0)
        self.assertEqual(mu300_boot.preinit(PREINIT), PREINIT.replace("=4\n", "=1\n"))

    def test_apply_once_keeps_the_original_and_revert_puts_it_back(self):
        pre, hw = self.files()
        said = []
        for path, change in ((pre, mu300_boot.preinit), (hw, mu300_boot.hw)):
            mu300_boot.apply(path, change, said.append, self.originals)
            changed = path.read_text()
            mu300_boot.apply(path, change, said.append, self.originals)   # a second install or upgrade changes nothing
            self.assertEqual(path.read_text(), changed)
        self.assertEqual([s.split(": ", 1)[1].split(" (")[0] for s in said], ["changed", "already changed"] * 2)
        self.assertEqual(hw.stat().st_mode & 0o777, 0o755)
        self.assertEqual(sorted(p.name for p in self.originals.iterdir()), ["00_preinit.conf", "mu300-hw"])
        for path, original in ((pre, PREINIT), (hw, HW)):
            mu300_boot.revert(path, said.append, self.originals)
            self.assertEqual(path.read_text(), original)
        self.assertEqual(hw.stat().st_mode & 0o777, 0o755)

    def test_a_file_it_was_not_made_for_is_left_alone(self):
        pre, hw = self.files()
        hw.write_text(HW.replace("\twifi down", "\t/sbin/wifi down"))
        pre.write_text(PREINIT.replace("=4", "=2"))
        said = []
        mu300_boot.apply(hw, mu300_boot.hw, said.append, self.originals)
        mu300_boot.apply(pre, mu300_boot.preinit, said.append, self.originals)
        self.assertTrue(all("left alone" in s for s in said))
        self.assertFalse(self.originals.exists())


if __name__ == "__main__":
    unittest.main()
