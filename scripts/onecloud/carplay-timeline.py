#!/usr/bin/env python3
"""100 ms packet counters plus the receiver's touch forwarding timestamps.

Live Linux: --car-interface ap0 --phone-interface dpncm --car-address ADDRESS
  --phone-address ADDRESS --video-port PORT --seconds 20 > timeline.csv
PORT is the receiver's video TCP listening port from its current session log.
Offline: use --pcap capture.pcap (classic Ethernet pcap, not pcapng) with the same
addresses/port and --receiver-log receiver.log. No packet payloads are written.
Byte counts include TCP retransmissions and framing/encryption overhead. They
are timing evidence, not a byte-for-byte comparison of decoded video. Audio is
excluded from the iPhone video column by the selected port. Touch rows are actual
sendTouch completion times from the receiver, not guesses from encrypted traffic.
No touch log means unknown, not zero touches. Use viewer lagMs/decodeMs/stallMaxMs
to distinguish car rendering delay when the box forwards promptly.
"""
import argparse
import csv
import ipaddress
from pathlib import Path
import re
import select
import socket
import struct
import sys
import time


def tcp(frame):
    """Return source, destination, ports, payload bytes; ignore malformed/fragmented packets."""
    if len(frame) < 14:
        return None
    kind, offset = struct.unpack_from('!H', frame, 12)[0], 14
    while kind in (0x8100, 0x88a8):
        if len(frame) < offset + 4: return None
        kind, offset = struct.unpack_from('!H', frame, offset + 2)[0], offset + 4
    if kind == 0x0800:
        if len(frame) < offset + 20 or frame[offset] >> 4 != 4: return None
        size = (frame[offset] & 15) * 4
        total = struct.unpack_from('!H', frame, offset + 2)[0]
        if size < 20 or total < size or frame[offset + 9] != 6: return None
        if struct.unpack_from('!H', frame, offset + 6)[0] & 0x3fff: return None
        source = str(ipaddress.ip_address(frame[offset + 12:offset + 16]))
        target = str(ipaddress.ip_address(frame[offset + 16:offset + 20]))
        end, offset = offset + total, offset + size
    elif kind == 0x86dd:
        if len(frame) < offset + 40 or frame[offset] >> 4 != 6: return None
        end = offset + 40 + struct.unpack_from('!H', frame, offset + 4)[0]
        source = str(ipaddress.ip_address(frame[offset + 8:offset + 24]))
        target = str(ipaddress.ip_address(frame[offset + 24:offset + 40]))
        nxt, offset = frame[offset + 6], offset + 40
        # Hop-by-hop, routing and destination options. Fragments/ESP cannot be classified safely.
        while nxt in (0, 43, 60):
            if len(frame) < offset + 2: return None
            nxt, offset = frame[offset], offset + (frame[offset + 1] + 1) * 8
        if nxt != 6: return None
    else:
        return None
    if end > len(frame) or end < offset + 20: return None
    sport, dport = struct.unpack_from('!HH', frame, offset)
    header = (frame[offset + 12] >> 4) * 4
    if header < 20 or offset + header > end: return None
    return source, target, sport, dport, end - offset - header


def pcap(path):
    with Path(path).open('rb') as stream:
        header = stream.read(24)
        variants = {b'\xd4\xc3\xb2\xa1': ('<', 1e6), b'\xa1\xb2\xc3\xd4': ('>', 1e6),
                    b'\x4d\x3c\xb2\xa1': ('<', 1e9), b'\xa1\xb2\x3c\x4d': ('>', 1e9)}
        if len(header) != 24 or header[:4] not in variants: raise ValueError('Use classic pcap, not pcapng')
        order, scale = variants[header[:4]]
        if struct.unpack_from(order + 'I', header, 20)[0] != 1: raise ValueError('Use Ethernet capture interfaces, not tcpdump -i any')
        while raw := stream.read(16):
            if len(raw) != 16: raise ValueError('Truncated pcap record')
            sec, sub, size, _ = struct.unpack(order + 'IIII', raw)
            if size > 4 * 1024 * 1024: raise ValueError('Oversized pcap record')
            frame = stream.read(size)
            if len(frame) != size: raise ValueError('Truncated packet')
            yield sec + sub / scale, frame


