# Root-cause analysis: 46 W idle battery drain

Investigated on 11 October 2026. The laptop drew 46 W while idle on battery, so a full charge lasted about an hour. About 34 W of that was the NVIDIA GPU running without a driver. With the GPU properly powered off, idle draw is now **14.5 W**.

On 12 October 2026, after the upgrade to Ubuntu 26.04, the GPU stopped sleeping again. This time the cause was NVIDIA's Dynamic Boost daemon. See [Regression: nvidia-powerd](#regression-nvidia-powerd-12-october-2026) and [Preventing a repeat](#preventing-a-repeat).

## Summary

| | |
|---|---|
| **Symptom** | Idle draw of about 46 W on battery, and about 1 hour of battery life. The dashboard showed about 50 W "Battery draw" next to about 5 W "CPU package-0". |
| **Root cause** | The graphics mode was set to "intel" (`prime-select intel`), which blocks the NVIDIA driver. Without its driver, the RTX 3070 was never actually powered down, even though Linux and the firmware both reported it as off. It drew about 34 W. |
| **Fix** | Load the NVIDIA driver so it can power the GPU off, then stop everything that kept the GPU awake: the X11 session, GNOME Shell, CoolerControl, the NVIDIA display modules, this app's own fan service, and Xwayland and browsers through the graphics libraries. After the Ubuntu 26.04 upgrade, also NVIDIA's Dynamic Boost daemon (`nvidia-powerd`). |
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

- **Whole laptop:** the battery's own energy counter (`/sys/class/power_supply/BAT0/energy_now`), read on battery. It updates about once a second. Each measurement ran from one counter change to another, over 60 to 92 seconds. Linux shows no working `power_now` or `current_now` for this battery, so the energy counter was the only source during the investigation. The embedded controller does have a live current sensor; see [Whole-laptop power sensor](#whole-laptop-power-sensor-13-october-2026).
- **Noise:** two identical baseline runs read 45.8 W and 46.5 W, so differences under about 1 W are not meaningful.
- **CPU chip:** Intel RAPL energy counters, read through `turbostat` as root.
- **Not measurable:** the firmware's platform sensor (RAPL `psys`) reads 0.11 W, so it isn't implemented. No sensor covers the screen, RAM, SSD, Wi-Fi, fans or chipset. Those were measured by switching parts off and comparing, or estimated.
- **Cross-check:** the user's real-world battery life of about 1 hour matches a draw of about 50 W from a 63 Wh battery.

## Why the dashboard showed 50 W and 5 W

Before this was fixed, the POWER tile showed one of two different measurements:

- **Battery draw:** the whole laptop. It only appeared on battery, and after 30 s, because the estimate needs at least 30 s of data.
- **CPU package (RAPL):** the CPU chip only. It was shown at all other times: in the first 30 s, and whenever the charger was connected.

So the tile showed about 5 W for the first 30 s and then jumped to about 50 W. On the charger it showed about 5 W again. Nothing in the power draw jumped; the tile switched between these two readings. Only the small label under the number said which one was showing.

**Fixed:** the tile is now called **LAPTOP POWER**. Its big number and its graph only ever show the whole laptop. The CPU chip appears in the line underneath, for example "Whole laptop · CPU chip 3.1 W". When the whole-laptop figure isn't available, the big number shows "—" and the line explains why: "Measuring (30 s)" or "On charger".

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

## Regression: nvidia-powerd (12 October 2026)

The GPU was found awake again a day after the fix, at about 9.5 W.

