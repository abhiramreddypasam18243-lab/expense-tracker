"""Step 10 — desktop launcher and internet demo entry point (AC-58, AC-59)."""

import struct
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "launcher"


def test_ac_58_launcher_scripts():
    start = (LAUNCHER / "start.ps1").read_text(encoding="utf-8")
    assert "_stcore/health" in start
    assert start.index("Test-Healthy") < start.index("docker compose up -d --build")  # health check first
    assert "--app=" in start and "http://127.0.0.1:8501" in start  # IPv4: WSL doesn't forward ::1
    assert "AddSeconds(" in start  # waits with a timeout
    assert "docker-compose.public.yml" not in start and "EXPENSES_PUBLIC" not in start

    stop = (LAUNCHER / "stop.ps1").read_text(encoding="utf-8")
    assert "docker compose stop" in stop and "down -v" not in stop  # never deletes data

    install = (LAUNCHER / "install-shortcuts.ps1").read_text(encoding="utf-8")
    assert "Desktop" in install and "Programs" in install and "icon.ico" in install
    assert "start.ps1" in install and "stop.ps1" in install


def test_ac_58_icon_is_valid_ico():
    data = (LAUNCHER / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count >= 3
    sizes = set()
    for i in range(count):
        width, height, _, _, _, _, size, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i : 22 + 16 * i])
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + size <= len(data)
        sizes.add(width or 256)
    assert {16, 32, 256} <= sizes


def test_ac_59_demo_entry_point_is_always_demo(tmp_path, monkeypatch):
    real_db = tmp_path / "real.db"
    monkeypatch.setenv("EXPENSES_DB", str(real_db))
    monkeypatch.setenv("EXPENSES_PUBLIC", "1")  # would normally refuse without a hash
    monkeypatch.delenv("EXPENSES_PASSPHRASE_HASH", raising=False)
    # setenv (not delenv) so pytest removes the EXPENSES_DEMO=1 that demo_app.py sets afterwards.
    monkeypatch.setenv("EXPENSES_DEMO", "0")
    at = AppTest.from_file(str(ROOT / "demo_app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert not at.error
    assert any("sample data" in i.value for i in at.info)
    assert [m for m in at.metric if m.label == "Spend"]
    assert not real_db.exists()
