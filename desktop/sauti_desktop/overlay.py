"""Small always-on-top status pill near the bottom of the screen.

It never takes focus or mouse input, so the user keeps typing into their own
application while it shows recording, processing and connection state.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

COLORS = {
    "recording": QColor("#e5483b"),
    "processing": QColor("#e6a23c"),
    "done": QColor("#3fb59b"),
    "info": QColor("#8fa3b5"),
    "error": QColor("#ef7d73"),
}


class Overlay(QWidget):
    W, H = 420, 64

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(self.W, self.H)
        self.kind = "info"
        self.title = ""
        self.detail = ""
        self.badge = ""
        self.level = 0.0
        self.started = time.monotonic()
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._anim = QTimer(self, interval=50, timeout=self.update)
        self.enabled = True

    def show_state(self, kind: str, title: str, detail: str = "", hide_after_ms: int = 0) -> None:
        if not self.enabled:
            return
        self.kind, self.title, self.detail = kind, title, detail
        if kind == "recording" and not self._anim.isActive():
            self.started = time.monotonic()
        if kind in ("recording", "processing"):
            self._anim.start()
        else:
            self._anim.stop()
        self._place()
        if not self.isVisible():
            self.show()
        self.update()
        if hide_after_ms:
            self._hide_timer.start(hide_after_ms)
        else:
            self._hide_timer.stop()

    def set_detail(self, text: str) -> None:
        self.detail = text
        self.update()

    def set_level(self, level: float) -> None:
        self.level = 0.7 * self.level + 0.3 * min(1.0, level * 3)

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(QPoint(geo.center().x() - self.W // 2, geo.bottom() - self.H - 48))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1, 1, self.W - 2, self.H - 2)
        path = QPainterPath()
        path.addRoundedRect(rect, self.H / 2 - 1, self.H / 2 - 1)
        p.fillPath(path, QColor(22, 26, 30, 235))
        p.setPen(QColor(255, 255, 255, 30))
        p.drawPath(path)

        color = COLORS.get(self.kind, COLORS["info"])
        cx, cy = 32, self.H / 2
        t = time.monotonic() - self.started
        if self.kind == "recording":
            r = 7 + 5 * self.level + 1.5 * math.sin(t * 6)
            halo = QColor(color)
            halo.setAlpha(70)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QRectF(cx - r - 5, cy - r - 5, 2 * (r + 5), 2 * (r + 5)))
        elif self.kind == "processing":
            r = 8
            p.setPen(Qt.NoPen)
            for i in range(3):
                a = (math.sin(t * 5 - i) + 1) / 2
                c = QColor(color)
                c.setAlphaF(0.35 + 0.65 * a)
                p.setBrush(c)
                p.drawEllipse(QRectF(cx - 14 + i * 10, cy - 3, 6, 6))
            r = 0
        else:
            r = 7
        if r:
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))

        left = 58
        width = self.W - left - 20
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor("#f1f2ef"))
        title = self.title + (f"  ·  {int(t)} s" if self.kind == "recording" else "")
        p.drawText(QRectF(left, 10, width, 22), Qt.AlignLeft | Qt.AlignVCenter, title)
        if self.badge:
            bf = QFont(f)
            bf.setPointSizeF(7.5)
            p.setFont(bf)
            p.setPen(QColor("#e6a23c"))
            p.drawText(QRectF(left, 10, width, 22), Qt.AlignRight | Qt.AlignVCenter, self.badge)
        if self.detail:
            f2 = QFont(self.font())
            f2.setPointSizeF(9)
            p.setFont(f2)
            p.setPen(QColor("#aab4bd"))
            text = QFontMetrics(f2).elidedText(self.detail, Qt.ElideLeft, int(width))
            p.drawText(QRectF(left, 33, width, 20), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.end()
