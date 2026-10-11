# HP Omen Fan Control (Linux)

This tool provides fan control for HP Omen Max, Victus and Omen laptops on Linux. It includes installer for a kernel driver patch (`hp-wmi`) to expose PWM controls and a userspace utility to manage fan curves, create watchdog that sets the fan configuration periodically and a simple stress test tool to see the fan curve in effect. It also allows periodically reversing the fan spin direction on supported boards to expel dust.

## Quick start: dashboard and power report

The dashboard shows the fan controls and the live power report in one window.

Install this repository's version of the app first. Its fan service leaves a sleeping NVIDIA GPU alone (see [Battery life with NVIDIA graphics](#battery-life-with-nvidia-graphics)). From the cloned folder:

```bash
pipx install --force --editable .                 # the commands and the fan service run the code in this folder
sudo ln -sf "$HOME/.local/bin/omen-fan-control"* /usr/local/bin/
sudo omen-fan-control service install            # first install only
sudo systemctl restart omen-fan-control          # after changing the code or a git pull, so the service loads it
```

`--editable` links the app to this folder instead of copying it, so `omen-fan-control`, `omen-fan-control-gui` and the fan service all use the code here. Keep the folder where it is: moving or deleting it breaks the commands until you reinstall.

Then run these from the repository folder:

```bash
sudo -E ./omen-dashboard               # starts in the background; the terminal is free to close
sudo -E ./omen-dashboard --foreground  # stays in this terminal and prints its messages here
./omen-dashboard                       # no sudo: read-only, so you can view everything but not apply fan changes
```

