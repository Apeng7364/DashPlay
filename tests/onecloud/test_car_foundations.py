"""Regressions from the October 15 drive; no box or network is touched."""
import ipaddress
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import own_domain
import platform_debian
import setup_wizard


class CarFoundationsTests(unittest.TestCase):
    def test_conflict_stays_inside_tesla_reachable_space(self):
        usual = ipaddress.ip_interface('192.168.78.1/24')
        occupied = [ipaddress.ip_interface('100.96.0.200/24')]
        address, warning = setup_wizard.box_hotspot_address(usual, True, occupied)
        self.assertEqual(str(address), '100.97.0.1/24')
        self.assertTrue(warning)
        self.assertTrue(setup_wizard.tesla_reaches(address.ip))
        self.assertEqual(setup_wizard.box_hotspot_address(usual, False, occupied), (usual, None))
        occupied.append(ipaddress.ip_interface('100.97.0.2/24'))
        self.assertEqual(str(setup_wizard.box_hotspot_address(usual, True, occupied)[0]), '100.98.0.1/24')

    def test_ipv6_is_enabled_before_receiver_can_start(self):
        with patch('platform_debian.subprocess.run') as run:
            platform_debian.DebianPlatform().ensure_receiver_ipv6()
        self.assertEqual(run.call_args.args[0], ['sysctl', '-w', 'net.ipv6.conf.lo.disable_ipv6=0'])
        self.assertTrue(run.call_args.kwargs['check'])
        source = (Path(__file__).resolve().parents[2] / 'scripts/onecloud/supervisor.py').read_text()
        self.assertLess(source.index('platform.ensure_receiver_ipv6()'), source.index('start("receiver", receiver_argv'))

    def test_tesla_domain_never_publishes_unreachable_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state').mkdir()
            for address in ('192.168.78.1', '10.0.0.1', '172.16.0.1'):
                worker = own_domain.Maintainer(root, {'domain': 'example'}, address, threading.Event(), tesla=True)
                self.assertIsNone(worker.address)
                self.assertEqual(own_domain.read_status(root / 'state')['record'], 'blocked')
            self.assertEqual(own_domain.Maintainer(root, {'domain': 'example'}, '100.97.0.1',
                                                  threading.Event(), tesla=True).address, '100.97.0.1')
