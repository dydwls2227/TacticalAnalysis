"""하단 타임라인 — 표시 구간을 막대로 보여주고 끌어서 편집한다.

예전에는 `여기부터`/`여기까지` 버튼으로 구간을 만들고 결과는 `40~100, 200~260`
같은 글자로만 보였다. 여기서는 선수·구역·자막을 한 줄씩 막대로 그려
- 막대 양끝을 끌면 시작/끝이 바뀌고
- 막대 가운데를 끌면 길이를 유지한 채 옮겨지고
- 빈 곳을 더블클릭하면 그 자리에 1초짜리 구간이 생기고
- 막대를 우클릭하면 그 구간을 지운다.

구간이 하나도 없는 선수·구역은 "항상 표시" 상태라 전체 길이에 옅은 막대로
그린다. 이 막대 끝을 끌면 그때부터 명시적인 구간이 된다.

가로축은 **결과 영상의 시간**이다. 정지 카드는 실제 길이만큼의 블록으로 자리를
차지하고(그 뒤는 밀린다), 정지 구간 안에서도 막대를 시작·끝낼 수 있다. 정지
블록의 오른쪽 끝을 끌면 멈춤 길이가, 가운데를 끌면 멈추는 장면이 바뀐다 — 뒤쪽
막대들이 함께 밀려야 하므로 손을 뗄 때 한 번에 적용한다.

⌘-클릭(라벨이나 막대)으로 여러 줄을 함께 고를 수 있다. 그 상태에서 한 막대의 끝을
끌면 고른 줄들의 막대가 **같은 만큼** 함께 늘고 줄며, 가운데를 끌면 함께 옮겨진다.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..core.timeline import TimeMap
from . import theme

LABEL_W = 132
RULER_H = 24
ROW_H = 26
BAR_H = 16
EDGE = 6          # 막대 끝 잡기 허용 범위(px)


@dataclass
class Row:
    kind: str          # player | marker | caption | freeze
    obj: object        # PlayerState | Marker | Caption | None(freeze 행)
    label: str
    color: QColor


class Timeline(QWidget):
    seek = Signal(int)
    selected = Signal(str, object, bool)   # kind, obj, 라벨에서 골랐나(→ 그 장면으로)
    multi_changed = Signal(int)            # 함께 고른 줄 수
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.project = None
        self.n = 1                 # 결과 영상 길이(프레임)
        self.n_src = 1             # 원본 영상 길이
        self.fps = 30.0
        self._ghost = None         # 정지 블록을 끄는 중 (인덱스, 시작, 끝)
        self.frame = 0
        self.sel_obj = None
        self.multi: list = []      # ⌘-클릭으로 함께 고른 줄의 객체들 (선수·구역·자막)
        self.rows: list[Row] = []
        self._drag = None          # (mode, row, seg_idx, grab_frame, orig_a, orig_b)
        self._group: list = []     # 함께 움직일 막대 [행, 인덱스, 원래 시작, 원래 끝]
        self._moved = False
        self.range: tuple[int, int] | None = None     # 분석·내보내기 구간
        self._update_height()

    # ---------------------------------------------------------------- 상태
    def set_project(self, project, n_src: int, fps: float) -> None:
        self.project, self.n_src, self.fps = project, max(1, n_src), fps or 30.0
        self.rebuild()

    def set_frame(self, frame: int) -> None:
        if frame != self.frame:
            self.frame = frame
            self.update()

    def set_range(self, a: int, b: int) -> None:
        self.range = (a, b)
        self.update()

    def set_selected(self, obj) -> None:
        """밖에서 고른 항목. 함께 고른 묶음에 들어 있으면 묶음은 그대로 둔다."""
        self.sel_obj = obj
        if obj is None or not self._in_multi(obj):
            self._set_multi([obj] if self._groupable(obj) else [])
        self.update()

    def _in_multi(self, obj) -> bool:
        return any(o is obj for o in self.multi)

    def _groupable(self, obj) -> bool:
        return obj is not None and any(r.obj is obj for r in self.rows)

    def _set_multi(self, objs: list) -> None:
        old = len(self.multi)
        self.multi = objs
        if len(objs) != old:
            self.multi_changed.emit(len(objs))

    def _toggle_multi(self, obj) -> None:
        if self._in_multi(obj):
            self._set_multi([o for o in self.multi if o is not obj])
        else:
            self._set_multi(self.multi + [obj])

    @staticmethod
    def _cmd(ev) -> bool:
        # macOS 의 ⌘ 는 Qt 에서 Control, 실제 Ctrl 키는 Meta 로 들어온다 — 둘 다 받는다
        return bool(ev.modifiers() & (Qt.ControlModifier | Qt.MetaModifier))

    def rebuild(self) -> None:
        rows: list[Row] = []
        p = self.project
        self.n = max(1, p.timemap(self.fps).total(self.n_src)) if p is not None else 1
        if p is not None:
            for ps in sorted((x for x in p.players.values() if x.enabled),
                             key=lambda x: x.order):
                rows.append(Row("player", ps, ps.display, theme.bgr_to_qcolor(ps.style.color)
                                if ps.style.color != (255, 255, 255)
                                else theme.TRACK_COLORS["player"]))
            for m in p.markers:
                rows.append(Row("marker", m, m.display, theme.TRACK_COLORS["marker"]))
            for i, c in enumerate(sorted(p.timeline.captions, key=lambda c: c.start), 1):
                head = c.text.strip().splitlines()[0][:14] if c.text.strip() else f"자막 {i}"
                rows.append(Row("caption", c, head, theme.TRACK_COLORS["caption"]))
            if p.timeline.freezes:
                rows.append(Row("freeze", None, "정지 카드", theme.TRACK_COLORS["freeze"]))
        self.rows = rows
        self._update_height()
        self.update()

    def _update_height(self) -> None:
        self.setFixedHeight(RULER_H + max(1, len(self.rows)) * ROW_H + 8)

    # ---------------------------------------------------------------- 좌표
    def _x(self, f: float) -> float:
        w = max(1, self.width() - LABEL_W - 12)
        return LABEL_W + f / self.n * w

    def _f(self, x: float) -> int:
        w = max(1, self.width() - LABEL_W - 12)
        return int(max(0, min(self.n - 1, round((x - LABEL_W) / w * self.n))))

    def _row_at(self, y: float) -> int | None:
        i = int((y - RULER_H) // ROW_H)
        return i if 0 <= i < len(self.rows) and y >= RULER_H else None

    # ---------------------------------------------------------------- 구간 모델
    def segments(self, row: Row) -> list[tuple[int, int, int | None]]:
        """(시작, 끝(포함), 인덱스). 인덱스 None = 구간 없는 '항상 표시'."""
        last = self.n - 1
        if row.kind in ("player", "marker"):
            if not row.obj.spans:
                return [(0, last, None)]
            return [(a, last if b < 0 else min(b, last), i)
                    for i, (a, b) in enumerate(row.obj.spans)]
        if row.kind == "caption":
            return [(row.obj.start, max(row.obj.start, row.obj.end - 1), 0)]
        if row.kind == "freeze":
            tm = self.project.timemap(self.fps)
            out = []
            for i, fz in enumerate(self.project.timeline.freezes):
                if self._ghost and self._ghost[0] == i:
                    out.append((self._ghost[1], self._ghost[2], i))
                else:
                    o0, o1 = tm.block(fz)
                    out.append((o0, max(o0, o1 - 1), i))
            return out
        return []

    def _set_segment(self, row: Row, idx, a: int, b: int) -> None:
        last = self.n - 1
        a, b = max(0, min(a, last)), max(0, min(b, last))
        if b < a:
            a, b = b, a
        if row.kind in ("player", "marker"):
            end = -1 if b >= last else b
            if idx is None:
                row.obj.spans = [[a, end]]
            else:
                row.obj.spans[idx] = [a, end]
        elif row.kind == "caption":
            row.obj.start, row.obj.end = a, b + 1
        elif row.kind == "freeze":
            self._ghost = (idx, a, b)       # 손을 뗄 때 적용 (_apply_freeze)

    def _apply_freeze(self, mode: str, idx: int, a0: int) -> None:
        """끌던 정지 블록을 실제로 적용한다 — 뒤쪽 구간까지 함께 민다."""
        g, self._ghost = self._ghost, None
        if g is None:
            return
        p, fps = self.project, self.fps
        fz = p.timeline.freezes[idx]
        if mode == "right":
            p.set_freeze_seconds(fz, max(1, g[2] - g[1] + 1) / fps, fps)
            return
        # 멈추는 장면 옮기기: 이 블록을 뺀 시간축에서 새 시작 바로 앞 원본 프레임
        tm = p.timemap(fps)
        o0, o1 = tm.block(fz)
        target = g[1] - 1 if g[1] <= o0 else g[1] - 1 - (o1 - o0)
        others = TimeMap([f for f in p.timeline.freezes if f is not fz], fps)
        fi, inside, _ = others.locate(max(0, target))
        if inside is None and all(f.frame != fi for f in p.timeline.freezes if f is not fz):
            p.move_freeze(fz, min(fi, self.n_src - 1), fps)

    def _delete_segment(self, row: Row, idx) -> None:
        p = self.project
        if row.kind == "player":
            if idx is not None and len(row.obj.spans) > 1:
                del row.obj.spans[idx]
            else:                                   # 마지막 구간 = 강조 끄기
                p.set_enabled(row.obj.tid, False)
                row.obj.spans = []
        elif row.kind == "marker":
            if idx is not None and len(row.obj.spans) > 1:
                del row.obj.spans[idx]
            else:
                p.remove_marker(row.obj.mid)
        elif row.kind == "caption":
            p.timeline.captions.remove(row.obj)
        elif row.kind == "freeze":
            p.remove_freeze(p.timeline.freezes[idx], self.fps)

    def _hit(self, pos) -> tuple[int, int | None, str] | None:
        """(행, 구간 인덱스, 'left'|'right'|'move') — 막대 위가 아니면 None."""
        r = self._row_at(pos.y())
        if r is None or pos.x() < LABEL_W:
            return None
        row = self.rows[r]
        for a, b, idx in self.segments(row):
            x0, x1 = self._x(a), self._x(b + 1)
            if x0 - EDGE <= pos.x() <= x1 + EDGE:
                if row.kind == "freeze":            # 정지 블록: 오른쪽 끝 = 길이, 나머지 = 이동
                    return r, idx, "right" if abs(pos.x() - x1) <= EDGE else "move"
                if abs(pos.x() - x0) <= EDGE:
                    return r, idx, "left"
                if abs(pos.x() - x1) <= EDGE:
                    return r, idx, "right"
                if x0 <= pos.x() <= x1:
                    return r, idx, "move"
        return None

    # ---------------------------------------------------------------- 마우스
    def mousePressEvent(self, ev) -> None:
        pos = ev.position()
        if self.project is None:
            return
        r = self._row_at(pos.y())
        fz = self.project.timeline.freezes
        if pos.x() < LABEL_W:                       # 라벨 = 고르고 그 장면으로
            if r is not None:
                row = self.rows[r]
                if self._cmd(ev) and row.obj is not None:
                    self._toggle_multi(row.obj)     # ⌘-클릭 = 묶음에 넣고 빼기
                    self.update()
                    if self.multi:
                        self.selected.emit(self.rows[r].kind, row.obj, False)
                    return
                obj = fz[0] if row.kind == "freeze" and fz else row.obj
                self._set_multi([obj] if row.obj is not None else [])
                self.selected.emit(row.kind, obj, True)
            return
        hit = self._hit(pos) if r is not None else None
        if hit is None:                             # 빈 곳 = 재생 위치 옮기기
            if ev.button() == Qt.LeftButton:
                self._drag = ("scrub",)
                self.seek.emit(self._f(pos.x()))
            return
        row = self.rows[r]
        _, idx, mode = hit
        if self._cmd(ev) and row.obj is not None and ev.button() == Qt.LeftButton:
            self._toggle_multi(row.obj)
            self.update()
            self.selected.emit(row.kind, row.obj, False)
            return
        # 묶음 안의 막대를 잡으면 묶음 유지, 밖의 막대를 잡으면 그 하나만
        if row.obj is None or not self._in_multi(row.obj):
            self._set_multi([row.obj] if row.obj is not None else [])
        self.selected.emit(row.kind, fz[idx] if row.kind == "freeze" else row.obj, False)
        if ev.button() == Qt.RightButton:
            menu = QMenu(self)
            act = menu.addAction(theme.icon("trash"), "이 구간 지우기")
            if menu.exec(ev.globalPosition().toPoint()) == act:
                self._delete_segment(row, idx)
                self.rebuild()
                self.selected.emit("", None, False)
                self.changed.emit()
            return
        segs = {sg[2]: sg for sg in self.segments(row)}
        a, b, _ = segs[idx]
        self._moved = False
        self._drag = (mode, row, idx, self._f(pos.x()), a, b)
        self._group = self._group_targets(row, mode, a, b) if row.kind != "freeze" else []
        self.seek.emit(self._f(pos.x()))

    def _group_targets(self, prim: Row, mode: str, a: int, b: int) -> list:
        """함께 고른 다른 줄마다, 잡은 막대에 대응하는 막대 [행, 인덱스, 시작, 끝].

        끝을 끌면 끝이 가장 가까운 막대, 시작을 끌면 시작이 가장 가까운 막대,
        가운데를 끌면 가운데가 가장 가까운 막대를 함께 움직인다.
        """
        out = []
        for row in self.rows:
            if row is prim or row.kind == "freeze" or not self._in_multi(row.obj):
                continue
            segs = self.segments(row)
            if not segs:
                continue
            key = {"left": lambda sg: abs(sg[0] - a), "right": lambda sg: abs(sg[1] - b)}.get(
                mode, lambda sg: abs((sg[0] + sg[1]) - (a + b)))
            sa, sb, si = min(segs, key=key)
            out.append([row, si, sa, sb])
        return out

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._drag is None:
            hit = self._hit(pos)
            shape = Qt.ArrowCursor
            if hit:
                shape = Qt.SizeHorCursor if hit[2] in ("left", "right") else Qt.OpenHandCursor
            self.setCursor(shape)
            return
        if self._drag[0] == "scrub":
            self.seek.emit(self._f(pos.x()))
            return
        mode, row, idx, grab, a, b = self._drag
        f = self._f(pos.x())
        if mode == "left":
            na, nb = min(f, b), b
        elif mode == "right":
            na, nb = a, max(f, a)
        else:
            d = f - grab
            d = max(-a, min(self.n - 1 - b, d))
            na, nb = a + d, b + d
        self._moved = True
        self._set_segment(row, idx, na, nb)
        # '항상 표시' 막대를 처음 끌면 명시 구간이 생긴다 — 이후엔 0번 구간으로 잡는다
        if idx is None:
            self._drag = (mode, row, 0, grab, a, b)
        # 함께 고른 막대들 — 같은 만큼(시작 Δ, 끝 Δ) 움직인다. 각자 범위 안으로 자른다.
        da, db = na - a, nb - b
        last = self.n - 1
        for g in self._group:
            grow, gi, ga, gb = g
            if mode == "left":
                ga2, gb2 = min(ga + da, gb), gb
            elif mode == "right":
                ga2, gb2 = ga, max(gb + db, ga)
            else:
                d = max(-ga, min(last - gb, da))
                ga2, gb2 = ga + d, gb + d
            self._set_segment(grow, gi, max(0, ga2), min(last, gb2))
            if gi is None:
                g[1] = 0
        self.update()
        self.seek.emit(na if mode != "right" else nb)

    def mouseReleaseEvent(self, ev) -> None:
        was = self._drag
        self._drag = None
        if was and was[0] != "scrub" and self._moved:
            if was[1].kind == "freeze":
                self._apply_freeze(was[0], was[2], was[4])
            self.rebuild()
            self.changed.emit()

    def mouseDoubleClickEvent(self, ev) -> None:
        pos = ev.position()
        r = self._row_at(pos.y())
        if r is None or pos.x() < LABEL_W or self._hit(pos) is not None:
            return
        row = self.rows[r]
        if row.kind not in ("player", "marker") or not row.obj.spans:
            return
        f = self._f(pos.x())
        row.obj.spans.append([f, min(self.n - 1, f + int(round(self.fps)))])
        row.obj.spans.sort()
        self.rebuild()
        self.changed.emit()

    # ---------------------------------------------------------------- 그리기
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        small = QFont(); small.setPointSize(10)
        p.setFont(small)

        if self.project is None or not self.project.video_path:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "타임라인 — 영상을 추가하면 나타납니다")
            return

        # 눈금자
        p.setPen(QColor(theme.MUTED))
        secs = self.n / self.fps
        step = 1 if secs <= 20 else 2 if secs <= 45 else 5 if secs <= 120 else 10
        t = 0
        while t <= secs:
            x = self._x(t * self.fps)
            p.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H - 1))
            p.drawText(QPointF(x + 3, RULER_H - 9), f"{int(t // 60)}:{int(t % 60):02d}")
            t += step

        # 정지 구간 — 모든 트랙 뒤에 빗금으로 깔아 "여기서 영상이 멈춘다"를 보여준다
        tm = self.project.timemap(self.fps)
        hatch = QBrush(QColor(224, 176, 76, 38), Qt.BDiagPattern)
        for fz in self.project.timeline.freezes:
            o0, o1 = tm.block(fz)
            band = QRectF(self._x(o0), RULER_H, self._x(o1) - self._x(o0),
                          self.height() - RULER_H)
            p.fillRect(band, QColor(224, 176, 76, 22))
            p.fillRect(band, hatch)

        if not self.rows:
            p.drawText(QRectF(LABEL_W, RULER_H, self.width() - LABEL_W, ROW_H),
                       Qt.AlignVCenter,
                       "강조한 선수 · 구역 · 자막이 여기에 막대로 나타납니다")

        for i, row in enumerate(self.rows):
            y = RULER_H + i * ROW_H
            sel = row.obj is not None and (row.obj is self.sel_obj or
                                           (len(self.multi) > 1 and self._in_multi(row.obj)))
            if sel:
                p.fillRect(QRectF(0, y, self.width(), ROW_H), QColor(theme.ACCENT_BG))
                if len(self.multi) > 1 and self._in_multi(row.obj):
                    p.fillRect(QRectF(0, y + 3, 3, ROW_H - 6), QColor(theme.ACCENT))
            # 라벨
            p.setPen(Qt.NoPen)
            p.setBrush(row.color)
            p.drawEllipse(QPointF(14, y + ROW_H / 2), 4, 4)
            p.setPen(QColor(theme.TEXT if sel else theme.MUTED))
            p.drawText(QRectF(24, y, LABEL_W - 28, ROW_H), Qt.AlignVCenter,
                       p.fontMetrics().elidedText(row.label, Qt.ElideRight, LABEL_W - 30))
            # 트랙 바탕
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.RAISED))
            p.drawRoundedRect(QRectF(LABEL_W, y + 4, self.width() - LABEL_W - 12,
                                     ROW_H - 8), 4, 4)
            for a, b, idx in self.segments(row):
                fz = self.project.timeline.freezes[idx] if row.kind == "freeze" else None
                if fz is not None and fz is self.sel_obj:
                    sel = True
                c = QColor(row.color)
                if idx is None:
                    c.setAlpha(70)
                rect = QRectF(self._x(a), y + (ROW_H - BAR_H) / 2,
                              max(3.0, self._x(b + 1) - self._x(a)), BAR_H)
                p.setBrush(c)
                p.setPen(QPen(QColor("white"), 1.2) if sel else Qt.NoPen)
                p.drawRoundedRect(rect, 4, 4)
                if idx is None and rect.width() > 60:
                    p.setPen(QColor(theme.TEXT))
                    p.drawText(rect.adjusted(6, 0, 0, 0), Qt.AlignVCenter, "항상 표시")
                if fz is not None and rect.width() > 34:
                    p.setPen(QColor("#2a2110"))
                    p.drawText(rect.adjusted(6, 0, 0, 0), Qt.AlignVCenter,
                               f"❚❚ {(b - a + 1) / self.fps:.1f}초")
                if row.kind == "freeze":
                    sel = False
                # 손잡이
                if sel and idx is not None:
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor("white"))
                    for hx in (rect.left() + 3, rect.right() - 3):
                        p.drawRoundedRect(QRectF(hx - 1.5, rect.top() + 3, 3,
                                                 rect.height() - 6), 1.5, 1.5)

        # 분석·내보내기 구간 밖은 어둡게
        if self.range is not None:
            a, b = self.range
            dim = QColor(0, 0, 0, 110)
            right = self.width() - 12
            if a > 0:
                p.fillRect(QRectF(LABEL_W, 0, self._x(a) - LABEL_W, self.height()), dim)
            if 0 < b < self.n:
                p.fillRect(QRectF(self._x(b), 0, right - self._x(b), self.height()), dim)

        # 재생 위치
        x = self._x(self.frame)
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(x, 2), QPointF(x, self.height()))
        path = QPainterPath()
        path.addRoundedRect(QRectF(x - 5, 2, 10, 8), 2, 2)
        p.fillPath(path, QColor(theme.ACCENT))
