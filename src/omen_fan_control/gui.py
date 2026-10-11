"""Fan control and power report in one window.

Left: fan speeds, mode, manual speed and the temperature curve.
Right: live temperature, power and load tiles, then the full power report.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections import deque
from string import Template
from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSlider, QSplitter,
    QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import get_assets_dir
from . import laptop_power, power_report
from .fan_controller import FanController
from .fan_curve_widget import FanCurveEditor

POWER_INTERVAL = 2.0  # seconds between power and activity samples
LIVE_POWER_INTERVAL = 1.0  # seconds between LAPTOP POWER readings
GPU_INTERVAL = 10.0  # nvidia-smi can wake a sleeping dGPU, so it is polled slowly
GPU_IDLE_INTERVAL = 60.0  # back off further while the GPU does not answer
TOP_PROCESSES = 8
RESIZE_BORDER = 6  # px along each window edge that starts a resize; the frameless window has no system border
STRESS_DURATIONS = {"30s": 30, "1m": 60, "5m": 300, "30m": 1800, "Indefinite": -1}
MODE_LABELS = {"auto": "Auto", "max": "Max", "manual": "Manual", "curve": "Curve"}
MODE_NOTES = {"auto": "The firmware controls both fans.", "max": "Both fans run at full speed."}
MODE_COLORS = {"auto": "faint", "max": "amber", "manual": "green", "curve": "blue"}
GROUP_TITLES = (
    ("SYSTEM", "Battery & charger"),
    ("COMPONENT", "Component power · overlapping, do not add"),
    ("ACTIVITY", "Activity & cooling"),
)

PALETTE = {
    "bg": "#0c0e13", "card": "#151923", "edge": "#262d3b", "inset": "#0f121a",
    "text": "#e8ebf2", "muted": "#8b94a8", "faint": "#596175",
    "red": "#e5484d", "blue": "#3b8eea", "amber": "#f0a33a", "green": "#3ecf8e", "violet": "#a78bfa",
}

STYLE = Template("""
QMainWindow, QWidget#Root { background: $bg; }
QWidget { color: $text; }
QFrame#Header { background: $card; border-bottom: 1px solid $edge; }
QFrame#Card { background: $card; border: 1px solid $edge; border-radius: 14px; }
QLabel { background: transparent; }
QLabel#AppTitle { font-size: 16px; font-weight: bold; }
QLabel#Caption { color: $muted; font-size: 11px; font-weight: bold; }
QLabel#Muted { color: $muted; }
QLabel#Faint { color: $faint; font-size: 11px; }
QLabel#Big { font-size: 24px; font-weight: bold; }
QLabel#Badge { color: $bg; font-size: 11px; font-weight: bold; border-radius: 9px; padding: 2px 10px; }
QPushButton { background: $inset; border: 1px solid $edge; border-radius: 8px; padding: 7px 14px; }
QPushButton:hover { border-color: $faint; }
QPushButton:disabled { color: $faint; }
QPushButton#Primary { background: $red; border: none; border-radius: 10px; color: white; font-weight: bold; padding: 10px; }
QPushButton#Primary:hover { background: #f0585d; }
QPushButton#Primary:disabled { background: $inset; color: $faint; border: 1px solid $edge; }
QPushButton#Small { padding: 4px 10px; font-size: 12px; }
QFrame#SegBar { background: $inset; border: 1px solid $edge; border-radius: 10px; }
QPushButton#Seg { background: transparent; border: none; border-radius: 7px; color: $muted; font-weight: bold; padding: 6px 4px; }
QPushButton#Seg:hover { color: $text; }
QPushButton#Seg:checked { background: $edge; color: $text; }
QPushButton#Seg:disabled { color: $faint; }
QSlider::groove:horizontal { height: 6px; background: $inset; border: 1px solid $edge; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $red; border-radius: 3px; }
QSlider::handle:horizontal { background: $text; width: 16px; margin: -6px 0; border-radius: 8px; }
QComboBox { background: $inset; border: 1px solid $edge; border-radius: 8px; padding: 4px 10px; min-width: 90px; }
QComboBox QAbstractItemView { background: $card; border: 1px solid $edge; selection-background-color: $edge; }
QCheckBox { color: $muted; spacing: 8px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: $muted; font-weight: bold; padding: 8px 14px; border: none; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: $text; border-bottom: 2px solid $red; }
QTabBar::tab:hover:!selected { color: $text; }
QTreeWidget, QTableWidget { background: $inset; color: $text; alternate-background-color: #121520; border: 1px solid $edge; border-radius: 10px; outline: 0; }
QTreeWidget::item, QTableWidget::item { padding: 3px 6px; }
QHeaderView::section { background: $inset; color: $muted; border: none; border-bottom: 1px solid $edge; padding: 6px; font-weight: bold; }
QStatusBar { background: $card; color: $muted; border-top: 1px solid $edge; }
QStatusBar QLabel { color: $muted; }
QToolTip { background: $card; color: $text; border: 1px solid $edge; padding: 4px; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: $edge; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: transparent; }
QSplitter::handle:hover { background: $edge; }
""").substitute(PALETTE)


def stylesheet() -> str:
    return STYLE


def rgba(color: str, alpha: float) -> str:
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def pwm_to_percent(pwm) -> int:
    return max(0, min(100, round(int(pwm) / 255 * 100)))


def friendly_title(title: str) -> str:
    for prefix, text in GROUP_TITLES:
        if title.startswith(prefix):
            return text
    return title


class PowerSampler(QThread):
    """Runs the power report off the GUI thread. GPU queries have their own, slower, timer."""

    updated = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, interval: float):
        super().__init__()
        self.interval = interval
        self.args = SimpleNamespace(no_gpu=False, top=TOP_PROCESSES)
        self._stopping = threading.Event()
        self._gpu: list[dict] = []

    def stop(self):
        self._stopping.set()
        self.wait(3000)

    def _poll_gpu(self):
        while True:
            self._gpu = power_report.gpu_samples(False)
            # A sleeping GPU is checked through sysfs without waking it, so it is watched at the normal rate.
            idle = all(s["temp"] is None and s["power"] is None and not s.get("asleep") for s in self._gpu)
            if self._stopping.wait(GPU_IDLE_INTERVAL if idle else GPU_INTERVAL):
                return

    def run(self):
        try:
            zones = power_report.find_zones()
            before = power_report.snapshot(zones)
        except Exception as error:
            self.failed.emit(f"Power sensors unavailable: {error}")
            return
        threading.Thread(target=self._poll_gpu, daemon=True).start()
        history = deque([{"time": before["time"], "batteries": before["batteries"]}])
        while not self._stopping.wait(self.interval):
            try:
                after = power_report.snapshot(zones)
                history.append({"time": after["time"], "batteries": after["batteries"]})
                # A rolling battery baseline of at least 30 s gives the coarse energy-based estimate.
                while len(history) > 2 and after["time"] - history[1]["time"] >= 30:
                    history.popleft()
                report = power_report.collect_report(before, after, zones, self.args,
                                                     gpu=self._gpu, battery_before=history[0])
                self.updated.emit(report)
                before = after
            except Exception as error:
                self.failed.emit(f"Power sample failed: {error}")


class LaptopPowerSampler(QThread):
    """Reads the battery and CPU chip counters every second, off the GUI thread: battery reads go through ACPI."""

    updated = pyqtSignal(object)

    def __init__(self, battery_window: float, calibration_window: float):
        super().__init__()
        self.estimator = laptop_power.LaptopPowerEstimator(battery_window, calibration_window)
        self._stopping = threading.Event()

    def stop(self):
        self._stopping.set()
        self.wait(3000)

    def run(self):
        try:
            zones = laptop_power.package_zones()
        except OSError:
            zones = []
        while True:
            try:
                self.updated.emit(self.estimator.update(time.monotonic(), laptop_power.read_sample(zones)))
            except Exception:
                pass  # a failed read is a gap in the graph, which the next second fills
            if self._stopping.wait(LIVE_POWER_INTERVAL):
                return


class Chip(QWidget):
    """Status pill, painted directly so a state change never restyles the window."""

    LEVELS = {"ok": PALETTE["green"], "warn": PALETTE["amber"], "bad": PALETTE["red"], "idle": PALETTE["faint"]}

    def __init__(self):
        super().__init__()
        self.setFixedHeight(26)
        self._text = ""
        self._level = "idle"
        self.set_state("—", "idle")

    def set_state(self, text: str, level: str):
        if (text, level) == (self._text, self._level):
            return
        self._text, self._level = text, level
        self.setFixedWidth(QFontMetrics(self.font()).horizontalAdvance(text) + 34)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(PALETTE["edge"]), 1))
        painter.setBrush(QColor(PALETTE["inset"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 13, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self.LEVELS[self._level]))
        painter.drawEllipse(QPointF(14, self.height() / 2), 4, 4)
        painter.setPen(QColor(PALETTE["text"]))
        painter.drawText(QRectF(26, 0, self.width() - 30, self.height()),
                         Qt.AlignmentFlag.AlignVCenter, self._text)
        painter.end()


class FanGauge(QWidget):
    """Arc gauge for one fan: the arc shows how far the fan is from its maximum speed."""

    def __init__(self, title: str, color: str):
        super().__init__()
        self._title = title
        self._color = QColor(color)
        self._key = None
        self._rpm = 0
        self._percent = 0
        self._reverse = False
        self._big = QFont()
        self._big.setPixelSize(26)
        self._big.setBold(True)
        self._small = QFont()
        self._small.setPixelSize(11)
        self.setMinimumSize(150, 160)

    def set_reading(self, rpm: int, max_rpm: int, reverse: bool):
        percent = min(100, round(100 * rpm / max_rpm)) if max_rpm > 0 else 0
        key = (rpm, percent, reverse)
        if key == self._key:
            return
        self._key = key
        self._rpm, self._percent, self._reverse = rpm, percent, reverse
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height() - 22) - 18
        rect = QRectF((self.width() - side) / 2, 22 + (self.height() - 22 - side) / 2, side, side)
        painter.setFont(self._small)
        painter.setPen(QColor(PALETTE["muted"]))
        painter.drawText(QRectF(0, 0, self.width(), 18), Qt.AlignmentFlag.AlignHCenter, self._title.upper())

        painter.setPen(QPen(QColor(PALETTE["inset"]), 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, 225 * 16, -270 * 16)
        if self._percent:
            painter.setPen(QPen(self._color, 10, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, 225 * 16, -int(270 * 16 * self._percent / 100))

        painter.setFont(self._big)
        painter.setPen(QColor(PALETTE["text"]))
        painter.drawText(QRectF(rect.x(), rect.y() + rect.height() * 0.30, rect.width(), 32),
                         Qt.AlignmentFlag.AlignCenter, f"{self._rpm:,}")
        painter.setFont(self._small)
        painter.setPen(QColor(PALETTE["amber"] if self._reverse else PALETTE["muted"]))
        suffix = " · reverse" if self._reverse else ""
        painter.drawText(QRectF(rect.x(), rect.y() + rect.height() * 0.30 + 32, rect.width(), 18),
                         Qt.AlignmentFlag.AlignCenter, f"RPM · {self._percent}%{suffix}")
        painter.end()


class Sparkline(QWidget):
    """Last minute of samples as a filled line; redrawn only when a new sample arrives."""

    def __init__(self, color: str, samples: int = 60):
        super().__init__()
        self._color = QColor(color)
        self._values = deque(maxlen=samples)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(160, 72)

    def push(self, value):
        if value is not None:
            self._values.append(float(value))
            self.update()

    def paintEvent(self, _event):
        if len(self._values) < 2:
            return
        low, high = min(self._values), max(self._values)
        pad = max((high - low) * 0.1, 0.5)
        low, high = low - pad, high + pad
        width, height = self.width(), self.height()
        step = width / (len(self._values) - 1)
        x0 = 0.0
        path = QPainterPath()
        for index, value in enumerate(self._values):
            x = x0 + index * step
            y = height - 2 - (value - low) / (high - low) * (height - 4)
            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        area = QPainterPath(path)
        area.lineTo(width, height)
        area.lineTo(x0, height)
        area.closeSubpath()
        fade = QLinearGradient(0, 0, 0, height)
        top, bottom = QColor(self._color), QColor(self._color)
        top.setAlpha(90)
        bottom.setAlpha(0)
        fade.setColorAt(0, top)
        fade.setColorAt(1, bottom)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(area, fade)
        painter.setPen(QPen(self._color, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class HistoryChart(QWidget):
    """Last two minutes: both fans as a percentage of max, and CPU temperature on a fixed 30-100 °C scale."""

    TEMP_LOW, TEMP_HIGH = 30, 100

    def __init__(self, samples: int = 120):
        super().__init__()
        self._fans = deque(maxlen=samples)
        self._temps = deque(maxlen=samples)
        self.setMinimumHeight(120)

    def push(self, cpu_percent: int, gpu_percent: int, temp: int):
        self._fans.append((cpu_percent, gpu_percent))
        self._temps.append(temp)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(6, 26, -6, -4)
        painter.setPen(QPen(QColor(PALETTE["edge"]), 1, Qt.PenStyle.DashLine))
        for fraction in (0.25, 0.5, 0.75):
            y = rect.bottom() - fraction * rect.height()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        if len(self._fans) >= 2:
            step = rect.width() / (len(self._fans) - 1)
            x0 = rect.left()
            fans = list(self._fans)
            temps = list(self._temps)
            self._draw_line(painter, rect, x0, step, [cpu for cpu, _ in fans], 0, 100, PALETTE["red"], False)
            self._draw_line(painter, rect, x0, step, [gpu for _, gpu in fans], 0, 100, PALETTE["blue"], False)
            self._draw_line(painter, rect, x0, step, temps, self.TEMP_LOW, self.TEMP_HIGH, PALETTE["amber"], True)
        legend = (("CPU fan", PALETTE["red"]), ("GPU fan", PALETTE["blue"]), ("CPU temp", PALETTE["amber"]))
        x = 8
        painter.setFont(self.font())
        for text, color in legend:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x + 4, 13), 4, 4)
            painter.setPen(QColor(PALETTE["muted"]))
            painter.drawText(QRectF(x + 14, 3, 120, 20), Qt.AlignmentFlag.AlignVCenter, text)
            x += 26 + QFontMetrics(self.font()).horizontalAdvance(text)
        painter.end()

    @staticmethod
    def _draw_line(painter, rect, x0, step, values, low, high, color, dashed):
        path = QPainterPath()
        span = max(high - low, 1)
        for index, value in enumerate(values):
            y = rect.bottom() - (min(high, max(low, value)) - low) / span * rect.height()
            point = QPointF(x0 + index * step, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
        painter.setPen(QPen(QColor(color), 2, style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class PowerChart(QWidget):
    """Last two minutes of laptop power on a scale from 0 W: the live estimate and the battery average.

    A missing reading leaves a gap. Live readings made on an old calibration (on the charger) are dashed.
    """

    SPAN = 120.0  # seconds shown

    def __init__(self, battery_window: float):
        super().__init__()
        self._points = deque()  # (time, live W, battery W, live is estimated)
        self._legend = (("Live · 1 s", PALETTE["amber"]), (f"Battery · {battery_window:g} s avg", PALETTE["blue"]))
        self.setMinimumHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

    def sizeHint(self):
        return QSize(160, 96)

    def push(self, now: float, live, battery, estimated: bool):
        self._points.append((now, live, battery, estimated))
        while self._points and now - self._points[0][0] > self.SPAN:
            self._points.popleft()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        small = QFont(self.font())
        small.setPointSizeF(max(small.pointSizeF() - 1.5, 7))
        painter.setFont(small)
        metrics = QFontMetrics(small)

        x = 0
        for text, color in self._legend:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(QPointF(x + 4, 8), 3.5, 3.5)
            painter.setPen(QColor(PALETTE["muted"]))
            painter.drawText(QRectF(x + 12, 0, 200, 16), Qt.AlignmentFlag.AlignVCenter, text)
            x += 24 + metrics.horizontalAdvance(text)

        rect = QRectF(self.rect()).adjusted(0, 22, -(metrics.horizontalAdvance("88 W") + 6), -2)
        values = [v for _, live, battery, _ in self._points for v in (live, battery) if v is not None]
        top = max(5.0, -(-max(values, default=0) * 1.15 // 5) * 5)  # next 5 W above the peak, at least 5 W
        for fraction in (0.5, 1.0):
            y = rect.bottom() - fraction * rect.height()
            painter.setPen(QPen(QColor(PALETTE["edge"]), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(PALETTE["faint"]))
            painter.drawText(QRectF(rect.right() + 4, y - 8, 40, 16), Qt.AlignmentFlag.AlignVCenter,
                             f"{top * fraction:g} W")
        painter.setPen(QPen(QColor(PALETTE["edge"]), 1))
        painter.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))

        if self._points:
            newest = self._points[-1][0]

            def point(t, watts):
                return QPointF(rect.right() - (newest - t) / self.SPAN * rect.width(),
                               rect.bottom() - min(watts, top) / top * rect.height())

            self._draw(painter, [(t, b, False) for t, _, b, _ in self._points], point, PALETTE["blue"])
            self._draw(painter, [(t, live, est) for t, live, _, est in self._points], point, PALETTE["amber"])
        painter.end()

    @staticmethod
    def _draw(painter, series, point, color):
        """One line, broken at missing readings and wherever the dashed/solid style changes."""
        runs, run, previous = [], [], None
        for t, watts, dashed in series:
            gap = watts is None or (previous is not None and t - previous[0] > 2.5)
            if gap or (previous is not None and dashed != previous[1]):
                if len(run) > 1:
                    runs.append((run, previous[1]))
                run = [] if gap else run[-1:]  # a style change keeps the line joined
            if watts is not None:
                run.append(point(t, watts))
                previous = (t, dashed)
            else:
                previous = None
        if len(run) > 1:
            runs.append((run, previous[1]))
        for points, dashed in runs:
            path = QPainterPath(points[0])
            for p in points[1:]:
                path.lineTo(p)
            style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
            painter.setPen(QPen(QColor(color), 1.8, style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)


class Tile(QFrame):
    """Metric card: caption, big value, a short note and a sparkline (or another chart)."""

    def __init__(self, caption: str, color: str, chart: QWidget | None = None):
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(2)
        title = QLabel(caption)
        title.setObjectName("Caption")
        self.value = QLabel("—")
        self.value.setObjectName("Big")
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        for label in (title, self.value, self.note):
            # Text can be clipped, but it must never widen the layout and make the window jump.
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.spark = chart or Sparkline(color)
        layout.addWidget(title)
        layout.addWidget(self.value)
        layout.addWidget(self.note)
        layout.addSpacing(4)
        layout.addWidget(self.spark, 1)

    def set_values(self, value: str, note: str, series=None):
        self.value.setText(value)
        self.note.setText(note)
        if series is not None:
            self.spark.push(series)


class ReadingsTree(QTreeWidget):
    """Measurements grouped by report section. Rows are updated in place, never rebuilt."""

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(["Measurement", "Value", "Details"])
        self.setColumnWidth(0, 240)
        self.setColumnWidth(1, 120)
        self.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._items: dict[tuple, QTreeWidgetItem] = {}

    def update_sections(self, sections):
        active = set()
        for title, rows in sections:
            group = self._groups.get(title)
            if group is None:
                group = QTreeWidgetItem(self, [friendly_title(title)])
                group.setFirstColumnSpanned(True)
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                group.setExpanded(True)
                self._groups[title] = group
            counts: dict[str, int] = {}
            for label, value, detail in rows:
                occurrence = counts.get(label, 0)
                counts[label] = occurrence + 1
                key = (title, label, occurrence)
                active.add(key)
                item = self._items.get(key)
                if item is None:
                    item = QTreeWidgetItem(group)
                    item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self._items[key] = item
                item.setHidden(False)
                for column, text in enumerate((label.strip(), value, detail)):
                    if item.text(column) != text:
                        item.setText(column, text)
                        item.setToolTip(column, text)
        for key, item in self._items.items():
            if key not in active:
                item.setHidden(True)


class ProcessTable(QTableWidget):
    def __init__(self):
        super().__init__(0, 4)
        self.setHorizontalHeaderLabels(["PID", "Process", "CPU %", "RAM (MiB)"])
        self.verticalHeader().hide()
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def update_rows(self, rows):
        if self.rowCount() != len(rows):
            self.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = self.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    if column != 1:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.setItem(row, column, item)
                if item.text() != text:
                    item.setText(text)


class TitleBar(QFrame):
    """The header is also the title bar: drag it to move the window, double-click it to maximize or restore."""

    def mousePressEvent(self, event):
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.window().toggle_maximized()


class WindowButton(QAbstractButton):
    """Minimize, maximize or restore, and close. Painted here because the window has no system title bar."""

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.restore = False
        self.setToolTip({"minimize": "Minimize", "maximize": "Maximize", "close": "Close"}[kind])
        self.setFixedWidth(46)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hot = self.underMouse() or self.isDown()
        close = self.kind == "close"
        if hot:
            painter.fillRect(self.rect(), QColor(PALETTE["red"] if close else PALETTE["edge"]))
        ink = QColor("#ffffff") if hot and close else QColor(PALETTE["text"] if hot else PALETTE["muted"])
        painter.setPen(QPen(ink, 1.2))
        cx, cy = self.width() / 2, self.height() / 2
        if close:
            painter.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
            painter.drawLine(QPointF(cx - 5, cy + 5), QPointF(cx + 5, cy - 5))
        elif self.kind == "minimize":
            painter.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
        elif self.restore:
            # Two overlapping squares: the back one is hidden under the front one where they meet.
            painter.drawRect(QRectF(cx - 3, cy - 6, 8, 8))
            front = QRectF(cx - 5, cy - 2, 8, 8)
            painter.fillRect(front, QColor(PALETTE["edge"] if hot else PALETTE["card"]))
            painter.drawRect(front)
        else:
            painter.drawRect(QRectF(cx - 5, cy - 5, 10, 10))
        painter.end()


class Dashboard(QMainWindow):
    def __init__(self, controller: FanController | None = None):
        super().__init__()
        self.setWindowTitle("Omen Control")
        # No system frame: the header is the title bar, and the window draws its own buttons and resize edges.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.resize(1240, 820)
        self.setMinimumSize(1100, 720)
        self._resize_shape = None
        QApplication.instance().installEventFilter(self)
        self.ctl = controller or FanController()
        self.is_root = os.geteuid() == 0
        self.has_gpu = self.ctl.has_gpu()
        self.gpu_asleep = power_report.nvidia_gpu_asleep()  # asleep, it has no temperature to drive the curve
        self.board_status, self.board_name = self.ctl.check_board_support()
        self.driver_ok = False
        self.service_running = False
        self.gpu_temp = None
        self.fan_max = (0, 0)
        self.cpu_rpm = 0
        self.gpu_rpm = 0
        self.hold_since = None
        self.temp_history = deque(maxlen=max(1, int(self.ctl.config.get("ma_window", 5))))
        self.applied_mode = "auto"
        self.selected_mode = "auto"
        self.saved_manual = 50
        self.curve_dirty = False
        self.banner_key = None
        self.badge_key = None
        self.sampler = PowerSampler(POWER_INTERVAL)
        cfg = self.ctl.config
        self.battery_window = float(cfg.get("power_battery_window_s", 15))
        self.live_sampler = LaptopPowerSampler(self.battery_window, float(cfg.get("power_calibration_window_s", 60)))

        self._build_ui()
        self.sampler.updated.connect(self._on_power)
        self.sampler.failed.connect(self.statusBar().showMessage)
        self.live_sampler.updated.connect(self._on_laptop_power)

        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._tick)
        self.slow_timer = QTimer(self)
        self.slow_timer.timeout.connect(self._slow_tick)
        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.timeout.connect(self._watchdog)
        self.stress_timer = QTimer(self)
        self.stress_timer.setSingleShot(True)
        self.stress_timer.timeout.connect(lambda: self.stress_btn.setChecked(False))

        self._load_saved()
        self._slow_tick()
        self._tick()
        self.tick_timer.start(1000)
        self.slow_timer.start(5000)
        self.sampler.start()
        self.live_sampler.start()

        geometry = self.frameGeometry()
        geometry.moveCenter(self.screen().availableGeometry().center())
        self.move(geometry.topLeft())

    # ----- layout -----

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(root)
        outer.addWidget(self._build_header())

        body = QVBoxLayout()
        body.setContentsMargins(18, 14, 18, 12)
        body.setSpacing(12)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self.banner.hide()
        body.addWidget(self.banner)
        # The divider between the panes can be dragged; the fan card is the one the user adjusts most.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(16)
        self.splitter.addWidget(self._build_fan_card())
        self.splitter.addWidget(self._build_right_column())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([520, 640])
        body.addWidget(self.splitter, 1)
        outer.addLayout(body, 1)

        self.sample_label = QLabel("Collecting first sample…")
        self.sample_label.setObjectName("Muted")
        self.sample_label.setMinimumWidth(270)  # Fixed widths keep the status bar from shifting as text changes.
        self.clock = QLabel("--:--:--")
        self.clock.setObjectName("Muted")
        self.clock.setMinimumWidth(64)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().addPermanentWidget(self.sample_label)
        self.statusBar().addPermanentWidget(self.clock)

    def _build_header(self):
        header = TitleBar()
        header.setObjectName("Header")
        header.setFixedHeight(64)
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 0, 0, 0)  # No right margin: the window buttons sit flush with the edge.
        row.setSpacing(10)

        logo = QLabel()
        icon = get_assets_dir() / "logo.png"
        if icon.exists():
            logo.setPixmap(QPixmap(str(icon)).scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name = QLabel("Omen Control")
        name.setObjectName("AppTitle")
        subtitle = QLabel("Fan control · power report")
        subtitle.setObjectName("Faint")
        titles.addWidget(name)
        titles.addWidget(subtitle)
        row.addWidget(logo)
        row.addLayout(titles)
        row.addStretch()

        self.chip_service = Chip()
        self.chip_driver = Chip()
        self.chip_power = Chip()
        for chip in (self.chip_service, self.chip_driver, self.chip_power):
            row.addWidget(chip)
        row.addSpacing(10)

        self.sensor_bar = QFrame()
        self.sensor_bar.setObjectName("SegBar")
        bar_row = QHBoxLayout(self.sensor_bar)
        bar_row.setContentsMargins(4, 4, 4, 4)
        bar_row.setSpacing(2)
        self.sensor_group = QButtonGroup(self)
        self.sensor_cpu = QPushButton("CPU")
        self.sensor_gpu = QPushButton("GPU")
        for sensor, button in (("cpu", self.sensor_cpu), ("gpu", self.sensor_gpu)):
            button.setObjectName("Seg")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip("Sensor that drives the fan curve")
            self.sensor_group.addButton(button)
            bar_row.addWidget(button)
            button.clicked.connect(lambda _checked, s=sensor: self._set_reference(s))
        row.addWidget(self.sensor_bar, 0, Qt.AlignmentFlag.AlignVCenter)  # its own height, not the header's 64 px

        self.header_temp = QLabel("— °C")
        self.header_temp.setObjectName("Big")
        self.header_temp.setMinimumWidth(96)
        self.header_temp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.header_temp)
        row.addSpacing(14)
        self.min_btn = WindowButton("minimize")
        self.max_btn = WindowButton("maximize")
        self.close_btn = WindowButton("close")
        self.min_btn.clicked.connect(self.showMinimized)
        self.max_btn.clicked.connect(self.toggle_maximized)
        self.close_btn.clicked.connect(self.close)
        for button in (self.min_btn, self.max_btn, self.close_btn):
            row.addWidget(button)
        return header

    def _build_fan_card(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumWidth(440)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        top = QHBoxLayout()
        caption = QLabel("FAN CONTROL")
        caption.setObjectName("Caption")
        self.badge = QLabel("AUTO")
        self.badge.setObjectName("Badge")
        top.addWidget(caption)
        top.addStretch()
        top.addWidget(self.badge)
        layout.addLayout(top)

        gauges = QHBoxLayout()
        gauges.setSpacing(8)
        self.gauge_cpu = FanGauge("CPU fan", PALETTE["red"])
        self.gauge_gpu = FanGauge("GPU fan", PALETTE["blue"])
        gauges.addWidget(self.gauge_cpu)
        gauges.addWidget(self.gauge_gpu)
        layout.addLayout(gauges)

        seg = QFrame()
        seg.setObjectName("SegBar")
        seg_row = QHBoxLayout(seg)
        seg_row.setContentsMargins(4, 4, 4, 4)
        seg_row.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for mode, text in MODE_LABELS.items():
            button = QPushButton(text)
            button.setObjectName("Seg")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_group.addButton(button)
            seg_row.addWidget(button)
            self.mode_buttons[mode] = button
            button.clicked.connect(lambda _checked, m=mode: self._select_mode(m))
        layout.addWidget(seg)

        self.pages = QStackedWidget()
        self.pages.setMinimumHeight(200)
        self.pages.addWidget(self._page_firmware())
        self.pages.addWidget(self._page_manual())
        self.pages.addWidget(self._page_curve())
        layout.addWidget(self.pages, 1)

        self.apply_btn = QPushButton("Applied")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_btn.clicked.connect(self.apply_selected)
        layout.addWidget(self.apply_btn)

        tools = QHBoxLayout()
        self.watchdog_check = QCheckBox(f"Watchdog · {self.ctl.config.get('watchdog_interval', 90)} s")
        self.watchdog_check.setToolTip("Re-apply the last mode periodically when the service is not running")
        self.watchdog_check.toggled.connect(self._toggle_watchdog)
        tools.addWidget(self.watchdog_check)
        tools.addStretch()
        self.stress_duration = QComboBox()
        self.stress_duration.addItems(list(STRESS_DURATIONS))
        self.stress_btn = QPushButton("Stress CPU")
        self.stress_btn.setObjectName("Small")
        self.stress_btn.setCheckable(True)
        self.stress_btn.toggled.connect(self._toggle_stress)
        tools.addWidget(self.stress_duration)
        tools.addWidget(self.stress_btn)
        layout.addLayout(tools)
        return card

    def _page_firmware(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)
        self.note_label = QLabel()
        self.note_label.setObjectName("Muted")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        self.history = HistoryChart()
        layout.addWidget(self.history, 1)
        return page

    def _page_manual(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 14, 4, 0)
        layout.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(QLabel("Fan speed"))
        head.addStretch()
        self.speed_value = QLabel("50%")
        self.speed_value.setObjectName("Big")
        self.speed_value.setMinimumWidth(90)
        self.speed_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.speed_value)
        layout.addLayout(head)
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.setPageStep(10)
        self.speed_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed_slider.setTickInterval(25)
        self.speed_slider.valueChanged.connect(self._on_slider)
        layout.addWidget(self.speed_slider)
        presets = QHBoxLayout()
        presets.setSpacing(8)
        for percent in (25, 50, 75, 100):
            button = QPushButton(f"{percent}%")
            button.setObjectName("Small")
            button.clicked.connect(lambda _checked, p=percent: self.speed_slider.setValue(p))
            presets.addWidget(button)
        presets.addStretch()
        layout.addLayout(presets)
        self.speed_hint = QLabel()
        self.speed_hint.setObjectName("Muted")
        layout.addWidget(self.speed_hint)
        layout.addStretch()
        return page

    def _page_curve(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        self.curve = FanCurveEditor(points=self.ctl.config.get("curve") or None)
        self.curve.setToolTip("Drag points · right-click to add · middle-click to remove")
        self.curve.curveChanged.connect(self._on_curve_edited)
        layout.addWidget(self.curve, 1)
        row = QHBoxLayout()
        hint = QLabel("Drag points to reshape the curve")
        hint.setObjectName("Faint")
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.setObjectName("Small")
        self.revert_btn.clicked.connect(self._revert_curve)
        default_btn = QPushButton("Default")
        default_btn.setObjectName("Small")
        default_btn.clicked.connect(self._default_curve)
        row.addWidget(hint)
        row.addStretch()
        row.addWidget(self.revert_btn)
        row.addWidget(default_btn)
        layout.addLayout(row)
        return page

    def _build_right_column(self):
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(14)
        tiles = QGridLayout()
        tiles.setSpacing(12)
        self.tile_temp = Tile("CPU TEMPERATURE", PALETTE["red"])
        self.power_chart = PowerChart(self.battery_window)
        self.tile_power = Tile("LAPTOP POWER", PALETTE["amber"], self.power_chart)
        self.tile_power.setToolTip(
            "Live (amber, every second): the CPU chip's reading plus the rest of the laptop, calibrated against the\n"
            "battery over the last minute. Dashed on the charger, where it runs on the last calibration.\n"
            f"Battery (blue): the battery's own energy counter averaged over {self.battery_window:g} s; on battery only.\n"
            "The CPU chip reading in the note is part of the total, not extra.")
        self.tile_busy = Tile("CPU LOAD", PALETTE["green"])
        self.tile_ram = Tile("MEMORY", PALETTE["violet"])
        for index, tile in enumerate((self.tile_temp, self.tile_power, self.tile_busy, self.tile_ram)):
            tiles.addWidget(tile, index // 2, index % 2)
        tiles.setColumnStretch(0, 1)
        tiles.setColumnStretch(1, 1)
        column.addLayout(tiles)

        self.power_tree = ReadingsTree()
        self.system_tree = ReadingsTree()
        self.process_table = ProcessTable()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.power_tree, "Power")
        self.tabs.addTab(self.system_tree, "System")
        self.tabs.addTab(self.process_table, "Processes")
        column.addWidget(self.tabs, 1)

        self.notes = QLabel()
        self.notes.setObjectName("Faint")
        self.notes.setWordWrap(True)
        column.addWidget(self.notes)
        return panel

    # ----- state -----

    def _load_saved(self):
        cfg = self.ctl.config
        mode = cfg.get("mode", "auto")
        self.applied_mode = mode if mode in MODE_LABELS else "auto"
        self.selected_mode = self.applied_mode
        self.saved_manual = pwm_to_percent(cfg["manual_pwm"]) if "manual_pwm" in cfg else 50
        self.speed_slider.setValue(self.saved_manual)
        self._on_slider(self.saved_manual)
        self._show_mode(self.selected_mode)
        self._sync_sensor_buttons()

    def _reference(self) -> str:
        """The sensor the curve actually reads. A sleeping GPU has no temperature, so the CPU stands in for it."""
        sensor = self.ctl.config.get("reference_sensor", "cpu")
        return "gpu" if sensor == "gpu" and self.has_gpu and not self.gpu_asleep else "cpu"

    def _reference_note(self) -> str:
        if self.ctl.config.get("reference_sensor") == "gpu" and self._reference() == "cpu":
            return "Curve reads CPU · GPU is off"
        return f"Curve reads {self._reference().upper()}"

    def _control_temp(self, cpu_temp: int) -> int:
        if self._reference() == "gpu" and self.gpu_temp:
            return self.gpu_temp
        return cpu_temp

    def _select_mode(self, mode: str):
        self.selected_mode = mode
        self._show_mode(mode)
        self._refresh_controls()

    def _show_mode(self, mode: str):
        self.mode_buttons[mode].setChecked(True)
        self.pages.setCurrentIndex({"auto": 0, "max": 0, "manual": 1, "curve": 2}[mode])
        self.note_label.setText(MODE_NOTES.get(mode, ""))

    def _on_slider(self, value: int):
        self.speed_value.setText(f"{value}%")
        cpu_max, _gpu_max = self.fan_max
        if cpu_max > 0:
            self.speed_hint.setText(f"About {round(cpu_max * value / 100):,} RPM on the CPU fan")
        else:
            self.speed_hint.setText("Applies to both fans")
        self._refresh_controls()

    def _on_curve_edited(self, _points):
        self.curve_dirty = True
        self._refresh_controls()

    def _revert_curve(self):
        self.curve.set_points(self.ctl.config.get("curve"))
        self.curve_dirty = False
        self._refresh_controls()

    def _default_curve(self):
        self.curve.set_points(None)
        self.curve_dirty = True
        self._refresh_controls()

    def _refresh_controls(self):
        mode = self.selected_mode
        for button in self.mode_buttons.values():
            button.setEnabled(self.driver_ok)
            button.setToolTip("" if self.driver_ok else "Needs the fan driver")
        pending = (
            mode != self.applied_mode
            or (mode == "manual" and self.speed_slider.value() != self.saved_manual)
            or (mode == "curve" and self.curve_dirty)
        )
        if not self.driver_ok:
            text = "Driver needed"
        elif not self.is_root:
            text = "Run with sudo to apply"
        elif not pending:
            text = "Applied"
        elif mode == "manual":
            text = f"Apply {self.speed_slider.value()}%"
        elif mode == "curve":
            text = "Save curve" if mode == self.applied_mode else "Apply Curve"
        else:
            text = f"Apply {MODE_LABELS[mode]}"
        self.apply_btn.setText(text)
        self.apply_btn.setEnabled(pending and self.driver_ok and self.is_root)
        self.revert_btn.setEnabled(self.curve_dirty)

    def _refresh_badge(self):
        key = self.applied_mode
        if key == self.badge_key:
            return
        self.badge_key = key
        self.badge.setText(MODE_LABELS[key].upper())
        self.badge.setStyleSheet(f"background: {PALETTE[MODE_COLORS[key]]};")

    def _refresh_banner(self):
        notes = []
        if not self.is_root:
            notes.append(("warn", "Read-only: run with sudo to change fan speed. Power readings work without it."))
        if not self.driver_ok:
            notes.append(("bad", "The fan driver is not loaded, so fan control is unavailable. "
                                 "Install it from the full omen-fan-control-gui app."))
        if self.board_status == "POSSIBLY_SUPPORTED" and not self.ctl.config.get("enable_experimental"):
            notes.append(("warn", f"Board {self.board_name} has only experimental support."))
        key = tuple(notes)
        if key == self.banner_key:
            return
        self.banner_key = key
        if not notes:
            self.banner.hide()
            return
        color = PALETTE["red"] if any(level == "bad" for level, _ in notes) else PALETTE["amber"]
        self.banner.setStyleSheet(
            f"QLabel {{ background: {rgba(color, 0.12)}; color: {PALETTE['text']}; "
            f"border: 1px solid {color}; border-radius: 10px; padding: 8px 12px; }}")
        self.banner.setText("\n".join(text for _, text in notes))
        self.banner.show()

    def _sync_sensor_buttons(self):
        reference = self._reference()
        self.sensor_cpu.setChecked(reference == "cpu")
        self.sensor_gpu.setChecked(reference == "gpu")
        self.sensor_gpu.setEnabled(not self.gpu_asleep)
        self.sensor_gpu.setToolTip(
            "The NVIDIA GPU is powered off, so it has no temperature. The curve uses the CPU until the GPU wakes."
            if self.gpu_asleep else "Sensor that drives the fan curve")
        self.sensor_bar.setVisible(self.has_gpu)

    # ----- periodic updates -----

    def _slow_tick(self):
        """Every 5 s: config, driver and service state."""
        self.ctl.config = self.ctl.load_config()
        mode = self.ctl.config.get("mode", "auto")
        if mode in MODE_LABELS:
            self.applied_mode = mode
        if not (self.ctl.pwm1_path and self.ctl.pwm1_path.exists()):
            self.ctl._find_paths()
        self.driver_ok = bool(self.ctl.pwm1_path and self.ctl.pwm1_path.exists())
        self.fan_max = self.ctl.get_effective_fan_limits()
        installed = self.ctl.is_service_installed()
        self.service_running = installed and self.ctl.is_service_running()
        if not installed:
            self.chip_service.set_state("Service off", "idle")
        elif self.service_running:
            self.chip_service.set_state("Service on", "ok")
        else:
            self.chip_service.set_state("Service stopped", "warn")
        self.chip_driver.set_state("Driver loaded" if self.driver_ok else "Driver missing",
                                   "ok" if self.driver_ok else "bad")
        self._sync_sensor_buttons()
        self._refresh_badge()
        self._refresh_banner()
        self._refresh_controls()

    def _tick(self):
        """Every second: fan speeds, temperatures and the local curve loop."""
        (_, cpu_rpm, cpu_reverse), (_, gpu_rpm, gpu_reverse) = self.ctl.get_both_fan_speeds()
        self.cpu_rpm, self.gpu_rpm = cpu_rpm, gpu_rpm
        cpu_max, gpu_max = self.fan_max
        self.gauge_cpu.set_reading(cpu_rpm, cpu_max, cpu_reverse)
        self.gauge_gpu.set_reading(gpu_rpm, gpu_max, gpu_reverse)

        cpu_temp = self.ctl.get_cpu_temp()
        temp = self._control_temp(cpu_temp)
        self.header_temp.setText(f"{temp} °C" if temp else "— °C")
        self.tile_temp.set_values(f"{cpu_temp} °C" if cpu_temp else "—", self._reference_note(), cpu_temp or None)
        cpu_percent = round(100 * cpu_rpm / cpu_max) if cpu_max > 0 else None
        self.curve.set_live(temp or None, cpu_percent)
        gpu_percent = round(100 * gpu_rpm / gpu_max) if gpu_max > 0 else 0
        self.history.push(cpu_percent or 0, gpu_percent, cpu_temp)

        if self.applied_mode == "curve" and not self.service_running and self.driver_ok and temp > 0:
            self._curve_step(temp)
        self.clock.setText(time.strftime("%H:%M:%S"))

    def _curve_step(self, temp: int):
        """Local curve loop, used only when the background service is not running."""
        self.temp_history.append(temp)
        average = sum(self.temp_history) / len(self.temp_history)
        cpu_pwm, gpu_pwm = self.ctl.calculate_dual_target_pwm(average, self.gpu_temp)
        max_rpm = max(self.fan_max)
        current = max(self.cpu_rpm, self.gpu_rpm)
        if max_rpm > 0 and abs(cpu_pwm / 255 * max_rpm - current) < 200:
            # Already close to the target: wait a minute before re-sending to avoid chatter.
            now = time.monotonic()
            if self.hold_since is None:
                self.hold_since = now
            if now - self.hold_since <= 60:
                return
        self.hold_since = None
        self.ctl.set_fan_pwm(cpu_pwm, gpu_pwm)

    def _on_power(self, report: dict):
        metrics = report["metrics"]
        self.gpu_temp = metrics.get("gpu_temp")
        asleep = bool(metrics.get("gpu_asleep"))
        if (self.gpu_temp is not None and not self.has_gpu) or asleep != self.gpu_asleep:
            self.has_gpu = self.has_gpu or self.gpu_temp is not None
            self.gpu_asleep = asleep
            self._sync_sensor_buttons()

        busy = metrics.get("cpu_busy")
        self.tile_busy.set_values(f"{busy:.0f}%" if busy is not None else "—",
                                  f"{os.cpu_count()} logical CPUs", busy)
        used, total = metrics.get("ram_used_gib"), metrics.get("ram_total_gib")
        if used is not None and total:
            percent = 100 * used / total
            self.tile_ram.set_values(f"{used:.1f} GiB", f"of {total:.1f} GiB · {percent:.0f}%", percent)

        external = metrics.get("external_power")
        if external is not None:
            self.chip_power.set_state("On AC" if external else "On battery", "ok" if external else "warn")

        sections = report["sections"]
        self.power_tree.update_sections([s for s in sections if not s[0].startswith("ACTIVITY")])
        self.system_tree.update_sections([s for s in sections if s[0].startswith("ACTIVITY")])
        self.process_table.update_rows(report["processes"])
        self.notes.setText("\n".join(report["notes"]))
        self.sample_label.setText(f"Power sample {report['elapsed']:.1f} s · {report['timestamp'][11:]}")

    def _on_laptop_power(self, reading: dict):
        """Every second. The big number is the live estimate, or the battery average while there is none."""
        live, battery, cpu_w = reading["live_w"], reading["battery_w"], reading["cpu_w"]
        self.power_chart.push(time.monotonic(), live, battery, reading["estimated"])
        if cpu_w is not None:
            cpu = f"CPU chip {cpu_w:.1f} W"
        else:
            cpu = "CPU chip needs sudo" if not self.is_root else "CPU chip: no sensor"
        window = f"{self.battery_window:g} s"
        if not reading["has_battery"]:
            status = "No battery"
        elif reading["on_ac"]:
            status = "On charger · estimated" if live is not None else "On charger · unplug to calibrate"
        elif battery is None:
            status = f"Calibrating ({window})"
        else:
            status = f"Battery {battery:.1f} W ({window})"
        shown = live if live is not None else battery
        self.tile_power.set_values(f"{shown:.1f} W" if shown is not None else "—", f"{status} · {cpu}")

    # ----- window -----

    def toggle_maximized(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self.max_btn.restore = self.isMaximized()
            self.max_btn.setToolTip("Restore" if self.isMaximized() else "Maximize")
            self.max_btn.update()

    def eventFilter(self, obj, event):
        # The frameless window has no system border, so resizing from its edges is handled here for every widget.
        kind = event.type()
        if kind == QEvent.Type.Leave and obj is self:
            self._set_resize_cursor(Qt.Edge(0))
        elif kind in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress) and isinstance(obj, QWidget) \
                and obj.window() is self:
            edges = self._resize_edges(event.globalPosition().toPoint())
            if kind == QEvent.Type.MouseMove:
                self._set_resize_cursor(edges)
            elif edges and event.button() == Qt.MouseButton.LeftButton and self.windowHandle() is not None:
                self.windowHandle().startSystemResize(edges)
                return True
        return super().eventFilter(obj, event)

    def _resize_edges(self, global_pos) -> Qt.Edge:
        edges = Qt.Edge(0)
        if self.isMaximized():
            return edges
        local = self.mapFromGlobal(global_pos)
        x, y = local.x(), local.y()
        if not (0 <= x < self.width() and 0 <= y < self.height()):
            return edges
        if x < RESIZE_BORDER:
            edges |= Qt.Edge.LeftEdge
        elif x >= self.width() - RESIZE_BORDER:
            edges |= Qt.Edge.RightEdge
        if y < RESIZE_BORDER:
            edges |= Qt.Edge.TopEdge
        elif y >= self.height() - RESIZE_BORDER:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _set_resize_cursor(self, edges: Qt.Edge):
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        shape = None
        if horizontal and vertical:
            # Top-left and bottom-right corners share one diagonal, top-right and bottom-left the other.
            same_corner = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
            shape = Qt.CursorShape.SizeFDiagCursor if same_corner else Qt.CursorShape.SizeBDiagCursor
        elif horizontal:
            shape = Qt.CursorShape.SizeHorCursor
        elif vertical:
            shape = Qt.CursorShape.SizeVerCursor
        if shape == self._resize_shape:
            return
        if self._resize_shape is None:
            QApplication.setOverrideCursor(QCursor(shape))
        elif shape is None:
            QApplication.restoreOverrideCursor()
        else:
            QApplication.changeOverrideCursor(QCursor(shape))
        self._resize_shape = shape

    # ----- actions -----

    def apply_selected(self):
        mode = self.selected_mode
        if not (self.driver_ok and self.is_root):
            return
        cfg = self.ctl.config
        cfg["mode"] = mode
        if mode == "manual":
            cfg["manual_pwm"] = round(self.speed_slider.value() / 100 * 255)
            self.saved_manual = self.speed_slider.value()
        elif mode == "curve":
            cfg["curve"] = self.curve.get_points()
        self.ctl.save_config()
        self.applied_mode = mode
        self.curve_dirty = False
        self._refresh_badge()

        if self.ctl.is_service_running():
            self.service_running = True
            self.statusBar().showMessage(f"Saved. The background service is applying {MODE_LABELS[mode]} mode.")
        else:
            self.service_running = False
            self._apply_locally(mode)
        self._refresh_controls()

    def _apply_locally(self, mode: str):
        if mode in ("auto", "max"):
            self.ctl.set_fan_mode(mode)
            self.statusBar().showMessage(f"{MODE_LABELS[mode]} mode applied.")
        elif mode == "manual":
            cpu_pwm, gpu_pwm = self.ctl.get_dual_manual_pwm()
            self.ctl.set_fan_pwm(cpu_pwm, gpu_pwm)
            self.statusBar().showMessage(
                f"Manual speed applied · CPU {round(cpu_pwm / 255 * 100)}% · GPU {round(gpu_pwm / 255 * 100)}%")
        else:
            self.hold_since = None
            self.temp_history.clear()
            self.statusBar().showMessage("Curve running; the temperature is checked every second.")

    def _watchdog(self):
        if self.applied_mode in ("auto", "max", "manual") and self.driver_ok and self.is_root \
                and not self.service_running:
            self._apply_locally(self.applied_mode)

    def _toggle_watchdog(self, checked: bool):
        if checked:
            self.watchdog_timer.start(int(self.ctl.config.get("watchdog_interval", 90)) * 1000)
            self.statusBar().showMessage("Watchdog on: the mode is re-applied periodically.")
        else:
            self.watchdog_timer.stop()
            self.statusBar().showMessage("Watchdog off.")

    def _toggle_stress(self, checked: bool):
        if checked:
            label = self.stress_duration.currentText()
            seconds = STRESS_DURATIONS[label]
            self.ctl.start_stress_test(seconds)
            self.stress_btn.setText("Stop stress")
            if seconds > 0:
                self.stress_timer.start(seconds * 1000)
            self.statusBar().showMessage(f"CPU stress running ({label}).")
        else:
            self.stress_timer.stop()
            self.ctl.stop_stress_test()
            self.stress_btn.setText("Stress CPU")
            self.statusBar().showMessage("CPU stress stopped.")

    def _set_reference(self, sensor: str):
        self.ctl.config["reference_sensor"] = sensor
        self.ctl.save_config()
        self._sync_sensor_buttons()
        self._tick()

    def run_startup_checks(self):
        cfg = self.ctl.config
        if self.board_status != "UNSUPPORTED" or cfg.get("debug_experimental_ui") or cfg.get("bypass_patch_warning"):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Unsupported device")
        box.setText(f"Warning: your board '{self.board_name}' is not in the known compatible list.")
        box.setInformativeText("This application could potentially cause system instability or damage on "
                               "unsupported hardware.\n\nProceed at your own risk?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        dont_show = QCheckBox("Don't show this again")
        box.setCheckBox(dont_show)
        box.exec()
        if box.standardButton(box.clickedButton()) != QMessageBox.StandardButton.Yes:
            QApplication.quit()
            return
        if dont_show.isChecked():
            cfg["bypass_patch_warning"] = True
            self.ctl.save_config()

    def closeEvent(self, event):
        cfg = self.ctl.config
        if cfg.get("cleaner_in_progress") or cfg.get("cleaner_transitioning"):
            running = self.ctl.is_service_running()
            detail = ("The background service will finish the cleaning cycle and restore the fans." if running
                      else "The background service is not running, so closing aborts the cleaning cycle.")
            reply = QMessageBox.question(
                self, "Fan cleaning in progress",
                f"A fan cleaning cycle is in progress.\n\n{detail}\n\nClose anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            if not running:
                self.ctl.emergency_stop_fan_cleaning()
        self.ctl.stop_stress_test()
        for timer in (self.tick_timer, self.slow_timer, self.watchdog_timer, self.stress_timer):
            timer.stop()
        self.sampler.stop()
        self.live_sampler.stop()
        self._set_resize_cursor(Qt.Edge(0))
        QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    if os.name == "posix" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("Error: no display found (DISPLAY or WAYLAND_DISPLAY).")
        print("Under sudo, keep the environment with 'sudo -E'.")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    icon_path = get_assets_dir() / "logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = Dashboard()
    window.show()
    QTimer.singleShot(0, window.run_startup_checks)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
