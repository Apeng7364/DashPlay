"""Online update: signed manifest, downloads from GitHub or a mirror, guards, apk plan, rollback (update.py)."""
import functools
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import update

# OpenSSL 3 signs and checks Ed25519 with -rawin; a Mac's own openssl is LibreSSL.
OPENSSL = next((p for p in (os.environ.get('DASHPLAY_OPENSSL'), '/opt/homebrew/opt/openssl@3/bin/openssl',
                            '/usr/local/opt/openssl@3/bin/openssl', shutil.which('openssl'))
                if p and Path(p).exists() and 'OpenSSL 3' in subprocess.run([p, 'version'], capture_output=True,
                                                                              text=True).stdout), None)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Server:
    """GitHub stand-in: /gh/<path> serves files (or fails when listed in `down`); /mirror/<full url> serves the same."""

    def __init__(self, root):
        self.root, self.down, self.seen = Path(root), set(), []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                server.seen.append(self.path)
                path = self.path
                if path.startswith('/mirror/'):
                    path = '/gh/' + path.split('/gh/', 1)[1]
                elif path.removeprefix('/gh/') in server.down:
                    return self.send_error(503)
                target = server.root / path.removeprefix('/gh/')
                if not path.startswith('/gh/') or not target.is_file():
                    return self.send_error(404)
                data = target.read_bytes()
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}/gh/'
        self.mirror = f'http://127.0.0.1:{self.httpd.server_port}/mirror/'

    def put(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class VersionTests(unittest.TestCase):
    def test_dates_compare_as_numbers(self):
        self.assertTrue(update.newer('2026.10.19.1', '2026.10.19'))
        self.assertTrue(update.newer('2026.10.20', '2026.10.19.1'))
        self.assertTrue(update.newer('2026.11.2', '2026.10.30'))
        self.assertFalse(update.newer('2026.10.19', '2026.10.19'))
        self.assertTrue(update.newer('2026.10.19', None), 'an unknown installed version takes the release')
        self.assertFalse(update.newer('latest', '2026.10.19'), 'a malformed release is never newer')

    def test_the_installed_version_comes_from_the_package_list(self):
        # `apk info -v` on OpenWrt 25.12; `apk info -v dashplay` prints the description instead (lab, 2026-10-19).
        listing = 'busybox-1.37.0-r6\ndashplay-2026.10.19.1-r1\ndashplay-jre-21.0.12.1-r1\nluci-app-dashplay-0.261019.40345\n'
        self.assertEqual(update.apk_version('dashplay', listing), '2026.10.19.1-r1')
        self.assertEqual(update.apk_version('dashplay-jre', listing), '21.0.12.1-r1')
        self.assertIsNone(update.apk_version('dashplay', 'dashplay: Receives CarPlay from an iPhone\n'))

    def test_a_box_names_its_release_from_the_bundle(self):
        # 2026-10-20: a user's N1 still ran the image's 2026.10.15 program; nothing on the box said so.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(update.bundled_version(root), 'a development build or an older release')
            update.save(root / 'state/update.json', {'version': '2026.10.19.2'})
            self.assertEqual(update.current_version(root, False), '2026.10.19.2')
            (root / 'version.txt').write_text('2026.10.20\n')
            self.assertEqual(update.bundled_version(root), '2026.10.20')
            self.assertEqual(update.current_version(root, False), '2026.10.20', 'the files on the box win')

    def test_the_apk_plan_may_not_touch_the_kernel_kmods_or_bluez(self):
        # Shape of `apk add --upgrade --simulate` on the F50 (2026-10-19).
        plan = ('(1/4) Upgrading dashplay (2026.10.16-r1 -> 2026.10.19.1-r1)\n'
                '(2/4) Upgrading luci-base (26.280.64158~a450f0d -> 26.288.56989~75a306d)\n'
                '(3/4) Upgrading luci-app-dashplay (0.261016.36841 -> 0.261019.40345)\n'
                '(4/4) Upgrading luci-i18n-dashplay-zh-cn (0.261016.36841 -> 0.261019.40345)\nOK: 267.2 MiB\n')
        self.assertEqual(update.forbidden_in_plan(plan), [])
        plan += ('(5/7) Installing kmod-bluetooth (6.12.94-r1)\n(6/7) Installing bluez-daemon (5.83-r1)\n'
                 '(7/7) Upgrading kernel (6.12.94~abc-r1)\n')
        self.assertEqual(update.forbidden_in_plan(plan), ['bluez-daemon', 'kernel', 'kmod-bluetooth'])


@unittest.skipUnless(OPENSSL, 'needs OpenSSL 3')
class SignedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.key, self.pub = self.dir / 'key.pem', self.dir / 'pub.pem'
        subprocess.run([OPENSSL, 'genpkey', '-algorithm', 'ed25519', '-out', str(self.key)], check=True, capture_output=True)
        subprocess.run([OPENSSL, 'pkey', '-in', str(self.key), '-pubout', '-out', str(self.pub)], check=True, capture_output=True)
        self.server = Server(self.dir / 'www')
        self.addCleanup(self.server.close)
        self.root = self.dir / 'opt/dashplay'
        (self.root / 'state/receiver').mkdir(parents=True)
        (self.root / 'identity/offline-mfi').mkdir(parents=True)
        (self.root / 'identity/offline-mfi/identity.pk8').write_bytes(b'secret identity')
        (self.root / 'state/logs/boot1').mkdir(parents=True)
        (self.root / 'state/logs/boot1/receiver.log').write_text('log')
        (self.root / 'state/bluetooth').mkdir()
        (self.root / 'state/bluetooth/pairing').write_text('link key')
        for name, value in (('OPENSSL', OPENSSL), ('LATEST', self.server.base + 'latest/'), ('MIRRORS', ()),
                            ('PREVIOUS_FEED', self.server.base + 'owrt/v{version}/')):
            p = patch.object(update, name, value)
            p.start()
            self.addCleanup(p.stop)

    def sign(self, data):
        (self.dir / 'm').write_bytes(data)
        subprocess.run([OPENSSL, 'pkeyutl', '-sign', '-inkey', str(self.key), '-rawin', '-in', str(self.dir / 'm'),
                        '-out', str(self.dir / 's')], check=True, capture_output=True)
        return (self.dir / 's').read_bytes()

    def publish(self, manifest, signature=None):
        data = json.dumps(manifest).encode()
        self.server.put('latest/dashplay-update.json', data)
        self.server.put('latest/dashplay-update.json.sig', signature if signature is not None else self.sign(data))

    def openwrt_release(self, version, packages, jre='21.0.12.1-r1'):
        """Serve packages {name: bytes} as openwrt-dashplay v<version>; returns the manifest's openwrt part."""
        base = f'owrt/v{version}/'
        entries, sums = {}, []
        for name, data in packages.items():
            file = f'{name}-{version}-r1.apk' if name != 'dashplay-jre' else f'dashplay-jre-{jre}_aarch64_generic.apk'
            self.server.put(base + file, data)
            sums.append(f'{sha(data)}  {file}')
            entries[name] = {'file': file, 'sha256': sha(data), 'size': len(data)}
        self.server.put(base + 'SHA256SUMS', ('\n'.join(sums) + '\n').encode())
        jre_entry = entries.pop('dashplay-jre', {'file': 'x', 'sha256': '0', 'size': 1})
        return {'base': self.server.base + base, 'packages': entries,
                'jre': {'version': jre, 'files': {'aarch64_generic': jre_entry}}}

    def test_only_a_manifest_signed_by_the_release_key_counts(self):
        manifest = {'format': 1, 'version': '2026.10.20', 'online': True, 'notes': '- x'}
        self.publish(manifest)
        self.assertEqual(update.latest_manifest(self.root, True, self.pub)['version'], '2026.10.20')
        data = json.dumps(manifest).encode()
        self.server.put('latest/dashplay-update.json', data.replace(b'2026.10.20', b'2026.10.21'))   # tampered
        with self.assertRaisesRegex(update.UpdateError, '签名不对'):
            update.latest_manifest(self.root, True, self.pub)
        other = self.dir / 'other.pem'
        subprocess.run([OPENSSL, 'genpkey', '-algorithm', 'ed25519', '-out', str(other)], check=True, capture_output=True)
        subprocess.run([OPENSSL, 'pkey', '-in', str(other), '-pubout', '-out', str(self.dir / 'other.pub')],
                       check=True, capture_output=True)
        self.publish(manifest)
        with self.assertRaisesRegex(update.UpdateError, '签名不对'):
            update.latest_manifest(self.root, True, self.dir / 'other.pub')

    def test_the_mirror_is_used_when_github_fails(self):
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True})
        self.server.down.add('latest/dashplay-update.json')
        with patch.object(update, 'MIRRORS', (self.server.mirror,)):
            self.assertEqual(update.latest_manifest(self.root, False, self.pub)['version'], '2026.10.20')
        self.assertTrue(any(p.startswith('/mirror/') for p in self.server.seen))
        with self.assertRaisesRegex(update.UpdateError, '下载失败'):
            update.latest_manifest(self.root, False, self.pub)

    def test_a_download_must_match_the_manifest(self):
        self.server.put('f.apk', b'package')
        target = self.dir / 'f.apk'
        update.fetch(self.server.base + 'f.apk', target=target, sha256=sha(b'package'), size=7)
        self.assertEqual(target.read_bytes(), b'package')
        with self.assertRaisesRegex(update.UpdateError, '下载失败'):
            update.fetch(self.server.base + 'f.apk', target=target, sha256=sha(b'other'), size=7)

    def test_check_reports_a_newer_openwrt_release(self):
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True, 'notes': '- 新功能'})
        with patch.object(update, 'apk_version', lambda name: '2026.10.19.1-r1'):
            result = update.check(self.root, True, self.pub)
        self.assertEqual((result['current'], result['latest'], result['available'], result['notes']),
                         ('2026.10.19.1', '2026.10.20', True, '- 新功能'))
        self.assertEqual(json.loads((self.root / 'state/update-check.json').read_text())['latest'], '2026.10.20')
        with patch.object(update, 'apk_version', lambda name: '2026.10.20-r1'):
            self.assertFalse(update.check(self.root, True, self.pub)['available'])

    def test_a_box_flashed_from_an_image_is_compared_by_its_files(self):
        (self.root / 'supervisor.py').write_text('new')
        files = {'supervisor.py': sha(b'new')}
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True, 'debian': {'files': files}})
        result = update.check(self.root, False, self.pub)
        self.assertEqual((result['available'], result['current']), (False, '2026.10.20'))
        (self.root / 'supervisor.py').write_text('old')
        result = update.check(self.root, False, self.pub)
        self.assertEqual((result['available'], result['current']), (True, None))

    def test_a_box_with_an_older_system_is_not_updated_online(self):
        # A OneCloud flashed with the 2026-10-12 images: its install.sh is not the one the release was made for.
        (self.root / 'firmware').mkdir()
        (self.root / 'firmware/install.sh').write_text('2026-10-12 system')
        files = {'supervisor.py': sha(b'new'), 'firmware/install.sh': sha(b'2026-10-15 system')}
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True, 'debian': {'files': files}})
        result = update.check(self.root, False, self.pub)
        self.assertEqual((result['available'], result['online']), (True, False))
        self.assertIn('系统比这一版要求的旧', result['reason'])
        self.assertEqual(update.install(self.root, False, self.pub), 1)
        status = json.loads((self.root / 'state/update-status.json').read_text())
        self.assertIn('刷机包', status['message'])
        (self.root / 'firmware/install.sh').write_text('2026-10-15 system')
        self.assertTrue(update.check(self.root, False, self.pub)['online'])

    def test_a_failed_check_is_written_too(self):
        result = update.check(self.root, True, self.pub)   # nothing published
        self.assertIn('下载失败', result['error'])

    def test_no_install_while_carplay_is_in_use_or_when_the_system_changed(self):
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True})
        (self.root / 'state/receiver/phone-presence.json').write_text(json.dumps({'active': '02:00:00:00:00:01'}))
        with patch.object(update, 'apk_version', lambda name: '2026.10.19.1-r1'):
            self.assertEqual(update.install(self.root, True, self.pub), 1)
            self.assertIn('正在使用 CarPlay', json.loads((self.root / 'state/update-status.json').read_text())['message'])
            self.assertIn('正在使用 CarPlay', update.launch(self.root, True))
            (self.root / 'state/receiver/phone-presence.json').write_text('{}')
            self.publish({'format': 1, 'version': '2026.10.20', 'online': False})
            self.assertEqual(update.install(self.root, True, self.pub), 1)
            self.assertIn('刷机包', json.loads((self.root / 'state/update-status.json').read_text())['message'])
        with patch.object(update, 'apk_version', lambda name: '2026.10.20-r1'):
            self.assertEqual(update.install(self.root, True, self.pub), 0)
            self.assertIn('已是最新', json.loads((self.root / 'state/update-status.json').read_text())['message'])

    def openwrt_install(self, simulation_extra='', comes_up=(True,), old_published=True):
        # Larger than fetch()'s default limit, as real packages are (37 MB; lab, 2026-10-19).
        if old_published:
            self.openwrt_release('2026.10.19.1', {n: f'old {n}'.encode() * 200000 for n in update.OPENWRT_PACKAGES})
        new = self.openwrt_release('2026.10.20', {n: f'new {n}'.encode() for n in update.OPENWRT_PACKAGES})
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True, 'openwrt': new})
        calls, ups = [], list(comes_up)

        def run(*args, timeout=300):
            if args[0] == OPENSSL:   # the signature is checked for real
                return subprocess.run(list(args), capture_output=True, text=True)
            calls.append(list(args))
            out = ''
            if args[:3] == ('apk', 'add', '--upgrade') and '--simulate' in args:
                out = '(1/3) Upgrading dashplay (2026.10.19.1-r1 -> 2026.10.20-r1)\n' + simulation_extra
            return subprocess.CompletedProcess(args, 0, out, '')

        arch = self.dir / 'arch'
        arch.write_text('aarch64_generic\n')
        real_path = Path

        def fake_path(*parts):
            return arch if parts == ('/etc/apk/arch',) else real_path(*parts)

        versions = {'dashplay': '2026.10.19.1-r1', 'dashplay-jre': '21.0.12.1-r1'}
        with patch.object(update, 'run', run), patch.object(update, 'apk_version', versions.get), \
                patch.object(update, 'Path', fake_path), patch.object(update, 'settings_port', lambda: 18080), \
                patch.object(update, 'service_up', lambda port, seconds=150: ups.pop(0)), \
                patch.object(update, 'BACKUP', 'backup.tar.gz'):
            code = update.install(self.root, True, self.pub)
        return code, calls, json.loads((self.root / 'state/update-status.json').read_text())

    def test_openwrt_installs_the_three_packages_after_a_clean_plan(self):
        code, calls, status = self.openwrt_install()
        self.assertEqual((code, status['phase']), (0, 'done'), status)
        self.assertIn(['apk', 'update'], calls)
        real = [c for c in calls if c[:3] == ['apk', 'add', '--upgrade'] and '--simulate' not in c]
        self.assertEqual(len(real), 1)
        self.assertEqual(sorted(Path(p).name for p in real[0][4:]),
                         ['dashplay-2026.10.20-r1.apk', 'luci-app-dashplay-2026.10.20-r1.apk',
                          'luci-i18n-dashplay-zh-cn-2026.10.20-r1.apk'], 'no JRE: its version did not change')
        self.assertLess(calls.index(['apk', 'update']), calls.index(real[0]))
        with tarfile.open(self.root.parent / 'backup.tar.gz') as saved:
            names = saved.getnames()
        self.assertIn('identity/offline-mfi/identity.pk8', names)
        self.assertIn('state/bluetooth/pairing', names)
        self.assertFalse([n for n in names if n.startswith('state/logs')], 'logs stay out of the backup')
        self.assertEqual(os.stat(self.root.parent / 'backup.tar.gz').st_mode & 0o777, 0o600)

    def test_openwrt_stops_first_when_the_installed_version_was_never_published(self):
        # F50 on a test build, 2026-10-19: it fetched 35 MB, then stopped with a download error that read like the network.
        code, calls, status = self.openwrt_install(old_published=False)
        self.assertEqual((code, status['phase']), (1, 'failed'))
        self.assertIn('找不到当前版本 2026.10.19.1 的安装包', status['message'])
        self.assertNotIn('网络', status['message'])
        self.assertFalse([p for p in self.server.seen if p.endswith('.apk')], 'stopped before the new release was fetched')
        self.assertFalse([c for c in calls if c[:2] == ['apk', 'add']])

    def test_openwrt_refuses_a_plan_with_kernel_modules(self):
        code, calls, status = self.openwrt_install('(2/3) Installing kmod-bluetooth (6.12.94-r1)\n')
        self.assertEqual((code, status['phase']), (1, 'failed'))
        self.assertIn('kmod-bluetooth', status['message'])
        self.assertFalse([c for c in calls if c[:3] == ['apk', 'add', '--upgrade'] and '--simulate' not in c])

    def test_openwrt_goes_back_when_the_new_version_does_not_come_up(self):
        code, calls, status = self.openwrt_install(comes_up=(False, True))
        self.assertEqual((code, status['phase']), (1, 'failed'))
        self.assertIn('已退回 2026.10.19.1', status['message'])
        back = [c for c in calls if c[:2] == ['apk', 'add'] and '--upgrade' not in c]
        self.assertEqual(sorted(Path(p).name for p in back[0][3:]),
                         ['dashplay-2026.10.19.1-r1.apk', 'luci-app-dashplay-2026.10.19.1-r1.apk',
                          'luci-i18n-dashplay-zh-cn-2026.10.19.1-r1.apk'])

    def test_a_box_runs_the_new_releases_upgrader_with_the_checked_bundle(self):
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode='w') as tar:
            for name, data in (('./firmware/upgrade.py', b'# new upgrader'), ('./supervisor.py', b'new')):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        data = bundle.getvalue()
        self.server.put('dp/v2026.10.20/dashplay-bundle-2026.10.20.tar', data)
        self.publish({'format': 1, 'version': '2026.10.20', 'online': True,
                      'debian': {'base': self.server.base + 'dp/v2026.10.20/', 'files': {'supervisor.py': sha(b'new')},
                                 'bundle': {'file': 'dashplay-bundle-2026.10.20.tar', 'sha256': sha(data), 'size': len(data)}}})
        calls = []

        def run(*args, timeout=300):
            if args[0] == OPENSSL:
                return subprocess.run(list(args), capture_output=True, text=True)
            calls.append(list(args))
            (self.root / 'supervisor.py').write_text('new')
            return subprocess.CompletedProcess(args, 0, '', '')

        with patch.object(update, 'run', run), patch.object(update, 'BACKUP', 'backup.tar.gz'):
            self.assertEqual(update.install(self.root, False, self.pub), 0)
        self.assertEqual(Path(calls[0][1]).name, 'upgrade.py')
        self.assertEqual(Path(calls[0][2]).name, 'dashplay-bundle-2026.10.20.tar')
        self.assertEqual(json.loads((self.root / 'state/update.json').read_text())['version'], '2026.10.20')
        self.assertFalse(json.loads((self.root / 'state/update-check.json').read_text())['available'])


