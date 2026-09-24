"""Machine readings for the status card, sampled on a background thread.

Started explicitly by the window (not at import), so importing the GUI
modules — e.g. from tests — never spawns a polling thread.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console flash from a windowed build
_PERIOD = 2.0


@dataclass(frozen=True)
class Reading:
    cpu: float = 0.0         # %
    memory: float = 0.0      # %
    network_mb_s: float = 0.0
    gpu: float | None = None  # % or None if unknown
    temperature: float | None = None  # °C or None if unknown


class Sampler:
    def __init__(self):
        self.latest = Reading()
        self._nvidia = shutil.which("nvidia-smi")
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            threading.Thread(target=self._run, name="sysinfo", daemon=True).start()

    def _gpu(self) -> float | None:
        if not self._nvidia:
            return None
        try:
            out = subprocess.run([self._nvidia, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=2, creationflags=_NO_WINDOW).stdout
            values = [float(v) for v in out.split() if v.replace(".", "", 1).isdigit()]
            return sum(values) / len(values) if values else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    @staticmethod
    def _temperature() -> float | None:
        reader = getattr(psutil, "sensors_temperatures", None)   # not available on Windows
        if reader is None:
            return None
        try:
            groups = reader()
        except Exception:
            return None
        for name in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
            if groups.get(name):
                return groups[name][0].current
        for sensors in groups.values():
            if sensors:
                return sensors[0].current
        return None

    def _run(self) -> None:
        net = psutil.net_io_counters()
        stamp = time.monotonic()
        psutil.cpu_percent(None)   # prime the counter
        while True:
            time.sleep(_PERIOD)
            try:
                now_net, now = psutil.net_io_counters(), time.monotonic()
                moved = (now_net.bytes_sent - net.bytes_sent) + (now_net.bytes_recv - net.bytes_recv)
                rate = moved / max(now - stamp, 1e-6) / 1_048_576
                net, stamp = now_net, now
                self.latest = Reading(psutil.cpu_percent(None), psutil.virtual_memory().percent,
                                      rate, self._gpu(), self._temperature())
            except Exception:
                continue


sampler = Sampler()
