"""Synthetic byte-stream tests. No Bluetooth or accessory identity involved."""
import os
from pathlib import Path
import socket
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'linux/bt-bridge'))
from relay import relay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.local, self.peer = socket.socketpair()
        self.input_read, self.input_write = os.pipe()
        self.output_read, self.output_write = os.pipe()
        self.stop = threading.Event()
        self.errors = []
        def run():
            try:
                relay(self.local.fileno(), self.input_read, self.output_write, self.stop)
            except Exception as error:
                self.errors.append(error)
        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.peer.settimeout(3)

    def tearDown(self):
        self.stop.set()
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive(), 'relay did not stop')
        self.local.close()
        self.peer.close()
        for fd in (self.input_read, self.input_write, self.output_read, self.output_write):
            if fd is not None:
                os.close(fd)

    def test_binary_both_directions_and_backpressure(self):
        data = bytes(range(256)) * 8192
        received = bytearray()
        def feed():
            offset = 0
            while offset < len(data):
                offset += os.write(self.input_write, data[offset:offset+7001])
        writer = threading.Thread(target=feed)
        writer.start()
        while len(received) < len(data):
            received.extend(self.peer.recv(1703))
        writer.join(3)
        self.assertEqual(bytes(received), data)
        self.assertFalse(writer.is_alive())
        received.clear()
        writer = threading.Thread(target=lambda: self.peer.sendall(data))
        writer.start()
        while len(received) < len(data):
            received.extend(os.read(self.output_read, 901))
        writer.join(3)
        self.assertEqual(bytes(received), data)
        self.assertEqual(self.errors, [])

    def test_stdin_eof_exits(self):
        os.close(self.input_write)
        self.input_write = None
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.errors, [])

    def test_remote_eof_exits(self):
        self.peer.shutdown(socket.SHUT_WR)
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.errors, [])

    def test_cancel_while_output_backpressured(self):
        self.peer.setblocking(False)
        for _ in range(100):
            try:
                self.peer.send(b'x'*65536)
            except BlockingIOError:
                break
        self.stop.set()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

if __name__ == '__main__':
    unittest.main()