class Timeline:
    def __init__(self, car, phone, video_port, viewer_port=18443, start=None, seconds=20):
        self.car, self.phone = str(ipaddress.ip_address(car)), str(ipaddress.ip_address(phone))
        self.video_port, self.viewer_port, self.start, self.seconds = video_port, viewer_port, start, seconds
        self.rows = {}

    def row(self, stamp):
        if self.start is None: self.start = stamp
        index = int(round((stamp - self.start) * 1000000)) // 100000
        if index < 0 or index >= int(self.seconds * 10): return None
        return self.rows.setdefault(index, [0, 0, 0, []])

    def packet(self, stamp, frame):
        info = tcp(frame)
        if not info or not info[4]: return
        source, target, sport, dport, size = info
        index = (0 if source == self.phone and dport == self.video_port else
                 1 if target == self.car and sport == self.viewer_port else
                 2 if source == self.car and dport == self.viewer_port else None)
        if index is not None:
            row = self.row(stamp)
            if row is not None: row[index] += size

    def touches(self, text):
        for match in re.finditer(r'touch forwarded receivedMs=(\d+) epochMs=(\d+) down=(\d+) sent=(true|false)', text):
            received, epoch, down, sent = match.groups()
            row = self.row(int(epoch) / 1000)
            if row is not None:
                row[3].append(f'{epoch}:down={down}:sent={sent}:forwardMs={int(epoch)-int(received)}')

    def write(self, out):
        writer = csv.writer(out)
        writer.writerow(['offset_s', 'epoch_ms', 'iphone_video_bytes', 'box_to_car_bytes', 'car_to_box_bytes', 'touch_forwarded'])
        if self.start is None: return
        for index in range(int(self.seconds * 10)):
            a, b, c, taps = self.rows.get(index, [0, 0, 0, []])
            writer.writerow([f'{index / 10:.1f}', round((self.start + index / 10) * 1000), a, b, c, ';'.join(taps)])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pcap')
    parser.add_argument('--car-interface')
    parser.add_argument('--phone-interface')
    parser.add_argument('--car-address', required=True)
    parser.add_argument('--phone-address', required=True)
    parser.add_argument('--video-port', type=int, required=True)
    parser.add_argument('--viewer-port', type=int, default=18443)
    parser.add_argument('--receiver-log', default='/run/dashplay/receiver.log')
    parser.add_argument('--seconds', type=float, default=20)
    args = parser.parse_args()
    if not 0 < args.seconds <= 300 or not all(0 < n < 65536 for n in (args.video_port, args.viewer_port)):
        parser.error('seconds must be 0..300 and ports 1..65535')
    timeline = Timeline(args.car_address, args.phone_address, args.video_port, args.viewer_port, seconds=args.seconds)
    if args.pcap:
        for stamp, frame in pcap(args.pcap):
            if timeline.start is None: timeline.start = stamp
            if stamp - timeline.start >= args.seconds: break
            timeline.packet(stamp, frame)
    else:
        if not args.car_interface or not args.phone_interface: parser.error('Live capture needs both interfaces')
        if not hasattr(socket, 'AF_PACKET'): parser.error('Live capture needs Linux; use --pcap on this host')
        sockets = []
        try:
            for interface in sorted({args.car_interface, args.phone_interface}):
                sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
                sockets.append(sock)
                sock.bind((interface, 0))
            timeline.start = time.time()
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                ready, _, _ = select.select(sockets, [], [], min(.1, max(0, end - time.monotonic())))
                for sock in ready:
                    frame = sock.recv(65535)
                    timeline.packet(time.time(), frame)
        finally:
            for sock in sockets: sock.close()
    try:
        # Read only the tail of the current log; no log contents or credentials are echoed.
        with Path(args.receiver_log).open('rb') as log:
            log.seek(0, 2); log.seek(max(0, log.tell() - 8 * 1024 * 1024))
            timeline.touches(log.read().decode('utf-8', errors='replace'))
    except OSError:
        print('Touch log unavailable; empty touch cells mean unknown.', file=sys.stderr)
    timeline.write(sys.stdout)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError) as error:
        print('Timeline failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
