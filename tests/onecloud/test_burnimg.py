import hashlib
import struct
import sys
import unittest
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud/firmware'))
import burnimg


def record(main, sub):
    data = bytearray(burnimg.RECORD)
    data[32:32 + len(main)] = main.encode(); data[288:288 + len(sub)] = sub.encode()
    return bytes(data)


def synthetic():
    rootfs = burnimg.sparse(b'\0' * 8192 + b'A' * 4096 + b'\0' * 4096)
    items = [{'record': record('USB', 'DDR'), 'main': 'USB', 'sub': 'DDR', 'data': b'ddr!'},
             {'record': record('PARTITION', 'rootfs'), 'main': 'PARTITION', 'sub': 'rootfs', 'data': rootfs},
             {'record': record('VERIFY', 'rootfs'), 'main': 'VERIFY', 'sub': 'rootfs',
              'data': b'sha1sum ' + hashlib.sha1(rootfs).hexdigest().encode()}]
    header = bytearray(burnimg.HEADER)
    struct.pack_into('<IIIQII', header, 0, 0, 2, burnimg.MAGIC, 0, 4, len(items))
    return burnimg.build({'header': bytes(header), 'align': 4, 'items': items})


class BurnImageTests(unittest.TestCase):
    def test_rebuild_is_byte_identical(self):
        image = synthetic()
        self.assertEqual(burnimg.build(burnimg.parse(image)), image)

    def test_replace_updates_verify_offsets_and_crc(self):
        new = burnimg.sparse(b'B' * 4096 * 3)
        out = burnimg.build(burnimg.replace(burnimg.parse(synthetic()), 'rootfs', new))
        parsed = burnimg.parse(out)   # raises on a bad CRC
        part = next(i for i in parsed['items'] if i['main'] == 'PARTITION')
        verify = next(i for i in parsed['items'] if i['main'] == 'VERIFY')
        self.assertEqual(part['data'], new)
        self.assertEqual(verify['data'], b'sha1sum ' + hashlib.sha1(new).hexdigest().encode())
        self.assertEqual(struct.unpack_from('<Q', out, 12)[0], len(out))
        with self.assertRaises(KeyError):
            burnimg.replace(parsed, 'missing', b'')

    def test_sparse_round_trip_and_corruption_detected(self):
        raw = b'\0' * 4096 + bytes(range(256)) * 16 + b'\0' * 8192 + b'z' * 4096
        self.assertEqual(burnimg.unsparse(burnimg.sparse(raw)), raw)
        bad = bytearray(synthetic()); bad[-1] ^= 1
        with self.assertRaises(ValueError):
            burnimg.parse(bytes(bad))


if __name__ == '__main__':
    unittest.main()
