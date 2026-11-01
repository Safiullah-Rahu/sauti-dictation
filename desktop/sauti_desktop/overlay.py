"""Small always-on-top status pill near the bottom of the screen.

It never takes focus or mouse input, so the user keeps typing into their own
application while it shows recording, processing and connection state.
While recording it draws a live waveform from the microphone level, so it is
obvious at a glance that the microphone is open.
"""
from __future__ import annotations

import time
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QWidget

COLORS = {
    "recording": QColor("#ff5a4e"),
    "processing": QColor("#e8a844"),
    "done": QColor("#3fb59b"),
    "info": QColor("#8fa3b5"),
    "error": QColor("#f08a80"),
}


class Overlay(QWidget):
    W, H = 460, 70

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
        self.chip = ""  # e.g. "Clean · Auto"
        self.level = 0.0
        self.levels: deque[float] = deque([0.0] * 22, maxlen=22)
        self.started = time.monotonic()
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.hide)
        self._anim = QTimer(self, interval=40, timeout=self._tick)
        self.enabled = True

    def show_state(self, kind: str, title: str, detail: str = "", hide_after_ms: int = 0) -> None:
        if not self.enabled:
            return
        if kind == "recording" and self.kind != "recording":
            self.started = time.monotonic()
            self.levels.extend([0.0] * self.levels.maxlen)
        self.kind, self.title, self.detail = kind, title, detail
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
        self.level = 0.6 * self.level + 0.4 * min(1.0, level * 3.2)

    def _tick(self) -> None:
        if self.kind == "recording":
            self.levels.append(self.level)
        self.update()

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(QPoint(geo.center().x() - self.W // 2, geo.bottom() - self.H - 44))

    # -- painting --------------------------------------------------------------------
    def _icon(self, p: QPainter, cx: float, cy: float, color: QColor, t: float) -> None:
        p.setPen(Qt.NoPen)
        if self.kind == "recording":
            # Waveform: bars follow the recent microphone level, newest on the right.
            n = len(self.levels)
            w, gap, x0 = 3.0, 2.0, cx - (n * 5.0) / 2
            p.setBrush(color)
            for i, lv in enumerate(self.levels):
                hgt = 4 + lv * 26
                p.drawRoundedRect(QRectF(x0 + i * (w + gap), cy - hgt / 2, w, hgt), 1.5, 1.5)
            return
        halo = QColor(color)
        halo.setAlpha(46)
        p.setBrush(halo)
        p.drawEllipse(QRectF(cx - 16, cy - 16, 32, 32))
        pen = QPen(color, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if self.kind == "processing":
            start = int((t * 360 * 1.4) % 360) * 16
            p.drawArc(QRectF(cx - 9, cy - 9, 18, 18), -start, 250 * 16)
        elif self.kind == "done":
            path = QPainterPath(QPointF(cx - 6, cy + 0.5))
            path.lineTo(cx - 1.5, cy + 5)
            path.lineTo(cx + 6.5, cy - 4.5)
            p.drawPath(path)
        elif self.kind == "error":
            p.drawLine(QPointF(cx, cy - 6), QPointF(cx, cy + 1.5))
            p.drawPoint(QPointF(cx, cy + 5.5))
        else:
            p.drawLine(QPointF(cx, cy - 1), QPointF(cx, cy + 5.5))
            p.drawPoint(QPointF(cx, cy - 5))

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(1.5, 1.5, self.W - 3, self.H - 3)
        path = QPainterPath()
        path.addRoundedRect(rect, self.H / 2 - 2, self.H / 2 - 2)
        bg = QLinearGradient(0, 0, 0, self.H)
        bg.setColorAt(0, QColor(30, 38, 37, 242))
        bg.setColorAt(1, QColor(18, 24, 23, 242))
        p.fillPath(path, bg)
        color = COLORS.get(self.kind, COLORS["info"])
        edge = QColor(color)
        edge.setAlpha(110 if self.kind == "recording" else 60)
        p.setPen(QPen(edge, 1.2))
        p.drawPath(path)

        t = time.monotonic() - self.started
        icon_w = 118 if self.kind == "recording" else 44
        self._icon(p, 18 + icon_w / 2, self.H / 2, color, time.monotonic())

        left = 22 + icon_w + 8
        right_pad = 20
        f = QFont(self.font())
        f.setPointSizeF(10.5)
        f.setWeight(QFont.DemiBold)
        badge_text = self.badge or self.chip
        bf = QFont(self.font())
        bf.setPointSizeF(7.6)
        bf.setWeight(QFont.DemiBold)
        badge_w = QFontMetrics(bf).horizontalAdvance(badge_text) + 18 if badge_text else 0
        if badge_text:
            br = QRectF(self.W - right_pad - badge_w, 13, badge_w, 18)
            chip_col = QColor("#e8a844") if self.badge else QColor("#3fb59b")
            fill = QColor(chip_col)
            fill.setAlpha(38)
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(br, 9, 9)
            p.setPen(chip_col)
            p.setFont(bf)
            p.drawText(br, Qt.AlignCenter, badge_text)
        width = self.W - left - right_pad - (badge_w + 8 if badge_text else 0)
        p.setFont(f)
        p.setPen(QColor("#f2f5f3"))
        title = self.title + (f"  {int(t) // 60}:{int(t) % 60:02d}" if self.kind == "recording" else "")
        p.drawText(QRectF(left, 11, width, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetrics(f).elidedText(title, Qt.ElideRight, int(width)))
        if self.detail:
            f2 = QFont(self.font())
            f2.setPointSizeF(9)
            p.setFont(f2)
            p.setPen(QColor("#a9b6b2"))
            dw = self.W - left - right_pad
            p.drawText(QRectF(left, 35, dw, 22), Qt.AlignLeft | Qt.AlignVCenter,
                       QFontMetrics(f2).elidedText(self.detail, Qt.ElideLeft, int(dw)))
        p.end()
