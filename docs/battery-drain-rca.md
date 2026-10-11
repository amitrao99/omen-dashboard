# Root-cause analysis: 46 W idle battery drain

Investigated on 11 October 2026. The laptop drew 46 W while idle on battery, so a full charge lasted about an hour. About 34 W of that was the NVIDIA GPU running without a driver. With the GPU properly powered off, idle draw is now **14.5 W**.

## Summary

| | |
|---|---|
| **Symptom** | Idle draw of about 46 W on battery, and about 1 hour of battery life. The dashboard showed about 50 W "Battery draw" next to about 5 W "CPU package-0". |
| **Root cause** | The graphics mode was set to "intel" (`prime-select intel`), which blocks the NVIDIA driver. Without its driver, the RTX 3070 was never actually powered down, even though Linux and the firmware both reported it as off. It drew about 34 W. |
| **Fix** | Load the NVIDIA driver so it can power the GPU off, then stop everything that kept the GPU awake: the X11 session, GNOME Shell, CoolerControl, the NVIDIA display modules, this app's own fan service, and Xwayland and browsers through the graphics libraries. |
| **Result** | 46.1 W → 22.6 W (driver loaded, GPU awake) → **14.5 W** (GPU off). Idle battery life went from about 1.4 h to about 4.3 h, on a 63 Wh battery. |
| **Cost** | The NVIDIA GPU can no longer be used for graphics, and a monitor on a port wired to it shows nothing. The changes are undone by deleting two files (see [Undo](#undo)). |

## System

| | |
|---|---|
| Laptop | OMEN by HP Laptop 16-b0xxx, board 88FD, BIOS F.48 (08/06/2024) |
| CPU | 11th Gen Intel Core i7-11800H (Tiger Lake-H), with Intel UHD graphics |
| Discrete GPU | NVIDIA GeForce RTX 3070 Laptop GPU (GA104M, PCI `10de:24dd`, at `0000:01:00.0`) |
| Memory and storage | 15 GiB RAM; Samsung PM9A1 NVMe behind Intel VMD |
| Battery | 83.0 Wh design, 63.0 Wh full (75.8% health) |
| Software | Ubuntu 24.04.2 LTS, kernel 6.14.0-27-generic, GNOME Shell 46.0, NVIDIA driver 575.64.03 |

## How the numbers were measured

- **Whole laptop:** the battery's own energy counter (`/sys/class/power_supply/BAT0/energy_now`), read on battery. It updates about once a second. Each measurement ran from one counter change to another, over 60 to 92 seconds. This battery has no `power_now` or `current_now` sensor, so the energy counter is the only source.
- **Noise:** two identical baseline runs read 45.8 W and 46.5 W, so differences under about 1 W are not meaningful.
- **CPU chip:** Intel RAPL energy counters, read through `turbostat` as root.
- **Not measurable:** the firmware's platform sensor (RAPL `psys`) reads 0.11 W, so it isn't implemented. No sensor covers the screen, RAM, SSD, Wi-Fi, fans or chipset. Those were measured by switching parts off and comparing, or estimated.
- **Cross-check:** the user's real-world battery life of about 1 hour matches a draw of about 50 W from a 63 Wh battery.

## Why the dashboard showed 50 W and 5 W

The POWER tile shows one of two different measurements:

- **Battery draw:** the whole laptop. It only appears on battery, and after 30 s, because the estimate needs at least 30 s of data.
- **CPU package (RAPL):** the CPU chip only. It's shown at all other times: in the first 30 s, and whenever the charger is connected.

So the tile showed about 5 W for the first 30 s and then jumped to about 50 W. On the charger it showed about 5 W again. Nothing in the power draw jumped; the tile switched between these two readings. The label under the number says which one is showing.

## Investigation

### 1. Baseline: where does 46 W go?

| Reading | Value |
|---|---|
| Whole laptop, idle, dashboard closed | 47.7 W over 90 s, CPU 2.5% busy; earlier 49.9 W and 49.3 W |
| CPU chip (RAPL package) | 4.6–5.1 W: cores 0.9–1.4 W, Intel graphics 0.2 W |
| CPU idle states (turbostat) | Cores in their deepest state (C7) about 96% of the time. The chip as a whole only reached PC2 (about 26%) and PC3 (about 55%). PC6, PC8 and PC10 were never reached (0%). |
| NVIDIA GPU, as reported | PCI state D3cold, runtime suspended, ACPI power resource `PG00` off, `real_power_state` D3cold. Every software indicator said "off". |
| Keyboard backlight, USB-C port | Off, and no current |

That left about 41 W outside the CPU chip with no sensor covering it.

### 2. Switch-off test

The test switched off one part at a time for 60 s each and compared against the 46.1 W baseline:

| Step | Change |
|---|---|
| Screen off (display power-save through GNOME) | −2.4 W |
| Wi-Fi and Bluetooth off | No measurable change (within noise) |
| Runtime power saving on for 11 PCI devices | −3.7 W, of which −0.9 W was on the CPU chip |
| NVIDIA GPU's PCIe port forced on | +2.5 W |

The last result was misread at the time as proof that the GPU really was off. See [False leads](#false-leads-and-corrections).

### 3. PCIe link power saving (ruled out)

The kernel log said `ACPI FADT declares the system doesn't support PCIe ASPM`, so Linux leaves link power saving as the BIOS set it. `lspci` showed L1 power saving already enabled on the SSD, Ethernet, card reader and root-port links. Only the NVIDIA GPU's link had it off, and that GPU was supposed to be off. This was not the cause.

### 4. The NVIDIA driver was blocked

`prime-select query` returned `intel`, which blacklists every NVIDIA kernel module. The NVIDIA driver (575) was installed but never loaded, so nothing managed the GPU's power. The kernel's generic PCI power management put the GPU's port into D3cold, and the firmware reported the GPU's power resource as off.

### 5. Proof: loading the driver halved the draw

After `sudo prime-select on-demand` and a reboot, the driver loaded and the GPU could report its own power:

| Reading | Value |
|---|---|
| GPU state | P8 (lowest active state), 0% load |
| GPU power, from its own sensor | 10.2–10.7 W |
| Whole laptop | **22.6 W** over 92 s |

So the rest of the laptop used about 22.6 − 10.3 ≈ **12.3 W**. In "intel" mode, the GPU therefore cost about 46.1 − 12.3 ≈ **34 W**. Chrome was open in the first run and closed in the second, which is worth under 1 W, so the honest range is 33–35 W.

**Mechanism (likely, not proven):** without the NVIDIA driver's power-down sequence, the firmware's power-off for the GPU's port doesn't cut the GPU's main power. The GPU then sits uninitialized at its default clocks. This is also why forcing the port on in step 2 added only 2.5 W: the GPU was already powered.

### 6. Why the GPU still didn't sleep, one blocker at a time

With the driver loaded, the GPU idled at 10–12 W but never entered its powered-off state. Each blocker was found and removed in turn:

| # | What held the GPU awake | How it was found | Fix |
|---|---|---|---|
| 1 | **The Xorg session.** Ubuntu's `61-gdm.rules` makes the login screen prefer X11 with NVIDIA drivers 510 and newer, and Xorg opened the GPU. | `nvidia-smi` listed `Xorg` (4 MiB) | Log in with "Ubuntu" (Wayland) |
| 2 | **GNOME Shell** opened the GPU to drive its outputs | `nvidia-smi` listed `gnome-shell` (2 MiB) | First a udev `mutter-device-ignore` rule. Later replaced by #4, which removes the GPU's display devices altogether. |
| 3 | **CoolerControl** (`coolercontrold` 2.2.2) polled the GPU's sensors every second, holding `/dev/nvidia0`, `/dev/nvidiactl` and `/dev/nvidia-uvm`. It had no device settings, so it was only monitoring. | `sudo lsof /dev/nvidia*` | Removed: the packages, its apt repository and key, and its settings |
| 4 | **The NVIDIA display modules** (`nvidia_drm`, `nvidia_modeset`). With them loaded, the GPU's runtime usage count stayed at 1 and it never suspended, even with no program holding it. After `modprobe -r` it reached D3cold within seconds. `/lib/udev/rules.d/71-nvidia.rules` loads them whenever the driver starts. | No user-space holders, but `runtime_usage=1`; unloading the modules made it sleep | `system/nvidia-no-display.conf`: `install nvidia_drm /bin/true`, `install nvidia_modeset /bin/true` |
| 5 | **This app's own fan service.** `get_gpu_temp()` ran `nvidia-smi` every 2 s in the service loop (the dashboard every 10 s), and each run woke the GPU. In "intel" mode the call failed instantly, so it never mattered before. | GPU cycling between D3cold and D0 every few seconds; `nvidia-smi` caught running with the fan service as its parent | Code: skip the query while the driver has the GPU asleep (`nvidia_gpu_asleep()` in `power_report.py`). The fan logic then uses the CPU temperature, as it already did with no GPU reading. |
| 6 | **Xwayland and Chrome.** They opened the NVIDIA render node and `/dev/nvidia0` through NVIDIA's EGL and Vulkan libraries. Xwayland starts with the first X11 app, then stays for the session because it serves GNOME's own helpers (`gsd-xsettings`, `ibus-x11`, `mutter-x11-frames`). | Holder scan after opening Chrome; `modprobe -r` refused with "Module nvidia_drm is in use" | `system/90-nvidia-hidden.conf`: EGL limited to Mesa, Vulkan limited to Intel |

### 7. Final measurement

After the reboot with all the fixes in place:

| Reading | Value |
|---|---|
| NVIDIA display modules | Not loaded |
| GPU | D3cold. Since boot: asleep 74 s, awake 7 s. During the measurement: asleep 100% of the time, 0 wake-ups. |
| Whole laptop | **14.5 W** over 92 s (CPU 2.6% busy, brightness 11%, Chrome and dashboard closed) |

## Results

| Setup | Idle draw | Idle battery life (63 Wh) |
|---|---|---|
| "intel" mode, NVIDIA driver blocked | 46.1 W | ~1.4 h |
| Driver loaded, GPU awake | 22.6 W | ~2.8 h |
| Driver loaded, GPU off (final) | **14.5 W** | **~4.3 h** |

### Where the remaining 14.5 W goes

| Part | Watts | Source |
|---|---|---|
| CPU chip | ~4.1 | Measured (RAPL) |
| Extra from 11 PCI devices with runtime power saving off | ~2.8 | Measured by switching it on (−3.7 W, of which 0.9 W is on the CPU chip) |
| Screen at 11% brightness | ~2.4 | Measured by switching it off |
| NVIDIA GPU | 0 | Off for the whole measurement |
| RAM, SSD, Wi-Fi and Bluetooth, fans, chipset, power conversion losses | ~5 | What's left over; no sensors |

The middle rows were measured in "intel" mode, before the fixes, so they are approximate for the final setup.

## False leads and corrections

These were wrong turns during the investigation. They're recorded so that nobody repeats them.

| Claim at the time | What was actually true |
|---|---|
| "CPU package-0" might be the whole laptop's draw | It's the CPU chip only. See [Why the dashboard showed 50 W and 5 W](#why-the-dashboard-showed-50-w-and-5-w). |
| Missing deep CPU idle states were the main cause | The cores do reach their deepest state. The chip as a whole never goes below PC3, which is real, but worth a few watts at most. The cause is still open. |
| PCIe power saving was disabled by the BIOS (FADT) | The links already had L1 enabled |
| The NVIDIA GPU was off, because PCI D3cold, ACPI and `real_power_state` all said so | It was drawing about 34 W. Software power states describe what the kernel requested, not what the hardware is doing. |
| Forcing the GPU's port on added only 2.5 W, so the GPU really was off | The GPU was already powered, so switching the port on changed little |
| The NVIDIA console framebuffer (`nvidia_drm fbdev=1`, the default since driver 570) kept the GPU awake | With `fbdev=0` it still never slept. Blocking the display modules entirely (#4) is what worked. |
| The GNOME udev rule would let the GPU sleep | It made GNOME let go of the GPU, but the display modules and other programs still held it. It was dropped once #4 made it unnecessary. |

## Changes made

### Repository (branch `omen-dashboard`)

| Commit | Change |
|---|---|
| `0cf47c6` | The fan service and the dashboard skip `nvidia-smi` while the GPU is asleep (`power_report.nvidia_gpu_asleep()`, `fan_controller.get_gpu_temp()` and `has_gpu()`, the dashboard's GPU poller) |
| `c9c8adc` | README: install the app as editable, so the commands and the fan service run this folder's code |
| `5c373bd` | `system/nvidia-no-display.conf`, `system/90-nvidia-hidden.conf`, and the README battery section with the measured results |

### System (once per machine)

| Change | Command or file |
|---|---|
| NVIDIA driver allowed to load | `sudo prime-select on-demand` |
| Wayland session | "Ubuntu" chosen under the gear icon on the login screen; GNOME remembers it |
| NVIDIA display modules blocked | `/etc/modprobe.d/nvidia-no-display.conf`, then `sudo update-initramfs -u` |
| Apps use Intel graphics only | `/etc/environment.d/90-nvidia-hidden.conf` |
| CoolerControl removed | `apt purge coolercontrol coolercontrold coolercontrol-liqctld`, plus removing its apt repository and key, `/etc/coolercontrol`, and its settings in the home folder |
| NoteBook FanControl removed | A leftover second fan controller, installed by hand: it started at boot but never controlled the fans (auto-start off, configured for an OMEN 15). Removed with `systemctl disable --now nbfc.service`, plus deleting `/etc/systemd/system/nbfc.service`, `/etc/systemd/system/nbfc-sleep.service`, `/opt/nbfc` and `/etc/NbfcService`. Its Mono runtime (`mono-complete`, about 250 MB) was removed too, with `apt purge` and `apt autoremove`. |
| Fan service runs this repository's code | `pipx install --force --editable .`; the service restarts at boot |
| Removed again (did not help) | `/etc/modprobe.d/nvidia-drm-fbdev-off.conf`, `/etc/udev/rules.d/61-mutter-ignore-nvidia.rules` |

## Verification

```bash
cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status   # "suspended" while the GPU is off
lsmod | grep -E "^nvidia_(drm|modeset)"                       # prints nothing
prime-select query                                            # on-demand
echo "$XDG_SESSION_TYPE"                                      # wayland
sudo lsof /dev/nvidia*                                        # lists whatever holds the GPU, if it stays awake
```

To measure the whole laptop, unplug the charger and leave the laptop idle. The counter is in µWh:

```bash
e0=$(cat /sys/class/power_supply/BAT0/energy_now); sleep 90; e1=$(cat /sys/class/power_supply/BAT0/energy_now)
echo "scale=1; ($e0 - $e1) * 3600 / 90 / 1000000" | bc   # watts
```

## Undo

- **Let apps use the NVIDIA GPU again:** run `sudo rm /etc/modprobe.d/nvidia-no-display.conf /etc/environment.d/90-nvidia-hidden.conf`, then `sudo update-initramfs -u`, and reboot. The GPU then stays awake, as in the 22.6 W row.
- **Go back to "intel" mode:** `sudo prime-select intel`. This brings back the 46 W drain, because the GPU runs without a driver again.

## Open items

- **PCI runtime power saving:** turning it on for the 11 devices that have it off saved 3.7 W in the switch-off test. That would bring the laptop to roughly 11 W (an estimate; not yet measured in the final setup).
- **CPU chip never goes below PC3:** PC6, PC8 and PC10 stay at 0%, and the cause is unknown. The next step is the Intel PMC debug files (`/sys/kernel/debug/pmc_core/`, root only), which show which part blocks deeper states.
- **Dashboard POWER tile:** it switches silently between the whole laptop and the CPU chip, which caused confusion twice during this investigation. It should show both values, each with its own label.
- **Battery health:** 75.8%, which shortens runtime separately from everything above.