- **Stop it:** click the **X** in the window. If the window is gone, run `sudo pkill -f omen_fan_control.gui`.
- **Why `sudo -E`:** fan control needs root, and `-E` keeps your display settings so the window can open.
- **Log:** background runs write to `/run/user/<your user id>/omen-dashboard.log`. The path is printed when it starts.
- **Requirement:** the dashboard uses the Python environment of the installed app, so install the app first (see [Installation](#installation)). Without it, python3 needs PyQt6 and click.

Power report in the terminal, without a window:

```bash
./power-report.py            # one report
./power-report.py --watch    # keeps refreshing until Ctrl+C
sudo ./power-report.py       # some readings, such as RAPL, need root
./power-report.py --help     # all options
```

## Battery life with NVIDIA graphics

On an OMEN 16 (i7-11800H, RTX 3070 Laptop GPU), the laptop drew about 46 W while idle on battery, so a full charge lasted about an hour. The CPU used only 4–5 W of that. Most of the rest was the NVIDIA GPU. Ubuntu's "intel" graphics mode blocks the NVIDIA driver, and without its driver the GPU ran unmanaged, even though Linux reported it as switched off. Loading the driver brought the idle draw down to 22.6 W while the GPU was still awake; by its own reading, it then drew 10–12 W. The steps below also let it switch off fully whenever nothing uses it.

Each step is done once and survives reboots:

1. **Load the NVIDIA driver and keep the screen on Intel graphics:**
   ```bash
   sudo prime-select on-demand
   ```
2. **Log in with Wayland.** On the login screen, click your name, then the gear icon, and choose **Ubuntu** (not "Ubuntu on Xorg"). With NVIDIA drivers 510 and newer, Ubuntu picks X11 by default, and X11 keeps the GPU awake.
3. **Stop GNOME from holding the GPU open:**
   ```bash
   echo 'SUBSYSTEM=="drm", KERNEL=="card[0-9]*", KERNEL!="card*-*", DRIVERS=="nvidia", TAG+="mutter-device-ignore"' | sudo tee /etc/udev/rules.d/61-mutter-ignore-nvidia.rules
   ```
   Trade-off: while this rule exists, a monitor plugged into a port wired to the NVIDIA GPU shows nothing.
4. **Turn off the NVIDIA driver's text console**, which otherwise keeps the GPU busy checking its display connectors:
   ```bash
   echo "options nvidia-drm fbdev=0" | sudo tee /etc/modprobe.d/nvidia-drm-fbdev-off.conf
   sudo update-initramfs -u
   ```
5. **Avoid tools that read the GPU every second**, such as CoolerControl. Every reading wakes the GPU. This app's fan service and dashboard skip the GPU while it sleeps and use the CPU temperature instead.
6. **Reboot.**

- **Check it:** `cat /sys/bus/pci/devices/0000:01:00.0/power/runtime_status` prints `suspended` while the GPU sleeps. `lspci -D | grep -i nvidia` shows your GPU's address if it differs. If it stays `active`, `sudo lsof /dev/nvidia*` lists the programs holding it.
- **Undo:** delete the files from steps 3 and 4, run `sudo update-initramfs -u`, and reboot. `sudo prime-select intel` blocks the driver again, which brings back the high idle draw.

## Context

This tool includes a backported `hp-wmi` driver patch from the latest Linux kernel, which introduces native fan control support for many devices from the following models:

1. **HP Omen Max**
2. **HP Victus**
3. **HP Omen**

This patch applies across all 6.x releases and kernels 7.0–7.2.

**Reference Kernel Commit:**
[platform/x86: hp-wmi: add manual fan control for Victus S models](https://git.kernel.org/pub/scm/linux/kernel/git/pdx86/platform-drivers-x86.git/commit/?h=for-next&id=46be1453e6e61884b4840a768d1e8ffaf01a4c1c)

This program also includes a modification that sets the max speed according to calibration if the query to get the Max RPM fails for your device.

## Tested Hardware
* **Models:** HP OMEN MAX 16-ah0xxxnt (8D41), HP OMEN Transcend 16-u1xxx (8C4D), HP OMEN Transcend 16-u0xxx (8BB3), OMEN by HP Gaming Laptop 16-xf0xxx (8BCA), HP Victus 16 s1xxx (8C9C), HP Omen 15 ek1xxx (88C8), HP Omen 16-ap0000 (8E35), HP Victus 15-fb2xxx (8C2F), HP Omen 17 17z-db100 (8E10)

* **OS:** Arch Linux 6.18, 6.19, 7.0, 7.1, 7.2; Ubuntu 24.04 (HWE 7.0)

> Would you like to add your model here? Create an issue with the "Tested Hardware" label and include these after successfully installing the patch and using the tool:
> * Your laptop's board id (you can find it in /sys/class/dmi/id/board_name)
> * Your kernel version (you can find it in /proc/version)

## Installation

### System Dependencies
You must install kernel headers and build tools for the driver patch to compile and persist across kernel updates:

* **Arch Linux / CachyOS / Manjaro:**
  ```bash
  sudo pacman -S linux-headers base-devel dkms
  # Optional (for fan cleaner feature):
  sudo pacman -S acpi_call-dkms
  ```

* **Debian / Ubuntu / Pop!_OS / Linux Mint:**
  ```bash
  sudo apt install linux-headers-$(uname -r) \
                   linux-headers-$(uname -r | sed 's/-[^-]*$/-common/') \
                   "linux-kbuild-$(uname -r | cut -d. -f1,2,3 | cut -d+ -f1)*" \
                   build-essential dkms
  # Optional (for fan cleaner feature):
  sudo apt install acpi_call-dkms
  ```
  > **Note:** Debian and Ubuntu split kernel headers into multiple packages: architecture-specific (`linux-headers-<version>-amd64`), common (`linux-headers-<version>-common`), and build scripts (`linux-kbuild-<version>`). All must be installed for out-of-tree module compilation.

* **Fedora / RHEL / Rocky Linux:**
  ```bash
  sudo dnf install kernel-devel-$(uname -r) kernel-headers gcc make dkms
  ```

* **CachyOS / Clang Kernels:** The Makefile detects if your kernel was built with Clang/LLVM and passes the correct `LLVM=1` flags to the build system. No extra configuration is needed.

---

### Choose an Installation Method

Choose one of the following: pipx/uv (recommended), Arch Linux packages, Debian packages, or clone + run from source.

### Option A: uv or pipx (recommended)

Install the app in an isolated environment. Driver sources are bundled; you can run `install-patch` from the app.

**Using uv:**
```bash
uv tool install git+https://github.com/arfelious/omen-fan-control.git
sudo ln -sf "$HOME/.local/bin/omen-fan-control"* /usr/local/bin/

```

**Using pipx:**
```bash
pipx install git+https://github.com/arfelious/omen-fan-control.git
sudo ln -sf "$HOME/.local/bin/omen-fan-control"* /usr/local/bin/

```
**After installation**
```bash
sudo omen-fan-control status

# For calibration, driver installation, and fan control daemon installation.
sudo uv run omen-fan-control calibrate
sudo uv run omen-fan-control install-patch permanent
sudo uv run omen-fan-control service install

# For GUI
sudo omen-fan-control-gui
```


> **Note:** Because hardware fan control requires root permissions, symlinking the binaries to `/usr/local/bin` ensures `sudo` can locate them.


### Option B: Arch Linux (PKGBUILD)

Build and install from `arch/omen-fan-control`:

```bash
cd omen-fan-control/arch/omen-fan-control
makepkg -sf
sudo pacman -U omen-fan-control-*.pkg.tar.zst
```

Driver sources are bundled with the app. After installation, calibrate and install the permanent patch (which configures DKMS with your calibrated values):
```bash
sudo omen-fan-control calibrate
sudo omen-fan-control install-patch permanent
sudo omen-fan-control service install
```

### Option C: Debian / Ubuntu (deb)

Build and install from `deb/`:

```bash
# Build package
cd omen-fan-control/deb
./build.sh

# Install package
sudo dpkg -i build/omen-fan-control_*.deb
```

Driver sources are bundled with the app. After installation, calibrate and install the permanent patch:
```bash
sudo omen-fan-control calibrate
sudo omen-fan-control install-patch permanent
sudo omen-fan-control service install
```

### Option D: Clone and run from source

```bash
git clone https://github.com/arfelious/omen-fan-control.git
cd omen-fan-control

# With uv (recommended for source)
uv sync
sudo uv run omen-fan-control status
sudo uv run omen-fan-control calibrate
sudo uv run omen-fan-control install-patch permanent
sudo uv run omen-fan-control service install
sudo uv run omen-fan-control-gui

# With pip (editable install)
pip install -e .
sudo omen-fan-control status

# Or with pip + PYTHONPATH
pip install -r requirements.txt
export PYTHONPATH=src
sudo env PYTHONPATH=src python -m omen_fan_control.cli status
```

## Usage

After installation, use `omen-fan-control` (CLI) and `omen-fan-control-gui` (GUI).
From a clone, replace with `uv run omen-fan-control` or `PYTHONPATH=src python -m omen_fan_control.cli`.

### GUI

```bash
sudo omen-fan-control-gui
```

**GUI Fan Curve:**

| <img width="400" height="300" alt="Omen Fan Control GUI" src="https://github.com/user-attachments/assets/57f1a966-d7a6-4c5f-8090-a1e947349bd9" /> |
|---|

### CLI

**Check Status:**
```bash
sudo omen-fan-control status
```

**Settings:**
```bash
omen-fan-control settings --help
omen-fan-control settings
sudo omen-fan-control options --ma-window 10 --curve-interpolation smooth
```

**Fan Control:**
```bash
# Manual speed
sudo omen-fan-control fan-control --mode manual --value 80%

# Curve mode (requires service)
sudo omen-fan-control fan-control --mode curve

# Auto (default)
sudo omen-fan-control fan-control --mode auto

# Custom curve CSV (temp, percent per line)
sudo omen-fan-control fan-control --curve-csv my_curve.csv
```

**Temporary Settings (`--no-save`):**
Use the `--no-save` flag to apply settings to the current session only. This prevents the utility from updating your persistent configuration file and is useful for one-off manual adjustments, hooks or scripts.

> When using `--no-save` in **Manual** or **Auto** modes, the utility will automatically stop the background service to prevent it from overwriting your temporary setting. However, in **Curve** mode, the service is kept running as it is required to process the temperature data and apply the fan curve.

```bash
sudo omen-fan-control fan-control --mode manual --value 100% --no-save
```

**Detailed Information for Commands:**
Commands provide detailed information when `--help` is passed with the command:
```bash
omen-fan-control fan-control --help
```

### Calibrate & Install Driver Patch

Most supported models do not report maximum fan RPM via BIOS queries. Running calibration first allows the utility to measure your hardware's actual fan limits and patch them into the driver source before building.

```bash
# 1. Calibrate fan speeds (or calibrate via GUI)
sudo omen-fan-control calibrate

# 2. Permanent Installation with DKMS (Recommended)
sudo omen-fan-control install-patch permanent

# Or temporary installation (until reboot)
sudo omen-fan-control install-patch temporary
```

### Install Background Service

```bash
sudo omen-fan-control service install
```

You can also install it from the settings page in the graphical interface.

## Fan Cleaning

This is currently the only Linux utility capable of recreating the fan cleaning routine. On supported laptops, it reverses fan spin direction to dislodge accumulated dust. The cleaning interval, manual cleanup, and further configuration can be done using the **Fan Cleaner** page. The page is not visible on unsupported hardware to prevent unexpected behavior.

Fan cleaning feature requires the `acpi_call` module to be loaded:
```bash
sudo modprobe acpi_call
```

If you don't have the module installed:

**Ubuntu / Debian / Pop!_OS:**
```bash
sudo apt update
sudo apt install acpi-call-dkms
sudo modprobe acpi_call
```

**Arch Linux / Manjaro / CachyOS:**
```bash
sudo pacman -S acpi_call-dkms
sudo modprobe acpi_call
```

## Contributing

Pull requests, issues, and notifying about tested hardwares are welcome!

**We are looking for testers. If you'd like to help, feel free to look at [Issue #35](https://github.com/arfelious/omen-fan-control/issues/35)**


## Related Projects

This utility is part of an ongoing effort to bring more **OMEN Gaming Hub** features to Linux. You might also want to check this project out:

[Omen RGB Linux](https://github.com/arfelious/omen-rgb-linux): RGB lighting controller that supports 4-zone, single-zone and per-key keyboards with GUI and SDK for OMEN/Victus laptops on Linux.

## Uninstallation

1. **Remove Service:**
   ```bash
   sudo omen-fan-control service remove
   ```

2. **Restore Driver:**
   ```bash
   sudo omen-fan-control install-patch restore
   ```
   This restores the original `.ko` files from the backups created during installation.

3. **Uninstall Package & Symlinks:**
   * **uv:**
     ```bash
     sudo rm -rf ~/.local/share/uv/tools/omen-fan-control ~/.local/bin/omen-fan-control* /usr/local/bin/omen-fan-control*
     ```
     >Note: Regular `uv tool uninstall` will likely fail with permission error  as `.pyc` files will be root-owned
   * **pipx:**
     ```bash
     pipx uninstall omen-fan-control
     sudo rm -f /usr/local/bin/omen-fan-control*
     ```
   * **Arch:** `sudo pacman -R omen-fan-control`
   * **Debian:** `sudo apt remove omen-fan-control`

## Disclaimer

**USE AT YOUR OWN RISK.**
Modifying kernel drivers and manipulating thermal control systems can potentially damage your hardware or cause instability. This software is provided "as is" without warranty of any kind. This was tested on my personal hardware, and the used `hp-wmi.c` is a patched and modified version of the one in the Linux kernel, so your mileage may vary.

<details>
<summary>Acknowledgements</summary>
<br>

**Probes:**
- https://github.com/alou-S/omen-fan/blob/main/docs/probes.md

**Linux 6.20 Kernel HP-WMI Driver:**
- https://git.kernel.org/pub/scm/linux/kernel/git/pdx86/platform-drivers-x86.git/commit/?h=for-next&id=46be1453e6e61884b4840a768d1e8ffaf01a4c1c

**RGB Support for 4-zone and single-zone keyboards:**
- https://lore.kernel.org/all/B3i0fyryR3yLLMsNVCrg3Q@aquinas.su/

</details>