| | |
|---|---|
| **Symptom** | The GPU sat in D0, P8 at about 9.5 W. Since boot it had been awake about 58 minutes and suspended for 175 ms in total, so it was never sleeping rather than waking now and then. Every earlier fix was still in place: `on-demand` mode, display modules not loaded, the EGL and Vulkan settings present in GNOME Shell's environment, Wayland session. |
| **Root cause** | `nvidia-powerd`, NVIDIA's Dynamic Boost daemon, held `/dev/nvidiactl` and `/dev/nvidia0` (11 handles) for as long as it ran. While anything holds the device, the driver can't power the GPU off. It also logged `DC power limits table is not supported` and `NvPcfApi is not initialized`, so it wasn't doing anything useful on this laptop. |
| **How it came back** | On 11 October at 22:03 the driver moved from series 575 to 580, then Ubuntu was upgraded from 24.04 to 26.04 (22:23–22:51). The daemon ships in `nvidia-kernel-common-580`, a newly installed package, and installing a package enables its services. Whether the daemon ran during the [final measurement](#7-final-measurement) on driver 575 isn't recorded. |
| **How it was found** | `runtime_status` stuck at `active`, with no `nvidia-smi` used to check (it would have woken the GPU). Then `sudo lsof /dev/nvidia*` listed only `nvidia-powerd`. |
| **Fix** | `sudo systemctl mask --now nvidia-powerd`. The GPU reached D3cold within seconds and its awake-time counter stopped. It was first only disabled, which a future driver series would undo. See [Preventing a repeat](#preventing-a-repeat). |
| **Cost** | No Dynamic Boost, which moves power from the CPU to the GPU under load. It does nothing while the GPU is unused. |

The environment file (#6) can't prevent this kind of blocker. It only changes which graphics libraries desktop apps load. A system service that opens `/dev/nvidia*` directly is not affected by it.

The same upgrade also broke #6. Mesa in 26.04 renamed the Intel Vulkan manifest from `intel_icd.x86_64.json` to `intel_icd.json`, so `VK_DRIVER_FILES` named a file that no longer existed and Vulkan apps found no driver at all. The GPU stayed asleep, but Vulkan didn't work. `system/90-nvidia-hidden.conf` now names `intel_icd.json`.

## Preventing a repeat

Each fix can be undone by something outside this repository. Driver-series changes are the main risk, because they install packages under new names. Those packages enable their services again and can add new daemons.

| Fix | What can undo it | Protection |
|---|---|---|
| `nvidia-powerd` off | A new driver series ships it in a new package (`nvidia-kernel-common-<series>`), which enables it on install. `disable` doesn't survive that. | **Mask it**: `sudo systemctl mask --now nvidia-powerd`. Masking links the unit name to `/dev/null` in `/etc`, so no package of any series can start it until it's unmasked. |
| `on-demand` mode | Running `prime-select intel`, or a graphics settings tool that runs it | `prime-select query` |
| Display modules blocked | The file lives in `/etc/modprobe.d`, which upgrades leave alone. A new driver could add a display module with a different name. | `lsmod \| grep -E "^nvidia_(drm\|modeset)"` prints nothing |
| Apps on Intel graphics | The file lives in `/etc/environment.d`. A Mesa or Vulkan upgrade that renames `50_mesa.json` or `intel_icd.x86_64.json` would leave apps with no driver. Apps then fail to start; they don't wake the GPU. The 26.04 upgrade did rename the Vulkan file. | Both files named in `system/90-nvidia-hidden.conf` exist |
| Wayland session | GDM's `61-gdm.rules` may prefer Xorg again after a GDM upgrade | `echo "$XDG_SESSION_TYPE"` prints `wayland` |
| No GPU-polling tools | Installing a new monitor (CoolerControl, `nvtop`, a GNOME extension) that reads the GPU every second | `sudo lsof /dev/nvidia*` prints nothing |

**After every driver update or Ubuntu upgrade**, run the [Verification](#verification) block. If `runtime_status` says `active` for more than a few seconds, `sudo lsof /dev/nvidia*` names the culprit. Don't check with `nvidia-smi`: it wakes the GPU, so it can't show whether the GPU sleeps.

## Whole-laptop power sensor (13 October 2026)

After a boot on a full battery, the dashboard's LAPTOP POWER tile showed about 2 W. The real draw was about 14 W.

### Why the tile showed 2 W

The battery's energy counter stayed at exactly `energy_full` (63.377 Wh) for 10 minutes after the laptop booted on a full charge. It didn't step once. Meanwhile `voltage_now` changed on every read, so the firmware was answering. At 20:14:27 the counter jumped straight to 59.880 Wh and stepped normally after that. Every earlier measurement had started below 100%, so this "hold at full" behavior never showed before.

The tile's live line is the CPU chip plus "the rest of the laptop", where the rest is the battery's fit minus the CPU chip. A frozen counter fits to 0 W, so the rest came out as 0 and the tile showed the CPU chip alone. Neither the Ubuntu 26.04 upgrade nor the move to KDE (KWin) was involved: `energy_now` comes from the battery firmware through ACPI.

The first measurement after the counter moved read **14.1 W** over 62 s, CPU 2.4% busy, brightness 36%. turbostat put the CPU chip at **1.3 W** (cores 0.56 W, Intel graphics 0.18 W), far below the 4–5 W measured during the investigation. The chip as a whole still never went below PC3.

### The sensor in the embedded controller

The DSDT's `_BST` (battery status) calls `UPBS`, which fills in the battery's rate like this:

```
If (BRTM == One)  →  rate = ATOW(EC.MCUR)     // the battery's live current
Else              →  rate = 0xFFFFFFFF        // "unknown"
```

`BRTM` is a flag in a memory block the BIOS fills in (`EXBU`, at 0x54D2B018). On this laptop it isn't 1, so `_BST` always reports the rate as unknown, and the kernel answers `power_now` with "No such device" (`-ENODEV`). The current is there all the same, in the EC's memory:

| Field | EC offset | Meaning |
|---|---|---|
| `MBST` | 0x82 | Battery state; bit 0 means discharging |
| `MCUR` | 0x83–0x84 | Current in mA, signed 16-bit, negative while discharging |
| `MBRM` | 0x85–0x86 | Remaining charge in mAh |
| `MBCV` | 0x87–0x88 | Voltage in mV |
| `BVLB`, `BVHB` | 0x74, 0x75 | Design voltage in mV (11,580) |

The same code explains the counter's steps. `MBRM` counts in mAh, and `ATOW` converts it to mWh with the *design* voltage: 1 mAh × 11.58 V = 11.58 mWh, the "~12 mWh step" the probe measured. So the counter steps each time 1 mAh is used, at irregular times.

Six readings of the EC through `ec_sys`, read-only, one second apart:

| State | Current | Voltage | Power |
|---|---|---|---|
| 0x01 | −1143 mA | 12,102 mV | 13.83 W |
| 0x01 | −1206 mA | 12,091 mV | 14.58 W |
| 0x01 | −1140 mA | 12,102 mV | 13.80 W |
| 0x01 | −1135 mA | 12,119 mV | 13.76 W |
| 0x01 | −1124 mA | 12,083 mV | 13.58 W |
| 0x01 | −1095 mA | 12,119 mV | 13.27 W |

These average 13.8 W, against 14.1 W from the energy counter a few minutes earlier. The voltage matches `voltage_now` (12.09–12.14 V). The current changes every second, so it's a live reading, not a cached value.

### What the dashboard does now

On battery, the LAPTOP POWER tile takes its live line from the EC: current × voltage, every second, labelled "Battery sensor · 1 s". The battery counter's 15 s fit is still drawn as the blue line. The EC reading also recalibrates "the rest of the laptop", so on the charger the estimated line starts from a fresh value.

- **Root only.** The dashboard loads `ec_sys` if it isn't loaded. It loads it with write support, the same as the fan controller, because a module loaded without it stays loaded and the fan controller could then no longer write. The power code only reads.
- **Other models are protected.** The EC reading is used only while the battery is discharging, its current is negative, and its voltage is within 300 mV of the kernel's `voltage_now`. A model with a different register map fails that check and falls back to the counters.
- **On the charger** the EC reports the battery's charging current, not what the laptop uses, so the counters remain the source there.
- **Without the EC** (not root, or a different model), the tile works as before, including the 2 W reading while a full battery's counter is frozen.

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
| The 2 W reading after the 26.04 upgrade and the move to KDE meant something in the new system broke power reporting | The battery firmware holds its counter at "full" for about 10 minutes after a full charge. See [Whole-laptop power sensor](#whole-laptop-power-sensor-13-october-2026). |
| This battery has no current sensor, because `power_now` fails | The EC has one (`MCUR`); the firmware withholds it because the BIOS flag `BRTM` isn't set |
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
| Apps use Intel graphics only | `/etc/environment.d/90-nvidia-hidden.conf`; recopied on 12 October 2026 for the renamed Vulkan file |
| CoolerControl removed | `apt purge coolercontrol coolercontrold coolercontrol-liqctld`, plus removing its apt repository and key, `/etc/coolercontrol`, and its settings in the home folder |
| NoteBook FanControl removed | A leftover second fan controller, installed by hand: it started at boot but never controlled the fans (auto-start off, configured for an OMEN 15). Removed with `systemctl disable --now nbfc.service`, plus deleting `/etc/systemd/system/nbfc.service`, `/etc/systemd/system/nbfc-sleep.service`, `/opt/nbfc` and `/etc/NbfcService`. Its Mono runtime (`mono-complete`, about 250 MB) was removed too, with `apt purge` and `apt autoremove`. |
| Dynamic Boost daemon masked | `sudo systemctl mask --now nvidia-powerd` (12 October 2026, Ubuntu 26.04, driver 580) |
| Fan service runs this repository's code | `pipx install --force --editable .`; the service restarts at boot |
| Removed again (did not help) | `/etc/modprobe.d/nvidia-drm-fbdev-off.conf`, `/etc/udev/rules.d/61-mutter-ignore-nvidia.rules` |

## Verification

```bash
cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status   # "suspended" while the GPU is off
lsmod | grep -E "^nvidia_(drm|modeset)"                       # prints nothing
prime-select query                                            # on-demand
echo "$XDG_SESSION_TYPE"                                      # wayland
systemctl is-enabled nvidia-powerd                            # masked
sudo lsof /dev/nvidia*                                        # lists whatever holds the GPU, if it stays awake
```

To measure the whole laptop, unplug the charger and leave the laptop idle. The counter is in µWh:

```bash
e0=$(cat /sys/class/power_supply/BAT0/energy_now); sleep 90; e1=$(cat /sys/class/power_supply/BAT0/energy_now)
echo "scale=1; ($e0 - $e1) * 3600 / 90 / 1000000" | bc   # watts
```

## Undo

- **Let apps use the NVIDIA GPU again:** run `sudo rm /etc/modprobe.d/nvidia-no-display.conf /etc/environment.d/90-nvidia-hidden.conf`, then `sudo update-initramfs -u`, and reboot. The GPU then stays awake, as in the 22.6 W row.
- **Bring back Dynamic Boost:** `sudo systemctl unmask nvidia-powerd`, then `sudo systemctl enable --now nvidia-powerd`. The GPU then stays awake at about 9.5 W.
- **Go back to "intel" mode:** `sudo prime-select intel`. This brings back the 46 W drain, because the GPU runs without a driver again.

## Open items

- **PCI runtime power saving:** turning it on for the 11 devices that have it off saved 3.7 W in the switch-off test. That would bring the laptop to roughly 11 W (an estimate; not yet measured in the final setup).
- **CPU chip never goes below PC3:** PC6, PC8 and PC10 stay at 0%, and the cause is unknown. The next step is the Intel PMC debug files (`/sys/kernel/debug/pmc_core/`, root only), which show which part blocks deeper states.
- **Warn when the GPU won't sleep:** the dashboard already reads the GPU's `runtime_status` without waking it. It could warn when the GPU stays awake for over a minute, which would have caught the `nvidia-powerd` regression on the day of the upgrade.
- **Faster readings without root:** the counter steps once per mAh used, so timing those steps (energy between the first and last step ÷ the time between them) would beat the 15 s straight-line fit. It still can't help while the counter holds at "full".
- **Battery health:** 75.8%, which shortens runtime separately from everything above.
