"""TLS certificate helpers for the receiver (Armbian, openssl CLI).

The box keeps one local CA (state/tls/ca.*). The web server's leaf certificate is re-signed by that
CA whenever the set of names/addresses changes (e.g. the car-hotspot address), so a head unit that
trusts the CA once (the settings page offers ca.crt) never sees a warning again. The leaf is written as the PKCS12 keystore the
PlayPort server loads (alias and password "playport", fixed in its source).
"""
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import tempfile

KEYSTORE_ALIAS = "playport"
KEYSTORE_PASSWORD = "playport"   # constant in PlayPort's TlsCertificate.kt; protects nothing
LEAF_DAYS = 397                  # keep under browsers' 398-day limit
RENEW_DAYS = 30                  # re-sign this long before it runs out (by the box clock)


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


def _leaf_current(leaf: Path, days=RENEW_DAYS) -> bool:
    """The certificate stays valid for `days` more (a box without a clock may run behind; it renews late)."""
    if not leaf.exists():
        return False
    out = subprocess.run(["openssl", "x509", "-checkend", str(days * 86400), "-noout", "-in", str(leaf)],
                         capture_output=True, timeout=30)
    return out.returncode == 0


def _keystore(state: Path, key: Path, chain: Path, extra=()):
    """The PKCS12 keystore the PlayPort server loads, written atomically."""
    keystore = state / "receiver" / "tls-keystore.p12"
    with tempfile.TemporaryDirectory(dir=state / "tls") as work:
        # One PBKDF2/MAC iteration: the password is public (see tls.security); 10000 cost ~2.7 s/start.
        _openssl("pkcs12", "-export", "-inkey", str(key), "-in", str(chain), *extra,
                 "-name", KEYSTORE_ALIAS, "-passout", f"pass:{KEYSTORE_PASSWORD}",
                 "-keypbe", "AES-256-CBC", "-certpbe", "AES-256-CBC", "-macalg", "sha256",
                 "-iter", "1", "-nomaciter", "-out", "tls-keystore.p12", cwd=Path(work))
        keystore.parent.mkdir(parents=True, exist_ok=True)
        target = keystore.with_suffix(".p12.new")
        (Path(work) / "tls-keystore.p12").replace(target)
        target.chmod(0o600)
        target.replace(keystore)


def _own_certificate(state: Path):
    """state/tls/own/{fullchain,key}.pem: a certificate of one's own (e.g. Let's Encrypt for a DuckDNS name,
    renewed by acme.sh), served while it is valid for one more day; a car browser that refuses the local
    CA opens the page with it (F50, 2026-10-13). Otherwise the local CA's certificate."""
    own = state / "tls" / "own"
    chain, key = own / "fullchain.pem", own / "key.pem"
    return (chain, key) if key.exists() and _leaf_current(chain, days=1) else None


def ensure_leaf(state: Path, names, addresses):
    """Re-sign the web certificate if its names/addresses or the CA changed or it runs out within RENEW_DAYS
    (it used to be kept for good: audit 2026-10-13, A4); returns True when rewritten."""
    state = state.resolve()   # openssl runs in a scratch directory
    tls_dir = state / "tls"
    ca_crt = ensure_ca(tls_dir)
    marker = tls_dir / "leaf.san.json"
    keystore = state / "receiver" / "tls-keystore.p12"
    own = _own_certificate(state)
    if own:
        wanted = json.dumps({"own": hashlib.sha256(own[0].read_bytes()).hexdigest()[:16]})
        if keystore.exists() and marker.exists() and marker.read_text() == wanted:
            return False
        _keystore(state, own[1], own[0])
        marker.write_text(wanted)
        return True
    names = sorted({n for n in names if n})
    addresses = sorted({str(ipaddress.ip_address(a)) for a in addresses if a})
    ca = hashlib.sha256(ca_crt.read_bytes()).hexdigest()[:16]
    wanted = json.dumps({"dns": names, "ip": addresses, "ca": ca})
    leaf = tls_dir / "leaf.crt"
    if keystore.exists() and marker.exists() and marker.read_text() == wanted and _leaf_current(leaf):
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
        _keystore(state, w / "leaf.key", w / "leaf.crt", ("-certfile", str(ca_crt)))
        (w / "leaf.crt").replace(leaf)   # public; kept to check its end date
    marker.write_text(wanted)
    return True
