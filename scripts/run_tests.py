"""Run every tests/test_*.py module in its own Python process.

One process for the whole suite let tests interfere: a background
system-monitor thread started by one module called subprocess.run while
test_release_bundle had it patched (Windows CI, 2026-09-28), and in the
public repo the same setup aborted the interpreter natively. A fresh process
per module keeps them apart, and a crash then names the module.

    python scripts/run_tests.py            # all modules
    python scripts/run_tests.py test_trader_engine test_voice_health
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def main(argv: list[str]) -> int:
    modules = argv[1:] or sorted(p.stem for p in TESTS.glob("test_*.py"))
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(TESTS), os.environ.get("PYTHONPATH")]))}
    failed = []
    started = time.monotonic()
    for module in modules:
        print(f"===== {module}", flush=True)
        code = subprocess.call([sys.executable, "-X", "faulthandler", "-m", "unittest", "-v", module],
                               cwd=ROOT, env=env)
        if code != 0:
            failed.append(f"{module} (exit {code})")
    print(f"\n{len(modules)} test modules in {time.monotonic() - started:.0f}s; "
          f"{len(failed)} failed{': ' + ', '.join(failed) if failed else ''}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
