"""작은 편집용 다이얼로그 모음."""
from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QDialog, QDialogButtonBox, QFormLayout,
                               QLineEdit, QPushButton, QVBoxLayout)


class PlayerEditDialog(QDialog):
    """영상에서 선수를 더블클릭하면 뜨는 창 — 등번호·이름·색을 한 번에 고친다.

    예전엔 더블클릭 = 색 선택창이 전부였다. 이름을 바꾸려면 오른쪽 목록까지 가서
    셀을 찾아 더블클릭해야 했는데, 그 두 동작을 하나로 합쳤다.
    """

    def __init__(self, name: str, number: str, color: tuple[int, int, int],
                title: str = "선수 정보", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._color = color

        self.txt_number = QLineEdit(number)
        self.txt_number.setPlaceholderText("예: 7")
        self.txt_number.setFixedWidth(70)

        self.txt_name = QLineEdit(name)
        self.txt_name.setPlaceholderText("예: 손흥민")

        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(90)
        self.btn_color.setToolTip("강조 색 선택")
        self._update_swatch()
        self.btn_color.clicked.connect(self._pick_color)

        form = QFormLayout()
        form.addRow("등번호", self.txt_number)
        form.addRow("이름", self.txt_name)
        form.addRow("색", self.btn_color)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(buttons)

        self.txt_name.setFocus()
        self.txt_name.selectAll()

    def _update_swatch(self) -> None:
        b, g, r = self._color
        lum = 0.114 * b + 0.587 * g + 0.299 * r
        fg = "#222" if lum > 165 else "#eee"
        self.btn_color.setStyleSheet(
            f"background-color: rgb({r},{g},{b}); color: {fg};")
        self.btn_color.setText(f"{r:02x}{g:02x}{b:02x}".upper())

    def _pick_color(self) -> None:
        b, g, r = self._color
        c = QColorDialog.getColor(QColor(r, g, b), self, "색 선택")
        if c.isValid():
            self._color = (c.blue(), c.green(), c.red())
            self._update_swatch()

    def values(self) -> tuple[str, str, tuple[int, int, int]]:
        """(이름, 등번호, 색) — dialog.exec() 이 Accepted 일 때만 의미 있다."""
        return self.txt_name.text().strip(), self.txt_number.text().strip(), self._color
