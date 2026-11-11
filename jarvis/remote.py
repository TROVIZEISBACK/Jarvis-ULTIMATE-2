"""Remote access: use JARVIS from a phone, tablet or Windows PC on the same Wi-Fi.

The device opens https://<this computer>:8766 in its browser. The brain and every tool still
run on this computer; the device is a full interface for it, with its own microphone and
speaker.

Security, because a paired device can drive this computer:
- Off until switched on in the JARVIS window; the setting is remembered.
- HTTPS only, with a certificate made on this computer (browsers need HTTPS for the mic).
- A device must pair first with a 6-digit code shown in the JARVIS window. Codes last
  2 minutes and allow 5 tries. Pairing gives the device a random 256-bit key, kept in a
  Secure, HttpOnly, SameSite=Strict cookie; only a hash of it is stored here.
- Every request needs that key; the WebSocket also checks the page's origin.
- The tool bridge Claude Code uses (/mcp) is never served on this port.
- Devices can be removed one by one in the JARVIS window.
"""
import hashlib
import ipaddress
import json
import re
import secrets
import socket
import ssl
import subprocess
import time

from . import config, plat

DIR = config.DATA_DIR / "remote"
COOKIE = "jarvis_device"
CODE_TTL = 120
CODE_TRIES = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def lan_ip() -> str | None:
    """This computer's address on the local network (no packets are sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def local_hostname() -> str | None:
    """The computer's .local name (Bonjour / mDNS), e.g. my-pc.local."""
    try:
        if plat.MAC:
            name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=3).stdout.strip()
        else:
            name = socket.gethostname().split(".")[0]
        return f"{name}.local" if name and re.fullmatch(r"[A-Za-z0-9-]+", name) else None
    except Exception:
        return None


def _make_certificate(names: list[str], cert_path, key_path) -> None:
    """A self-signed certificate for these names (the `cryptography` package works the same on
    every OS; no openssl command needed)."""
    import datetime
    import ipaddress as ipa
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "JARVIS on this computer")])
    alt = [x509.IPAddress(ipa.ip_address(n)) if _is_ip(n) else x509.DNSName(n) for n in names]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class RemoteAccess:
    def __init__(self):
        DIR.mkdir(parents=True, exist_ok=True)
        self.state_path, self.devices_path = DIR / "state.json", DIR / "devices.json"
        self.cert, self.key, self.san_path = DIR / "cert.pem", DIR / "key.pem", DIR / "san.json"
        self.enabled = self._read(self.state_path, {}).get("enabled", False)
        self.devices: dict[str, dict] = self._read(self.devices_path, {})  # token hash -> device
        self.code: str | None = None
        self.code_expires = 0.0
        self.code_tries = 0

    @staticmethod
    def _read(path, default):
        try:
            return json.loads(path.read_text())
        except Exception:
            return default

    def _save_devices(self):
        tmp = self.devices_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.devices, indent=1))
        tmp.replace(self.devices_path)
        try:
            self.devices_path.chmod(0o600)
        except OSError:
            pass

    def set_enabled(self, on: bool):
        self.enabled = on
        self.state_path.write_text(json.dumps({"enabled": on}))
        if not on:
            self.code = None

    # --- addresses & certificate ----------------------------------------------
    def addresses(self) -> list[str]:
        host, ip = local_hostname(), lan_ip()
        return [f"https://{h}:{config.REMOTE_PORT}" for h in (ip, host) if h]

    def ssl_context(self) -> ssl.SSLContext:
        """Self-signed certificate for this computer's current names. Remade if the address changes,
        and kept within the 825-day limit iPhones and iPads enforce."""
        names = sorted({n for n in (lan_ip(), local_hostname(), "localhost", "127.0.0.1") if n})
        stale = self._read(self.san_path, {}).get("names") != names or not self.cert.exists() or \
            time.time() - self.cert.stat().st_mtime > 700 * 86400
        if stale:
            _make_certificate(names, self.cert, self.key)
            try:
                self.key.chmod(0o600)
            except OSError:
                pass
            self.san_path.write_text(json.dumps({"names": names}))
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.cert, self.key)
        return ctx

    # --- pairing ----------------------------------------------------------------
    def new_code(self) -> str:
        self.code = f"{secrets.randbelow(10 ** 6):06d}"
        self.code_expires = time.time() + CODE_TTL
        self.code_tries = 0
        return self.code

    def pair(self, code: str, name: str) -> str | None:
        """Exchange a valid pairing code for a device key. Codes are single-use."""
        if not self.enabled or not self.code or time.time() > self.code_expires:
            return None
        self.code_tries += 1
        ok = secrets.compare_digest(str(code).strip(), self.code)
        if ok or self.code_tries >= CODE_TRIES:
            self.code = None  # used, or too many wrong guesses
        if not ok:
            return None
        token = secrets.token_urlsafe(32)
        self.devices[_hash(token)] = {
            "id": secrets.token_hex(4), "name": (name or "Device").strip()[:40],
            "paired": time.time(), "seen": time.time(),
        }
        self._save_devices()
        return token

    def device(self, token: str | None) -> dict | None:
        if not (self.enabled and token):
            return None
        d = self.devices.get(_hash(token))
        if d and time.time() - d.get("seen", 0) > 3600:
            d["seen"] = time.time()
            self._save_devices()
        return d

    def revoke(self, device_id: str) -> bool:
        for h, d in list(self.devices.items()):
            if d["id"] == device_id:
                del self.devices[h]
                self._save_devices()
                return True
        return False

    def describe(self) -> dict:
        code_live = self.code and time.time() < self.code_expires
        return {
            "enabled": self.enabled, "addresses": self.addresses() if self.enabled else [],
            "code": self.code if code_live else None,
            "code_left": int(self.code_expires - time.time()) if code_live else 0,
            "devices": [{"id": d["id"], "name": d["name"], "paired": d["paired"], "seen": d["seen"]}
                        for d in sorted(self.devices.values(), key=lambda d: d["paired"])],
        }


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
