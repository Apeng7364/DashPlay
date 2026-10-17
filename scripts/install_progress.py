"""Progress of a long install step, for the person and their AI assistant (2026-10-17).

Users waited 8-10 minutes in silence, took it for a failure, and some unplugged the device in the middle; many AI
tools show a command's output only once it ends. So every long step
- prints a line every HEARTBEAT seconds: what runs, how far it is, how long it usually takes, and whether unplugging
  now is harmless or not;
- keeps WORK/progress/<step>.json (state, percent, time left, last note, unplug) and WORK/progress/<step>.log, which
  the assistant reads while the command runs in the background or in the person's own terminal (sudo).
The lines avoid emoji: a Chinese Windows console (GBK) cannot print them.
"""
import json
import os
from pathlib import Path
import threading
import time

HEARTBEAT = 15


def size_text(n):
    for unit, scale in (('GB', 10**9), ('MB', 10**6), ('KB', 10**3)):
        if n >= scale:
            return f'{n / scale:.1f} {unit}'
    return f'{n} B'


def time_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f'{seconds} 秒'
    minutes, rest = divmod(seconds, 60)
    return f'{minutes} 分 {rest} 秒' if minutes < 10 and rest else f'{minutes} 分钟'


def owner(path):
    """Run with sudo (writing a USB stick): the files stay the person's, so the next step without sudo can write them."""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if hasattr(os, 'geteuid') and os.geteuid() == 0 and uid and gid:
        os.chown(path, int(uid), int(gid))


class Progress:
    """with Progress(work, 'n1-usb', '写 U 盘', expected=300, total=size, unplug='...') as p: p.advance(n)

    expected: seconds the step usually takes (what an elapsed-only step is compared to); total: units of work (bytes
    unless unit says otherwise) for a percent and the time left; unplug: what unplugging now does (None: harmless).
    """

    def __init__(self, work, step, title, expected=None, total=None, unit='B', unplug=None, out=None, heartbeat=HEARTBEAT):
        self.dir = Path(work) / 'progress'
        self.file, self.logfile = self.dir / f'{step}.json', self.dir / f'{step}.log'
        self.step, self.title, self.expected, self.total, self.unit, self.unplug = step, title, expected, total, unit, unplug
        self.out = out or (lambda line: print(line, flush=True))
        self.done, self.note, self.state = 0, '', 'running'
        self.started = time.time()
        self.lock, self.stopped = threading.Lock(), threading.Event()
        self.heartbeat = heartbeat
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.logfile.write_text('', encoding='utf-8')
            owner(self.dir), owner(self.logfile)
        except OSError:
            pass   # progress is a help: never the reason a step fails
        self.log(f'start: {title}')
        self.out(self.line())
        self.thread = threading.Thread(target=self.beat, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, trace):
        self.finish('ok' if kind is None else 'failed', None if kind is None else f'{kind.__name__}: {error}')
        return False

    def set(self, done=None, total=None, note=None, unplug=False, expected=None, title=None, unit=None):
        """unplug=False keeps the current text; None makes unplugging harmless."""
        with self.lock:
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total
            if unplug is not False:
                self.unplug = unplug
            if expected is not None:
                self.expected = expected
            if title is not None:
                self.title = title
            if unit is not None:
                self.unit = unit
        if note is not None:
            self.log(note)
        self.write()

    def advance(self, n):
        with self.lock:
            self.done += n

    def log(self, text):
        with self.lock:
            self.note = text
            try:
                with self.logfile.open('a', encoding='utf-8') as f:
                    f.write(f'{time.strftime("%H:%M:%S")} {text}\n')
            except OSError:
                pass
        self.write()

    def snapshot(self):
        with self.lock:
            elapsed = time.time() - self.started
            percent = eta = None
            if self.total:
                percent = min(100.0, 100.0 * self.done / self.total)
                if self.done and self.state == 'running':
                    eta = elapsed * (self.total - self.done) / self.done
            elif self.expected and self.state == 'running':
                eta = max(0, self.expected - elapsed)
            return {'step': self.step, 'title': self.title, 'state': self.state,
                    'started': time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(self.started)),
                    'updated': time.strftime('%Y-%m-%dT%H:%M:%S'), 'elapsed_s': round(elapsed),
                    'done': self.done, 'total': self.total, 'unit': self.unit,
                    'percent': None if percent is None else round(percent, 1),
                    'eta_s': None if eta is None else round(eta), 'expected_s': self.expected,
                    'unplug': self.unplug or '可以中断，重新运行这一步即可', 'safe_to_unplug': not self.unplug,
                    'note': self.note}

    def line(self):
        s = self.snapshot()
        parts = [s['title']]
        if s['percent'] is not None:
            amount = (f'{size_text(s["done"])}/{size_text(s["total"])}' if self.unit == 'B'
                      else f'{s["done"]}/{s["total"]} {self.unit}')
            parts.append(f'{s["percent"]:.0f}%（{amount}）')
        parts.append(f'已 {time_text(s["elapsed_s"])}')
        if s['state'] == 'running':
            if s['percent'] is not None and s['eta_s'] is not None:
                parts.append(f'约还要 {time_text(s["eta_s"])}')
            elif s['expected_s']:
                over = s['elapsed_s'] > s['expected_s']
                parts.append(f'通常约 {time_text(s["expected_s"])}' + ('，比平时久，还在进行，请继续等' if over else ''))
            parts.append(('【注意】' + self.unplug) if self.unplug else '现在中断也没关系')
        else:
            parts.append({'ok': '完成', 'failed': '失败'}.get(s['state'], s['state']))
        return f'[{time.strftime("%H:%M:%S")}] ' + ' · '.join(parts)

    def write(self):
        data = json.dumps(self.snapshot(), ensure_ascii=False, indent=1) + '\n'
        tmp = self.file.with_name(self.file.name + '.tmp')
        try:
            tmp.write_text(data, encoding='utf-8')
            owner(tmp)
            os.replace(tmp, self.file)
        except OSError:
            pass   # progress is a help: never the reason a step fails

    def beat(self):
        while not self.stopped.wait(self.heartbeat):
            self.write()
            self.out(self.line())

    def finish(self, state='ok', note=None):
        if self.state != 'running':
            return
        self.stopped.set()
        with self.lock:
            self.state = state
            if state == 'ok' and self.total:
                self.done = self.total
        self.log(note or ('done' if state == 'ok' else 'failed'))
        self.out(self.line())
