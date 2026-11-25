"""다크 테마 · 색 토큰 · 아이콘.

영상 편집기는 화면이 영상에 집중되도록 어둡게 둔다. 색은 이 파일의 토큰으로만
쓰고, 위젯마다 색을 하드코딩하지 않는다.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication

BG = "#16181c"          # 창 바탕
PANEL = "#1f2228"       # 패널
RAISED = "#2a2e36"      # 입력칸·버튼
BORDER = "#323741"
TEXT = "#e7e9ee"
MUTED = "#8d93a0"
ACCENT = "#4c8dff"
ACCENT_BG = "#1d3459"
DANGER = "#e5534b"

# 속성 패널의 빠른 색 견본 (BGR — 렌더러가 BGR 을 쓴다)
SWATCHES: list[tuple[int, int, int]] = [
    (255, 255, 255),   # 흰색
    (60, 76, 231),     # 빨강
    (230, 140, 50),    # 파랑
    (40, 180, 250),    # 노랑
    (80, 190, 70),     # 초록
    (220, 100, 200),   # 보라
]

# 타임라인 막대 색 (종류별)
TRACK_COLORS = {
    "player": QColor("#5b8def"),
    "marker": QColor("#e5706b"),
    "caption": QColor("#9a86e8"),
    "freeze": QColor("#e0b04c"),
}

QSS = f"""
* {{ font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#root {{ background: {BG}; }}
QWidget#panel, QFrame#panel {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 8px; }}
QLabel#muted {{ color: {MUTED}; font-size: 12px; }}
QLabel#section {{ color: {MUTED}; font-size: 11px; font-weight: 600;
    letter-spacing: 0.5px; padding-top: 6px; }}
QLabel#title {{ font-size: 15px; font-weight: 600; }}
QToolBar {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 10px; spacing: 6px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar QLabel {{ color: {MUTED}; }}

QPushButton, QToolButton {{ background: {RAISED}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 5px 10px; }}
QPushButton:hover, QToolButton:hover {{ border-color: #4a505c; }}
QPushButton:pressed, QToolButton:pressed {{ background: #333844; }}
QPushButton:disabled, QToolButton:disabled {{ color: #5b606b; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT_BG};
    border-color: {ACCENT}; color: #cfe0ff; }}
QPushButton#primary, QToolButton#primary {{ background: {ACCENT}; border-color: {ACCENT};
    color: white; font-weight: 600; }}
QPushButton#primary:hover, QToolButton#primary:hover {{ background: #5d9aff; }}
QPushButton#primary:disabled, QToolButton#primary:disabled {{ background: {RAISED};
    border-color: {BORDER}; color: #5b606b; }}
QPushButton#ghost, QToolButton#ghost {{ background: transparent; border-color: transparent; }}
QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {RAISED}; }}
QPushButton#danger {{ color: {DANGER}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton[popupMode="1"] {{ padding-right: 22px; }}
QToolButton::menu-button {{ border: none; border-left: 1px solid rgba(255,255,255,0.15);
    width: 18px; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {RAISED}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 4px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_BG}; }}

QSlider::groove:horizontal {{ height: 4px; background: {RAISED}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px; }}

QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #4a505c; background: {RAISED}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border-radius: 8px; padding: 4px; margin: 2px 0; }}
QListWidget::item:selected {{ background: {ACCENT_BG}; border: 1px solid {ACCENT}; }}
QListWidget::item:hover:!selected {{ background: {RAISED}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: #3a3f4a; border-radius: 4px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT_BG}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMenu::indicator {{ width: 12px; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item:selected {{ background: {RAISED}; }}

QProgressBar {{ background: {RAISED}; border: none; border-radius: 4px; height: 8px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QSplitter::handle {{ background: {BG}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    padding: 4px 6px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in [(QPalette.Window, BG), (QPalette.Base, RAISED),
                    (QPalette.AlternateBase, PANEL), (QPalette.Text, TEXT),
                    (QPalette.WindowText, TEXT), (QPalette.Button, RAISED),
                    (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, PANEL),
                    (QPalette.ToolTipText, TEXT), (QPalette.PlaceholderText, MUTED)]:
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def icon(name: str, color: str = TEXT) -> QIcon:
    """Phosphor 아이콘. qtawesome 이 없거나 이름이 틀려도 앱이 죽지 않게 빈 아이콘."""
    try:
        import qtawesome as qta

        return qta.icon(f"ph.{name}", color=color, color_disabled="#5b606b")
    except Exception:
        return QIcon()


def dot_icon(color: QColor, size: int = 12) -> QIcon:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap

    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(255, 255, 255, 90))
    p.setBrush(color)
    p.drawEllipse(2, 2, size * 2 - 4, size * 2 - 4)
    p.end()
    return QIcon(pm)


def bgr_to_qcolor(bgr: tuple[int, int, int]) -> QColor:
    b, g, r = bgr
    return QColor(r, g, b)