class ServiceUpTests(unittest.TestCase):
    def test_the_page_must_still_answer_a_while_later(self):
        answers = iter([True, False, False, False])   # up at once, then gone: a crash after the start
        def connect(*_):
            if not next(answers, False):
                raise OSError('refused')
            return io.BytesIO()
        with patch.object(update.socket, 'create_connection', connect), patch.object(update.time, 'sleep'):
            self.assertFalse(update.service_up(18080, seconds=0.05, steady=0))
        answers = iter([False, True, True])
        with patch.object(update.socket, 'create_connection', connect), patch.object(update.time, 'sleep'):
            self.assertTrue(update.service_up(18080, seconds=5, steady=0))


class LaunchTests(unittest.TestCase):
    def test_a_launch_that_cannot_start_says_so_and_leaves_nothing_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def missing(*a, timeout=30):
                raise FileNotFoundError(2, 'No such file or directory', a[0])
            with patch.object(update, 'run', missing):
                self.assertIn('无法启动升级', update.launch(root, True))
            status = json.loads((root / 'state/update-status.json').read_text())
            self.assertEqual(status['phase'], 'failed')
            self.assertFalse(update.running(root), 'the next try is not refused')

    def test_launch_starts_the_separate_service_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = []
            with patch.object(update, 'run', lambda *a, timeout=30: calls.append(a) or subprocess.CompletedProcess(a, 0, '', '')):
                self.assertIn('已开始升级', update.launch(root, True))
                # A procd service for this run over ubus: no init script that installing a package would start.
                self.assertEqual([c[:4] for c in calls], [('ubus', 'call', 'service', 'delete'), ('ubus', 'call', 'service', 'set')])
                service = json.loads(calls[1][4])
                self.assertEqual(service['name'], 'dashplay-update')
                self.assertEqual(service['instances']['update']['command'][2:], ['install', '--root', str(root)])
                self.assertIn('正在升级', update.launch(root, True), 'a second click while it runs starts nothing')
                self.assertEqual(len(calls), 2)
                update.save(root / 'state/update-status.json', {'phase': 'done', 'at': 0})
                update.launch(root, False)
                self.assertEqual(calls[2][0], 'systemd-run')
                self.assertIn('--unit=dashplay-update', calls[2])


