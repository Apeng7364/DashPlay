import importlib.util
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('presence', Path(__file__).resolve().parents[2] / 'scripts/onecloud/phone-presence.py')
presence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presence)


class PresenceTests(unittest.TestCase):
    def test_boot_grant_not_repeated_by_service_restart(self):
        state = {}
        self.assertTrue(presence.grant_boot(state, 'boot-one'))
        self.assertFalse(presence.grant_boot(state, 'boot-one'))
        self.assertTrue(presence.grant_boot(state, 'boot-two'))

    def test_invite_without_presence_falls_back_to_the_only_paired_phone(self):
        # A phone used on the USB cable only was never remembered as the last wireless user.
        self.assertEqual(presence.invite_phone({}, {'A'}), 'A')
        self.assertIsNone(presence.invite_phone({}, {'A', 'B'}))
        self.assertIsNone(presence.invite_phone({}, set()))
        self.assertEqual(presence.invite_phone({'last_phone': 'B'}, {'A', 'B'}), 'B')
        self.assertEqual(presence.invite_phone({'last_phone': 'C'}, {'A'}), 'A')   # C was removed

    def test_new_network_epoch_invites_the_last_phone_again(self):
        state = {}
        self.assertTrue(presence.grant_boot(state, presence.boot_key('boot-one', 'e1')))
        self.assertFalse(presence.grant_boot(state, presence.boot_key('boot-one', 'e1')))   # same receiver run
        self.assertTrue(presence.grant_boot(state, presence.boot_key('boot-one', 'e2')))    # network rebuilt
        self.assertEqual(presence.boot_key('boot-one'), 'boot-one')   # without a supervisor: per boot only

    def test_reply_requires_arp_reply_to_our_interface_and_ip(self):
        local = bytes.fromhex('020000000001')
        phone = bytes.fromhex('020000000002')
        ip = '192.0.2.1'
        frame = (local + phone + b'\x08\x06' + struct.pack('!HHBBH', 1, 0x0800, 6, 4, 2) +
                 phone + socket.inet_aton('192.0.2.2') + local + socket.inet_aton(ip))
        self.assertEqual(presence.sender(frame, local, ip), ('192.0.2.2', phone.hex()))
        self.assertIsNone(presence.sender(frame, phone, ip))
        self.assertIsNone(presence.sender(frame, local, '192.0.2.3'))
        self.assertIsNone(presence.sender(frame[:20], local, ip))
        self.assertIsNone(presence.sender(frame[:20] + b'\x00\x01' + frame[22:], local, ip))

    def test_probe_rejects_another_device_reusing_the_phone_ip(self):
        sock = MagicMock()
        sock.__enter__.return_value = sock
        sock.recv.side_effect = [b'wrong', b'right']
        with patch.object(presence.socket, 'AF_PACKET', 17, create=True), \
             patch.object(presence.Path, 'read_text', return_value='02:00:00:00:00:01'), \
             patch.object(presence.socket, 'socket', return_value=sock), \
             patch.object(presence.select, 'select', side_effect=[([sock], [], []), ([sock], [], [])]), \
             patch.object(presence, 'sender', side_effect=[('192.0.2.2', '020000000099'), ('192.0.2.2', '020000000002')]):
            result = presence.probe('test0', '192.0.2.1', ['192.0.2.2'], '020000000002')
        self.assertEqual(result, {'ip': '192.0.2.2', 'mac': '020000000002'})
        self.assertEqual(sock.recv.call_count, 2)

    def test_request_is_link_local_broadcast_with_no_credentials(self):
        frame = presence.request(bytes.fromhex('020000000001'), '192.0.2.1', '192.0.2.2')
        self.assertEqual(len(frame), 42)
        self.assertEqual(frame[:6], b'\xff' * 6)
        self.assertEqual(frame[-4:], socket.inet_aton('192.0.2.2'))

if __name__ == '__main__':
    unittest.main()
