"""Run the real shell hook against a fake provider, with no network or credentials."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


HOOK = Path(__file__).resolve().parents[2] / 'scripts/onecloud/acme/dnsapi/dns_desec.sh'
STUBS = '''
_info() { :; }
_debug() { :; }
_err() { :; }
_secure_debug2() { :; }
_saveaccountconf_mutable() { :; }
_lower_case() { tr 'A-Z' 'a-z'; }
_contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }
_get_root() { _domain=car.example.com; _sub_domain=_acme-challenge.box01; }
DEDYN_TOKEN=synthetic-test-token
'''


class DesecHookTests(unittest.TestCase):
    def test_add_atomically_replaces_stale_values_at_minimum_ttl(self):
        code = STUBS + '''
_desec_rest() {
  echo "$1" >> calls
  printf '%s' "$3" > payload
  _code=200; response='{"records":["\\"current-value\\""]}'
}
dns_desec_add _acme-challenge.box01.car.example.com current-value
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['sh', '-c', '. "$1"; ' + code, 'hook-test', str(HOOK)],
                                    cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((Path(tmp) / 'calls').read_text().splitlines(), ['PUT'])
            payload = json.loads((Path(tmp) / 'payload').read_text())
            self.assertEqual(payload[0]['records'], ['"current-value"'])
            self.assertEqual(payload[0]['ttl'], 900)

    def test_transport_failure_is_not_hidden_by_rate_limit_sleep(self):
        code = STUBS + '''
_post() { return 7; }
_sleep() { return 0; }
_tail_n() { tail -n "$1"; }
_normalizeJson() { cat; }
HTTP_HEADER=header
printf 'HTTP/1.1 200 OK\\r\\n' > "$HTTP_HEADER"
_desec_rest PUT https://provider.invalid/api '{}'
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['sh', '-c', '. "$1"; ' + code, 'hook-test', str(HOOK)],
                                    cwd=tmp, capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_cleanup_does_not_write_after_a_failed_read(self):
        code = STUBS + '''
_desec_rest() { echo "$1" >> calls; return 1; }
dns_desec_rm _acme-challenge.box01.car.example.com current-value
'''
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(['sh', '-c', '. "$1"; ' + code, 'hook-test', str(HOOK)], cwd=tmp)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((Path(tmp) / 'calls').read_text().splitlines(), ['GET'])
