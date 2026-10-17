"""install-image.py offline: download with resume and its check, unpack, waiting for the box, the USB disk checks.
Writing a real stick: a macOS RAM disk (hdiutil attach -nomount ram://...) was written, read back and compared
independently, and the internal disk and a partition were refused, 2026-10-17."""
import hashlib
import http.server
import importlib.util
import json
import lzma
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).resolve().parents[2] / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


image = load('install-image')
DATA = os.urandom(3 * 1024 * 1024 + 123)


class Server(http.server.BaseHTTPRequestHandler):
    """The release file and its .sha256; ranges like GitHub's file servers."""
    ranges = True
    def log_message(self, *_): pass
    def do_GET(self):
        if self.path.endswith('.sha256'):
            body = f'{hashlib.sha256(DATA).hexdigest()}  n1.img.xz\n'.encode(); self.send_response(200)
        else:
            start = int(self.headers.get('Range', 'bytes=0-')[6:].rstrip('-')) if self.ranges else 0
            body = DATA[start:]; self.send_response(206 if start else 200)
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)


class InstallImageTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup); self.dir = Path(d.name)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=self.server.serve_forever, daemon=True).start(); self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1/n1.img.xz'

    def run_tool(self, *argv):
        with patch.object(sys, 'argv', ['install-image.py', '--work', str(self.dir), *argv]):
            image.main()

    def test_download_resumes_a_broken_one_and_checks_it(self):
        (self.dir / 'dl').mkdir(); (self.dir / 'dl/n1.img.xz.part').write_bytes(DATA[:1000000])
        self.run_tool('download', self.url, str(self.dir / 'dl'))
        self.assertEqual((self.dir / 'dl/n1.img.xz').read_bytes(), DATA)
        progress = json.loads((self.dir / 'progress/download-n1.img.xz.json').read_text(encoding='utf-8'))
        self.assertEqual((progress['state'], progress['total']), ('ok', len(DATA)))
        self.assertIn('resuming at 1000000', (self.dir / 'progress/download-n1.img.xz.log').read_text(encoding='utf-8'))

    def test_a_server_that_does_not_resume_starts_over_and_a_bad_file_is_refused(self):
        Server.ranges = False; self.addCleanup(setattr, Server, 'ranges', True)
        (self.dir / 'dl').mkdir(); (self.dir / 'dl/n1.img.xz.part').write_bytes(b'garbage')
        self.run_tool('download', self.url, str(self.dir / 'dl'))
        self.assertEqual((self.dir / 'dl/n1.img.xz').read_bytes(), DATA)
        (self.dir / 'dl/n1.img.xz').write_bytes(b'changed')   # what is there is checked, then fetched again
        self.run_tool('download', self.url, str(self.dir / 'dl'))
        self.assertEqual((self.dir / 'dl/n1.img.xz').read_bytes(), DATA)

    def test_unpack_with_progress(self):
        (self.dir / 'n1.img.xz').write_bytes(lzma.compress(DATA))
        self.run_tool('unpack', str(self.dir / 'n1.img.xz'))
        self.assertEqual((self.dir / 'n1.img').read_bytes(), DATA)
        self.assertFalse((self.dir / 'n1.part').exists())
        self.assertEqual(json.loads((self.dir / 'progress/unpack-n1.img.json').read_text(encoding='utf-8'))['state'], 'ok')

    def test_wait_until_the_box_answers_or_say_it_did_not(self):
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(); self.addCleanup(listener.close)
        self.run_tool('wait', '127.0.0.1', str(listener.getsockname()[1]), '--expect', '60')
        free = socket.socket(); free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]; free.close()
        with patch.object(image.time, 'sleep'), self.assertRaises(SystemExit):
            self.run_tool('wait', '127.0.0.1', str(port), '--expect', '1', '--timeout', '1', '--step', 'first-boot')
        s = json.loads((self.dir / 'progress/first-boot.json').read_text(encoding='utf-8'))
        self.assertEqual(s['state'], 'failed'); self.assertIn('did not answer', s['note'])

    def test_linux_writes_only_a_whole_usb_disk_not_in_use_and_big_enough(self):
        def lsblk(**disk):
            base = dict(name='sdb', size=8 * 10**9, type='disk', tran='usb', rm=True, mountpoint=None, model='Cruzer')
            return subprocess.CompletedProcess([], 0, json.dumps({'blockdevices': [dict(base, **disk)]}), '')
        cases = [({'type': 'part'}, 'give the whole disk'), ({'tran': 'nvme', 'rm': False}, 'not a USB'),
                 ({'children': [{'mountpoint': '/'}]}, 'system'), ({'size': 2 * 10**9}, 'is 2.0 GB')]
        with patch.object(image.sys, 'platform', 'linux'):
            for disk, why in cases:
                with patch.object(image.subprocess, 'run', return_value=lsblk(**disk)), self.assertRaises(SystemExit) as stop:
                    image.usb_disk('/dev/sdb', 4 * 10**9)
                self.assertIn(why, str(stop.exception))
            calls = []
            with patch.object(image.subprocess, 'run', side_effect=lambda argv, **kw: calls.append(argv) or lsblk()):
                self.assertEqual(image.usb_disk('/dev/sdb', 4 * 10**9), ('/dev/sdb', 'Cruzer 8.0 GB (usb)'))
            self.assertEqual(calls[-1][0], 'sh', 'its partitions are unmounted before writing')
        with patch.object(image.sys, 'platform', 'win32'), self.assertRaisesRegex(SystemExit, 'balenaEtcher'):
            image.usb_disk('\\\\.\\PhysicalDrive1', 1)


if __name__ == '__main__':
    unittest.main()
