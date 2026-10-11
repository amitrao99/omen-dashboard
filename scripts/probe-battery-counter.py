#!/usr/bin/env python3
"""Probe the battery energy counter and RAPL zones, to size the LAPTOP POWER graph.

Run on battery (charger unplugged), with the laptop idle:
    sudo python3 scripts/probe-battery-counter.py [seconds]
Root is only needed for the RAPL readings. The default run is 75 s.
"""
import statistics
import sys
import time
from pathlib import Path

SUPPLY = Path(sys.argv[2] if len(sys.argv) > 2 else "/sys/class/power_supply")
POWERCAP = Path("/sys/class/powercap")
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 75.0
PERIOD = 0.05  # 20 reads a second, so the counter's own update rate shows


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def fit_watts(points):
    """Least-squares slope of (seconds, µWh) points, as watts drawn."""
    ts, es = zip(*points)
    t0, e0 = statistics.fmean(ts), statistics.fmean(es)
    den = sum((t - t0) ** 2 for t in ts)
    return -sum((t - t0) * (e - e0) for t, e in points) / den * 3600 / 1e6 if den else None


print("== Power supplies")
battery = None
for p in sorted(SUPPLY.glob("*")):
    kind, online = read(p / "type"), read(p / "online")
    print(f"  {p.name}: type={kind}" + (f" online={online}" if online is not None else ""))
    if kind == "Battery" and read(p / "present") != "0" and battery is None:
        battery = p
if battery is None:
    sys.exit("No battery found.")

print(f"\n== {battery.name} attributes")
for f in sorted(battery.iterdir()):
    if f.is_file() and f.name != "uevent":
        value = read(f)
        if value is not None and len(value) < 60:
            print(f"  {f.name:28} {value}")

status = read(battery / "status")
key = next((k for k in ("energy_now", "charge_now") if read(battery / k) is not None), None)
for rate in ("power_now", "current_now"):
    if read(battery / rate) is not None:
        print(f"\nNOTE: {rate} exists ({read(battery / rate)}): an instant rate sensor is available.")
if status != "Discharging":
    print(f"\nWARNING: status is {status!r}. Unplug the charger for meaningful results.")
if key is None:
    sys.exit("No energy_now or charge_now counter.")
if key == "charge_now":
    print("NOTE: only charge_now (µAh) exists; converting with voltage_now.")


def energy():
    value = int(read(battery / key))
    if key == "charge_now":
        value = value * int(read(battery / "voltage_now") or 0) // 1_000_000
    return value


# RAPL baseline, taken now so its average covers the same span as the battery sample.
zones = {}
for z in sorted(POWERCAP.glob("intel-rapl*")):
    if not (z / "energy_uj").exists():
        continue  # the "intel-rapl" control folder itself has no counter
    name, raw = read(z / "name"), read(z / "energy_uj")
    zones[z] = (name or "?", int(raw) if raw and raw.isdigit() else None)

print(f"\n== Sampling {key} every {PERIOD * 1000:.0f} ms for {DURATION:.0f} s ...")
start = time.monotonic()
samples, changes = [], []
last = None
while (now := time.monotonic()) - start < DURATION:
    value = energy()
    samples.append((now - start, value))
    if value != last:
        changes.append((now - start, value))
        last = value
    time.sleep(PERIOD)
elapsed = time.monotonic() - start

steps = [abs(b[1] - a[1]) for a, b in zip(changes, changes[1:])]
gaps = [b[0] - a[0] for a, b in zip(changes, changes[1:])]
print(f"  distinct values: {len(changes)}   changes: {len(steps)}")
if not steps:
    sys.exit("  The counter never changed. Run longer, or check it is discharging.")
print(f"  step size (mWh):      min {min(steps) / 1000:.1f}  median {statistics.median(steps) / 1000:.1f}  "
      f"max {max(steps) / 1000:.1f}")
print(f"  time between changes: min {min(gaps):.2f} s  median {statistics.median(gaps):.2f} s  max {max(gaps):.2f} s")
total = fit_watts(samples)
print(f"  average draw over the run: {total:.2f} W")

print("\n== Window sizes (straight-line fit, re-evaluated every second)")
print("   window   spread (std dev)   range              worst-case step error")
step = statistics.median(steps)
for window in (5, 10, 15, 30, 60):
    if window > elapsed - 1:
        print(f"   {window:>3} s    (run longer than {window + 5} s to test)")
        continue
    results = []
    t = window
    while t <= elapsed:
        pts = [s for s in samples if t - window <= s[0] <= t]
        if len(pts) > 2 and (w := fit_watts(pts)) is not None:
            results.append(w)
        t += 1
    spread = statistics.pstdev(results) if len(results) > 1 else 0
    print(f"   {window:>3} s    ±{spread:5.2f} W           {min(results):5.2f}–{max(results):5.2f} W"
          f"      ±{step * 3600 / window / 1e6:.2f} W")

print("\n== RAPL zones (same span)")
for z, (name, before) in zones.items():
    after = read(z / "energy_uj")
    if before is None or not (after and after.isdigit()):
        print(f"  {z.name:18} {name:10} unreadable (run with sudo)")
        continue
    delta = int(after) - before
    if delta < 0:
        delta += int(read(z / "max_energy_range_uj") or 0)
    print(f"  {z.name:18} {name:10} {delta / elapsed / 1e6:6.2f} W")
