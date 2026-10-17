"""install_progress.py: what the person and their AI assistant see during a long install step (2026-10-17)."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import install_progress
from install_progress import Progress


class ProgressTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.work, self.lines = Path(d.name), []

    def read(self, step):
        return json.loads((self.work / 'progress' / f'{step}.json').read_text(encoding='utf-8'))

    def test_bytes_give_a_percent_the_time_left_and_whether_unplugging_hurts(self):
        p = Progress(self.work, 'n1-usb', '写 U 盘', total=4 * 10**9, unplug='不要拔 U 盘：中途拔掉要重写', out=self.lines.append, heartbeat=3600)
        p.started -= 120
        p.advance(10**9); p.log('block 250')
        s = self.read('n1-usb')
        self.assertEqual((s['state'], s['percent'], s['safe_to_unplug'], s['note']), ('running', 25.0, False, 'block 250'))
        self.assertAlmostEqual(s['eta_s'], 360, delta=2)
        line = p.line()
        for text in ('写 U 盘', '25%（1.0 GB/4.0 GB）', '已 2 分钟', '约还要 6 分钟', '【注意】不要拔 U 盘'):
            self.assertIn(text, line)
        p.finish()
        s = self.read('n1-usb')
        self.assertEqual((s['state'], s['percent'], s['eta_s']), ('ok', 100.0, None))
        self.assertIn('完成', self.lines[-1])
        log = (self.work / 'progress' / 'n1-usb.log').read_text(encoding='utf-8')
        self.assertIn('start: 写 U 盘', log); self.assertIn('block 250', log)

    def test_a_step_without_a_total_is_compared_to_how_long_it_usually_takes(self):
        p = Progress(self.work, 'f50-flash', 'F50 备份并 Root', expected=480, out=self.lines.append, heartbeat=3600)
        p.started -= 200
        self.assertIn('通常约 8 分钟', p.line()); self.assertIn('现在中断也没关系', p.line())
        self.assertEqual(self.read('f50-flash')['unplug'], '可以中断，重新运行这一步即可')
        p.started -= 400
        self.assertIn('比平时久，还在进行，请继续等', p.line(), 'past the usual time it says so, not that it failed')
        p.set(unplug='不要拔线、不要断电：正在写 F50 的启动分区', total=6, unit='项')
        p.advance(3)
        self.assertIn('50%（3/6 项）', p.line()); self.assertIn('【注意】不要拔线', p.line())
        self.assertFalse(self.read('f50-flash')['safe_to_unplug'])
        p.finish()

    def test_a_failure_inside_marks_the_step_failed_and_still_raises(self):
        with self.assertRaises(RuntimeError):
            with Progress(self.work, 'f50-openwrt', 'F50 装 OpenWrt', expected=600, out=self.lines.append, heartbeat=3600):
                raise RuntimeError('installer stopped')
        s = self.read('f50-openwrt')
        self.assertEqual(s['state'], 'failed'); self.assertIn('installer stopped', s['note'])
        self.assertIn('失败', self.lines[-1])

    def test_heartbeat_prints_while_the_step_is_silent(self):
        with Progress(self.work, 'wait', '等盒子开机', expected=120, out=self.lines.append, heartbeat=0.05):
            time.sleep(0.3)
        self.assertGreaterEqual(len(self.lines), 4, 'start, heartbeats, end')
        self.assertTrue(all(line.startswith('[') for line in self.lines))

    def test_no_emoji_a_chinese_windows_console_could_not_print(self):
        p = Progress(self.work, 'x', '写 U 盘', total=10, unplug='不要拔', out=self.lines.append, heartbeat=3600)
        p.advance(5); p.finish()
        for line in self.lines + [p.line()]:
            line.encode('gbk')

    def test_an_unwritable_work_folder_does_not_stop_the_step(self):
        blocked = self.work / 'file'; blocked.write_text('')
        with Progress(blocked, 'x', 'step', expected=10, out=self.lines.append, heartbeat=3600) as p:
            p.log('still running')
        self.assertIn('完成', self.lines[-1])


class TextTests(unittest.TestCase):
    def test_sizes_and_times(self):
        self.assertEqual(install_progress.size_text(3_686_793_216), '3.7 GB')
        self.assertEqual(install_progress.time_text(45), '45 秒')
        self.assertEqual(install_progress.time_text(130), '2 分 10 秒')
        self.assertEqual(install_progress.time_text(600), '10 分钟')


if __name__ == '__main__':
    unittest.main()
