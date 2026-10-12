"""Entry point for the public internet demo (spec 8.3).

Always demo mode with synthetic data, whatever the host's environment says: real data
can never be shown from a hosted copy. Deploy this file, not app.py.
"""

import os
import runpy
from pathlib import Path

os.environ["EXPENSES_DEMO"] = "1"
for name in ("EXPENSES_PUBLIC", "EXPENSES_PASSPHRASE_HASH", "EXPENSES_DB"):
    os.environ.pop(name, None)

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
