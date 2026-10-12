"""Create EXPENSES_PASSPHRASE_HASH for the passphrase gate.

Usage:
    python tools/hash_passphrase.py

The passphrase is read without echo and never written anywhere; only the salted
PBKDF2 hash is printed. Pass it at runtime, e.g. in a local .env file (git-ignored).
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expenses.auth import hash_passphrase  # noqa: E402

first = getpass.getpass("Passphrase: ")
if len(first) < 10:
    sys.exit("Use at least 10 characters.")
if getpass.getpass("Repeat: ") != first:
    sys.exit("Passphrases did not match.")
print(f"EXPENSES_PASSPHRASE_HASH={hash_passphrase(first)}")
