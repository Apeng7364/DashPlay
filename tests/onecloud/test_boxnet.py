from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/onecloud"))
import boxnet


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command")
class LeafRenewalTests(unittest.TestCase):
    """The web certificate was reused for as long as its names stayed the same, even expired or signed by a CA
    that no longer exists (audit 2026-10-13, A4)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)

    def leaf(self):
        return self.state / "tls" / "leaf.crt"

    def test_same_names_and_a_valid_certificate_keep_it(self):
        self.assertTrue(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))
        self.assertFalse(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))

    def test_a_certificate_near_its_end_is_renewed(self):
        with patch.object(boxnet, "LEAF_DAYS", 1):   # one day left: inside the renewal margin
            self.assertTrue(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))
        self.assertTrue(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))
        out = subprocess.run(["openssl", "x509", "-checkend", str(300 * 86400), "-noout", "-in", str(self.leaf())])
        self.assertEqual(out.returncode, 0, "renewed for the full term")

    def test_a_new_ca_signs_a_new_certificate(self):
        self.assertTrue(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))
        for name in ("ca.key", "ca.crt"):
            (self.state / "tls" / name).unlink()
        self.assertTrue(boxnet.ensure_leaf(self.state, ["dashplay.lan"], ["192.168.77.1"]))
        out = subprocess.run(["openssl", "verify", "-CAfile", str(self.state / "tls" / "ca.crt"), str(self.leaf())],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)



@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command")
class OwnCertificateTests(unittest.TestCase):
    """A certificate of one's own (e.g. Let's Encrypt for a DuckDNS name) in state/tls/own is served while valid:
    a car browser that refuses the local CA still opens the page (F50, 2026-10-13)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"; self.state.mkdir()
        self.work = Path(tmp.name)

    def own(self, days):
        own = self.state / "tls" / "own"; own.mkdir(parents=True, exist_ok=True)
        run = lambda *a: subprocess.run(["openssl", *a], check=True, capture_output=True, cwd=self.work)
        run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key",
            "-out", "ca.crt", "-days", "30", "-subj", "/CN=Public CA")
        run("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", str(own / "key.pem"),
            "-out", "leaf.csr", "-subj", "/CN=car.example")
        (self.work / "ext").write_text("subjectAltName=DNS:car.example\n")
        run("x509", "-req", "-in", "leaf.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", str(days),
            "-extfile", "ext", "-out", "leaf.crt")
        (own / "fullchain.pem").write_text((self.work / "leaf.crt").read_text() + (self.work / "ca.crt").read_text())

    def served(self):
        out = subprocess.run(["openssl", "pkcs12", "-in", str(self.state / "receiver" / "tls-keystore.p12"), "-nokeys",
                              "-clcerts", "-passin", "pass:" + boxnet.KEYSTORE_PASSWORD], capture_output=True, text=True)
        if out.returncode:   # OpenSSL 3 reads the old PBE only with the legacy provider
            out = subprocess.run(["openssl", "pkcs12", "-legacy", "-in", str(self.state / "receiver" / "tls-keystore.p12"),
                                  "-nokeys", "-clcerts", "-passin", "pass:" + boxnet.KEYSTORE_PASSWORD], capture_output=True, text=True)
        return out.stdout

    def test_a_valid_certificate_of_ones_own_is_served(self):
        self.own(60)
        self.assertTrue(boxnet.ensure_leaf(self.state, ["car.example"], ["192.168.77.1"]))
        self.assertRegex(self.served(), r"subject=\s*CN\s*=\s*car\.example")
        self.assertFalse(boxnet.ensure_leaf(self.state, ["car.example"], ["192.168.77.1"]), "the same one is kept")
        self.own(60)   # renewed by acme.sh
        self.assertTrue(boxnet.ensure_leaf(self.state, ["car.example"], ["192.168.77.1"]))

    def test_an_expired_one_falls_back_to_the_local_certificate(self):
        self.own(0)
        self.assertTrue(boxnet.ensure_leaf(self.state, ["car.example"], ["192.168.77.1"]))
        self.assertRegex(self.served(), r"subject=\s*CN\s*=\s*DashPlay\n")


if __name__ == "__main__":
    unittest.main()
