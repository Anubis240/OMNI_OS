"""Which address the phone should use, and the dashboard's HTTPS certificate.

HTTPS is required because phone browsers only allow the microphone on
secure pages; the certificate is self-signed for this machine's LAN address,
so the phone shows a one-time warning the first time it connects.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path

from core.app_paths import get_data_dir

CERT_LIFETIME_DAYS = 800   # under Apple's 825-day ceiling for TLS server certificates


def cert_paths() -> tuple[Path, Path]:
    folder = get_data_dir() / "config" / "certs"
    return folder / "seraph.crt", folder / "seraph.key"


def is_cgnat(ip: str) -> bool:
    """100.64.0.0/10 — what Tailscale and some carriers hand out. A phone on
    the real Wi-Fi can't reach it (GEMZ4US 2026-09, VPN left running)."""
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _default_route_address() -> str | None:
    """The source address the OS would use to reach the internet. Nothing is
    sent: connecting a UDP socket only selects a route."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))    # TEST-NET-1, never actually contacted
            return probe.getsockname()[0]
        except OSError:
            return None


def _private_interface_address() -> str | None:
    """A private LAN address on an interface that's up, for when the default
    route runs through a VPN. Home-router ranges are preferred."""
    import psutil
    up = {name for name, stats in psutil.net_if_stats().items() if stats.isup}
    found = []
    for name, addrs in psutil.net_if_addrs().items():
        if name not in up or any(v in name.lower() for v in ("vethernet", "virtualbox", "vmware", "wsl", "docker")):
            continue
        for a in addrs:
            if a.family == socket.AF_INET:
                ip = ipaddress.ip_address(a.address)
                if ip.is_private and not ip.is_loopback and not ip.is_link_local and not is_cgnat(a.address):
                    found.append(a.address)
    found.sort(key=lambda a: (not a.startswith("192.168."), not a.startswith("10."), a))
    return found[0] if found else None


def lan_address() -> str:
    route = _default_route_address()
    if route and not route.startswith("127.") and not is_cgnat(route):
        return route
    return _private_interface_address() or route or "127.0.0.1"


def _certificate_fits(pem: bytes, ip: str) -> bool:
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(pem)
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        valid = cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)
        return valid and ip in [str(v) for v in names.get_values_for_type(x509.IPAddress)]
    except Exception:
        return False


def regenerate_certificate() -> bool:
    """Forget the current certificate; a fresh one is made at next start."""
    ok = True
    for path in cert_paths():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            ok = False
    return ok


def ensure_certificate(ip: str) -> tuple[Path, Path] | None:
    """(cert, key) valid for `ip`, creating or replacing them as needed —
    e.g. after the machine moved to a different network. None if the
    cryptography package is missing (the dashboard then serves plain HTTP)."""
    cert_file, key_file = cert_paths()
    if cert_file.exists() and key_file.exists() and _certificate_fits(cert_file.read_bytes(), ip):
        return cert_file, key_file
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        return None
    key = ec.generate_private_key(ec.SECP256R1())
    alt_names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        alt_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
    except ValueError:
        pass
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Omni-OS {ip}")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(hours=1))
            .not_valid_after(now + dt.timedelta(days=CERT_LIFETIME_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_file, key_file
