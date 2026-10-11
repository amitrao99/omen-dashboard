#!/usr/bin/env python3
"""Command-line power report for a source checkout; the logic lives in omen_fan_control.power_report."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from omen_fan_control.power_report import main  # noqa: E402

if __name__ == "__main__":
    main()
