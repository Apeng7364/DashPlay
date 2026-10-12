"""Bounded binary relay. The caller owns all descriptors and closes them."""
import os
import select

LIMIT = 65536


def relay(peer, source, sink, stop):
    descriptors = (peer, source, sink)
    blocking = [os.get_blocking(fd) for fd in descriptors]
    outgoing, incoming = bytearray(), bytearray()
    input_eof = peer_eof = False
    try:
        for fd in descriptors:
            os.set_blocking(fd, False)
        while not stop.is_set():
            if (input_eof and not outgoing) or (peer_eof and not incoming):
                return
            readers = []
            if not input_eof and not peer_eof and len(outgoing) < LIMIT:
                readers.append(source)
            if not peer_eof and len(incoming) < LIMIT:
                readers.append(peer)
            writers = ([peer] if outgoing and not peer_eof else []) + ([sink] if incoming else [])
            ready_read, ready_write, _ = select.select(readers, writers, [], 0.1)
            for fd in ready_read:
                queue = outgoing if fd == source else incoming
                try:
                    chunk = os.read(fd, LIMIT - len(queue))
                except (BlockingIOError, InterruptedError):
                    continue
                if chunk:
                    queue.extend(chunk)
                elif fd == source:
                    input_eof = True
                else:
                    peer_eof = True
            for fd in ready_write:
                queue = outgoing if fd == peer else incoming
                if fd == peer and peer_eof:
                    continue
                try:
                    count = os.write(fd, queue)
                except (BlockingIOError, InterruptedError):
                    continue
                if count == 0:
                    raise OSError('zero-length write on a ready stream')
                del queue[:count]
    finally:
        for fd, was_blocking in zip(descriptors, blocking):
            os.set_blocking(fd, was_blocking)
