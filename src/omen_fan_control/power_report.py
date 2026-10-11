"""Read-only Linux power and activity report; Python 3 standard library only.

Sources: https://docs.kernel.org/power/powercap/powercap.html
         https://docs.kernel.org/power/power_supply_class.html
         https://docs.nvidia.com/deploy/nvidia-smi/index.html
RAPL watts are energy-counter deltas divided by elapsed time. Overlapping
domains are never added together. Process CPU usage is not a watts estimate.
"""

import argparse
import csv
from datetime import datetime
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(path):
    try:
        with open(path, "rb") as handle:  # Binary reads skip pathlib and text-decoding overhead.
            return handle.read().decode("utf-8", "replace").strip()
    except OSError:
        return None


def number(path):
    try:
        value = float(read(path))
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def clean(value):
    return "".join(c if c.isprintable() else " " for c in str(value))


def watts(value):
    return "unavailable" if value is None else f"{value:.2f} W"


def section(title):
    print(f"\n{title}\n{'-' * 100}")


def row(label, value, detail=""):
    print(f"{clean(label):<30} {clean(value):>17}  {clean(detail)}")


def energy_watts(before, after, elapsed, limit=None):
    if before is None or after is None or elapsed <= 0:
        return None
    delta = after - before
    if delta < 0:
        if limit is None or limit <= 0:
            return None
        delta += limit  # RAPL counters wrap at max_energy_range_uj.
    return delta / elapsed / 1_000_000 if delta >= 0 else None


def find_zones():
    root = Path("/sys/devices/virtual/powercap")
    paths = list(root.rglob("energy_uj")) if root.exists() else []
    if not paths:
        paths = list(Path("/sys/class/powercap").glob("*/energy_uj"))
    return sorted({p.parent.resolve() for p in paths})


def process_snapshot():
    processes = {}
    page_size = os.sysconf("SC_PAGE_SIZE")
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        text = read(f"/proc/{entry.name}/stat")
        if not text:
            continue
        try:
            end = text.rindex(")")
            fields = text[end + 2:].split()
            processes[int(entry.name)] = (
                text[text.index("(") + 1:end],
                int(fields[11]) + int(fields[12]),
                int(fields[19]),  # Start time protects against PID reuse.
                max(0, int(fields[21])) * page_size,
            )
        except (ValueError, IndexError):
            continue
    return processes


def network_snapshot():
    result = {}
    for line in (read("/proc/net/dev") or "").splitlines():
        if ":" in line:
            name, values = line.split(":", 1)
            values = values.split()
            if len(values) >= 9 and name.strip() != "lo":
                result[name.strip()] = (int(values[0]), int(values[8]))
    return result


def disk_snapshot():
    result = {}
    for device in Path("/sys/block").glob("*"):
        # Whole physical devices only; omit loopbacks and stacked volumes.
        if device.name.startswith(("loop", "ram", "dm-", "md", "sr", "zram")):
            continue
        values = (read(device / "stat") or "").split()
        if len(values) >= 7:
            result[device.name] = (int(values[2]) * 512, int(values[6]) * 512)
    return result


def battery_snapshot():
    result = {}
    for p in Path("/sys/class/power_supply").glob("*"):
        if read(p / "type") == "Battery" and read(p / "present") != "0":
            result[p.name] = {
                "status": read(p / "status"),
                **{k: number(p / k) for k in (
                    "power_now", "current_now", "voltage_now", "energy_now",
                    "energy_full", "energy_full_design", "capacity",
                )},
            }
    return result


def snapshot(zones):
    cpu = (read("/proc/stat") or "").splitlines()
    cpu = [int(x) for x in cpu[0].split()[1:9]] if cpu else []
    return {
        "energy": {z: (number(z / "energy_uj"), time.monotonic()) for z in zones},
        "cpu": cpu, "processes": process_snapshot(),
        "network": network_snapshot(), "disk": disk_snapshot(),
        "batteries": battery_snapshot(), "time": time.monotonic(),
    }


def battery_power(old, new, elapsed):
    if new["power_now"] is not None:
        return abs(new["power_now"]) / 1e6, "power_now sensor"
    if new["current_now"] is not None and new["voltage_now"] is not None:
        return abs(new["current_now"] * new["voltage_now"]) / 1e12, "current x voltage"
    # Battery firmware updates energy in coarse steps. Short samples can produce
    # enormous apparent rates, so only estimate across at least 30 seconds.
    if elapsed >= 30 and old and old["status"] == new["status"] and new["status"] in ("Charging", "Discharging"):
        if old["energy_now"] is not None and new["energy_now"] is not None:
            delta = new["energy_now"] - old["energy_now"]
            direction = 1 if new["status"] == "Charging" else -1
            if delta * direction > 0:
                return abs(delta) * 3600 / elapsed / 1e6, "estimate from coarse battery-energy updates"
    return None, "no rate sensor; energy estimate needs >=30s and a counter change"


