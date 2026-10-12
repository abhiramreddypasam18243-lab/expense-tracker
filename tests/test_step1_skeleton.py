"""Step 1 — walking skeleton (AC-38, AC-39, AC-40)."""

import re
import subprocess
import sys
from pathlib import Path

from tools.make_synthetic import generate_csv

ROOT = Path(__file__).resolve().parent.parent


def _port_mappings(compose_text: str) -> list[str]:
    """Return every published port entry in docker-compose.yml (short syntax)."""
    mappings = []
    in_ports = False
    for line in compose_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ports:"):
            in_ports = True
            continue
        if in_ports:
            if stripped.startswith("- "):
                mappings.append(stripped[2:].strip().strip("\"'"))
            elif stripped:
                in_ports = False
    return mappings


def test_ac_38_compose_binds_localhost_only():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    mappings = _port_mappings(text)
    assert mappings, "compose file must publish the UI port"
    for mapping in mappings:
        assert mapping.startswith("127.0.0.1:"), f"port not bound to localhost: {mapping}"
    assert "network_mode: host" not in text


def test_ac_39_synthetic_is_deterministic():
    assert generate_csv(seed=42, layout="amount") == generate_csv(seed=42, layout="amount")
    assert generate_csv(seed=42, layout="amount") != generate_csv(seed=43, layout="amount")


def test_ac_39_synthetic_covers_both_layouts():
    amount = generate_csv(seed=42, layout="amount")
    debit_credit = generate_csv(seed=42, layout="debit_credit")
    assert "Amount" in amount and "Withdrawal" not in amount
    assert "Withdrawal Amt" in debit_credit and "Deposit Amt" in debit_credit


def test_ac_39_synthetic_has_no_real_looking_account_numbers():
    for layout in ("amount", "debit_credit"):
        text = generate_csv(seed=42, layout=layout)
        assert not re.search(r"\d{9,}", text), "9+ digit runs look like real account/ref numbers"
        assert "SYNTHETIC" in text


def test_ac_39_synthetic_cli_writes_file(tmp_path):
    out = tmp_path / "s.csv"
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_synthetic.py"), "--seed", "42", "--out", str(out)],
        check=True,
    )
    assert out.read_text(encoding="utf-8") == generate_csv(seed=42, layout="amount")


def test_ac_40_dockerfile_non_root_with_healthcheck():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    users = re.findall(r"^USER\s+(\S+)", text, flags=re.MULTILINE)
    assert users and users[-1] not in ("root", "0"), "final USER must be non-root"
    assert re.search(r"^HEALTHCHECK\s", text, flags=re.MULTILINE)
    assert "/_stcore/health" in text
    assert "gatherUsageStats=false" in text
