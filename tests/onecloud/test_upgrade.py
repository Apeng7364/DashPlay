import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import io
import tarfile

spec = importlib.util.spec_from_file_location(
    "upgrade", Path(__file__).resolve().parents[2] / "scripts/onecloud/firmware/upgrade.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


def make(root, files):
    for path, text in files.items():
        target = Path(root) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


class UpgradeInPlaceTests(unittest.TestCase):
    """firmware/upgrade.py: a box keeps its first-run setup, iPhones, identity and settings (2026-10-14)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.box, self.bundle = Path(self.tmp.name, "box"), Path(self.tmp.name, "bundle")
        make(self.box, {
            "supervisor.py": "old", "run-receiver.py": "same", "playport/web/dist/assets/main-old.js": "old page",
            "service.json": '{"interface": "wlan0", "phone": "x"}',
            "state/hotspot.json": '{"ssid": "MyCar", "passphrase": "secret12"}',
            "state/receiver/pairings.properties": "paired", "state/receiver/app-cds.jsa": "old cache",
            "state/options.json": '{"tesla": true}', "identity/offline-mfi/identity.pk8": "key"})
        make(self.bundle, {
            "supervisor.py": "new", "run-receiver.py": "same", "playport/web/dist/assets/main-new.js": "new page",
            "firmware/upgrade.py": "tool", "service.json": '{"interface": "auto"}',
            "state/hotspot.json": '{"ssid": "DashPlay"}'})

    def test_only_program_files_change(self):
        remove, write = upgrade.plan(self.box, self.bundle)
        self.assertEqual(remove, ["playport/web/dist/assets/main-old.js"])
        self.assertEqual(write, ["firmware/upgrade.py", "playport/web/dist/assets/main-new.js", "supervisor.py"])
        upgrade.apply(self.box, self.bundle, remove, write)
        self.assertEqual((self.box / "supervisor.py").read_text(), "new")
        self.assertFalse((self.box / "playport/web/dist/assets/main-old.js").exists())
        self.assertEqual((self.box / "state/hotspot.json").read_text(), '{"ssid": "MyCar", "passphrase": "secret12"}')
        self.assertEqual((self.box / "service.json").read_text(), '{"interface": "wlan0", "phone": "x"}')
        for kept in ("state/receiver/pairings.properties", "state/options.json", "identity/offline-mfi/identity.pk8"):
            self.assertTrue((self.box / kept).exists(), kept)
        self.assertFalse((self.box / "state/receiver/app-cds.jsa").exists(), "the startup cache belongs to the old jars")
        self.assertEqual(upgrade.plan(self.box, self.bundle), ([], []), "a second run changes nothing")

    def test_rollback_puts_the_previous_program_back(self):
        previous = Path(self.tmp.name, "previous")
        make(previous, {"supervisor.py": "old", "run-receiver.py": "same", "playport/web/dist/assets/main-old.js": "old page"})
        upgrade.apply(self.box, self.bundle, *upgrade.plan(self.box, self.bundle))
        upgrade.apply(self.box, previous, *upgrade.plan(self.box, previous))
        self.assertEqual(upgrade.program_files(self.box), upgrade.program_files(previous))
        self.assertEqual((self.box / "supervisor.py").read_text(), "old")
        self.assertTrue((self.box / "state/receiver/pairings.properties").exists())

    def test_bundle_cannot_write_outside_staging(self):
        path = Path(self.tmp.name, 'bad.tar')
        with tarfile.open(path, 'w') as t:
            m = tarfile.TarInfo('../outside'); m.size = 4
            t.addfile(m, io.BytesIO(b'evil'))
        with self.assertRaises((tarfile.TarError, ValueError)):
            upgrade.extract_bundle(path, self.bundle)
        self.assertFalse(Path(self.tmp.name, 'outside').exists())

    def test_rollback_reports_service_failure(self):
        previous = Path(self.tmp.name, 'previous.tar'); previous.touch()
        with patch.object(upgrade, 'ROOT', self.box), patch.object(upgrade, 'PREVIOUS', previous), \
                patch.object(upgrade.os, 'geteuid', return_value=0), \
                patch.object(upgrade, 'restore'), patch.object(upgrade, 'run'), \
                patch.object(upgrade, 'service_up', return_value=False):
            self.assertEqual(upgrade.main(['--rollback']), 1)


if __name__ == "__main__":
    unittest.main()