class ManifestTests(unittest.TestCase):
    def test_the_release_manifest_lists_every_download_with_its_hash(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('make_update_manifest', Path(__file__).resolve().parents[2]
                                                      / 'scripts/make-update-manifest.py')
        maker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(maker)
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp)
            (release / 'openwrt').mkdir()
            (release / 'dashplay').mkdir()
            for name in ('dashplay-2026.10.20-r1.apk', 'luci-app-dashplay-0.261020.1.apk',
                         'luci-i18n-dashplay-zh-cn-0.261020.1.apk', 'dashplay-jre-21.0.12.1-r1_aarch64_generic.apk',
                         'dashplay-jre-21.0.12.1-r1_x86_64.apk'):
                (release / 'openwrt' / name).write_bytes(name.encode())
            with tarfile.open(release / 'dashplay/dashplay-bundle-2026.10.20.tar', 'w') as tar:
                for name, data in (('./supervisor.py', b'program'), ('./state/receiver/x', b'owner'),
                                   ('./identity/offline-mfi/identity.pk8', b'secret')):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            value = maker.manifest('2026.10.20', '- 新功能', release, 'owner', True)
        self.assertEqual((value['format'], value['version'], value['online'], value['notes']), (1, '2026.10.20', True, '- 新功能'))
        self.assertEqual(value['openwrt']['packages']['dashplay']['sha256'], sha(b'dashplay-2026.10.20-r1.apk'))
        self.assertEqual(value['openwrt']['jre']['version'], '21.0.12.1-r1')
        self.assertEqual(sorted(value['openwrt']['jre']['files']), ['aarch64_generic', 'x86_64'])
        self.assertEqual(value['openwrt']['base'], 'https://github.com/owner/openwrt-dashplay/releases/download/v2026.10.20/')
        self.assertEqual(value['debian']['files'], {'supervisor.py': sha(b'program')}, "the owner's data is no program file")


if __name__ == '__main__':
    unittest.main()
