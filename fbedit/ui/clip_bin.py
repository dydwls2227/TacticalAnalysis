"""왼쪽 클립 목록 — 썸네일과 상태 배지로 한눈에 본다.

위에서부터 순서대로 이어 붙는다. 끌어서 순서를 바꿀 수 있다.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QToolButton, QVBoxLayout, QWidget)

from ..core.session import Session
from . import theme

THUMB = QSize(112, 63)


class _List(QListWidget):
    reordered = Signal()

    def dropEvent(self, ev) -> None:
        super().dropEvent(ev)
        self.reordered.emit()


def _thumbnail(path: str) -> QPixmap:
    pm = QPixmap(THUMB)
    pm.fill(QColor(theme.RAISED))
    try:
        import cv2

        from ..core.video import FrameReader, probe

        info = probe(path)
        with FrameReader(path) as r:
            img = r.frame_at(max(0, info.n_frames // 10))
        if img is not None:
            img = cv2.resize(img, (THUMB.width(), THUMB.height()),
                             interpolation=cv2.INTER_AREA)
            rgb = img[..., ::-1].copy()
            q = QImage(rgb.data, rgb.shape[1], rgb.shape[0], 3 * rgb.shape[1],
                       QImage.Format_RGB888)
            pm = QPixmap.fromImage(q.copy())
    except Exception:
        pass
    return pm


class ClipBin(QWidget):
    clip_selected = Signal(int)
    add_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.session: Session | None = None
        self._thumbs: dict[str, QPixmap] = {}
        self._updating = False

        title = QLabel("클립")
        title.setObjectName("title")
        add = QToolButton()
        add.setIcon(theme.icon("plus"))
        add.setObjectName("ghost")
        add.setToolTip("영상 추가 (⌘O) — 여러 개를 한 번에 고를 수 있습니다")
        add.clicked.connect(self.add_requested.emit)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(add)

        self.list = _List()
        self.list.setIconSize(THUMB)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_row)
        self.list.reordered.connect(self._on_reordered)

        self.empty = QLabel("영상을 여기로\n끌어다 놓거나\n＋ 를 누르세요")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        for name, tip, slot in [("arrow-up", "위로 — 이어 붙일 때 앞으로", lambda: self._move(-1)),
                                ("arrow-down", "아래로", lambda: self._move(1)),
                                ("trash", "이 클립을 목록에서 뺍니다", self._remove)]:
            b = QToolButton()
            b.setIcon(theme.icon(name))
            b.setToolTip(tip)
            b.setObjectName("ghost")
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        self.lbl = QLabel("")
        self.lbl.setObjectName("muted")
        self.lbl.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.addLayout(head)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty, 1)
        lay.addLayout(btns)
        lay.addWidget(self.lbl)

    # ---------------------------------------------------------------- 갱신
    def set_session(self, session: Session | None) -> None:
        self.session = session
        self.refresh()

    def _badge(self, path: str, state: str) -> QPixmap:
        if path not in self._thumbs:
            self._thumbs[path] = _thumbnail(path) if Path(path).exists() else QPixmap(THUMB)
            if not Path(path).exists():
                self._thumbs[path].fill(QColor(theme.RAISED))
        pm = QPixmap(self._thumbs[path])
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        color = {"ready": "#3fb950", "todo": theme.MUTED, "missing": theme.DANGER}[state]
        p.setPen(QColor(0, 0, 0, 160))
        p.setBrush(QColor(color))
        p.drawEllipse(THUMB.width() - 16, 6, 10, 10)
        p.end()
        return pm

    def refresh(self) -> None:
        self._updating = True
        self.list.clear()
        s = self.session
        has = bool(s and s.clips)
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        if s:
            for i, proj in enumerate(s.clips):
                missing = not (proj.video_path and Path(proj.video_path).exists())
                if missing:
                    state, note = "missing", "영상 없음"
                elif proj.trackset is None:
                    state, note = "todo", "분석 전"
                else:
                    n = proj.enabled_count
                    state, note = "ready", f"강조 {n}명" if n else "분석 완료"
                name = Path(proj.video_path).stem if proj.video_path else "(영상 없음)"
                it = QListWidgetItem(QIcon(self._badge(proj.video_path, state)),
                                     f"{i + 1}. {name}\n{note}")
                it.setData(Qt.UserRole, id(proj))
                it.setToolTip(proj.video_path)
                it.setSizeHint(QSize(0, THUMB.height() + 12))
                if missing:
                    it.setForeground(QColor(theme.DANGER))
                self.list.addItem(it)
            self.list.setCurrentRow(s.current)
        self._updating = False
        self._update_label()

    def _update_label(self) -> None:
        s = self.session
        if not s or not s.clips:
            self.lbl.setText("")
            return
        w, h, fps = s.output_spec()
        self.lbl.setText(f"{len(s.clips)}개 · 분석 {len(s.ready_clips)}개\n"
                         f"이어 붙이면 {w}×{h} {fps:.0f}fps")

    # ---------------------------------------------------------------- 동작
    def _on_row(self, row: int) -> None:
        if self._updating or not self.session or row < 0:
            return
        if row != self.session.current:
            self.clip_selected.emit(row)

    def _on_reordered(self) -> None:
        s = self.session
        if not s:
            return
        cur = s.clip
        by_id = {id(p): p for p in s.clips}
        order = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if len(order) != len(s.clips) or set(order) != set(by_id):
            self.refresh()
            return
        s.clips = [by_id[k] for k in order]
        s.current = s.clips.index(cur) if cur in s.clips else 0
        self.refresh()
        self.changed.emit()

    def _move(self, delta: int) -> None:
        s = self.session
        if not s or not s.clips:
            return
        new = s.move(s.current, delta)
        if new != s.current:
            s.current = new
            self.refresh()
            self.changed.emit()

    def _remove(self) -> None:
        s = self.session
        if not s or not s.clips:
            return
        s.remove(s.current)
        self.refresh()
        self.clip_selected.emit(s.current)
        self.changed.emit()

