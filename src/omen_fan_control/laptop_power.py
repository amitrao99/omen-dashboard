"""Whole-laptop power, refreshed every second, from two sources that cannot do it alone.

The battery's energy counter covers the whole laptop but moves in ~12 mWh steps every ~3 s, so it needs a window of
15 s or more (measured with scripts/probe-battery-counter.py). The CPU chip's RAPL counter is exact every second but
covers only the chip. So:

- battery line: a straight-line fit through the battery counter over the last `battery_window` seconds;
- live line: the CPU chip's last second plus "the rest of the laptop", which is the battery fit minus the CPU chip's
  average over the last `calibration_window` seconds. On the charger the battery shows charging, not consumption, so
  the live line keeps the last calibration and is marked as estimated.
"""

from collections import deque
from pathlib import Path

from .power_report import find_zones, number, read

SUPPLY = Path("/sys/class/power_supply")


def fit_watts(points):
    """Least-squares slope of (seconds, energy) points, in watts. Energy is µWh for the battery, µJ for RAPL."""
    if len(points) < 2:
        return None
    t0 = sum(t for t, _ in points) / len(points)
    e0 = sum(e for _, e in points) / len(points)
    den = sum((t - t0) ** 2 for t, _ in points)
    return sum((t - t0) * (e - e0) for t, e in points) / den if den else None


def package_zones():
    """The RAPL package zones; their subdomains are already included in them."""
    return [z for z in find_zones() if (read(z / "name") or "").startswith("package-")]


def read_sample(zones):
    """One reading: battery energy (µWh) and status, whether a charger is in, and each package counter (µJ)."""
    battery = next((p for p in sorted(SUPPLY.glob("*"))
                    if read(p / "type") == "Battery" and read(p / "present") != "0"), None)
    on_ac = any(read(p / "type") in ("Mains", "USB", "USB_C", "USB_PD") and read(p / "online") == "1"
                for p in SUPPLY.glob("*"))
    return {
        "energy": number(battery / "energy_now") if battery else None,
        "status": read(battery / "status") if battery else None,
        "on_ac": on_ac,
        "packages": [(number(z / "energy_uj"), number(z / "max_energy_range_uj")) for z in zones],
    }


class LaptopPowerEstimator:
    def __init__(self, battery_window: float = 15, calibration_window: float = 60):
        self.battery_window = battery_window
        self.calibration_window = max(calibration_window, battery_window)
        self._run = deque()  # (time, battery µWh, CPU chip µJ) while discharging, newest last
        self._cpu_total = 0.0  # CPU chip µJ since start, with counter wraps undone
        self._last_raw = None
        self._last = None  # (time, CPU chip µJ total) of the previous sample
        self.rest = None  # watts the laptop draws beyond the CPU chip, from the last calibration

    def _cpu(self, packages):
        """Advance the unwrapped CPU chip total. None when a counter is unreadable (RAPL needs root)."""
        raw = [value for value, _ in packages]
        if not raw or any(value is None for value in raw):
            self._last_raw = None
            return None
        if self._last_raw is not None:
            for (value, limit), before in zip(packages, self._last_raw):
                delta = value - before
                if delta < 0 and limit:
                    delta += limit
                self._cpu_total += max(delta, 0)
        self._last_raw = raw
        return self._cpu_total

    def update(self, now: float, sample: dict) -> dict:
        cpu_total = self._cpu(sample["packages"])
        cpu_w = None
        if cpu_total is not None and self._last is not None and self._last[1] is not None and now > self._last[0]:
            cpu_w = (cpu_total - self._last[1]) / (now - self._last[0]) / 1e6
        self._last = (now, cpu_total)

        discharging = sample["status"] == "Discharging" and not sample["on_ac"] and sample["energy"] is not None
        if not discharging:
            self._run.clear()  # charging or idle: the counter no longer measures what the laptop uses
        else:
            self._run.append((now, sample["energy"], cpu_total))
            while self._run and now - self._run[0][0] > self.calibration_window:
                self._run.popleft()

        battery_w = self._battery(now, self.battery_window)
        calibrating = False
        if discharging:
            span = now - self._run[0][0]
            whole = self._battery(now, span) if span >= self.battery_window - 0.5 else None
            cpu_points = [(t, c) for t, _, c in self._run if c is not None]
            cpu_avg = fit_watts(cpu_points) if len(cpu_points) == len(self._run) else None
            if whole is not None and cpu_avg is not None:
                self.rest = max(whole - cpu_avg / 1e6, 0.0)
                calibrating = True

        live_w = cpu_w + self.rest if cpu_w is not None and self.rest is not None else None
        return {
            "live_w": live_w,
            "battery_w": battery_w,
            "cpu_w": cpu_w,
            "estimated": live_w is not None and not calibrating,  # running on an old calibration
            "on_ac": sample["on_ac"],
            "has_battery": sample["status"] is not None,
        }

    def _battery(self, now, window):
        """Battery draw over the last `window` seconds, or None until the run covers it."""
        if not self._run or now - self._run[0][0] < window - 0.5:
            return None
        slope = fit_watts([(t, e) for t, e, _ in self._run if now - t <= window + 0.5])
        return -slope * 3600 / 1e6 if slope is not None else None
