"""영상 뷰어 — 오버레이 미리보기, 마우스 입력, 떠 있는 도구 막대."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from ..core.tracking import Box
from . import theme


class VideoViewer(QWidget):
    """프레임을 비율 유지로 표시하고, 마우스 좌표를 원본 프레임 좌표로 바꿔 알린다."""

    pressed = Signal(float, float)          # 좌클릭 누름 (원본 좌표)
    dragged = Signal(float, float)          # 좌클릭 누른 채 이동
    released = Signal(float, float)
    double_clicked = Signal(float, float)
    right_clicked = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._img: QImage | None = None
        self._src_size = (0, 0)
        self._target = QRect()
        self.boxes: dict[int, Box] = {}
        self.labels: dict[int, str] = {}
        self.teams: dict[int, QColor] = {}     # 선수 → 팀 색 (이름 칩에 점으로)
        self.selected: int | None = None
        self.hover: int | None = None
        self.sel_circle: tuple[float, float, float, float] | None = None  # 원본 x, y, rx, ry
        self.show_boxes = False         # 모든 탐지 박스를 옅게 표시 (⌘B)
        self.hover_enabled = True
        self._down = False

        # 떠 있는 도구 막대 — 버튼은 메인 창이 채운다
        self.toolstrip = QFrame(self)
        self.toolstrip.setObjectName("toolstrip")
        self.toolstrip.setAttribute(Qt.WA_StyledBackground, True)
        self.toolstrip.setStyleSheet(
            "QFrame#toolstrip { background: rgba(22,24,28,0.88);"
            f" border: 1px solid {theme.BORDER}; border-radius: 10px; }}"
            " QFrame#toolstrip QToolButton { background: transparent; border: none;"
            " border-radius: 7px; padding: 6px; }"
            f" QFrame#toolstrip QToolButton:hover {{ background: {theme.RAISED}; }}"
            f" QFrame#toolstrip QToolButton:checked {{ background: {theme.ACCENT}; }}"
            f" QFrame#toolstrip QFrame#sep {{ background: {theme.BORDER}; }}")
        h = QHBoxLayout(self.toolstrip)
        h.setContentsMargins(5, 4, 5, 4)
        h.setSpacing(2)
        self.toolstrip.hide()

    def add_tool(self, w: QWidget) -> None:
        self.toolstrip.layout().addWidget(w)
        self.toolstrip.adjustSize()
        self._place_toolstrip()

    def add_separator(self) -> None:
        s = QFrame()
        s.setObjectName("sep")
        s.setFixedSize(1, 22)
        self.toolstrip.layout().addWidget(s)

    def _place_toolstrip(self) -> None:
        ts = self.toolstrip
        ts.adjustSize()
        ts.move((self.width() - ts.width()) // 2, 10)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_toolstrip()

    # ---------- 입력 ----------
    def set_frame(self, bgr: np.ndarray | None) -> None:
        if bgr is None:
            self._img = None
            self.boxes = {}
        else:
            h, w = bgr.shape[:2]
            rgb = np.ascontiguousarray(bgr[..., ::-1])
            self._img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
            self._src_size = (w, h)
        self.toolstrip.setVisible(self._img is not None)
        self.update()

    def set_boxes(self, boxes: dict[int, Box], selected: int | None,
                  labels: dict[int, str] | None = None,
                  teams: dict[int, QColor] | None = None) -> None:
        self.boxes, self.selected = boxes, selected
        self.labels = labels or {}
        self.teams = teams or {}
        if self.hover not in boxes:
            self.hover = None
        self.update()

    # ---------- 좌표 변환 ----------
    def _to_source(self, pt: QPoint) -> tuple[float, float] | None:
        if self._target.isEmpty() or not self._src_size[0]:
            return None
        if not self._target.contains(pt):
            return None
        sx = self._src_size[0] / self._target.width()
        sy = self._src_size[1] / self._target.height()
        return ((pt.x() - self._target.x()) * sx, (pt.y() - self._target.y()) * sy)

    def _to_source_clamped(self, pt: QPoint) -> tuple[float, float] | None:
        if self._target.isEmpty() or not self._src_size[0]:
            return None
        x = min(max(pt.x(), self._target.left()), self._target.right())
        y = min(max(pt.y(), self._target.top()), self._target.bottom())
        return self._to_source(QPoint(x, y))

    def _to_widget(self, x: float, y: float) -> tuple[float, float]:
        sx = self._target.width() / self._src_size[0]
        sy = self._target.height() / self._src_size[1]
        return (self._target.x() + x * sx, self._target.y() + y * sy)

    def _pick(self, x: float, y: float) -> int | None:
        hits = [(b.w * b.h, tid) for tid, b in self.boxes.items()
                if b.contains(x, y, pad=4)]
        return min(hits)[1] if hits else None

    # ---------- 마우스 ----------
    def mousePressEvent(self, ev) -> None:
        p = self._to_source(ev.position().toPoint())
        if p is None:
            return
        if ev.button() == Qt.LeftButton:
            self._down = True
            self.pressed.emit(*p)
        elif ev.button() == Qt.RightButton:
            self.right_clicked.emit(*p)

    def mouseMoveEvent(self, ev) -> None:
        pt = ev.position().toPoint()
        if self._down:
            p = self._to_source_clamped(pt)
            if p:
                self.dragged.emit(*p)
            return
        if not self.hover_enabled:
            if self.hover is not None:
                self.hover = None
                self.update()
            return
        p = self._to_source(pt)
        h = self._pick(*p) if p else None
        if h != self.hover:
            self.hover = h
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self._down:
            self._down = False
            p = self._to_source_clamped(ev.position().toPoint())
            if p:
                self.released.emit(*p)

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        p = self._to_source(ev.position().toPoint())
        if p:
            self.double_clicked.emit(*p)

    def leaveEvent(self, _ev) -> None:
        if self.hover is not None:
            self.hover = None
            self.update()

    # ---------- 그리기 ----------
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.BG))
        if self._img is None:
            p.setRenderHint(QPainter.Antialiasing)
            box = QRectF(self.width() / 2 - 190, self.height() / 2 - 90, 380, 180)
            p.setPen(QPen(QColor(theme.BORDER), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box, 14, 14)
            ico = theme.icon("film-strip", theme.MUTED).pixmap(40, 40)
            p.drawPixmap(int(box.center().x() - 20), int(box.top() + 30), ico)
            f = QFont(); f.setPointSize(14); f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT))
            p.drawText(box.adjusted(0, 80, 0, -50), Qt.AlignHCenter | Qt.AlignTop,
                       "영상을 끌어다 놓으세요")
            f.setPointSize(11); f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(theme.MUTED))
            p.drawText(box.adjusted(0, 112, 0, -10), Qt.AlignHCenter | Qt.AlignTop,
                       "또는 ⌘O 로 여러 개를 한 번에 추가")
            return

        sw, sh = self._src_size
        scale = min(self.width() / sw, self.height() / sh)
        tw, th = int(sw * scale), int(sh * scale)
        self._target = QRect((self.width() - tw) // 2, (self.height() - th) // 2, tw, th)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self._target, self._img)
        p.setRenderHint(QPainter.Antialiasing)

        if self.sel_circle is not None:
            x, y, r, ry = self.sel_circle
            cx, cy = self._to_widget(x, y)
            k = self._target.width() / sw
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("white"), 1.5, Qt.DashLine))
            p.drawEllipse(QPointF(cx, cy), r * k + 5, ry * k + 5)
            p.setBrush(QColor("white"))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)

        font = QFont(); font.setPointSize(10); p.setFont(font)
        accent = QColor(theme.ACCENT)
        for tid, b in self.boxes.items():
            sel, hov = tid == self.selected, tid == self.hover
            x0, y0 = self._to_widget(b.x1, b.y1)
            x1, y1 = self._to_widget(b.x2, b.y2)
            r = QRectF(x0, y0, x1 - x0, y1 - y0)
            if not (sel or hov):
                if self.show_boxes:
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(QColor(255, 255, 255, 80), 1))
                    p.drawRect(r)
                continue
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(accent if sel else QColor(255, 255, 255, 200), 2 if sel else 1.3))
            p.drawRoundedRect(r, 3, 3)
            text = self.labels.get(tid, f"ID {tid}")
            fm = p.fontMetrics()
            team = self.teams.get(tid)
            dot = 12 if team is not None else 0
            tw_ = fm.horizontalAdvance(text) + 12 + dot
            chip = QRectF(r.center().x() - tw_ / 2, r.bottom() + 6, tw_, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(accent if sel else QColor(20, 22, 26, 220))
            p.drawRoundedRect(chip, 9, 9)
            if team is not None:
                p.setPen(QPen(QColor(255, 255, 255, 160), 1))
                p.setBrush(team)
                p.drawEllipse(QPointF(chip.left() + 11, chip.center().y()), 4, 4)
            p.setPen(QColor("white"))
            p.drawText(chip.adjusted(dot, 0, 0, 0), Qt.AlignCenter, text)
