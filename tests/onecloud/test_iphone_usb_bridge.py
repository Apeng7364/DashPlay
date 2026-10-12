"""USB request layout of the wired-CarPlay helper: no device, fcntl.ioctl is mocked."""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[2] / 'scripts/onecloud/iphone-usb-bridge.py'
spec = importlib.util.spec_from_file_location('iphone_usb_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class UsbRequestTests(unittest.TestCase):
    def test_ioctl_numbers_follow_the_cpu(self):
        # struct usbdevfs_ctrltransfer / usbdevfs_bulktransfer end in a user pointer: 16 bytes on the
        # OneCloud's armv7, 24 on the N1's aarch64 (2026-10-12: a 4-byte pointer failed there).
        size = 24 if struct.calcsize('P') == 8 else 16
        self.assertEqual(bridge.USBDEVFS_CONTROL, 0xC0005500 | size << 16)
        self.assertEqual(bridge.USBDEVFS_BULK, 0xC0005502 | size << 16)

    def test_requests_carry_the_full_buffer_address(self):
        seen = []
        def ioctl(fd, number, arg, *rest):
            seen.append((number, bytes(arg)))
            return 3
        with patch.object(bridge.fcntl, 'ioctl', side_effect=ioctl):
            self.assertEqual(len(bridge.control_in(7, 0xC0, 0x52, 0, 4, 1)), 1)
            self.assertEqual(bridge.bulk(7, 0x81, length=8), b'\0\0\0')
        (control, setup), (bulk, request) = seen
        self.assertEqual(len(setup), (control >> 16) & 0x3FFF)
        self.assertEqual(len(request), (bulk >> 16) & 0x3FFF)
        self.assertEqual(struct.unpack(bridge.CONTROL_STRUCT, setup)[:6], (0xC0, 0x52, 0, 4, 1, 1000))
        endpoint, length, timeout, address = struct.unpack(bridge.BULK_STRUCT, request)
        self.assertEqual((endpoint, length, timeout), (0x81, 8, 1000))
        self.assertNotEqual(address, 0)


if __name__ == '__main__':
    unittest.main()
