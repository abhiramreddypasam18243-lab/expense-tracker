"""Step 12 — packaging robustness for hosted deployments (AC-66)."""

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_ac_66_project_packages_win_over_installed_namesakes(tmp_path):
    # A hosting image may have its own installed packages called "ui" or "tools". Our folders must still win.
    fake = tmp_path / "site-packages"
    for name in ("ui", "tools", "expenses"):
        (fake / name).mkdir(parents=True)
        (fake / name / "__init__.py").write_text("IMPOSTOR = True\n", encoding="utf-8")
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})   # Streamlit puts the app folder first
        sys.path.append({str(fake)!r})      # installed packages come later
        from ui import common, demo           # noqa
        from tools.make_synthetic import generate_csv  # noqa
        from expenses import combine           # noqa
        import ui, tools
        assert not hasattr(ui, "IMPOSTOR") and not hasattr(tools, "IMPOSTOR")
        print("ok")
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_ac_66_packages_have_init_files():
    for name in ("expenses", "ui", "tools"):
        assert (ROOT / name / "__init__.py").exists(), f"{name}/ must be a regular package"
