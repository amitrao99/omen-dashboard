"""Temperature-to-fan-speed curve editor used by the dashboard."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

MAX_TEMP = 105
DEFAULT_CURVE = [(40, 20), (55, 40), (70, 65), (85, 90), (95, 100)]

BACKGROUND = QColor("#10131a")
GRID = QColor("#262d3b")
MUTED = QColor("#8b94a8")
INK = QColor("#e8ebf2")
THEMES = {
    "cpu": (("#ffb020", "#e5484d"), ("#ffb020", "#e5484d")),
    "gpu": (("#00c2ff", "#3b6fe0"), ("#00a6ff", "#3b6fe0")),
}


class FanCurveEditor(QWidget):
    """Drag points to reshape the curve, right-click to add one, middle-click to remove one."""

    curveChanged = pyqtSignal(list)
    LEFT, RIGHT, TOP, BOTTOM = 46, 18, 16, 30

    def __init__(self, points=None):
        super().__init__()
        self.setMinimumSize(340, 168)
        self.setMouseTracking(True)
        self.theme = "cpu"
        self.live = None
        self.hover_index = -1
        self.dragging_index = -1
        self.points: list[QPointF] = []
        self.set_points(points)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BACKGROUND)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        plot = self._plot()
        painter.setPen(QPen(GRID, 1, Qt.PenStyle.DashLine))
        for pct in range(0, 101, 20):
            y = self._to_screen(QPointF(0, pct)).y()
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for temp in range(0, MAX_TEMP + 1, 15):
            x = self._to_screen(QPointF(temp, 0)).x()
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        painter.setPen(MUTED)
        for pct in range(0, 101, 20):
            y = self._to_screen(QPointF(0, pct)).y()
            painter.drawText(QRectF(0, y - 8, self.LEFT - 8, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{pct}%")
        for temp in range(0, MAX_TEMP + 1, 15):
            x = self._to_screen(QPointF(temp, 0)).x()
            painter.drawText(QRectF(x - 20, plot.bottom() + 6, 40, 16),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, f"{temp}°")

        screen = [self._to_screen(p) for p in self.points]
        if screen:
            outline = QPainterPath(QPointF(plot.left(), screen[0].y()))
            for spot in screen:
                outline.lineTo(spot)
            area = QPainterPath(outline)
            area.lineTo(plot.right(), plot.bottom())
            area.lineTo(plot.left(), plot.bottom())
            area.closeSubpath()

            fill_colors, line_colors = THEMES[self.theme]
            fill = QLinearGradient(plot.left(), 0, plot.right(), 0)
            start, end = (QColor(c) for c in fill_colors)
            start.setAlpha(110)
            end.setAlpha(60)
            fill.setColorAt(0, start)
            fill.setColorAt(1, end)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill))
            painter.drawPath(area)

            stroke = QLinearGradient(plot.left(), 0, plot.right(), 0)
            start, end = (QColor(c) for c in line_colors)
            stroke.setColorAt(0, start)
            stroke.setColorAt(1, end)
            painter.setPen(QPen(QBrush(stroke), 3, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(outline)

            for index, spot in enumerate(screen):
                active = index in (self.hover_index, self.dragging_index)
                radius = 6.5 if active else 4.5
                painter.setPen(QPen(BACKGROUND, 2))
                painter.setBrush(QColor("#ff7a7a") if active else INK)
                painter.drawEllipse(spot, radius, radius)

            index = self.dragging_index if self.dragging_index >= 0 else self.hover_index
            if 0 <= index < len(self.points):
                point = self.points[index]
                self._draw_tag(painter, screen[index], f"{point.x():.0f}°C · {point.y():.0f}%", -16)

        if self.live is not None:
            spot = self._to_screen(self.live)
            painter.setPen(QPen(INK, 1.5))
            painter.setBrush(QColor(255, 255, 255, 50))
            painter.drawEllipse(spot, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(INK)
            painter.drawEllipse(spot, 3, 3)
            self._draw_tag(painter, spot, f"now {self.live.x():.0f}°C · {self.live.y():.0f}%", -18)
        painter.end()

    def mousePressEvent(self, event):
        pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging_index = self._point_at(pos, 15)
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            if self._point_at(pos, 12) < 0:
                self._add_point(pos)
        elif event.button() == Qt.MouseButton.MiddleButton:
            index = self._point_at(pos, 15)
            if index >= 0 and len(self.points) > 2:  # Keep at least two points for a valid curve.
                del self.points[index]
                self._finish_edit()

    def mouseMoveEvent(self, event):
        pos = event.position()
        hover = self._point_at(pos, 15)
        if hover != self.hover_index:
            self.hover_index = hover
            self.update()
        if self.dragging_index < 0:
            return

        index = self.dragging_index
        raw = self._to_curve(pos)
        last = len(self.points) - 1
        min_x = self.points[index - 1].x() if index > 0 else 0
        max_x = self.points[index + 1].x() if index < last else MAX_TEMP
        min_y = self.points[index - 1].y() if index > 0 else 0
        max_y = self.points[index + 1].y() if index < last else 100
        x = MAX_TEMP if index == last else min(max_x, max(min_x, raw.x()))
        y = min(max_y, max(min_y, raw.y()))
        self.points[index] = QPointF(x, y)
        self.curveChanged.emit(self.get_points())
        self.update()

    def mouseReleaseEvent(self, _event):
        if self.dragging_index >= 0:
            self.dragging_index = -1
            self.update()

    def leaveEvent(self, _event):
        self.hover_index = -1
        self.update()

    def get_points(self):
        return [(p.x(), p.y()) for p in self.points]

    def set_points(self, points):
        self.points = [QPointF(x, y) for x, y in (points or DEFAULT_CURVE)]
        self._finish_edit(emit=False)

    def set_live(self, temp, percent):
        """Mark where the running system sits on the curve; None hides the marker."""
        live = None if temp is None or percent is None else QPointF(temp, percent)
        if live == self.live:
            return
        self.live = live
        self.update()

    def set_theme(self, theme: str = "cpu"):
        self.theme = theme
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(self.LEFT, self.TOP, self.width() - self.LEFT - self.RIGHT,
                      self.height() - self.TOP - self.BOTTOM)

    def _to_screen(self, point: QPointF) -> QPointF:
        plot = self._plot()
        return QPointF(plot.left() + point.x() / MAX_TEMP * plot.width(),
                       plot.bottom() - point.y() / 100 * plot.height())

    def _to_curve(self, pos: QPointF) -> QPointF:
        plot = self._plot()
        return QPointF((pos.x() - plot.left()) / plot.width() * MAX_TEMP,
                       (plot.bottom() - pos.y()) / plot.height() * 100)

    def _point_at(self, pos: QPointF, radius: float) -> int:
        for index, point in enumerate(self.points):
            spot = self._to_screen(point)
            if (pos.x() - spot.x()) ** 2 + (pos.y() - spot.y()) ** 2 < radius * radius:
                return index
        return -1

    def _add_point(self, pos: QPointF):
        raw = self._to_curve(pos)
        temp = min(MAX_TEMP, max(0, raw.x()))
        pct = min(100, max(0, raw.y()))
        # Keep the new point between its neighbours so the curve stays monotonic.
        prev_y, next_y = 0.0, 100.0
        for point in sorted(self.points, key=lambda p: p.x()):
            if point.x() <= temp:
                prev_y = point.y()
            if point.x() >= temp:
                next_y = point.y()
                break
        self.points.append(QPointF(temp, min(next_y, max(prev_y, pct))))
        self._finish_edit()

    def _finish_edit(self, emit=True):
        self.points.sort(key=lambda p: p.x())
        if self.points and self.points[-1].x() != MAX_TEMP:
            self.points[-1] = QPointF(MAX_TEMP, self.points[-1].y())
        if emit:
            self.curveChanged.emit(self.get_points())
        self.update()

    def _draw_tag(self, painter: QPainter, anchor: QPointF, text: str, dy: float):
        width = painter.fontMetrics().horizontalAdvance(text) + 14
        rect = QRectF(anchor.x() - width / 2, anchor.y() + dy - 11, width, 22)
        rect.moveLeft(max(4, min(rect.left(), self.width() - width - 4)))
        rect.moveTop(max(4, rect.top()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(12, 14, 20, 235))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(INK)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
