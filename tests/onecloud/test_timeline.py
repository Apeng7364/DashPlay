import importlib.util
import io
from pathlib import Path
import socket
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('timeline', Path(__file__).parents[2] / 'scripts/onecloud/carplay-timeline.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def frame(source, target, sport, dport, size, version=4):
    tcp = struct.pack('!HHIIHHHH', sport, dport, 1, 0, 5 << 12, 1000, 0, 0) + b'x' * size
    if version == 4:
        ip = struct.pack('!BBHHHBBH4s4s', 0x45, 0, len(tcp)+20, 0, 0, 64, 6, 0, socket.inet_aton(source), socket.inet_aton(target))
    else:
        ip = struct.pack('!IHBB16s16s', 6 << 28, len(tcp), 6, 64, socket.inet_pton(socket.AF_INET6, source), socket.inet_pton(socket.AF_INET6, target))
    return b'\0'*12 + struct.pack('!H', 0x800 if version == 4 else 0x86dd) + ip + tcp

class TimelineTests(unittest.TestCase):
    def test_separates_phone_video_car_output_and_real_touch_times(self):
        t = m.Timeline('192.0.2.2', 'fe80::2', 5000, start=1000, seconds=.4)
        t.packet(1000.01, frame('fe80::2', 'fe80::1', 6000, 5000, 3000, 6))
        t.packet(1000.03, frame('fe80::2', 'fe80::1', 6001, 5001, 999, 6)) # audio excluded
        t.packet(1000.11, frame('192.0.2.1', '192.0.2.2', 18443, 6000, 3100))
        t.packet(1000.12, frame('192.0.2.2', '192.0.2.1', 6000, 18443, 40))
        t.touches('touch forwarded receivedMs=1000120 epochMs=1000123 down=1 sent=true\n'
                  'touch forwarded receivedMs=1000310 epochMs=1000311 down=0 sent=false')
        self.assertEqual(t.rows[0][:3], [3000, 0, 0])
        self.assertEqual(t.rows[1][:3], [0, 3100, 40])
        self.assertIn('forwardMs=3', t.rows[1][3][0])
        self.assertIn('sent=false', t.rows[3][3][0])
        out = io.StringIO(); t.write(out)
        self.assertIn('0.2,1000200,0,0,0,', out.getvalue()) # silence remains visible
        self.assertNotIn('192.0.2', out.getvalue())

    def test_truncated_fragmented_vlan_and_ipv6(self):
        packet = frame('192.0.2.2', '192.0.2.1', 6000, 5000, 30)
        for n in range(len(packet)):
            self.assertIsNone(m.tcp(packet[:n]))
        self.assertEqual(m.tcp(packet)[4], 30)
        tagged = packet[:12] + b'\x81\x00\x00\x01' + packet[12:]
        self.assertEqual(m.tcp(tagged), m.tcp(packet))
        fragmented = bytearray(packet); fragmented[20:22] = b'\x20\x00'
        self.assertIsNone(m.tcp(fragmented))
        self.assertEqual(m.tcp(frame('fe80::2', 'fe80::1', 6000, 5000, 32, 6))[4], 32)

    def test_offline_pcap_keeps_capture_timestamps(self):
        packet = frame('192.0.2.2', '192.0.2.1', 6000, 5000, 30)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'example.pcap'
            path.write_bytes(struct.pack('<IHHIIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1) +
                             struct.pack('<IIII', 1000, 125000, len(packet), len(packet)) + packet)
            self.assertEqual(list(m.pcap(path)), [(1000.125, packet)])
            path.write_bytes(path.read_bytes()[:-1])
            with self.assertRaises(ValueError): list(m.pcap(path))
