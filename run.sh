#!/bin/bash
# Football Highlighter 실행 스크립트.
#
#   ./run.sh                 앱 실행 (처음이면 가상환경을 만들고 의존성 설치)
#   ./run.sh clip.mp4        영상을 열면서 실행
#   ./run.sh --setup         가상환경을 (다시) 만든다
#
# 환경변수
#   FBEDIT_VENV     가상환경 위치   (기본: ~/.venvs/tactical-analysis)
#   FBEDIT_PYTHON   가상환경을 만들 Python (기본: 아래 후보에서 자동 선택)
#
# 가상환경은 프로젝트 폴더가 아니라 ~/.venvs 에 둔다. 프로젝트가 iCloud 동기화 폴더
# (~/Desktop, ~/Documents) 안에 있으면 수백 MB 짜리 가상환경이 클라우드로 오프로드돼,
# 앱이 파일을 읽으려는 순간 아무 반응 없이 멈출 수 있다.
set -e
cd "$(dirname "$0")"

VENV="${FBEDIT_VENV:-$HOME/.venvs/tactical-analysis}"

# Anaconda Python 은 쓰지 않는다 — 그 폴더의 qt.conf 가 Qt 플러그인 경로를 덮어써
# 창이 뜨지 않는다. python.org → Homebrew 순으로 찾는다.
find_python() {
    if [ -n "$FBEDIT_PYTHON" ]; then echo "$FBEDIT_PYTHON"; return; fi
    for v in 3.13 3.12 3.11; do
        for p in "/Library/Frameworks/Python.framework/Versions/$v/bin/python3" \
                 "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v"; do
            [ -x "$p" ] && { echo "$p"; return; }
        done
    done
}

if [ "$1" = "--setup" ] || [ ! -x "$VENV/bin/python" ]; then
    PY_BASE="$(find_python)"
    if [ -z "$PY_BASE" ]; then
        echo "Python 3.11 ~ 3.13 을 찾지 못했습니다." >&2
        echo "https://www.python.org/downloads/macos/ 에서 Python 3.13 을 설치한 뒤 다시 실행하세요." >&2
        echo "(Anaconda Python 은 Qt 창이 뜨지 않아 지원하지 않습니다)" >&2
        exit 1
    fi
    if [ -f "$(dirname "$("$PY_BASE" -c 'import sys,os;print(os.path.realpath(sys.executable))')")/qt.conf" ]; then
        echo "$PY_BASE 는 Anaconda 계열이라 쓸 수 없습니다. FBEDIT_PYTHON 으로 다른 Python 을 지정하세요." >&2
        exit 1
    fi
    echo "가상환경 생성: $VENV  (Python: $PY_BASE)"
    rm -rf "$VENV"
    mkdir -p "$(dirname "$VENV")"
    "$PY_BASE" -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -r requirements.txt
    [ "$1" = "--setup" ] && exit 0
fi

exec "$VENV/bin/python" app.py "$@"
