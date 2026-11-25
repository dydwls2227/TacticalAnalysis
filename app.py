"""Football Highlighter — 축구 영상 선수 강조 편집기.

    python app.py [영상경로]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _check_hidden_flag() -> None:
    """venv 에 macOS 숨김 플래그(UF_HIDDEN)가 걸렸는지 본다.

    걸리면 Qt 는 플러그인 폴더를 '빈 폴더'로 본다 — QDir 이 기본적으로 숨김 항목을
    제외하기 때문이다. os.listdir 로는 파일이 멀쩡히 보여서 원인을 찾기 어렵고,
    증상은 Anaconda 충돌과 똑같은 메시지로 나타난다.
    """
    import stat

    try:
        import PySide6
    except ImportError:
        return
    d = Path(PySide6.__file__).parent / "Qt" / "plugins" / "platforms"
    if not d.is_dir():
        return
    hidden = [p for p in d.glob("*.dylib")
              if getattr(p.stat(), "st_flags", 0) & stat.UF_HIDDEN]
    if not hidden:
        return
    venv = Path(sys.prefix)
    sys.stderr.write(f"""
[실행 불가] Qt 플러그인 파일에 macOS 숨김 플래그가 걸려 있습니다.
Qt 는 숨김 파일을 무시하므로 플러그인 폴더를 빈 폴더로 봅니다.

  chflags -R nohidden {venv}

위 명령으로 해제한 뒤 다시 실행하세요.
""")
    raise SystemExit(1)


def _check_qt_conflict() -> None:
    """Anaconda 기반 venv 를 미리 걸러낸다.

    venv 의 python 은 기반 인터프리터 디렉터리를 가리키는 심볼릭 링크다. 그 디렉터리에
    qt.conf 가 있으면(Anaconda 가 그렇다) QApplication 생성 시 Qt 가 그 파일을 읽어
    플러그인 경로를 conda 쪽으로 덮어쓴다. conda 에는 Qt6 플러그인이 없어
    `Could not find the Qt platform plugin "cocoa" in ""` 로 죽는데, 원인이 전혀
    드러나지 않는 메시지라 여기서 미리 잡아 안내한다.
    """
    base_bin = Path(getattr(sys, "_base_executable", sys.executable)).resolve().parent
    if not (base_bin / "qt.conf").exists():
        return
    sys.stderr.write(f"""
[실행 불가] 이 venv 는 {base_bin} 의 Python 으로 만들어졌습니다.
그 디렉터리의 qt.conf 가 Qt 플러그인 경로를 덮어써서 창을 띄울 수 없습니다.

Anaconda 가 아닌 Python(python.org 또는 Homebrew)으로 가상환경을 다시 만드세요:

  ./run.sh --setup

주의: conda 가 활성화된 셸(프롬프트에 (base))에서 `python3 -m venv` 를 쓰면
Anaconda 기반으로 다시 만들어집니다. run.sh 는 Anaconda 를 피해 Python 을 찾습니다.
""")
    raise SystemExit(1)


_check_qt_conflict()
_check_hidden_flag()

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from fbedit.ui import theme  # noqa: E402
from fbedit.ui.main_window import MainWindow  # noqa: E402


def _install_excepthook() -> None:
    """Qt 슬롯 안에서 난 예외는 기본적으로 stderr 로만 새어나가 화면상 '아무 반응
    없음'처럼 보인다. 항상 눈에 띄게 만든다."""
    import traceback

    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        print(text, file=sys.stderr)
        if QApplication.instance():
            QMessageBox.critical(None, "오류", text[-2000:])

    sys.excepthook = hook


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Football Highlighter")
    theme.apply(app)
    _install_excepthook()

    win = MainWindow()
    win.show()

    # 인자로 영상·프로젝트를 주면 바로 연다
    for arg in sys.argv[1:]:
        path = Path(arg)
        if path.exists():
            if path.suffix.lower() == ".fbproj":
                win._open_project_path(str(path.resolve()))
            else:
                win._open_video_path(str(path.resolve()))
            break

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
