"""Runtime configuration and the passphrase gate (spec section 8.1).

Environment variables:
  EXPENSES_PUBLIC=1             the operator exposes the app beyond localhost -> passphrase required
  EXPENSES_PASSPHRASE_HASH=...  output of tools/hash_passphrase.py (never the passphrase itself)
  EXPENSES_DEMO=1               demo mode: synthetic data in a throwaway database
  EXPENSES_TLS_PROXY=1          an HTTPS reverse proxy is in front (else Streamlit's own TLS is required)

Hash format: pbkdf2_sha256:<iterations>:<salt hex>:<hash hex>. ':' is used instead of the
usual '$' because docker-compose treats '$' in env values as variable substitution.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-HMAC-SHA256
_HASH_FORMAT = re.compile(r"^pbkdf2_sha256:(\d+):([0-9a-f]{32,}):([0-9a-f]{64})$")


class ConfigError(RuntimeError):
    """Unsafe or invalid runtime configuration; the app must not serve pages."""


@dataclass(frozen=True)
class Config:
    public: bool = False
    passphrase_hash: str | None = None
    demo: bool = False


def hash_passphrase(passphrase: str, iterations: int = ITERATIONS, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{salt.hex()}:{digest.hex()}"


def verify_passphrase(passphrase: str, encoded: str) -> bool:
    """Recompute with the stored salt and iterations; constant-time comparison."""
    match = _HASH_FORMAT.match(encoded or "")
    if not match:
        return False
    iterations, salt_hex, expected_hex = match.groups()
    digest = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), expected_hex)


def load_config(env: Mapping[str, str] | None = None, tls_enabled: bool = False) -> Config:
    """Read and validate the runtime settings. `tls_enabled`: Streamlit itself serves HTTPS."""
    env = os.environ if env is None else env
    public = env.get("EXPENSES_PUBLIC", "").strip() == "1"
    demo = env.get("EXPENSES_DEMO", "").strip() == "1"
    passphrase_hash = env.get("EXPENSES_PASSPHRASE_HASH", "").strip() or None
    if public and passphrase_hash is None:
        raise ConfigError(
            "EXPENSES_PUBLIC=1 but EXPENSES_PASSPHRASE_HASH is not set. Refusing to serve: create one with "
            "`python tools/hash_passphrase.py` and pass it as an environment variable."
        )
    if public and not (tls_enabled or env.get("EXPENSES_TLS_PROXY", "").strip() == "1"):
        raise ConfigError(
            "EXPENSES_PUBLIC=1 needs HTTPS, or the passphrase would cross the network in plain text. Set "
            "STREAMLIT_SERVER_SSL_CERT_FILE and STREAMLIT_SERVER_SSL_KEY_FILE, or EXPENSES_TLS_PROXY=1 if an "
            "HTTPS reverse proxy is in front."
        )
    if passphrase_hash is not None and not _HASH_FORMAT.match(passphrase_hash):
        raise ConfigError("EXPENSES_PASSPHRASE_HASH is malformed; generate it with tools/hash_passphrase.py.")
    return Config(public=public, passphrase_hash=passphrase_hash, demo=demo)
