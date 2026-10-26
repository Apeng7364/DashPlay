import http.server
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/onecloud'))
import own_domain
import supervisor
import upgrade_checks
from test_own_domain import make_certificate


class DomainUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'; self.state.mkdir()
        self.program = self.root / 'program'
        (self.program / 'acme/dnsapi').mkdir(parents=True)
        (self.program / 'acme/dnsapi/dns_desec.sh').touch()
        self.name = 'box01.car.example.com'
        self.config = {'provider': 'desec', 'domain': self.name, 'token': 'synthetic-token-for-test-00000'}
        (self.state / 'ddns.json').write_text(json.dumps(self.config))
        (self.state / 'setup.json').write_text(json.dumps({'mode': 'box_hotspot', 'done': True}))
        (self.state / 'options.json').write_text(json.dumps({'tesla': True}))
        (self.root / 'service.json').write_text(json.dumps({'address': '192.168.78.1/24'}))
        mocked = patch.object(upgrade_checks, '__file__', str(self.program / 'upgrade_checks.py'))
        mocked.start(); self.addCleanup(mocked.stop)

    def test_candidate_must_read_existing_provider_and_include_hook(self):
        before = (self.state / 'ddns.json').read_bytes()
        self.assertEqual(upgrade_checks.check(self.root), {'https': False})
        self.assertEqual((self.state / 'ddns.json').read_bytes(), before)
        with patch.object(own_domain, 'load', return_value=None), self.assertRaises(ValueError):
            upgrade_checks.check(self.root)
        (self.program / 'acme/dnsapi/dns_desec.sh').unlink()
        with self.assertRaises(ValueError):
            upgrade_checks.check(self.root)

    def test_waiting_for_identity_does_not_require_an_unstarted_receiver(self):
        with patch.object(own_domain, 'certificate_for', return_value=True):
            self.assertEqual(upgrade_checks.check(self.root), {'https': False})
            identity = self.root / 'identity/offline-mfi'; identity.mkdir(parents=True)
            for name in ('identity.pk8', 'certificate.p7b'):
                (identity / name).write_bytes(b'synthetic fixture')
            self.assertEqual(upgrade_checks.check(self.root), {'https': True})

    def test_debian_hotspot_answers_desec_even_without_tesla_or_optional_dns(self):
        (self.state / 'options.json').write_text('{}')
        self.assertEqual(supervisor.hotspot_names(self.state, self.name, 'box_hotspot'), [self.name])
        self.assertEqual(supervisor.hotspot_names(self.state, self.name, 'mifi'), [])

    def test_domain_health_rejects_wrong_dns_and_lost_certificate(self):
        with patch.object(upgrade_checks, 'dns_answers', return_value={'192.0.2.5'}):
            with self.assertRaises(ValueError):
                upgrade_checks.check(self.root, health=True)
        with patch.object(upgrade_checks, 'dns_answers', return_value={'100.96.0.1'}):
            self.assertEqual(upgrade_checks.check(self.root, health=True), {'https': False})
            with self.assertRaises(ValueError):
                upgrade_checks.check(self.root, health=True, require_https=True)

    def test_dns_health_uses_active_address_when_tesla_subnet_changed(self):
        own_domain.write_status(self.state, name=self.name, record='local', address='100.97.0.1')
        with patch.object(upgrade_checks, 'dns_answers', return_value={'100.97.0.1'}) as dns:
            upgrade_checks.check(self.root, health=True)
        dns.assert_called_once_with(self.name, '100.97.0.1')

    def test_https_checks_real_server_hostname_certificate_and_http_response(self):
        certificate = self.state / 'tls/own'; certificate.mkdir(parents=True)
        make_certificate(certificate, self.name)
        class Page(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b'viewer')
            def log_message(self, *args):
                pass
        # The real receiver binds only its Wi-Fi address, not loopback. Discover the local
        # interface without sending a packet, then bind the fixture only on that interface.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(('224.0.0.251', 5353))
            address = route.getsockname()[0]
        server = http.server.ThreadingHTTPServer((address, 0), Page)
        own_domain.write_status(self.state, name=self.name, address=address)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate / 'fullchain.pem', certificate / 'key.pem')
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(upgrade_checks.viewer_port, 'resolve', return_value=server.server_port):
                upgrade_checks.https_ready(self.state, self.name)
                with self.assertRaises(ssl.SSLCertVerificationError):
                    upgrade_checks.https_ready(self.state, 'other.car.example.com')
        finally:
            server.shutdown(); server.server_close(); thread.join(5)


class DnsProbeTests(unittest.TestCase):
    def test_reads_a_compressed_real_udp_response(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.bind(('127.0.0.1', 0)); server.settimeout(5)
            def respond():
                query, client = server.recvfrom(4096)
                header = query[:2] + struct.pack('!5H', 0x8180, 1, 1, 0, 0)
                answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 0, 4) + socket.inet_aton('100.96.0.1')
                server.sendto(header + query[12:] + answer, client)
            thread = threading.Thread(target=respond); thread.start()
            found = upgrade_checks.dns_answers('box01.car.example.com', '127.0.0.1', server.getsockname()[1])
            thread.join(5)
        self.assertEqual(found, {'100.96.0.1'})
