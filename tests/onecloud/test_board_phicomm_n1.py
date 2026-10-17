"""board_phicomm_n1.py: the N1's board-specific firmware names on the Cypress files, on a copy shaped like the image."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import board_phicomm_n1 as n1


class N1FirmwareTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.fw, self.kept, self.said = Path(d.name) / 'firmware', Path(d.name) / 'state' / 'n1-firmware-originals', []
        brcm, cypress = self.fw / 'brcm', self.fw / 'cypress'
        brcm.mkdir(parents=True); cypress.mkdir()
        (brcm / 'brcmfmac43455-sdio.bin').write_bytes(b'wl0: Feb 27 2018 version 7.45.154 FWID 01-4fbe0b04')
        os.symlink('brcmfmac43455-sdio.bin', brcm / 'brcmfmac43455-sdio.phicomm,n1.bin')
        (brcm / 'brcmfmac43455-sdio.phicomm,n1.txt').write_text('nvram')
        (brcm / 'brcmfmac43455-sdio.clm_blob').write_bytes(b'CLM 9.10.105')
        (cypress / 'cyfmac43455-sdio.bin').write_bytes(b'wl0: Apr 15 2021 version 7.45.234 (4ca95bb CY) FWID 01-996384e2')
        (cypress / 'cyfmac43455-sdio.clm_blob').write_bytes(b'CLM 1.29.4')
        self.brcm = brcm

    def apply(self):
        return n1.apply(self.fw, self.kept, self.said.append)

    def loaded(self, name):
        return (self.brcm / name).read_bytes()

    def test_board_names_point_at_cypress_and_nothing_shared_changes(self):
        self.assertTrue(self.apply())
        self.assertIn(b'7.45.234', self.loaded('brcmfmac43455-sdio.phicomm,n1.bin'))
        self.assertEqual(self.loaded('brcmfmac43455-sdio.phicomm,n1.clm_blob'), b'CLM 1.29.4')
        # other boards read the shared names: untouched, and so is the N1's nvram
        self.assertIn(b'7.45.154', self.loaded('brcmfmac43455-sdio.bin'))
        self.assertEqual(self.loaded('brcmfmac43455-sdio.clm_blob'), b'CLM 9.10.105')
        self.assertEqual(self.loaded('brcmfmac43455-sdio.phicomm,n1.txt'), b'nvram')
        self.assertFalse(self.apply(), 'every later start: nothing to change, no driver reload')

    def test_a_system_update_writing_the_file_again_is_undone_and_revert_brings_the_image_back(self):
        self.apply()
        (self.brcm / 'brcmfmac43455-sdio.phicomm,n1.bin').unlink()
        (self.brcm / 'brcmfmac43455-sdio.phicomm,n1.bin').write_bytes(b'version 7.45.154 from an update')
        self.assertTrue(self.apply())
        self.assertIn(b'7.45.234', self.loaded('brcmfmac43455-sdio.phicomm,n1.bin'))
        n1.revert(self.fw, self.kept, self.said.append)
        link = self.brcm / 'brcmfmac43455-sdio.phicomm,n1.bin'
        self.assertEqual(os.readlink(link), 'brcmfmac43455-sdio.bin', 'the first original: the image\'s own link')
        self.assertFalse((self.brcm / 'brcmfmac43455-sdio.phicomm,n1.clm_blob').exists(), 'it was not there before')
        self.assertEqual(list(self.kept.iterdir()), [])

    def test_links_made_by_hand_before_count_as_applied_and_revert_falls_back_to_the_shared_names(self):
        # The N1 of 2026-10-17 was linked by hand first (its own backups as *.orig-7.45.154 beside them).
        for name, target in n1.LINKS.items():
            link = self.brcm / name
            if link.is_symlink(): link.unlink()
            os.symlink(target, link)
        self.assertFalse(self.apply(), 'already applied: no reload')
        self.assertFalse(self.kept.exists())
        n1.revert(self.fw, self.kept, self.said.append)
        self.assertFalse(any((self.brcm / name).is_symlink() for name in n1.LINKS))
        self.assertIn(b'7.45.154', self.loaded('brcmfmac43455-sdio.bin'), 'the shared file brcmfmac then reads')

    def test_without_the_measured_cypress_firmware_the_image_keeps_its_own(self):
        (self.fw / 'cypress/cyfmac43455-sdio.bin').write_bytes(b'version 7.45.241')
        self.assertFalse(self.apply())
        (self.fw / 'cypress/cyfmac43455-sdio.clm_blob').unlink()
        self.assertFalse(self.apply())
        self.assertEqual(os.readlink(self.brcm / 'brcmfmac43455-sdio.phicomm,n1.bin'), 'brcmfmac43455-sdio.bin')
        self.assertFalse(self.kept.exists())

    def test_only_the_n1_and_the_driver_is_reloaded_vendor_parts_first(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        tree = Path(d.name) / 'compatible'
        tree.write_bytes(b'phicomm,n1\0amlogic,s905d\0amlogic,meson-gxl\0')
        self.assertTrue(n1.is_n1(tree))
        tree.write_bytes(b'thunder-onecloud\0amlogic,meson8b\0')
        self.assertFalse(n1.is_n1(tree))
        self.assertFalse(n1.is_n1(Path(d.name) / 'none'))
        calls = []
        n1.reload_driver(lambda argv, **kw: calls.append(argv),
                         lambda: 'brcmfmac_wcc 16384 0 - Live\nbrcmfmac 400000 1 brcmfmac_wcc, Live\nbrcmutil 16384 1 brcmfmac, Live\n')
        self.assertEqual(calls, [['modprobe', '-r', 'brcmfmac_wcc'], ['modprobe', '-r', 'brcmfmac'], ['modprobe', 'brcmfmac']])


if __name__ == '__main__':
    unittest.main()
