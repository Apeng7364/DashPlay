"""TLS certificate helpers for the receiver (Armbian, openssl CLI).

The box keeps one local CA (state/tls/ca.*). The web server's leaf certificate is re-signed by that
CA whenever the set of names/addresses changes (e.g. the car-hotspot address), so a head unit that
trusts the CA once (the settings page offers ca.crt) never sees a warning again. The leaf is written as the PKCS12 keystore the
PlayPort server loads (alias and password "playport", fixed in its source).
"""
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile

KEYSTORE_ALIAS = "playport"
KEYSTORE_PASSWORD = "playport"   # constant in PlayPort's TlsCertificate.kt; protects nothing
LEAF_DAYS = 397                  # keep under browsers' 398-day limit


def _openssl(*args, cwd=None):
    subprocess.run(["openssl", *args], check=True, cwd=cwd, capture_output=True, timeout=120)


def ensure_ca(tls_dir: Path):
    tls_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key, crt = tls_dir / "ca.key", tls_dir / "ca.crt"
    if key.exists() and crt.exists():
        return crt
    _openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
             "-keyout", str(key), "-out", str(crt), "-days", "3650", "-subj", "/CN=DashPlay local CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    key.chmod(0o600)
    crt.chmod(0o644)
    return crt


def ensure_leaf(state: Path, names, addresses):
    """Re-sign the web certificate if its names/addresses changed; returns True when rewritten."""
    state = state.resolve()   # openssl runs in a scratch directory
    tls_dir = state / "tls"
    ca_crt = ensure_ca(tls_dir)
    names = sorted({n for n in names if n})
    addresses = sorted({str(ipaddress.ip_address(a)) for a in addresses if a})
    wanted = json.dumps({"dns": names, "ip": addresses})
    marker = tls_dir / "leaf.san.json"
    keystore = state / "receiver" / "tls-keystore.p12"
    if keystore.exists() and marker.exists() and marker.read_text() == wanted:
        return False
    san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{a}" for a in addresses])
    with tempfile.TemporaryDirectory(dir=tls_dir) as work:
        w = Path(work)
        (w / "ext.cnf").write_text(
            f"subjectAltName={san}\nextendedKeyUsage=serverAuth\n"
            "keyUsage=critical,digitalSignature\nbasicConstraints=CA:FALSE\n")
        _openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
                 "-keyout", "leaf.key", "-out", "leaf.csr", "-subj", "/CN=DashPlay", cwd=w)
        _openssl("x509", "-req", "-in", "leaf.csr", "-CA", str(ca_crt), "-CAkey", str(tls_dir / "ca.key"),
                 "-CAcreateserial", "-CAserial", str(tls_dir / "ca.srl"), "-days", str(LEAF_DAYS),
                 "-sha256", "-extfile", "ext.cnf", "-out", "leaf.crt", cwd=w)
        # One PBKDF2/MAC iteration: the password is public (see tls.security); 10000 cost ~2.7 s/start.
        _openssl("pkcs12", "-export", "-inkey", "leaf.key", "-in", "leaf.crt", "-certfile", str(ca_crt),
                 "-name", KEYSTORE_ALIAS, "-passout", f"pass:{KEYSTORE_PASSWORD}",
                 "-keypbe", "AES-256-CBC", "-certpbe", "AES-256-CBC", "-macalg", "sha256",
                 "-iter", "1", "-nomaciter", "-out", "tls-keystore.p12", cwd=w)
        keystore.parent.mkdir(parents=True, exist_ok=True)
        target = keystore.with_suffix(".p12.new")
        (w / "tls-keystore.p12").replace(target)
        target.chmod(0o600)
        target.replace(keystore)
    marker.write_text(wanted)
    return True
