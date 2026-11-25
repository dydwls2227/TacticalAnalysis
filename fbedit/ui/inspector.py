"""오른쪽 속성 패널 — 고른 것에 따라 내용이 바뀐다.

예전에는 선수 / 구역 / 자막·정지 세 탭에 모든 설정이 늘 펼쳐져 있었다. 여기서는
- 아무것도 안 골랐으면 이 클립 전체 설정(분석 구간, 강조 모양, 기본 색, 대형선)
- 선수를 고르면 그 선수(이름·색·라인·모양·표시 구간)
- 구역을 고르면 그 구역, 자막·정지 카드를 고르면 그 글자
만 보여준다.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSlider, QSpinBox,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from ..core.formation import MAX_LINES, line_label
from ..core.teams import TEAM_NAMES
from ..core.text import EFFECTS, POSITIONS, available_fonts
from . import theme


# ---------------------------------------------------------------- 작은 부품
def section(text: str) -> QLabel:
    lb = QLabel(text.upper())
    lb.setObjectName("section")
    return lb


def muted(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("muted")
    lb.setWordWrap(True)
    return lb


def slider(lo: int, hi: int, suffix: str = "", scale: float = 1.0):
    """(행 위젯, 슬라이더). 오른쪽에 현재 값을 보여준다."""
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    val = QLabel()
    val.setObjectName("muted")
    val.setFixedWidth(40)
    val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    s.valueChanged.connect(lambda v: val.setText(f"{v * scale:g}{suffix}"))
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(s, 1)
    h.addWidget(val)
    return row, s


def labeled(text: str, w: QWidget) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    lb = QLabel(text)
    lb.setFixedWidth(64)
    h.addWidget(lb)
    h.addWidget(w, 1)
    return row


class Segmented(QWidget):
    """두세 개 중 하나 고르기 — 콤보박스보다 한눈에 보인다."""
    picked = Signal(object)

    def __init__(self, items: list[tuple[object, str]], parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._keys = []
        for i, (key, text) in enumerate(items):
            b = QPushButton(text)
            b.setCheckable(True)
            self.group.addButton(b, i)
            self._keys.append(key)
            h.addWidget(b)
        self.group.idClicked.connect(lambda i: self.picked.emit(self._keys[i]))

    def set(self, key) -> None:
        if key in self._keys:
            self.group.button(self._keys.index(key)).setChecked(True)
        else:
            self.group.setExclusive(False)
            for b in self.group.buttons():
                b.setChecked(False)
            self.group.setExclusive(True)


class Swatches(QWidget):
    """빠른 색 견본 + 직접 고르기."""
    picked = Signal(tuple)          # BGR

    def __init__(self, title: str = "색 선택", parent=None):
        super().__init__(parent)
        self._title = title
        self._cur = None
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self._btns = []
        for bgr in theme.SWATCHES:
            b = QToolButton()
            b.setFixedSize(24, 24)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, c=bgr: self.picked.emit(c))
            self._btns.append((bgr, b))
            h.addWidget(b)
        more = QToolButton()
        more.setFixedSize(24, 24)
        more.setStyleSheet("QToolButton { padding: 0; border-radius: 12px; }")
        more.setIcon(theme.icon("eyedropper"))
        more.setToolTip("다른 색…")
        more.clicked.connect(self._custom)
        h.addWidget(more)
        h.addStretch(1)
        self.set(None)

    def set(self, bgr) -> None:
        self._cur = tuple(bgr) if bgr is not None else None
        for c, b in self._btns:
            q = theme.bgr_to_qcolor(c).name()
            on = self._cur == tuple(c)
            b.setChecked(on)
            b.setStyleSheet(
                f"QToolButton {{ background: {q}; border-radius: 12px; padding: 0;"
                f" border: 2px solid {'#ffffff' if on else theme.BORDER}; }}")

    def _custom(self) -> None:
        start = theme.bgr_to_qcolor(self._cur) if self._cur else QColor("white")
        c = QColorDialog.getColor(start, self, self._title)
        if c.isValid():
            self.picked.emit((c.blue(), c.green(), c.red()))


def scrolled(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidget(widget)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return sa


class Page(QWidget):
    def __init__(self, ins: "Inspector"):
        super().__init__()
        self.ins = ins
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 6, 14, 14)
        self.lay.setSpacing(8)
        self._updating = False

    def add(self, *ws) -> None:
        for w in ws:
            self.lay.addWidget(w)

    def emit(self) -> None:
        if not self._updating:
            self.ins.changed.emit()

    def tc(self, f: int) -> str:
        fps = self.ins.fps or 30.0
        t = f / fps
        return f"{int(t // 60)}:{t % 60:04.1f}"

    def spans_text(self, spans) -> str:
        if not spans:
            return "항상 표시"
        return ", ".join(f"{self.tc(a)}–{'끝' if b < 0 else self.tc(b)}" for a, b in spans)


# ---------------------------------------------------------------- 클립 전체
class ProjectPage(Page):
    def __init__(self, ins):
        super().__init__(ins)
        self.add(muted("영상에서 선수나 구역을 클릭하거나, 아래 타임라인의 막대를 고르면 "
                       "여기서 그 항목을 편집합니다."))

        self.add(section("분석 구간"))
        self.sp_start, self.sp_end = QSpinBox(), QSpinBox()
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        for r, (text, sp, fn) in enumerate([
                ("시작", self.sp_start, lambda: self.sp_start.setValue(self.ins.src_frame)),
                ("끝", self.sp_end, lambda: self.sp_end.setValue(self.ins.src_frame + 1))]):
            sp.setSuffix(" f")
            sp.valueChanged.connect(self._on_range)
            b = QPushButton("현재 위치")
            b.clicked.connect(fn)
            grid.addWidget(QLabel(text), r, 0)
            grid.addWidget(sp, r, 1)
            grid.addWidget(b, r, 2)
        w = QWidget(); w.setLayout(grid); grid.setContentsMargins(0, 0, 0, 0)
        self.lbl_range = muted("")
        self.add(w, self.lbl_range)

        self.add(section("팀 구분"))
        self.lbl_teams = QLabel()
        self.lbl_teams.setWordWrap(True)
        self.lbl_teams.setTextFormat(Qt.RichText)
        self.add(self.lbl_teams)

        self.add(section("강조 모양 (강조한 선수 전체)"))
        self.seg_shape = Segmented([("bar", "기둥형"), ("ring", "링형")])
        self.seg_shape.picked.connect(self._shape)
        self.cb_label = QCheckBox("이름표 표시")
        self.cb_label.toggled.connect(self._labels)
        row, self.sl_alpha = slider(10, 95, "%")
        self.sl_alpha.valueChanged.connect(self._alpha)
        self.add(self.seg_shape, self.cb_label, labeled("진하기", row))

        self.add(section("애니메이션"))
        self.cb_enter = QCheckBox("등장·퇴장 애니메이션")
        self.cb_enter.setToolTip("표시 구간이 시작되면 원판 → 링 → 기둥이 솟아오르고 이름표가\n"
                                 "떠오릅니다. 구간이 끝날 때는 옅어지며 사라집니다.")
        self.cb_loop = QCheckBox("움직이는 효과 (링 회전 · 기둥 빛 흐름)")
        for cb in (self.cb_enter, self.cb_loop):
            cb.toggled.connect(self._anim)
        self.add(self.cb_enter, self.cb_loop,
                 muted("재생(Space)할 때와 내보낸 영상에서 보입니다. 멈춘 화면은 완성된 모습입니다."))

        self.add(section("기본 색"))
        self.sw_theme = Swatches("기본 색")
        self.sw_theme.picked.connect(self._theme)
        self.add(self.sw_theme, muted("모든 선수·대형선에 한 번에 적용됩니다."))

        self.add(section("대형선"))
        self.cb_line = QCheckBox("대형선 표시")
        self.cb_line.toggled.connect(self._formation)
        self.sw_line = Swatches("대형선 색")
        self.sw_line.picked.connect(self._line_color)
        r1, self.sl_w = slider(1, 6, "px")
        r2, self.sl_a = slider(20, 100, "%")
        self.cb_unify = QCheckBox("발밑 링을 대형선 색으로 통일")
        for s in (self.sl_w, self.sl_a):
            s.valueChanged.connect(self._formation)
        self.cb_unify.toggled.connect(self._formation)
        self.add(self.cb_line, self.sw_line, labeled("두께", r1), labeled("진하기", r2),
                 self.cb_unify)

        self.add(section("정리"))
        b_off = QPushButton(theme.icon("eye-slash"), "모든 강조 끄기")
        b_off.clicked.connect(self._all_off)
        b_reset = QPushButton(theme.icon("arrow-counter-clockwise", theme.DANGER),
                              "모든 작업 초기화…")
        b_reset.setObjectName("danger")
        b_reset.clicked.connect(self._reset)
        self.add(b_off, b_reset)
        self.lay.addStretch(1)

    def load(self) -> None:
        p = self.ins.project
        self._updating = True
        n = self.ins.n
        for sp in (self.sp_start, self.sp_end):
            sp.setRange(0, n)
        self.sp_start.setValue(p.export_start)
        self.sp_end.setValue(p.export_end or n)
        self._range_text()
        self._team_text()
        enabled = [ps for ps in p.players.values() if ps.enabled]
        ref = enabled[0].style if enabled else None
        self.seg_shape.set(None if ref is None else ("ring" if ref.ring and not ref.bar else "bar"))
        self.cb_label.setChecked(ref.show_label if ref else True)
        self.sl_alpha.setValue(int((ref.alpha if ref else 0.55) * 100))
        self.sw_theme.set(p.theme_color)
        self.cb_enter.setChecked(p.anim_enter)
        self.cb_loop.setChecked(p.anim_loop)
        f = p.formation
        self.cb_line.setChecked(f.show)
        self.sw_line.set(f.color)
        self.sl_w.setValue(f.thickness)
        self.sl_a.setValue(int(f.alpha * 100))
        self.cb_unify.setChecked(f.unify_ring)
        self._updating = False

    def _team_text(self) -> None:
        ts = self.ins.project.trackset
        if ts is None:
            self.lbl_teams.setText(f"<span style='color:{theme.MUTED}'>분석 후 유니폼 색으로 "
                                   "자동 구분합니다.</span>")
            return
        if not ts.team_colors:
            self.lbl_teams.setText(f"<span style='color:{theme.MUTED}'>이 분석에는 팀 정보가 "
                                   "없습니다. 다시 분석하면 생깁니다.</span>")
            return
        n = {0: 0, 1: 0, 2: 0}
        for tr in ts.tracks.values():
            n[tr.team] = n.get(tr.team, 0) + 1
        parts = []
        for k, c in enumerate(ts.team_colors, 1):
            q = theme.bgr_to_qcolor(c).name()
            parts.append(f"<span style='color:{q}; font-size:16px'>●</span> "
                         f"{TEAM_NAMES[k]} {n.get(k, 0)}명")
        parts.append(f"<span style='color:{theme.MUTED}'>기타 {n[0]}</span>")
        self.lbl_teams.setText("&nbsp;&nbsp;".join(parts))

    def _range_text(self) -> None:
        a, b = self.sp_start.value(), self.sp_end.value()
        fps = self.ins.fps or 30.0
        f = lambda v: f"{int(v / fps // 60)}:{v / fps % 60:04.1f}"
        self.lbl_range.setText(f"원본 {f(a)} – {f(b)} 구간만 분석·내보냅니다.")

    def _on_range(self) -> None:
        if self._updating:
            return
        p = self.ins.project
        if self.sp_end.value() <= self.sp_start.value():
            self._updating = True
            self.sp_end.setValue(min(self.ins.n, self.sp_start.value() + 1))
            self._updating = False
        p.export_start, p.export_end = self.sp_start.value(), self.sp_end.value()
        self._range_text()
        self.emit()

    def _enabled(self):
        return [ps for ps in self.ins.project.players.values() if ps.enabled]

    def _shape(self, key) -> None:
        bar = key == "bar"
        for ps in self._enabled():
            ps.style.bar = ps.style.ellipse = bar
            ps.style.ring = not bar
        self.emit()

    def _labels(self, on: bool) -> None:
        if self._updating:
            return
        for ps in self.ins.project.players.values():
            ps.style.show_label = on
        self.emit()

    def _alpha(self, v: int) -> None:
        if self._updating:
            return
        for ps in self.ins.project.players.values():
            ps.style.alpha = v / 100.0
        self.emit()

    def _anim(self) -> None:
        if self._updating:
            return
        p = self.ins.project
        p.anim_enter, p.anim_loop = self.cb_enter.isChecked(), self.cb_loop.isChecked()
        self.emit()

    def _theme(self, bgr) -> None:
        self.ins.project.apply_theme(bgr)
        self.load()
        self.emit()

    def _line_color(self, bgr) -> None:
        self.ins.project.formation.color = bgr
        self.sw_line.set(bgr)
        self.emit()

    def _formation(self) -> None:
        if self._updating:
            return
        f = self.ins.project.formation
        f.show = self.cb_line.isChecked()
        f.thickness = self.sl_w.value()
        f.alpha = self.sl_a.value() / 100.0
        f.unify_ring = self.cb_unify.isChecked()
        self.emit()

    def _all_off(self) -> None:
        p = self.ins.project
        for tid in list(p.players):
            p.set_enabled(tid, False)
        self.emit()
        self.ins.structure_changed.emit()

    def _reset(self) -> None:
        if QMessageBox.question(
                self, "모든 작업 초기화",
                "강조·이름·라인·표시 구간과 구역·자막·정지 카드를 모두 지웁니다.\n"
                "영상과 분석 결과는 그대로 두며, ⌘Z 로 되돌릴 수 있습니다.\n\n계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.ins.project.reset_edits()
        self.load()
        self.emit()
        self.ins.structure_changed.emit()
        self.ins.status.emit("모든 편집 내용을 초기화했습니다 (⌘Z 로 되돌리기)")


# ---------------------------------------------------------------- 구간 편집 공용
class SpanBox(QWidget):
    """여기부터 / 여기까지 / 항상 표시 + 현재 구간 글자."""

    def __init__(self, page: Page, with_all: bool = False):
        super().__init__()
        self.page = page
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        self.lbl = QLabel()
        self.lbl.setWordWrap(True)
        h = QHBoxLayout()
        h.setSpacing(4)
        self.b_from = QPushButton(theme.icon("arrow-line-right"), "여기부터")
        self.b_to = QPushButton(theme.icon("arrow-line-left"), "여기까지")
        self.b_all = QPushButton("항상")
        self.b_from.setToolTip("현재 위치부터 표시를 시작합니다")
        self.b_to.setToolTip("현재 위치에서 열려 있는 구간을 닫습니다")
        self.b_all.setToolTip("구간을 지우고 영상 내내 표시합니다")
        for b in (self.b_from, self.b_to, self.b_all):
            h.addWidget(b)
        v.addWidget(self.lbl)
        v.addLayout(h)
        self.cb_all = QCheckBox("강조한 모든 선수에 함께 적용")
        self.cb_all.setToolTip("시퀀스가 끝나는 지점에서 [여기까지]를 한 번에 누를 때 씁니다.\n"
                               "이미 닫힌 선수는 건드리지 않습니다.")
        if with_all:
            v.addWidget(self.cb_all)
        v.addWidget(muted("타임라인의 막대 끝을 끌어도 됩니다."))


# ---------------------------------------------------------------- 선수
class PlayerPage(Page):
    def __init__(self, ins):
        super().__init__(ins)
        self.ps = None
        self.txt_num = QLineEdit()
        self.txt_num.setPlaceholderText("번호")
        self.txt_num.setFixedWidth(64)
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText("이름")
        for t in (self.txt_num, self.txt_name):
            t.textEdited.connect(self._names)
        h = QHBoxLayout()
        h.addWidget(self.txt_num)
        h.addWidget(self.txt_name, 1)
        w = QWidget(); w.setLayout(h); h.setContentsMargins(0, 0, 0, 0)
        self.add(section("이름표"), w)

        self.add(section("색"))
        self.sw = Swatches("선수 색")
        self.sw.picked.connect(self._color)
        self.lbl_unify = muted("발밑 링은 '대형선 색으로 통일'이 켜져 있어 대형선 색을 따릅니다.")
        self.add(self.sw, self.lbl_unify)

        self.add(section("대형선 라인"))
        self.cmb_line = QComboBox()
        self.cmb_line.addItem("없음", 0)
        for n in range(1, MAX_LINES + 1):
            self.cmb_line.addItem(line_label(n), n)
        self.cmb_line.currentIndexChanged.connect(self._line)
        self.add(self.cmb_line)

        self.add(section("모양"))
        self.cb_bar = QCheckBox("빛기둥")
        self.cb_ell = QCheckBox("발밑 빛")
        self.cb_ring = QCheckBox("발밑 링")
        self.cb_lbl = QCheckBox("이름표")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        for i, cb in enumerate((self.cb_bar, self.cb_ell, self.cb_ring, self.cb_lbl)):
            cb.toggled.connect(self._style)
            grid.addWidget(cb, i // 2, i % 2)
        w = QWidget(); w.setLayout(grid)
        row, self.sl_alpha = slider(10, 95, "%")
        self.sl_alpha.valueChanged.connect(self._style)
        self.add(w, labeled("진하기", row))

        self.add(section("표시 구간"))
        self.spans = SpanBox(self, with_all=True)
        self.spans.b_from.clicked.connect(lambda: self._span("begin"))
        self.spans.b_to.clicked.connect(lambda: self._span("end"))
        self.spans.b_all.clicked.connect(lambda: self._span("clear"))
        self.add(self.spans)

        self.add(section("팀 · 추적 보정"))
        self.seg_team = Segmented([(1, TEAM_NAMES[1]), (2, TEAM_NAMES[2]), (0, TEAM_NAMES[0])])
        self.seg_team.setToolTip("유니폼 색으로 자동 구분합니다. 틀렸으면 직접 고르세요.\n"
                                 "다른 팀 조각끼리는 이어지지 않습니다.")
        self.seg_team.picked.connect(self._team)
        self.add(self.seg_team)
        fix = QGridLayout()
        fix.setContentsMargins(0, 0, 0, 0)
        fix.setSpacing(4)
        for op, icon, text, tip in [
                ("merge", "git-merge", "합치기…",
                 "끊겨서 다른 번호가 된 같은 선수를 이 선수에 합칩니다.\n"
                 "누른 뒤 영상(또는 타임라인 이름)에서 그 조각을 클릭하세요."),
                ("swap", "arrows-left-right", "여기서부터 맞바꾸기…",
                 "두 선수가 엇갈리며 번호가 뒤바뀌었을 때, 지금 프레임부터 둘을 맞바꿉니다.\n"
                 "누른 뒤 영상에서 상대 선수를 클릭하세요."),
                ("split", "scissors", "여기서 끊기",
                 "추적이 엉뚱한 선수로 넘어갔을 때, 지금 프레임부터를 새 선수로 떼어냅니다.")]:
            b = QPushButton(theme.icon(icon), text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, o=op: self.ins.track_tool.emit(o))
            pos = {"merge": (0, 0, 1, 1), "split": (0, 1, 1, 1), "swap": (1, 0, 1, 2)}[op]
            fix.addWidget(b, *pos)
        w = QWidget(); w.setLayout(fix)
        self.add(w, muted("번호가 끊겼으면 합치기, 엇갈려 바뀌었으면 맞바꾸기, 다른 선수로 "
                          "넘어갔으면 끊기."))

        self.lay.addSpacing(8)
        self.btn_toggle = QPushButton()
        self.btn_toggle.clicked.connect(self._toggle)
        self.add(self.btn_toggle)
        self.lay.addStretch(1)

    def load(self, ps) -> None:
        self.ps = ps
        self._updating = True
        self.txt_num.setText(ps.number)
        self.txt_name.setText(ps.name)
        self.sw.set(ps.style.color)
        self.lbl_unify.setVisible(ps.style.ring and self.ins.project.formation.unify_ring)
        self.cmb_line.setCurrentIndex(max(0, self.cmb_line.findData(ps.line)))
        self.cb_bar.setChecked(ps.style.bar)
        self.cb_ell.setChecked(ps.style.ellipse)
        self.cb_ring.setChecked(ps.style.ring)
        self.cb_lbl.setChecked(ps.style.show_label)
        self.sl_alpha.setValue(int(ps.style.alpha * 100))
        self._update_spans()
        self.btn_toggle.setText("강조 끄기" if ps.enabled else "강조 켜기")
        self.btn_toggle.setIcon(theme.icon("eye-slash" if ps.enabled else "eye"))
        self.btn_toggle.setObjectName("" if ps.enabled else "primary")
        self.btn_toggle.style().unpolish(self.btn_toggle)
        self.btn_toggle.style().polish(self.btn_toggle)
        for w in (self.cmb_line, self.spans):
            w.setEnabled(ps.enabled)
        ts = self.ins.project.trackset
        colors = ts.team_colors if ts else []
        for i, key in enumerate((1, 2, 0)):
            b = self.seg_team.group.button(i)
            c = (theme.bgr_to_qcolor(colors[key - 1]) if 0 < key <= len(colors)
                 else QColor(theme.MUTED))
            b.setIcon(theme.dot_icon(c))
        self.seg_team.set(self.ins.project.team_of(ps.tid))
        self._updating = False

    def _update_spans(self) -> None:
        ps = self.ps
        self.spans.lbl.setText(self.spans_text(ps.spans) if ps.enabled else "강조 꺼짐")

    def _names(self) -> None:
        self.ps.number = self.txt_num.text().strip()
        self.ps.name = self.txt_name.text().strip()
        self.ins.header_changed.emit()
        self.ins.emit_soon()

    def _color(self, bgr) -> None:
        self.ps.style.color = bgr
        self.sw.set(bgr)
        self.emit()
        self.ins.structure_changed.emit()

    def _line(self) -> None:
        if self._updating:
            return
        self.ps.line = self.cmb_line.currentData()
        self.emit()

    def _style(self) -> None:
        if self._updating:
            return
        s = self.ps.style
        s.bar, s.ellipse = self.cb_bar.isChecked(), self.cb_ell.isChecked()
        s.ring, s.show_label = self.cb_ring.isChecked(), self.cb_lbl.isChecked()
        s.alpha = self.sl_alpha.value() / 100.0
        self.emit()

    def _span(self, op: str) -> None:
        f = self.ins.frame
        targets = ([p for p in self.ins.project.players.values() if p.enabled]
                   if self.spans.cb_all.isChecked() else [self.ps])
        for ps in targets:
            {"begin": lambda: ps.span_begin(f), "end": lambda: ps.span_end(f),
             "clear": ps.span_clear}[op]()
        self._update_spans()
        self.emit()
        self.ins.structure_changed.emit()
        if len(targets) > 1:
            self.ins.status.emit(f"{len(targets)}명 전체에 표시 구간을 적용했습니다")

    def _team(self, team: int) -> None:
        if self._updating:
            return
        self.ins.track_edit.emit({"op": "team", "a": self.ps.tid, "team": team})

    def _toggle(self) -> None:
        ps = self.ps
        p = self.ins.project
        if ps.enabled:
            p.set_enabled(ps.tid, False)
            ps.spans = []
        else:
            p.set_enabled(ps.tid, True, line=self.ins.cur_line)
        self.load(ps)
        self.emit()
        self.ins.structure_changed.emit()


# ---------------------------------------------------------------- 구역
class MarkerPage(Page):
    def __init__(self, ins):
        super().__init__(ins)
        self.m = None
        self.txt = QLineEdit()
        self.txt.setPlaceholderText("이름 (비우면 표시 안 함)")
        self.txt.textEdited.connect(self._label)
        self.add(section("이름"), self.txt)

        self.add(section("색"))
        self.sw = Swatches("구역 색")
        self.sw.picked.connect(self._color)
        self.add(self.sw)

        self.add(section("모양"))
        r1, self.sl_r = slider(2, 25, "%")
        r2, self.sl_a = slider(10, 90, "%")
        r3, self.sl_tilt = slider(25, 100, "%")
        self.sl_tilt.setToolTip("잔디에 눕힌 정도 — 낮을수록 납작한 타원 (중계 화면 각도)")
        self.cb_ring = QCheckBox("테두리만")
        for s in (self.sl_r, self.sl_a, self.sl_tilt):
            s.valueChanged.connect(self._style)
        self.cb_ring.toggled.connect(self._style)
        self.add(labeled("크기", r1), labeled("진하기", r2), labeled("원근", r3), self.cb_ring,
                 muted("영상에서 원을 끌어 옮길 수 있습니다."))

        self.add(section("표시 구간"))
        self.spans = SpanBox(self)
        self.spans.b_from.clicked.connect(lambda: self._span("begin"))
        self.spans.b_to.clicked.connect(lambda: self._span("end"))
        self.spans.b_all.clicked.connect(lambda: self._span("clear"))
        self.add(self.spans)

        self.lay.addSpacing(8)
        b = QPushButton(theme.icon("trash", theme.DANGER), "구역 삭제")
        b.setObjectName("danger")
        b.clicked.connect(self._delete)
        self.add(b)
        self.lay.addStretch(1)

    def load(self, m) -> None:
        self.m = m
        self._updating = True
        self.txt.setText(m.label)
        self.sw.set(m.color)
        self.sl_r.setValue(int(round(m.radius * 100)))
        self.sl_a.setValue(int(round(m.alpha * 100)))
        self.sl_tilt.setValue(int(round(m.tilt * 100)))
        self.cb_ring.setChecked(m.ring)
        self.spans.lbl.setText(self.spans_text(m.spans))
        self._updating = False

    def _label(self) -> None:
        self.m.label = self.txt.text().strip()
        self.ins.header_changed.emit()
        self.ins.emit_soon()

    def _color(self, bgr) -> None:
        self.m.color = bgr
        self.sw.set(bgr)
        self.emit()

    def _style(self) -> None:
        if self._updating:
            return
        self.m.radius = self.sl_r.value() / 100.0
        self.m.alpha = self.sl_a.value() / 100.0
        self.m.tilt = self.sl_tilt.value() / 100.0
        self.m.ring = self.cb_ring.isChecked()
        self.emit()

    def _span(self, op: str) -> None:
        f = self.ins.frame
        {"begin": lambda: self.m.span_begin(f), "end": lambda: self.m.span_end(f),
         "clear": self.m.span_clear}[op]()
        self.spans.lbl.setText(self.spans_text(self.m.spans))
        self.emit()
        self.ins.structure_changed.emit()

    def _delete(self) -> None:
        self.ins.project.remove_marker(self.m.mid)
        self.ins.removed.emit()


# ---------------------------------------------------------------- 자막 / 정지 카드
class TextPage(Page):
    def __init__(self, ins):
        super().__init__(ins)
        self.obj = None
        self.kind = ""
        self.txt = QPlainTextEdit()
        self.txt.setFixedHeight(84)
        self.txt.setPlaceholderText("자막 내용")
        self.txt.textChanged.connect(self._text)
        self.add(section("내용"), self.txt)

        self.add(section("시간"))
        self.sp_len = QDoubleSpinBox()
        self.sp_len.setRange(0.2, 60.0)
        self.sp_len.setSingleStep(0.5)
        self.sp_len.setSuffix(" 초")
        self.sp_len.valueChanged.connect(self._apply)
        self.lbl_len = QLabel("길이")
        self.lbl_len.setFixedWidth(64)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.lbl_len)
        h.addWidget(self.sp_len, 1)
        w = QWidget(); w.setLayout(h)
        self.btn_preview = QPushButton(theme.icon("play"), "정지 구간 재생")
        self.btn_preview.clicked.connect(lambda: self.ins.preview.emit(self.obj))
        self.add(w, self.btn_preview)

        self.add(section("글자"))
        self.cmb_font = QComboBox()
        for face in available_fonts():
            self.cmb_font.addItem(face.name, face.name)
        self.cmb_font.currentIndexChanged.connect(self._apply)
        r1, self.sl_size = slider(20, 160, "%", 0.1)
        self.sl_size.valueChanged.connect(self._apply)
        self.sw = Swatches("글자 색")
        self.sw.picked.connect(self._color)
        self.add(labeled("글꼴", self.cmb_font), labeled("크기", r1), self.sw)

        self.seg_pos = Segmented(list(POSITIONS.items()))
        self.seg_pos.picked.connect(self._pos)
        self.add(labeled("위치", self.seg_pos))

        self.add(section("등장 효과"))
        self.cmb_eff = QComboBox()
        for k, v in EFFECTS.items():
            self.cmb_eff.addItem(v, k)
        self.cmb_eff.currentIndexChanged.connect(self._apply)
        self.sp_eff = QDoubleSpinBox()
        self.sp_eff.setRange(0.0, 3.0)
        self.sp_eff.setSingleStep(0.05)
        self.sp_eff.setSuffix(" 초")
        self.sp_eff.valueChanged.connect(self._apply)
        self.add(labeled("효과", self.cmb_eff), labeled("효과 시간", self.sp_eff))

        self.add(section("꾸밈"))
        self.cb_shadow = QCheckBox("그림자")
        self.cb_box = QCheckBox("배경 박스")
        for cb in (self.cb_shadow, self.cb_box):
            cb.toggled.connect(self._apply)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.cb_shadow)
        h.addWidget(self.cb_box)
        h.addStretch(1)
        w = QWidget(); w.setLayout(h)
        r2, self.sl_outline = slider(0, 30, "%")
        self.sl_outline.valueChanged.connect(self._apply)
        self.add(w, labeled("외곽선", r2))

        self.lay.addSpacing(8)
        self.btn_del = QPushButton(theme.icon("trash", theme.DANGER), "삭제")
        self.btn_del.setObjectName("danger")
        self.btn_del.clicked.connect(self._delete)
        self.add(self.btn_del)
        self.lay.addStretch(1)

    def load(self, kind: str, obj) -> None:
        self.kind, self.obj = kind, obj
        self._updating = True
        s = obj.style
        if self.txt.toPlainText() != obj.text:
            self.txt.setPlainText(obj.text)
        i = self.cmb_font.findData(s.font)
        self.cmb_font.setCurrentIndex(i if i >= 0 else 0)
        self.sl_size.setValue(int(round(s.size_pct * 10)))
        self.sw.set(s.color)
        self.seg_pos.set(s.position)
        self.cmb_eff.setCurrentIndex(max(0, self.cmb_eff.findData(s.effect)))
        self.sp_eff.setValue(s.effect_sec)
        self.cb_shadow.setChecked(s.shadow)
        self.cb_box.setChecked(s.box)
        self.sl_outline.setValue(int(round(s.outline * 100)))
        freeze = kind == "freeze"
        self.btn_preview.setVisible(freeze)
        self.lbl_len.setText("멈춤" if freeze else "길이")
        fps = self.ins.fps or 30.0
        self.sp_len.setValue(obj.seconds if freeze
                             else max(0.2, (obj.end - obj.start) / fps))
        self.btn_del.setText("정지 카드 삭제" if freeze else "자막 삭제")
        self._updating = False

    def _text(self) -> None:
        if self._updating:
            return
        self.obj.text = self.txt.toPlainText()
        self.ins.header_changed.emit()
        self.ins.emit_soon(structure=True)

    def _color(self, bgr) -> None:
        self.obj.style.color = bgr
        self.sw.set(bgr)
        self.ins.emit_soon()

    def _pos(self, key) -> None:
        self.obj.style.position = key
        self.ins.emit_soon()

    def _apply(self) -> None:
        if self._updating:
            return
        s = self.obj.style
        s.font = self.cmb_font.currentData() or ""
        s.size_pct = self.sl_size.value() / 10.0
        s.effect = self.cmb_eff.currentData()
        s.effect_sec = self.sp_eff.value()
        s.shadow = self.cb_shadow.isChecked()
        s.box = self.cb_box.isChecked()
        s.outline = self.sl_outline.value() / 100.0
        fps = self.ins.fps or 30.0
        if self.kind == "freeze":
            if abs(self.obj.seconds - self.sp_len.value()) > 1e-6:
                # 멈춤 길이가 바뀌면 뒤쪽 구간·자막이 함께 밀린다
                self.ins.project.set_freeze_seconds(self.obj, self.sp_len.value(), fps)
                self.ins.emit_soon(structure=True)
                return
        else:
            self.obj.end = self.obj.start + max(1, int(round(self.sp_len.value() * fps)))
        self.ins.emit_soon(structure=self.kind == "caption")

    def _delete(self) -> None:
        p = self.ins.project
        if self.kind == "freeze":
            if self.obj in p.timeline.freezes:
                p.remove_freeze(self.obj, self.ins.fps)      # 뒤쪽이 당겨진다
        elif self.obj in p.timeline.captions:
            p.timeline.captions.remove(self.obj)
        self.ins.removed.emit()


# ---------------------------------------------------------------- 패널
class Inspector(QWidget):
    changed = Signal()              # 미리보기 갱신 + 되돌리기 기록
    structure_changed = Signal()    # 타임라인 행/막대가 바뀜
    header_changed = Signal()       # 이름 등 제목만 바뀜
    removed = Signal()              # 고른 항목이 지워짐 → 선택 해제
    preview = Signal(object)        # 정지 카드 미리보기
    track_tool = Signal(str)        # 추적 보정: merge | swap | split
    track_edit = Signal(dict)       # 바로 적용할 보정 (팀 지정)
    status = Signal(str)

    TITLES = {"project": ("이 클립", "sliders-horizontal"),
              "player": ("선수", "user"), "marker": ("구역", "circle-dashed"),
              "caption": ("자막", "chat-text"), "freeze": ("정지 카드", "pause-circle")}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.project = None
        self.n, self.fps, self.frame = 1, 30.0, 0
        self.src_frame = 0          # 원본 프레임 (frame 은 결과 영상 기준)
        self.cur_line = 1
        self.kind, self.obj = "project", None

        self.ico = QLabel()
        self.title = QLabel()
        self.title.setObjectName("title")
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        head = QHBoxLayout()
        head.setContentsMargins(14, 12, 14, 4)
        head.setSpacing(10)
        head.addWidget(self.ico, 0, Qt.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        tv.addWidget(self.title)
        tv.addWidget(self.sub)
        head.addLayout(tv, 1)

        self.pages = {
            "project": ProjectPage(self), "player": PlayerPage(self),
            "marker": MarkerPage(self), "text": TextPage(self),
        }
        self.stack = QStackedWidget()
        self._index = {}
        for k, pg in self.pages.items():
            self._index[k] = self.stack.addWidget(scrolled(pg))

        self.placeholder = muted("영상을 추가하면 여기서 설정을 바꿀 수 있습니다.")
        self.placeholder.setAlignment(Qt.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.placeholder, 1)

        self._soon = QTimer(self)
        self._soon.setSingleShot(True)
        self._soon.setInterval(160)     # 글자를 칠 때마다 다시 그리면 입력이 버벅인다
        self._soon.timeout.connect(self._flush)
        self._soon_structure = False

        self.header_changed.connect(self._update_header)
        self.set_project(None)

    # ---------------------------------------------------------------- 외부
    def set_project(self, project, n_frames: int = 1, fps: float = 30.0) -> None:
        self.project = project
        self.n, self.fps = max(1, n_frames), fps or 30.0
        has = project is not None and bool(project.video_path)
        self.stack.setVisible(has)
        self.placeholder.setVisible(not has)
        self.show_for("project", None)

    def set_frame(self, frame: int, src_frame: int | None = None) -> None:
        self.frame = frame
        self.src_frame = frame if src_frame is None else src_frame

    def show_for(self, kind: str, obj) -> None:
        self._flush()
        if self.project is None or obj is None:
            kind, obj = "project", None
        self.kind, self.obj = kind, obj
        page = "text" if kind in ("caption", "freeze") else kind
        if self.project is not None and self.project.video_path:
            pg = self.pages[page]
            if page == "project":
                pg.load()
            elif page == "text":
                pg.load(kind, obj)
            else:
                pg.load(obj)
        self.stack.setCurrentIndex(self._index[page])
        self._update_header()

    def refresh(self) -> None:
        """밖에서(뷰어 클릭·타임라인 드래그·되돌리기) 값이 바뀌었을 때 다시 읽는다."""
        self.show_for(self.kind, self.obj)

    def emit_soon(self, structure: bool = False) -> None:
        self._soon_structure |= structure
        self._soon.start()

    def _flush(self) -> None:
        if not self._soon.isActive():
            return
        self._soon.stop()
        self.changed.emit()
        if self._soon_structure:
            self.structure_changed.emit()
        self._soon_structure = False

    def _update_header(self) -> None:
        name, icon = self.TITLES[self.kind]
        self.ico.setPixmap(theme.icon(icon, theme.ACCENT).pixmap(22, 22))
        o = self.obj
        if self.kind == "player":
            self.title.setText(o.display)
            self.sub.setText(f"ID {o.tid} · " + (f"{line_label(o.line)} 라인" if o.line
                                                  else "라인 없음"))
        elif self.kind == "marker":
            self.title.setText(o.display)
            self.sub.setText("공간 표시 원")
        elif self.kind in ("caption", "freeze"):
            head = o.text.strip().splitlines()[0][:20] if o.text.strip() else "(내용 없음)"
            self.title.setText(head)
            self.sub.setText(name)
        else:
            self.title.setText(name)
            self.sub.setText("선택한 항목 없음")