def gpu_samples(skip):
    """Query nvidia-smi once. Each entry has label/value/detail text plus numbers for the GUI."""

    def failed(value, detail):
        return [{"label": "NVIDIA GPU", "value": value, "detail": clean(detail),
                 "power": None, "temp": None}]

    if skip:
        return failed("not queried", "--no-gpu selected")
    command = shutil.which("nvidia-smi")
    if not command:
        return failed("unavailable", "nvidia-smi is not installed")
    try:
        result = subprocess.run(
            [command, "--query-gpu=name,power.draw,power.limit,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode:
            message = (result.stderr or result.stdout).strip().splitlines()
            return failed("unavailable", message[0] if message else "driver query failed")
        gpus = []
        for values in csv.reader(result.stdout.splitlines()):
            if len(values) != 5:
                continue
            name, draw, limit, usage, temp = [v.strip() for v in values]
            try:
                power = float(draw)
                power = power if math.isfinite(power) else None
            except ValueError:
                power = None
            gpus.append({
                "label": clean(name),
                "value": watts(power),
                "detail": clean(f"load {usage}%; temp {temp} C; power limit {limit} W"),
                "power": power,
                "temp": int(temp) if temp.isdigit() else None,
            })
        return gpus or failed("unavailable", "no GPU readings returned")
    except (OSError, subprocess.TimeoutExpired) as error:
        return failed("unavailable", type(error).__name__)


def collect_report(before, after, zones, args, gpu=None, battery_before=None):
    data = {"sections": [], "processes": [], "notes": [], "metrics": {},
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    metrics = data["metrics"]

    def section(title):
        data["sections"].append((title, []))

    def row(label, value, detail=""):
        data["sections"][-1][1].append((clean(label), clean(value), clean(detail)))

    elapsed = after["time"] - before["time"]
    data["elapsed"] = elapsed
    section("SYSTEM / BATTERY")
    supplies = [p for p in Path("/sys/class/power_supply").glob("*")
                if read(p / "type") in ("Mains", "USB", "USB_C", "USB_PD")
                and read(p / "online") == "1"]
    metrics["external_power"] = bool(supplies)
    row("External power", "connected" if supplies else "not detected", ", ".join(p.name for p in supplies))
    row("Total at wall socket", "unavailable", "requires an external power meter")
    discharging = []
    battery_baseline = battery_before or before
    battery_elapsed = after["time"] - battery_baseline["time"]
    for name, battery in after["batteries"].items():
        power, source = battery_power(battery_baseline["batteries"].get(name), battery, battery_elapsed)
        status = battery["status"] or "Unknown"
        capacity = battery["capacity"]
        metrics.setdefault("battery_pct", capacity)
        row(name, f"{capacity:g}%" if capacity is not None else "unknown", status)
        label = "Battery discharge" if status == "Discharging" else "Battery charge flow"
        row("  " + label, watts(power), source)
        if status == "Discharging":
            discharging.append(power)
        if battery["energy_now"] is not None:
            row("  Stored energy", f"{battery['energy_now'] / 1e6:.2f} Wh")
        full, design = battery["energy_full"], battery["energy_full_design"]
        if full is not None and design and design > 0:
            row("  Battery health", f"{100 * full / design:.1f}%", f"{full / 1e6:.1f} / {design / 1e6:.1f} Wh")
    if discharging and all(p is not None for p in discharging) and not supplies:
        metrics["battery_draw_w"] = sum(discharging)
        row("Total from battery", watts(sum(discharging)), "battery-side system draw; includes conversion losses")
    else:
        row("Whole-laptop draw", "unavailable", "charging flow is not total laptop consumption")

    section("COMPONENT POWER (overlapping readings; do not add these rows)")
    if not zones:
        row("CPU energy counters", "unavailable", "no RAPL zones exposed")
    labels = {"core": "CPU cores", "uncore": "Uncore domain", "dram": "RAM domain",
              "psys": "Platform (RAPL psys)"}
    packages = []
    for zone in zones:
        name = read(zone / "name") or zone.name
        label = "CPU " + name if name.startswith("package-") else labels.get(name, name)
        start, t0 = before["energy"][zone]
        end, t1 = after["energy"][zone]
        power = energy_watts(start, end, t1 - t0, number(zone / "max_energy_range_uj"))
        if name.startswith("package-") and power is not None:
            packages.append(power)
        if start is None or end is None:
            note = "run with sudo for protected energy counters" if os.geteuid() else "counter unreadable"
        elif name == "psys":
            note = "firmware-defined platform scope; overlaps CPU; not wall watts"
        elif name.startswith("package-"):
            note = "includes its core/uncore subdomains"
        else:
            note = "RAPL subdomain; may already be included above"
        row(label, watts(power), note)
    if packages:
        metrics["cpu_package_w"] = sum(packages)
    samples = gpu if gpu is not None else gpu_samples(args.no_gpu)
    for sample in samples:
        row(sample["label"], sample["value"], sample["detail"])
    metrics["gpu_temp"] = next((s["temp"] for s in samples if s["temp"] is not None), None)
    metrics["gpu_power"] = next((s["power"] for s in samples if s["power"] is not None), None)
    for sensor in Path("/sys/class/hwmon").glob("hwmon*"):
        name = read(sensor / "name") or sensor.name
        for path in sorted(sensor.glob("power*_input")):
            value = number(path)
            label = read(path.with_name(path.name.replace("_input", "_label"))) or path.stem
            row(f"{name}: {label}", watts(value / 1e6 if value is not None else None), "hwmon power sensor; may overlap other rows")
    data["notes"].append("SSD, display, Wi-Fi, fans and per-app watts: no complete per-part power accounting.")

    section("ACTIVITY / COOLING (usage figures, not watts)")
    if len(before["cpu"]) >= 5 and len(after["cpu"]) >= 5:
        deltas = [b - a for a, b in zip(before["cpu"], after["cpu"])]
        total = sum(deltas)
        if total > 0:
            busy = 100 * (total - deltas[3] - deltas[4]) / total
            metrics["cpu_busy"] = busy
            row("CPU busy", f"{busy:.1f}%", "across all logical CPUs")
    memory = {}
    for line in (read("/proc/meminfo") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            memory[parts[0].rstrip(":")] = int(parts[1])
    if memory.get("MemTotal") and "MemAvailable" in memory:
        used = memory["MemTotal"] - memory["MemAvailable"]
        metrics["ram_used_gib"] = used / 1048576
        metrics["ram_total_gib"] = memory["MemTotal"] / 1048576
        row("RAM used / total", f"{used / 1048576:.1f}/{memory['MemTotal'] / 1048576:.1f} GiB")
    for category, verbs in (("network", ("RX", "TX")), ("disk", ("read", "write"))):
        for name, values in after[category].items():
            old = before[category].get(name)
            if old:
                rates = [max(0, b - a) / elapsed / 1024 for a, b in zip(old, values)]
                row(name, f"{verbs[0]} {rates[0]:.1f} KiB/s", f"{verbs[1]} {rates[1]:.1f} KiB/s")
    for display in Path("/sys/class/backlight").glob("*"):
        level, maximum = number(display / "brightness"), number(display / "max_brightness")
        if level is not None and maximum and maximum > 0:
            row("Display brightness", f"{100 * level / maximum:.0f}%", display.name)
    for sensor in Path("/sys/class/hwmon").glob("hwmon*"):
        name = read(sensor / "name") or sensor.name
        for kind, scale, unit in (("temp", 1000, "C"), ("fan", 1, "RPM")):
            for path in sorted(sensor.glob(f"{kind}*_input")):
                value = number(path)
                if value is None:
                    continue
                label = read(path.with_name(path.name.replace("_input", "_label"))) or path.name.replace("_input", "")
                row(f"{name}: {label}", f"{value / scale:.1f} {unit}")

    processes = []
    ticks = os.sysconf("SC_CLK_TCK")
    for pid, (name, work, started, rss) in after["processes"].items():
        old = before["processes"].get(pid)
        if old and old[2] == started:
            usage = max(0, work - old[1]) / ticks / elapsed * 100
            processes.append((usage, rss, pid, name))
    for usage, rss, pid, name in sorted(processes, reverse=True)[:args.top]:
        data["processes"].append((str(pid), clean(name), f"{usage:.1f}", f"{rss / 1048576:.1f}"))
    data["notes"].append("Process CPU: 100% = one logical CPU; multithreaded apps can exceed 100%.")
    if not args.no_gpu:
        data["notes"].append("Querying NVIDIA telemetry can wake a sleeping GPU; use --no-gpu to skip it.")
    return data


def report(before, after, zones, args):
    data = collect_report(before, after, zones, args)
    print(f"OMEN POWER REPORT   {data['timestamp']}   sample {data['elapsed']:.1f}s")
    for title, rows in data["sections"]:
        section(title)
        for label, value, detail in rows:
            row(label, value, detail)
    section("BUSIEST PROCESSES (CPU activity; watts cannot be attributed from this)")
    print(f"{'PID':>7}  {'PROCESS':<30} {'CPU %':>9} {'RSS MiB':>12}")
    for pid, name, usage, rss in data["processes"]:
        print(f"{pid:>7}  {name[:30]:<30} {usage:>9} {rss:>12}")
    for note in data["notes"]:
        print("  " + note)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=float, default=5,
                        help="sample seconds, 0.5-60; >=30 allows coarse battery estimates (default: 5)")
    parser.add_argument("--watch", action="store_true", help="refresh continuously; Ctrl+C stops")
    parser.add_argument("--no-gpu", action="store_true", help="skip nvidia-smi queries")
    parser.add_argument("--top", type=int, default=8, help="number of processes to display (default: 8)")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or not 0.5 <= args.interval <= 60 or not 1 <= args.top <= 100:
        parser.error("--interval must be 0.5-60 seconds and --top must be 1-100")
    try:
        while True:
            zones = find_zones()
            before = snapshot(zones)
            time.sleep(args.interval)
            after = snapshot(zones)
            if args.watch and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            report(before, after, zones, args)
            sys.stdout.flush()
            if not args.watch:
                return
            if not sys.stdout.isatty():
                print()
    except KeyboardInterrupt:
        print("\nStopped.")
    except BrokenPipeError:
        pass
