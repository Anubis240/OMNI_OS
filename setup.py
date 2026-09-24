"""Developer setup for running Omni-OS from source: Python packages, then
Playwright's browser engines. (The installer build bundles both.)"""

import subprocess
import sys

for step, command in (
    ("Python packages", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]),
    ("browser engines for web automation", [sys.executable, "-m", "playwright", "install", "chromium", "firefox", "webkit"]),
):
    print(f"Installing {step}…")
    subprocess.run(command, check=True)

print("Ready. Start Omni-OS with:  python main.py")
