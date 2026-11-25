"""메인 윈도우.

    ┌ 도구 막대: 영상 추가 · 분석 ▾ · 내보내기 ▾ ── 진행 단계 ── 되돌리기 · 저장 ┐
    │ 클립 목록 │            영상 (+ 떠 있는 도구 막대)            │  속성 패널   │
    │ (썸네일)  │ ▶ 00:12 / 01:30                                 │ (고른 항목에 │
    │           │ 타임라인 — 선수·구역·자막 막대                   │   따라 바뀜) │
    └───────────────────────────────────────────────────────────────────────────┘

도구 모드
  V 선택     — 선수·구역을 골라 오른쪽에서 편집, 구역은 끌어서 이동
  H 선수 강조 — 클릭 = 이 프레임부터 강조 / 보이는 중이면 여기서 끝, 우클릭 = 다음 라인
  Z 구역     — 클릭한 곳에 공간 표시 원
  T / F      — 현재 위치에 자막 / 정지 카드 추가
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QMenu, QMessageBox, QProgressBar,
                               QScrollArea, QSizePolicy, QSplitter, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from ..core.formation import NAMED_LINES, line_label
from ..core.teams import TEAM_NAMES
from ..core.project import Project
from ..core.session import Session
from ..core.text import TextStyle
from ..core.timeline import Caption
from ..core.video import FrameReader, probe
from . import theme
from .clip_bin import ClipBin
from .dialogs import PlayerEditDialog
from .inspector import Inspector
from .timeline import Timeline
from .viewer import VideoViewer
from .workers import (DEFAULT_PRESET, PRESETS, AnalyzeWorker, ConcatExportWorker,
                      ExportWorker)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
VIDEO_FILTER = "영상 (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;모든 파일 (*)"
PROJ_FILTER = "축구 편집 프로젝트 (*.fbproj);;모든 파일 (*)"

TOOLS = {
    "select": ("cursor", "선택", "V",
               "클릭: 선수·구역 고르기 · 더블클릭: 이름/색 · 구역은 끌어서 이동"),
    "highlight": ("user-focus", "선수 강조", "H",
                  "클릭: 이 프레임부터 강조 · 보이는 선수를 다시 클릭: 여기서 끝 · "
                  "우클릭: 다음 라인"),
    "zone": ("circle-dashed", "구역 표시", "Z", "클릭한 곳에 구역 원을 놓습니다 · Esc 취소"),
}
STEPS = ["영상 추가", "분석", "선수 강조", "내보내기"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Football Highlighter")
        self.resize(1480, 900)

        self.session = Session()
        self.project = Project()            # 현재 편집 중인 클립
        self.reader: FrameReader | None = None
        self.info = None
        # 재생 위치는 결과 영상 기준 번호다(정지 카드 구간 포함). 그 순간 보이는 원본
        # 프레임은 cur_src — 정지 구간에서는 여러 번호가 같은 원본 프레임을 가리킨다.
        self.cur_frame = 0
        self.cur_src = 0
        self.cur_line = 1
        # 라인 잇기 — 끄면 강조 모드 클릭이 대형선 라인 없이 강조만 한다
        self.settings = QSettings("FootballHighlighter", "FootballHighlighter")
        self.link_lines = self.settings.value("link_lines", True, type=bool)                   # 강조 모드에서 클릭한 선수가 들어갈 라인
        # 현재 프레임 디코딩 결과. 설정을 바꿀 때마다 같은 프레임을 다시 디코딩하면
        # 수십 ms 가 그냥 날아간다.
        self._frame_cache: tuple[int, object] | None = None
        self.preset = DEFAULT_PRESET
        self.tool = "select"
        self.sel_kind, self.sel_obj = "", None
        self.selected_tid: int | None = None
        self._click_state = None            # 더블클릭 시 되돌릴 클릭 직전 상태
        self._drag_marker = None            # (marker, dx, dy) — 구역 끌어 옮기기
        self._pending = None                # (merge|swap, 선수 ID) — 상대 선수 클릭 대기
        self._team_q: dict = {}             # 선수 ID → 팀 색 (뷰어 표시용 캐시)
        self.worker: AnalyzeWorker | ExportWorker | None = None
        # 전체 분석 — 클립을 한 번에 하나씩 순서대로 돌린다 (큐)
        self._batch_queue: list[int] = []
        self._batch_total = 0
        self._batch_done = 0
        self._batch_failures: list[str] = []
        self._batch_current_idx = -1
        self._batch_resolved = False

        self.setAcceptDrops(True)
        self._build_actions()
        self._build_ui()
        self._build_toolbar()
        self._build_menu()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)

        # 되돌리기 — 편집 상태를 통째로 스냅샷해 쌓는다. 스냅샷이 작아서(선수 수십 명)
        # 이 방식이 개별 명령을 추적하는 것보다 훨씬 단순하고 빠뜨릴 여지가 없다.
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._undo_by_clip: dict[int, tuple[list, list, str | None]] = {}
        self._last_snap: str | None = None
        self._restoring = False
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.setInterval(500)   # 연속 입력은 한 단계로 묶는다
        self._undo_timer.timeout.connect(self._record_undo)

        self.set_tool("select")
        self._busy(False)
        self.statusBar().showMessage("영상을 끌어다 놓거나 ⌘O 로 추가하세요")

    # ================================================================ 구성
    def _act(self, text: str, slot, shortcut=None, icon: str | None = None,
             tip: str | None = None) -> QAction:
        a = QAction(text, self)
        if shortcut is not None:
            a.setShortcut(shortcut)
        if icon:
            a.setIcon(theme.icon(icon))
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)       # 메뉴에 없어도 단축키가 동작하도록
        return a

    def _build_actions(self) -> None:
        A = self._act
        self.act_add = A("영상 추가…", self.add_videos, QKeySequence.Open, "plus")
        self.act_new = A("새 세션", self.new_session, "Ctrl+N")
        self.act_open = A("프로젝트 열기…", self.open_project, "Ctrl+Shift+O")
        self.act_save = A("프로젝트 저장", self.save_project, QKeySequence.Save,
                          "floppy-disk", "프로젝트 저장 (⌘S)")
        self.act_analyze = A("이 클립 분석", self.analyze, "Ctrl+R", "magic-wand")
        self.act_analyze_all = A("모든 클립 분석", self.analyze_all, "Ctrl+Alt+R", "stack")
        self.act_export = A("이 클립 내보내기…", self.export, "Ctrl+E", "film-strip")
        self.act_export_all = A("전체 이어붙여 내보내기…", self.export_all,
                                "Ctrl+Shift+E", "queue")
        self.act_export_raw = A("분석 없이 그냥 이어붙이기…", self.export_all_raw,
                                "Ctrl+Alt+E")
        self.act_undo = A("실행 취소", self.undo, QKeySequence.Undo, "arrow-u-up-left",
                          "실행 취소 (⌘Z)")
        self.act_redo = A("다시 실행", self.redo, QKeySequence.Redo, "arrow-u-up-right",
                          "다시 실행 (⇧⌘Z)")
        self.act_delete = A("선택 항목 삭제", self.delete_selected, QKeySequence.Delete)
        self.act_delete.setShortcuts([QKeySequence.Delete, QKeySequence(Qt.Key_Backspace)])

        self.act_play = A("재생 / 일시정지", self.toggle_play, Qt.Key_Space)
        self.act_prev = A("이전 프레임", lambda: self._step(-1), Qt.Key_Left)
        self.act_next = A("다음 프레임", lambda: self._step(1), Qt.Key_Right)
        self.act_back = A("1초 뒤로", lambda: self._step(-self._fps()), "Shift+Left")
        self.act_fwd = A("1초 앞으로", lambda: self._step(self._fps()), "Shift+Right")
        self.act_boxes = A("모든 탐지 박스 표시", self._toggle_boxes, "Ctrl+B")
        self.act_boxes.setCheckable(True)

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_acts: dict[str, QAction] = {}
        for key, (icon, text, sc, _hint) in TOOLS.items():
            a = A(text, lambda _=False, k=key: self.set_tool(k), sc, icon,
                  f"{text} ({sc})")
            a.setCheckable(True)
            self.tool_group.addAction(a)
            self.tool_acts[key] = a
        self.act_link = A("라인 잇기 켜기/끄기", lambda: self.set_link_lines(not self.link_lines),
                          "L")
        self.act_caption = A("자막 추가", self.add_caption, "T", "text-t",
                             "현재 위치에 자막 추가 (T)")
        self.act_freeze = A("정지 카드 추가", self.add_freeze, "F", "pause-circle",
                            "현재 위치에서 멈추고 자막 띄우기 (F)")

        self.preset_group = QActionGroup(self)
        self.preset_acts = []
        for name in PRESETS:
            a = QAction(name, self, checkable=True, checked=name == self.preset)
            a.triggered.connect(lambda _=False, n=name: setattr(self, "preset", n))
            self.preset_group.addAction(a)
            self.preset_acts.append(a)

    def _build_ui(self) -> None:
        # ---- 영상
        self.viewer = VideoViewer()
        self.viewer.pressed.connect(self._on_viewer_pressed)
        self.viewer.dragged.connect(self._on_viewer_dragged)
        self.viewer.released.connect(self._on_viewer_released)
        self.viewer.double_clicked.connect(self._on_viewer_double_clicked)
        self.viewer.right_clicked.connect(self._on_viewer_right_clicked)
        for key in TOOLS:
            self.viewer.add_tool(self._tool_button(self.tool_acts[key]))
        self.viewer.add_separator()
        self.btn_link = QToolButton()
        self.btn_link.setCheckable(True)
        self.btn_link.setChecked(self.link_lines)
        self.btn_link.setToolTip("라인 잇기 (L)\n켜면 클릭한 선수를 대형선으로 잇습니다.\n"
                                 "끄면 강조만 하고 선은 긋지 않습니다.")
        self.btn_link.toggled.connect(self.set_link_lines)
        self.viewer.add_tool(self.btn_link)
        self.btn_line = QToolButton()
        self.btn_line.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_line.setIcon(theme.icon("line-segments"))
        self.btn_line.setToolTip("강조 모드에서 클릭한 선수가 이어질 대형선 라인\n"
                                 "누르거나 영상에서 우클릭하면 다음 라인으로")
        self.btn_line.clicked.connect(self.start_new_line)
        self.viewer.add_tool(self.btn_line)
        self.viewer.add_separator()
        self.viewer.add_tool(self._tool_button(self.act_caption))
        self.viewer.add_tool(self._tool_button(self.act_freeze))

        # ---- 재생 막대
        self.btn_prev = self._ghost("caret-left", "이전 프레임 (←)", lambda: self._step(-1))
        self.btn_play = self._ghost("play", "재생 / 일시정지 (Space)", self.toggle_play)
        self.btn_play.setIconSize(QSize(20, 20))
        self.btn_next = self._ghost("caret-right", "다음 프레임 (→)", lambda: self._step(1))
        self.lbl_time = QLabel("--:-- / --:--")
        self.lbl_time.setStyleSheet("font-family: Menlo, monospace; font-size: 12px;")
        self.lbl_hint = QLabel()
        self.lbl_hint.setObjectName("muted")
        transport = QHBoxLayout()
        transport.setContentsMargins(6, 4, 10, 2)
        for w in (self.btn_prev, self.btn_play, self.btn_next):
            transport.addWidget(w)
        transport.addSpacing(6)
        transport.addWidget(self.lbl_time)
        transport.addSpacing(18)
        transport.addWidget(self.lbl_hint, 1)

        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        tv.addWidget(self.viewer, 1)
        tv.addLayout(transport)

        # ---- 타임라인
        self.timeline = Timeline()
        self.timeline.seek.connect(self._on_timeline_seek)
        self.timeline.selected.connect(self._on_timeline_selected)
        self.timeline.changed.connect(self._on_timeline_changed)
        self.timeline.multi_changed.connect(
            lambda n: self.statusBar().showMessage(
                f"{n}개 함께 선택 — 아무 막대나 끝을 끌면 모두 같은 만큼 늘고 줄어듭니다 "
                "(⌘-클릭으로 넣고 빼기 · Esc 해제)", 6000) if n > 1 else None)
        tl_scroll = QScrollArea()
        tl_scroll.setWidget(self.timeline)
        tl_scroll.setWidgetResizable(True)
        tl_scroll.setFrameShape(QFrame.NoFrame)
        tl_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tl_scroll.setMinimumHeight(84)
        tl_box = QFrame()
        tl_box.setObjectName("panel")
        tl_lay = QVBoxLayout(tl_box)
        tl_lay.setContentsMargins(2, 4, 2, 4)
        tl_lay.addWidget(tl_scroll)

        center = QSplitter(Qt.Vertical)
        center.addWidget(top)
        center.addWidget(tl_box)
        center.setStretchFactor(0, 1)
        center.setSizes([640, 190])
        center.setChildrenCollapsible(False)

        # ---- 좌우 패널
        self.clip_bin = ClipBin()
        self.clip_bin.set_session(self.session)
        self.clip_bin.add_requested.connect(self.add_videos)
        self.clip_bin.clip_selected.connect(self.switch_clip)
        self.clip_bin.changed.connect(self._on_clips_changed)

        self.inspector = Inspector()
        self.inspector.changed.connect(self._on_inspector_changed)
        self.inspector.structure_changed.connect(self._on_structure_changed)
        self.inspector.header_changed.connect(self.timeline.rebuild)
        self.inspector.removed.connect(self._on_inspector_removed)
        self.inspector.preview.connect(self.preview_card)
        self.inspector.status.connect(lambda s: self.statusBar().showMessage(s, 6000))
        self.inspector.track_tool.connect(self.start_track_tool)
        self.inspector.track_edit.connect(self.apply_track_edit)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.clip_bin)
        split.addWidget(center)
        split.addWidget(self.inspector)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900, 330])
        split.setChildrenCollapsible(False)
        self.clip_bin.setMinimumWidth(180)
        self.inspector.setMinimumWidth(290)

        root = QWidget()
        root.setObjectName("root")
        rl = QVBoxLayout(root)
        rl.setContentsMargins(8, 8, 8, 4)
        rl.addWidget(split)
        self.setCentralWidget(root)

        # ---- 상태 표시줄
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setFixedHeight(8)
        self.btn_cancel = self._ghost("x", "작업 취소", self.cancel_work)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.btn_cancel)

    def _tool_button(self, act: QAction) -> QToolButton:
        b = QToolButton()
        b.setDefaultAction(act)
        b.setIconSize(QSize(18, 18))
        return b

    def _ghost(self, icon: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setObjectName("ghost")
        b.setIcon(theme.icon(icon))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    def _build_toolbar(self) -> None:
        tb = QToolBar("도구")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setContextMenuPolicy(Qt.PreventContextMenu)
        self.addToolBar(Qt.TopToolBarArea, tb)

        b_add = QToolButton()
        b_add.setDefaultAction(self.act_add)
        b_add.setText("영상 추가")
        b_add.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.addWidget(b_add)
        tb.addSeparator()

        m_an = QMenu(self)
        m_an.addAction(self.act_analyze)
        m_an.addAction(self.act_analyze_all)
        m_an.addSeparator()
        q = m_an.addSection("품질")
        q.setEnabled(False)
        for a in self.preset_acts:
            m_an.addAction(a)
        self.btn_analyze = QToolButton()
        self.btn_analyze.setText("분석")
        self.btn_analyze.setIcon(theme.icon("magic-wand"))
        self.btn_analyze.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_analyze.setPopupMode(QToolButton.MenuButtonPopup)
        self.btn_analyze.setMenu(m_an)
        self.btn_analyze.setToolTip("이 클립의 선수를 찾아 추적합니다 (⌘R)\n"
                                    "▾ 에서 모든 클립 분석 · 품질 선택")
        self.btn_analyze.clicked.connect(self.analyze)
        tb.addWidget(self.btn_analyze)

        m_ex = QMenu(self)
        for a in (self.act_export, self.act_export_all):
            m_ex.addAction(a)
        m_ex.addSeparator()
        m_ex.addAction(self.act_export_raw)
        self.btn_export = QToolButton()
        self.btn_export.setObjectName("primary")
        self.btn_export.setText("내보내기")
        self.btn_export.setIcon(theme.icon("export", "#ffffff"))
        self.btn_export.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_export.setPopupMode(QToolButton.InstantPopup)
        self.btn_export.setMenu(m_ex)
        tb.addWidget(self.btn_export)

        def spacer():
            w = QWidget()
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            return w

        tb.addWidget(spacer())
        self.lbl_steps = QLabel()
        tb.addWidget(self.lbl_steps)
        tb.addWidget(spacer())
        for a in (self.act_undo, self.act_redo, self.act_save):
            b = QToolButton()
            b.setDefaultAction(a)
            b.setObjectName("ghost")
            tb.addWidget(b)

    def _build_menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("파일")
        for a in (self.act_add, self.act_new, self.act_open, self.act_save, None,
                  self.act_export, self.act_export_all, self.act_export_raw):
            m.addSeparator() if a is None else m.addAction(a)

        e = mb.addMenu("편집")
        for a in (self.act_undo, self.act_redo, None, *self.tool_acts.values(), None,
                  self.act_link, self.act_caption, self.act_freeze, None, self.act_delete):
            e.addSeparator() if a is None else e.addAction(a)

        an = mb.addMenu("분석")
        an.addAction(self.act_analyze)
        an.addAction(self.act_analyze_all)
        an.addSeparator()
        for a in self.preset_acts:
            an.addAction(a)

        v = mb.addMenu("보기")
        for a in (self.act_play, self.act_prev, self.act_next, self.act_back,
                  self.act_fwd, None, self.act_boxes):
            v.addSeparator() if a is None else v.addAction(a)

    # ================================================================ 도구 모드
    def set_tool(self, key: str) -> None:
        self._pending = None
        self.tool = key
        self.tool_acts[key].setChecked(True)
        self.viewer.setCursor({"select": Qt.ArrowCursor, "highlight": Qt.PointingHandCursor,
                               "zone": Qt.CrossCursor}[key])
        self.viewer.hover_enabled = key != "zone"
        self.btn_link.setVisible(key == "highlight")
        self.btn_line.setVisible(key == "highlight" and self.link_lines)
        self.viewer._place_toolstrip()
        self._update_hint()

    def _update_hint(self) -> None:
        if self._pending:
            what = "합칠 조각" if self._pending[0] == "merge" else "맞바꿀 상대 선수"
            self.lbl_hint.setText(f"{what}를 영상이나 타임라인 이름에서 클릭하세요 · Esc 취소")
            return
        hint = TOOLS[self.tool][3]
        if self.tool == "highlight":
            if self.link_lines:
                hint += f" (지금: {line_label(self.cur_line)})"
            else:
                hint = ("클릭: 이 프레임부터 강조 · 보이는 선수를 다시 클릭: 여기서 끝 · "
                        "라인 잇기 꺼짐 (L)")
        self.lbl_hint.setText(hint)
        self.btn_link.setIcon(theme.icon("link" if self.link_lines else "link-break"))
        self.btn_line.setText(line_label(self.cur_line))

    def set_link_lines(self, on: bool) -> None:
        self.link_lines = on
        self.settings.setValue("link_lines", on)
        if self.btn_link.isChecked() != on:
            self.btn_link.setChecked(on)
        self.btn_line.setVisible(self.tool == "highlight" and on)
        self.viewer._place_toolstrip()
        self._update_hint()
        self.statusBar().showMessage(
            "라인 잇기 켜짐 — 클릭한 선수를 대형선으로 잇습니다" if on else
            "라인 잇기 꺼짐 — 클릭한 선수는 강조만 됩니다", 4000)

    def start_new_line(self) -> None:
        """지금 선을 끊고 다음 라인으로 — 수비 → 미드 → 공격 → GK → 수비 … 순환."""
        self.cur_line = self.cur_line % NAMED_LINES + 1
        self.inspector.cur_line = self.cur_line
        self._update_hint()
        self.statusBar().showMessage(
            f"새 라인 — 다음 클릭부터 '{line_label(self.cur_line)}' 로 이어집니다", 5000)

    def _update_steps(self) -> None:
        if not self.session.clips:
            cur = 0
        elif self.project.trackset is None:
            cur = 1
        elif self.project.enabled_count == 0 and not self.project.markers:
            cur = 2
        else:
            cur = 3
        parts = []
        for i, name in enumerate(STEPS):
            if i < cur:
                parts.append(f"<span style='color:{theme.MUTED}'>✓ {name}</span>")
            elif i == cur:
                parts.append(f"<span style='color:{theme.ACCENT}; font-weight:600'>"
                             f"{i + 1}  {name}</span>")
            else:
                parts.append(f"<span style='color:#5b606b'>{i + 1}  {name}</span>")
        sep = "<span style='color:#3a3f4a'>&nbsp;&nbsp;—&nbsp;&nbsp;</span>"
        self.lbl_steps.setText(sep.join(parts))

    # ================================================================ 드래그&드롭
    @staticmethod
    def _dropped_paths(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls()
                if u.toLocalFile() and Path(u.toLocalFile()).suffix.lower()
                in VIDEO_EXTS | {".fbproj"}]

    def dragEnterEvent(self, event) -> None:
        if self._dropped_paths(event):
            event.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event) -> None:
        paths = self._dropped_paths(event)
        if not paths:
            return
        event.acceptProposedAction()
        proj = [p for p in paths if p.lower().endswith(".fbproj")]
        if proj:
            self._open_project_path(proj[0])
            return
        self._add_paths(paths)

    # ================================================================ 클립 관리
    def add_videos(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "영상 추가 (여러 개 가능)", "",
                                                VIDEO_FILTER)
        self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        self._leave_clip()
        for p in paths:
            self.session.add_video(p)
        self.session.current = len(self.session.clips) - len(paths)
        self._load_current_clip()
        self.statusBar().showMessage(
            f"클립 {len(paths)}개 추가 — 총 {len(self.session.clips)}개. "
            f"다음은 [분석] (⌘R)", 6000)

    def _open_video_path(self, path: str) -> None:
        self._add_paths([path])

    def new_session(self) -> None:
        if self.session.clips and QMessageBox.question(
                self, "새 세션", "지금 세션의 클립을 모두 비웁니다. 계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.session = Session()
        self.project = Project()
        self._undo_by_clip.clear()
        self._close_reader()
        self.clip_bin.set_session(self.session)
        self.inspector.set_project(None)
        self.timeline.set_project(None, 1, 30.0)
        self.viewer.set_frame(None)
        self.lbl_time.setText("--:-- / --:--")
        self._select("", None)
        self._busy(False)
        self.statusBar().showMessage("새 세션 — 영상을 끌어다 놓거나 ⌘O 로 추가하세요")

    def _leave_clip(self) -> None:
        self._undo_timer.stop()
        self._record_undo()
        self._stash_undo()
        if self.timer.isActive():
            self.toggle_play()

    def switch_clip(self, index: int) -> None:
        """편집 대상 클립을 바꾼다. 클립마다 되돌리기 스택이 따로 유지된다."""
        if not (0 <= index < len(self.session.clips)):
            if not self.session.clips:
                self.new_session_state()
            return
        self._leave_clip()
        self.session.current = index
        self._load_current_clip()

    def new_session_state(self) -> None:
        """마지막 클립을 지워 목록이 비었을 때."""
        self.project = Project()
        self._close_reader()
        self.inspector.set_project(None)
        self.timeline.set_project(None, 1, 30.0)
        self.viewer.set_frame(None)
        self._select("", None)
        self._busy(False)

    def _close_reader(self) -> None:
        if self.reader:
            self.reader.close()
        self.reader, self.info = None, None
        self._frame_cache = None

    def _load_current_clip(self) -> None:
        proj = self.session.clip
        if proj is None:
            return
        self.project = proj
        self._close_reader()
        if proj.video_path and Path(proj.video_path).exists():
            try:
                self._load_video(proj.video_path)
            except Exception as e:
                self.statusBar().showMessage(f"영상을 열 수 없습니다: {e}", 8000)
        if self.reader is None:
            self.viewer.set_frame(None)
        n, fps = self._n_src(), self._fps()
        self.inspector.set_project(proj, n, fps)
        self.timeline.set_project(proj, n, fps)
        self._sync_range()
        self.clip_bin.refresh()
        self._restore_undo()
        self._refresh_team_cache()
        self._select("", None)
        self._busy(False)
        self._refresh_frame()

    def _on_clips_changed(self) -> None:
        self.clip_bin.refresh()
        self._busy(False)

    def _n_src(self) -> int:
        return self.info.n_frames if self.info else 1

    def _tm(self):
        return self.project.timemap(self._fps())

    def _n(self) -> int:
        """결과 영상 길이 — 원본 + 정지 카드 시간."""
        return self._tm().total(self._n_src()) if self.info else 1

    def _sync_range(self) -> None:
        """분석·내보내기 구간(원본 기준)을 타임라인(결과 기준)에 표시."""
        p, tm = self.project, self._tm()
        self.timeline.set_range(tm.out(p.export_start), tm.out(p.export_end or self._n_src()))

    def _tc(self, o: int) -> str:
        t = o / self._fps()
        return f"{int(t // 60):02d}:{t % 60:05.2f}"

    def _fps(self) -> float:
        return (self.info.fps if self.info else 0) or 30.0

    # ================================================================ 파일
    def _load_video(self, path: str) -> None:
        self.info = probe(path)
        self.reader = FrameReader(path)
        self.cur_frame = self.cur_src = 0
        self._frame_cache = None
        self.setWindowTitle(f"Football Highlighter — {Path(path).name}")
        self.statusBar().showMessage(
            f"{self.info.width}×{self.info.height} · {self.info.fps:.2f}fps · "
            f"{self.info.n_frames}프레임 ({self.info.duration:.1f}초)")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", PROJ_FILTER)
        if path:
            self._open_project_path(path)

    def _open_project_path(self, path: str) -> None:
        try:
            session = Session.load(path)
        except Exception as e:
            QMessageBox.critical(self, "오류", f"프로젝트를 열 수 없습니다:\n{e}")
            return
        if not session.clips:
            QMessageBox.warning(self, "빈 프로젝트", "클립이 들어 있지 않습니다.")
            return
        self.session = session
        self._undo_by_clip.clear()
        self.clip_bin.set_session(session)
        missing = [session.name_of(i) for i, p in enumerate(session.clips)
                   if not (p.video_path and Path(p.video_path).exists())]
        self._load_current_clip()
        if missing:
            QMessageBox.warning(
                self, "영상 없음",
                "원본 영상을 찾을 수 없는 클립이 있습니다. 목록에 빨간 점으로 표시됩니다:\n\n"
                + "\n".join(missing[:8]))

    def save_project(self) -> None:
        if not self.session.clips:
            return
        path = self.session.path
        if not path:
            first = self.session.clips[0].video_path or "project"
            path, _ = QFileDialog.getSaveFileName(
                self, "프로젝트 저장", str(Path(first).with_suffix(".fbproj")),
                PROJ_FILTER)
        if path:
            self.session.save(path)
            self.statusBar().showMessage(
                f"저장됨: {path} (클립 {len(self.session.clips)}개)", 5000)

    # ================================================================ 재생
    def toggle_play(self) -> None:
        if self.reader is None:
            return
        if self.timer.isActive():
            self.timer.stop()
            self.btn_play.setIcon(theme.icon("play"))
        else:
            if self.cur_frame + 1 >= self._n():
                self._goto(0)
            self.timer.start(max(10, int(1000 / self._fps())))
            self.btn_play.setIcon(theme.icon("pause"))

    def _on_tick(self) -> None:
        if self.cur_frame + 1 >= self._n():
            self.toggle_play()
            return
        self._goto(self.cur_frame + 1, sequential=True)

    def _step(self, d: float) -> None:
        if self.reader is None:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._goto(int(max(0, min(self._n() - 1, self.cur_frame + round(d)))))

    def _image_for(self, fi: int, sequential: bool = False):
        """원본 프레임 fi 이미지. 정지 구간에서는 같은 프레임을 계속 쓰므로 캐시를 본다."""
        if self._frame_cache is not None and self._frame_cache[0] == fi:
            return self._frame_cache[1]
        if sequential and self._frame_cache is not None and self._frame_cache[0] + 1 == fi:
            img = self.reader.read()
        else:
            img = self.reader.frame_at(fi)
        if img is not None:
            self._frame_cache = (fi, img)
        return img

    def _goto(self, o: int, sequential: bool = False) -> None:
        """결과 번호 o 로 이동."""
        if self.reader is None:
            return
        o = max(0, min(self._n() - 1, o))
        fi = self._tm().locate(o)[0]
        img = self._image_for(fi, sequential)
        if img is None:
            if self.timer.isActive():
                self.toggle_play()
            return
        self.cur_frame, self.cur_src = o, fi
        self._display(img, o)

    def _display(self, img, o: int) -> None:
        ts = self.project.trackset
        fps = self._fps()
        fi = self.cur_src
        # 재생 중에만 등장·퇴장 애니메이션을 보여준다. 멈춘 화면에서는 완전히 나타난
        # 상태로 그린다 — 선수를 클릭한 바로 그 프레임은 등장 0초라 아무것도 안
        # 보이게 되기 때문이다.
        self.viewer.set_frame(self.project.render_output(
            img, o, fps, animate=self.timer.isActive()))
        boxes = ts.boxes_at(fi) if ts else {}
        players = self.project.players
        labels = {t: players[t].display for t in boxes if t in players}
        teams = {t: self._team_q[t] for t in boxes if t in self._team_q}
        self.viewer.sel_circle = self._marker_circle(self.sel_obj) \
            if self.sel_kind == "marker" else None
        self.viewer.set_boxes(boxes, self.selected_tid, labels, teams)
        self.timeline.set_frame(o)
        self.inspector.set_frame(o, fi)
        frozen = self._tm().locate(o)[1] is not None
        self.lbl_time.setText(f"{self._tc(o)} / {self._tc(self._n())}"
                              + ("   ❚❚ 정지 구간" if frozen else ""))

    def _current_image(self):
        if self.reader is None:
            return None
        return self._image_for(self.cur_src)

    def _refresh_frame(self) -> None:
        # 정지 카드를 넣고 빼면 같은 번호가 다른 장면을 가리키므로 매번 다시 찾는다
        if self.reader is not None:
            self.cur_frame = max(0, min(self._n() - 1, self.cur_frame))
            self.cur_src = self._tm().locate(self.cur_frame)[0]
        img = self._current_image()
        if img is not None:
            self._display(img, self.cur_frame)
        self._update_steps()

    def _toggle_boxes(self, on: bool) -> None:
        self.viewer.show_boxes = on
        self.viewer.update()

    # ================================================================ 선택
    def _select(self, kind: str, obj, seek: bool = False) -> None:
        if obj is None:
            kind = ""
        self.sel_kind, self.sel_obj = kind, obj
        self.selected_tid = obj.tid if kind == "player" else None
        self.inspector.show_for(kind or "project", obj)
        self.timeline.set_selected(obj)
        if seek and self.reader is not None:
            f = self.cur_frame
            target = None
            if kind == "caption" and not (obj.start <= f < obj.end):
                target = obj.start
            elif kind == "freeze":
                o0, o1 = self._tm().block(obj)
                if not (o0 <= f < o1):
                    target = o0
            elif kind == "player":
                ts = self.project.trackset
                tr = ts.tracks.get(obj.tid) if ts else None
                if tr and not tr.at(self.cur_src):
                    target = self._tm().out(tr.first_frame)   # 화면에 없으면 첫 등장 위치로
            if target is not None and target != f:
                self._goto(target)
                return
        self._refresh_frame()

    def _sel_key(self):
        k, o = self.sel_kind, self.sel_obj
        tl = self.project.timeline
        if k == "player":
            return k, o.tid
        if k == "marker":
            return k, o.mid
        if k == "caption" and o in tl.captions:
            return k, tl.captions.index(o)
        if k == "freeze" and o in tl.freezes:
            return k, tl.freezes.index(o)
        return None

    def _sel_from_key(self, key):
        if key is None:
            return "", None
        k, v = key
        p = self.project
        try:
            if k == "player":
                return k, p.players[v]
            if k == "marker":
                return k, next(m for m in p.markers if m.mid == v)
            if k == "caption":
                return k, p.timeline.captions[v]
            if k == "freeze":
                return k, p.timeline.freezes[v]
        except (KeyError, IndexError, StopIteration):
            pass
        return "", None

    def delete_selected(self) -> None:
        k, o = self.sel_kind, self.sel_obj
        if o is None:
            return
        p = self.project
        if k == "player":
            p.set_enabled(o.tid, False)
            o.spans = []
            msg = f"{o.display} 강조를 껐습니다"
        elif k == "marker":
            p.remove_marker(o.mid)
            msg = "구역을 지웠습니다"
        elif k == "caption":
            p.timeline.captions.remove(o)
            msg = "자막을 지웠습니다"
        elif k == "freeze":
            p.remove_freeze(o, self._fps())         # 뒤쪽 구간이 당겨진다
            msg = "정지 카드를 지웠습니다"
        else:
            return
        self._after_structure()
        self._select("", None)
        self.statusBar().showMessage(f"{msg} (⌘Z 로 되돌리기)", 4000)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            if self._pending:
                self.set_tool(self.tool)
                self.statusBar().showMessage("추적 보정을 취소했습니다", 3000)
            elif self.tool != "select":
                self.set_tool("select")
            elif self.sel_obj is not None:
                self._select("", None)
            return
        super().keyPressEvent(ev)

    # ================================================================ 뷰어 입력
    def _marker_circle(self, m):
        if m is None or self.info is None:
            return None
        w, h = self.info.width, self.info.height
        r = max(4.0, m.radius * math.hypot(w, h))
        return m.x * w, m.y * h, r, r * m.tilt

    def _marker_at(self, x: float, y: float):
        for m in reversed(self.project.markers):
            if not m.visible_at(self.cur_frame):
                continue
            cx, cy, r, ry = self._marker_circle(m)
            if math.hypot((x - cx) / (r + 6), (y - cy) / (ry + 6)) <= 1.0:
                return m
        return None

    def _on_viewer_pressed(self, x: float, y: float) -> None:
        if self.reader is None or self.info is None:
            return
        if self.timer.isActive():
            self.toggle_play()
        if self._pending:
            ts = self.project.trackset
            self._finish_track_tool(ts.pick(self.cur_src, x, y) if ts else None)
            return
        if self.tool == "zone":
            # 구역 배치는 트래킹과 무관하므로 분석 여부와 상관없이 동작한다
            m = self.project.add_marker(x / self.info.width, y / self.info.height)
            m.span_begin(self.cur_frame)        # 놓은 순간부터 표시 (정지 구간 안이어도)
            self.set_tool("select")
            self._after_structure()
            self._select("marker", m)
            self.statusBar().showMessage(
                "구역을 놓았습니다 — 끌어서 옮기고, 오른쪽에서 크기·색을 바꾸세요", 5000)
            return

        if self.tool == "select":
            m = self._marker_at(x, y)
            if m is not None:
                cx, cy, _, _ = self._marker_circle(m)
                self._drag_marker = (m, x - cx, y - cy)
                self._select("marker", m)
                return

        ts = self.project.trackset
        tid = ts.pick(self.cur_src, x, y) if ts else None
        if self.tool == "select":
            if tid is not None:
                self._select("player", self.project.players[tid])
            else:
                self._select("", None)
                if ts is None:
                    self.statusBar().showMessage("분석을 마치면 선수를 고를 수 있습니다 (⌘R)",
                                                 3000)
            return

        # ---- 선수 강조 모드
        if ts is None:
            self.statusBar().showMessage("먼저 [분석]을 실행하세요 (⌘R)", 3000)
            return
        if tid is None:
            return
        ps = self.project.players[tid]
        # 더블클릭이 이어질 수 있다. Qt 는 더블클릭 앞에 단일 클릭을 먼저 보내므로,
        # 되돌릴 수 있게 클릭 직전 상태를 기억해 둔다(이름만 바꾸려는데 구간이
        # 끊기면 안 된다). 클릭을 지연시켜 판별하면 평소 클릭이 느려진다.
        self._click_state = (tid, ps.enabled, [list(s) for s in ps.spans],
                             ps.line, ps.order)
        # 클릭 = 이 프레임부터 표시. 이미 보이는 중이면 여기서 끊는다.
        if ps.visible_at(self.cur_frame):
            ps.span_stop(self.cur_frame)
            if not ps.spans:
                self.project.set_enabled(tid, False)
            msg = f"{ps.display} — 이 프레임에서 표시 끝"
        else:
            if not ps.enabled:
                self.project.set_enabled(tid, True,
                                         line=self.cur_line if self.link_lines else 0)
            ps.span_begin(self.cur_frame)
            msg = (f"{ps.display} · {line_label(ps.line)} 라인 · 이 프레임부터 표시"
                   if ps.line else f"{ps.display} · 이 프레임부터 강조")
            if self._mixed_team(ps):
                msg = ("⚠ 같은 라인에 다른 팀 선수가 섞였습니다 — 선이 상대 선수까지 "
                       "이어집니다. " + msg)
        self._after_structure()
        self._select("player", ps)
        self.statusBar().showMessage(f"{msg} — 강조 중 {self.project.enabled_count}명", 5000)

    def _on_viewer_dragged(self, x: float, y: float) -> None:
        if self._drag_marker is None or self.info is None:
            return
        m, dx, dy = self._drag_marker
        m.x = min(1.0, max(0.0, (x - dx) / self.info.width))
        m.y = min(1.0, max(0.0, (y - dy) / self.info.height))
        self._refresh_frame()

    def _on_viewer_released(self, _x: float, _y: float) -> None:
        if self._drag_marker is not None:
            self._drag_marker = None
            self._touch()

    def _on_viewer_right_clicked(self, _x: float, _y: float) -> None:
        if self.tool == "highlight":
            if self.link_lines:
                self.start_new_line()
            else:
                self.statusBar().showMessage("라인 잇기가 꺼져 있습니다 — L 로 켤 수 있습니다", 3000)

    def _on_viewer_double_clicked(self, x: float, y: float) -> None:
        """더블클릭 = 그 선수 등번호·이름·색을 한 번에 편집."""
        ts = self.project.trackset
        if ts is None or self.tool == "zone":
            return
        tid = ts.pick(self.cur_src, x, y)
        if tid is None:
            return
        ps = self.project.players[tid]
        # 앞서 들어온 단일 클릭의 부작용(강조 토글 / 구간 끊김)을 되돌린다
        st = self._click_state
        if st and st[0] == tid:
            ps.enabled, ps.spans, ps.line, ps.order = st[1], st[2], st[3], st[4]
        self._click_state = None

        dlg = PlayerEditDialog(ps.name, ps.number, ps.style.color,
                               title=f"{ps.display} 정보", parent=self)
        if dlg.exec() == QDialog.Accepted:
            ps.name, ps.number, ps.style.color = dlg.values()
            self.statusBar().showMessage(f"{ps.display} 정보를 수정했습니다", 4000)
        self._after_structure()
        self._select("player", ps)

    # ================================================================ 패널 신호
    def _after_structure(self) -> None:
        """선수·구역·자막 구성이 바뀌었다 — 타임라인·클립 목록·되돌리기 갱신."""
        self.timeline.rebuild()
        self.clip_bin.refresh()
        self._touch()

    def _on_inspector_changed(self) -> None:
        self._touch()
        self._sync_range()
        self._refresh_frame()

    def _on_structure_changed(self) -> None:
        self.timeline.rebuild()
        self._sync_range()
        self._refresh_frame()
        self.clip_bin.refresh()
        self._update_steps()

    def _on_inspector_removed(self) -> None:
        self.timeline.rebuild()
        self._sync_range()
        self._after_structure()
        self._select("", None)

    def _on_timeline_seek(self, f: int) -> None:
        if self.timer.isActive():
            self.toggle_play()
        self._goto(f)

    def _on_timeline_selected(self, kind: str, obj, from_label: bool) -> None:
        if self._pending and kind == "player":
            self._finish_track_tool(obj.tid)
            return
        self._select(kind, obj, seek=from_label)

    def _on_timeline_changed(self) -> None:
        self._sync_range()
        self._touch()
        self.clip_bin.refresh()
        self.inspector.refresh()
        self._refresh_frame()

    # ================================================================ 추적 보정
    def _refresh_team_cache(self) -> None:
        ts = self.project.trackset
        self._team_q = {}
        if ts is None:
            return
        for tid, tr in ts.tracks.items():
            t = tr.team
            if 0 < t <= len(ts.team_colors):
                self._team_q[tid] = theme.bgr_to_qcolor(ts.team_colors[t - 1])

    def _mixed_team(self, ps) -> bool:
        """같은 라인에 팀이 다른(둘 다 팀이 확실한) 선수가 있나."""
        mine = self.project.team_of(ps.tid)
        if not mine or not ps.line:
            return False
        return any(o.enabled and o.tid != ps.tid and o.line == ps.line
                   and self.project.team_of(o.tid) not in (0, mine)
                   for o in self.project.players.values())

    def start_track_tool(self, op: str) -> None:
        if self.sel_kind != "player" or self.project.trackset is None:
            return
        a = self.sel_obj.tid
        if op == "split":
            tr = self.project.trackset.tracks.get(a)
            f = self.cur_src                      # 추적 보정은 원본 프레임 기준
            if tr is None or not any(k < f for k in tr.boxes) or not any(k >= f for k in tr.boxes):
                self.statusBar().showMessage(
                    "이 선수가 지금 프레임 앞뒤로 모두 나와야 끊을 수 있습니다", 5000)
                return
            e = self.project.edit_tracks({"op": "split", "a": a, "frame": f})
            self._after_track_edit(a)
            self.statusBar().showMessage(
                f"{self.sel_obj.display if self.sel_obj else a} — 여기서부터를 새 선수 "
                f"ID {e['new']} 로 떼어냈습니다 (⌘Z 로 되돌리기)", 6000)
            return
        self._pending = (op, a)
        self.viewer.setCursor(Qt.CrossCursor)
        self._update_hint()
        what = "합칠 조각(끊긴 뒤 새 번호로 잡힌 같은 선수)" if op == "merge" \
            else "지금 프레임부터 번호를 맞바꿀 상대 선수"
        self.statusBar().showMessage(f"{what}를 클릭하세요 — 다른 프레임으로 옮겨 가서 "
                                     "클릭해도 됩니다 · Esc 취소")

    def _finish_track_tool(self, b: int | None) -> None:
        op, a = self._pending
        p = self.project
        if b is None:
            self.statusBar().showMessage("선수 박스 위를 클릭하세요 · Esc 취소", 3000)
            return
        if b == a:
            self.statusBar().showMessage("같은 선수입니다 — 다른 선수를 클릭하세요", 3000)
            return
        na = p.players[a].display if a in p.players else f"ID {a}"
        nb = p.players[b].display if b in p.players else f"ID {b}"
        if op == "merge":
            ov = p.trackset.overlap(a, b)
            if ov > 5 and QMessageBox.question(
                    self, "합치기",
                    f"{na} 와 {nb} 가 {ov}프레임 동안 화면에 함께 나옵니다.\n"
                    "서로 다른 사람일 가능성이 큽니다. 그래도 합칠까요?\n\n"
                    f"(겹치는 구간에서는 {nb} 의 위치를 씁니다)",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            p.edit_tracks({"op": "merge", "a": a, "b": b})
            msg = f"{nb} 를 {na} 에 합쳤습니다"
        else:
            p.edit_tracks({"op": "swap", "a": a, "b": b, "frame": self.cur_src})
            msg = f"{na} ↔ {nb} — 이 프레임부터 맞바꿨습니다"
        self.set_tool(self.tool)            # 대기 해제 + 커서 복원
        self._after_track_edit(a)
        self.statusBar().showMessage(f"{msg} (⌘Z 로 되돌리기)", 6000)

    def apply_track_edit(self, e: dict) -> None:
        self.project.edit_tracks(e)
        self._after_track_edit(e["a"])
        if e["op"] == "team":
            self.statusBar().showMessage(
                f"팀을 '{TEAM_NAMES[e['team']]}' 로 지정했습니다", 4000)

    def _after_track_edit(self, a: int) -> None:
        self._refresh_team_cache()
        self._after_structure()
        ps = self.project.players.get(a)
        self._select("player", ps) if ps else self._select("", None)

    # ================================================================ 자막 · 정지 카드
    def _can_edit(self) -> bool:
        if self.reader is None:
            self.statusBar().showMessage("먼저 영상을 추가하세요", 3000)
            return False
        return True

    def add_caption(self) -> None:
        if not self._can_edit():
            return
        n = max(1, int(round(2.0 * self._fps())))
        cap = Caption(text="자막을 입력하세요", start=self.cur_frame,
                      end=min(self._n(), self.cur_frame + n),
                      style=TextStyle(position="bottom", size_pct=3.0, effect="fade"))
        self.project.timeline.captions.append(cap)
        self._after_structure()
        self._select("caption", cap)
        self._focus_text()

    def add_freeze(self) -> None:
        """지금 장면에서 멈춤 3초 — 그 자리에 3초 구간이 생기고 뒤쪽은 밀린다."""
        if not self._can_edit():
            return
        tm = self._tm()
        fi, inside, _ = tm.locate(self.cur_frame)
        if inside is not None:
            self.statusBar().showMessage("이미 정지 구간 안입니다 — 길이는 오른쪽에서 바꾸세요",
                                         4000)
            self._select("freeze", inside)
            return
        if any(fz.frame == fi for fz in self.project.timeline.freezes):
            self._select("freeze", next(f for f in self.project.timeline.freezes
                                        if f.frame == fi))
            return
        card = self.project.add_freeze(
            fi, self._fps(), 3.0, text="자막을 입력하세요",
            style=TextStyle(position="bottom", size_pct=3.5, effect="pop"))
        self.timeline.rebuild()
        self._sync_range()
        self._after_structure()
        self._goto(self._tm().block(card)[0])       # 정지 구간 첫 프레임으로
        self._select("freeze", card)
        self._focus_text()
        self.statusBar().showMessage(
            "3초 정지 구간을 넣었습니다 — 이 안에서도 구역·강조·자막을 켜고 끌 수 있습니다", 6000)

    def _focus_text(self) -> None:
        t = self.inspector.pages["text"].txt
        t.setFocus()
        t.selectAll()

    def preview_card(self, card) -> None:
        """정지 구간 처음부터 재생 — 멈춘 화면 위의 자막·애니메이션을 그대로 본다."""
        if self.reader is None or card not in self.project.timeline.freezes:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._goto(self._tm().out(card.frame))
        self.toggle_play()

    # ================================================================ 되돌리기
    def _snap(self) -> str:
        return json.dumps(self.project.snapshot(), sort_keys=True, ensure_ascii=False)

    def _touch(self) -> None:
        """상태가 바뀌었을 수 있다 — 잠시 뒤 스냅샷을 확정한다."""
        if not self._restoring:
            self._undo_timer.start()

    def _record_undo(self) -> None:
        if self._restoring or not self.project.video_path:
            return
        cur = self._snap()
        if self._last_snap is None:
            self._last_snap = cur
            return
        if cur == self._last_snap:      # 실제로 바뀐 게 없으면 쌓지 않는다
            return
        self._undo.append(self._last_snap)
        del self._undo[:-80]
        self._redo.clear()
        self._last_snap = cur

    def reset_undo(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._last_snap = self._snap() if self.project.video_path else None

    def _stash_undo(self) -> None:
        """클립을 떠나기 전에 그 클립의 되돌리기 스택을 보관한다."""
        if self.project.video_path:
            self._undo_by_clip[id(self.project)] = (
                list(self._undo), list(self._redo), self._last_snap)

    def _restore_undo(self) -> None:
        saved = self._undo_by_clip.get(id(self.project))
        if saved is None:
            self.reset_undo()
            return
        self._undo, self._redo, self._last_snap = list(saved[0]), list(saved[1]), saved[2]

    def _apply_snap(self, snap: str) -> None:
        self._restoring = True
        try:
            key = self._sel_key()
            self.project.restore(json.loads(snap))
            self._last_snap = snap
            self._refresh_team_cache()
            self.timeline.rebuild()
            self.clip_bin.refresh()
            self._sync_range()
            self._select(*self._sel_from_key(key))
        finally:
            self._restoring = False

    def undo(self) -> None:
        self._undo_timer.stop()
        self._record_undo()             # 입력 중이던 변경을 먼저 확정
        if not self._undo:
            self.statusBar().showMessage("되돌릴 작업이 없습니다", 3000)
            return
        self._redo.append(self._last_snap)
        self._apply_snap(self._undo.pop())
        self.statusBar().showMessage(f"실행 취소 (남은 단계 {len(self._undo)})", 3000)

    def redo(self) -> None:
        self._undo_timer.stop()
        if not self._redo:
            self.statusBar().showMessage("다시 실행할 작업이 없습니다", 3000)
            return
        self._undo.append(self._last_snap)
        self._apply_snap(self._redo.pop())
        self.statusBar().showMessage(f"다시 실행 (남은 단계 {len(self._redo)})", 3000)

    # ================================================================ 작업
    def _needs_analysis(self, proj: Project) -> bool:
        return (proj.trackset is None and bool(proj.video_path)
                and Path(proj.video_path).exists())

    def _busy(self, on: bool) -> None:
        self.progress.setVisible(on)
        self.btn_cancel.setVisible(on)
        self.progress.setValue(0)
        self.clip_bin.setEnabled(not on)
        can = {
            self.act_analyze: self.reader is not None,
            self.act_analyze_all: any(self._needs_analysis(p) for p in self.session.clips),
            self.act_export: self.project.trackset is not None,
            self.act_export_all: bool(self.session.ready_clips),
            self.act_export_raw: bool(self.session.clips_with_video),
        }
        for a, ok in can.items():
            a.setEnabled(ok and not on)
        for a in self.preset_acts:
            a.setEnabled(not on)
        self.btn_analyze.setEnabled(not on and (can[self.act_analyze]
                                                or can[self.act_analyze_all]))
        self.btn_export.setEnabled(not on and any(
            can[a] for a in (self.act_export, self.act_export_all, self.act_export_raw)))
        self._update_steps()

    def cancel_work(self) -> None:
        self._batch_queue = []
        if self.worker:
            self.worker.cancel()
            self.statusBar().showMessage("취소하는 중…")

    def _progress(self, d: int, t: int) -> None:
        self.progress.setValue(int(d / max(1, t) * 100))

    def analyze(self) -> None:
        if self.reader is None or self.worker:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._busy(True)
        p = self.project
        w = AnalyzeWorker(p.video_path, self.preset, p.export_start,
                          p.export_end or self._n_src(), self)
        w.progress.connect(self._progress)
        w.status.connect(self.statusBar().showMessage)
        w.done.connect(self._on_analyzed)
        w.failed.connect(self._on_failed)
        w.finished.connect(lambda: setattr(self, "worker", None))
        self.worker = w
        w.start()

    def _after_analysis(self) -> None:
        n, fps = self._n_src(), self._fps()
        self.inspector.set_project(self.project, n, fps)
        self.timeline.set_project(self.project, n, fps)
        self._sync_range()
        self._refresh_team_cache()
        self._select("", None)
        # 분석 결과는 되돌리기 대상이 아니다 — 여기가 새 기준점
        self.reset_undo()

    def _on_analyzed(self, ts) -> None:
        if ts is None:
            self._busy(False)
            self.statusBar().showMessage("분석이 취소되었습니다", 4000)
            return
        # attach 가 먼저여야 _busy(False) 에서 내보내기가 켜진다
        self.project.attach(ts)
        self._busy(False)
        self._after_analysis()
        self.clip_bin.refresh()
        self.set_tool("highlight")
        self.statusBar().showMessage(
            f"분석 완료 — 트랙 {len(ts.tracks)}개, 컷 {len(ts.cuts)}개. "
            f"영상에서 선수를 클릭해 강조하세요")

    # ---------------------------------------------------------------- 전체 분석
    def analyze_all(self) -> None:
        """목록에서 아직 분석되지 않은 클립을 순서대로 전부 분석한다."""
        if self.worker:
            return
        targets = [i for i, p in enumerate(self.session.clips) if self._needs_analysis(p)]
        if not targets:
            QMessageBox.information(
                self, "분석할 클립 없음",
                "모든 클립이 이미 분석되었거나 원본 영상을 찾을 수 없습니다.")
            return
        if QMessageBox.question(
                self, "모든 클립 분석",
                f"클립 {len(targets)}개를 순서대로 분석합니다.\n품질: {self.preset}\n\n"
                "클립 수에 따라 시간이 오래 걸릴 수 있습니다. 계속할까요?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes) != QMessageBox.Yes:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._batch_queue = targets
        self._batch_total = len(targets)
        self._batch_done = 0
        self._batch_failures = []
        self._batch_current_idx = -1
        self._batch_resolved = False
        self._busy(True)
        self._analyze_next_in_batch()

    def _analyze_next_in_batch(self) -> None:
        """큐에서 다음 클립을 꺼내 분석한다.

        진행은 **오직 `finished` 신호에서만** 트리거한다. `done`/`failed` 핸들러는
        결과만 기록하고 여기를 다시 부르지 않는다.
        """
        if not self._batch_queue:
            self._busy(False)
            ok = self._batch_total - len(self._batch_failures)
            self.statusBar().showMessage(f"전체 분석 완료 — {ok}/{self._batch_total}개", 8000)
            if self._batch_failures:
                detail = "\n".join(f"- {f}" for f in self._batch_failures[:10])
                QMessageBox.warning(
                    self, "일부 클립 분석 실패",
                    f"{len(self._batch_failures)}개 클립 분석에 실패했습니다.\n\n"
                    f"{detail}\n\n클립 목록에서 해당 클립을 골라 다시 분석하세요.")
            return
        idx = self._batch_queue.pop(0)
        self._batch_current_idx = idx
        self._batch_resolved = False
        proj = self.session.clips[idx]
        name = self.session.name_of(idx)
        self._batch_done += 1
        n = self._batch_done
        self.statusBar().showMessage(f"[{n}/{self._batch_total}] {name} 분석 중…")
        end = proj.export_end
        if not end:
            try:
                end = probe(proj.video_path).n_frames
            except Exception:
                end = 0
        w = AnalyzeWorker(proj.video_path, self.preset, proj.export_start, end, self)
        w.progress.connect(self._progress)
        w.status.connect(lambda m, nm=name, i=n: self.statusBar().showMessage(
            f"[{i}/{self._batch_total}] {nm}: {m}"))
        w.done.connect(lambda ts, i=idx: self._on_batch_clip_analyzed(i, ts))
        w.failed.connect(lambda tb, i=idx, nm=name: self._on_batch_clip_failed(i, nm, tb))
        w.finished.connect(self._on_batch_worker_finished)
        self.worker = w
        # 워치독 — 분석 스레드가 done/failed 도 없이 그냥 멈춰버리는 경우를 대비한다
        # (torch MPS 가 세 번째 연속 추적 세션에서 조용히 죽는 걸 실제로 봤다).
        QTimer.singleShot(180_000, lambda w=w: self._watchdog_check(w))
        w.start()

    def _watchdog_check(self, w) -> None:
        if self.worker is w and not self._batch_resolved:
            w.cancel()
            name = self.session.name_of(self._batch_current_idx)
            self._batch_failures.append(f"{name}: 3분 넘게 응답 없어 건너뜀")
            self._batch_resolved = True
            self.worker = None
            self._analyze_next_in_batch()

    def _on_batch_clip_analyzed(self, index: int, ts) -> None:
        """결과만 기록한다 — 큐 진행은 `_on_batch_worker_finished` 가 담당."""
        self._batch_resolved = True
        if ts is None:
            self._batch_failures.append(f"{self.session.name_of(index)}: 취소됨")
            return
        proj = self.session.clips[index]
        proj.attach(ts)
        if index == self.session.current:
            self.project = proj
            self._after_analysis()
            self._refresh_frame()
        self.clip_bin.refresh()

    def _on_batch_clip_failed(self, index: int, name: str, tb: str) -> None:
        self._batch_resolved = True
        reason = tb.strip().splitlines()[-1] if tb else "알 수 없는 오류"
        self._batch_failures.append(f"{name}: {reason}")

    def _on_batch_worker_finished(self) -> None:
        """스레드가 끝났다 — 성공/실패/조용한 죽음 어느 경우든 항상 호출된다.

        `done`/`failed` 신호가 하나도 안 왔는데 스레드는 끝난 경우(네이티브 오류로
        조용히 죽는 경우가 실제로 있었다)를 여기서 잡아 실패로 알리고 큐를 계속
        진행한다. 이게 큐 전진의 유일한 지점이다.
        """
        if not self._batch_resolved:
            name = self.session.name_of(self._batch_current_idx)
            self._batch_failures.append(f"{name}: 응답 없이 종료됨 (원인 불명)")
        self.worker = None
        self._analyze_next_in_batch()

    # ---------------------------------------------------------------- 내보내기
    def export(self) -> None:
        if self.project.trackset is None or self.worker:
            return
        if self.project.enabled_count == 0 and not self.project.markers \
                and not self.project.timeline.captions and not self.project.timeline.freezes:
            QMessageBox.information(self, "강조된 선수 없음",
                                    "강조할 선수를 먼저 고르세요.\n"
                                    "선수 강조 도구(H)로 영상에서 선수를 클릭하면 됩니다.")
            return
        default = str(Path(self.project.video_path).with_name(
            Path(self.project.video_path).stem + "_highlight.mp4"))
        path, _ = QFileDialog.getSaveFileName(self, "내보내기", default, "MP4 영상 (*.mp4)")
        if not path:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._busy(True)
        w = ExportWorker(self.project, path, with_audio=True, parent=self)
        self._run_export(w)

    def export_all(self) -> None:
        """목록의 모든 클립을 순서대로 하나의 영상으로 내보낸다 (분석된 클립만)."""
        self._export_all(require_analysis=True)

    def export_all_raw(self) -> None:
        """분석 여부와 무관하게, 올린 영상을 목록 순서 그대로 이어 붙인다.

        강조·대형선은 붙지 않지만(트랙셋이 필요), 자막·정지 카드·구역은 반영된다.
        """
        self._export_all(require_analysis=False)

    def _export_all(self, require_analysis: bool) -> None:
        if self.worker:
            return
        pool = self.session.ready_clips if require_analysis else self.session.clips_with_video
        if not pool:
            if require_analysis:
                QMessageBox.information(self, "분석된 클립 없음",
                                        "각 클립을 먼저 [분석]해야 이어 붙일 수 있습니다.")
            else:
                QMessageBox.information(self, "영상 없음", "먼저 영상을 추가하세요.")
            return
        skipped = len(self.session.clips) - len(pool)
        w, h, fps = self.session.output_spec()
        title = "전체 이어붙이기" if require_analysis else "분석 없이 이어붙이기"
        msg = (f"클립 {len(pool)}개를 목록 순서대로 이어 붙입니다.\n"
               f"출력 규격: {w}×{h} · {fps:.0f}fps (가장 큰 클립 기준)\n"
               f"해상도가 다른 클립은 비율을 유지한 채 여백이 들어갑니다.")
        if not require_analysis:
            msg += "\n\n분석하지 않은 원본 그대로 붙입니다 — 강조·대형선은 들어가지 않습니다."
        if skipped:
            reason = "분석되지 않은" if require_analysis else "원본 영상을 찾을 수 없는"
            msg += f"\n\n{reason} 클립 {skipped}개는 제외됩니다."
        if QMessageBox.question(self, title, msg + "\n\n계속할까요?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.Yes) != QMessageBox.Yes:
            return
        base = Path(pool[0].video_path).with_name("combined.mp4")
        path, _ = QFileDialog.getSaveFileName(self, title, str(base), "MP4 영상 (*.mp4)")
        if not path:
            return
        if self.timer.isActive():
            self.toggle_play()
        self._busy(True)
        wk = ConcatExportWorker(self.session, path, with_audio=True,
                                require_analysis=require_analysis, parent=self)
        self._run_export(wk)

    def _run_export(self, w) -> None:
        w.progress.connect(self._progress)
        w.status.connect(self.statusBar().showMessage)
        w.done.connect(self._on_exported)
        w.failed.connect(self._on_failed)
        w.finished.connect(lambda: setattr(self, "worker", None))
        self.worker = w
        w.start()

    def _on_exported(self, path: str) -> None:
        self._busy(False)
        if path:
            self.statusBar().showMessage(f"내보내기 완료: {path}", 8000)
        else:
            self.statusBar().showMessage("내보내기가 취소되었습니다", 4000)

    def _on_failed(self, tb: str) -> None:
        self._busy(False)
        self.statusBar().showMessage("작업 실패", 4000)
        QMessageBox.critical(self, "오류", tb[-2000:])

    def closeEvent(self, ev) -> None:
        # 창을 닫는 도중에 큐가 다음 클립 워커를 새로 띄우면 안 된다
        self._batch_queue = []
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
        if self.reader:
            self.reader.close()
        super().closeEvent(ev)
