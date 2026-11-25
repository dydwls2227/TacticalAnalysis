# Third-Party Notices

이 프로젝트는 아래 오픈소스를 사용합니다. 이 저장소에는 이들의 소스나 바이너리가 들어 있지 않으며,
`pip install -r requirements.txt` 로 각 배포처에서 설치됩니다. 각 라이선스 전문은 해당 프로젝트에서 확인하세요.

| 구성 요소 | 용도 | 라이선스 |
|---|---|---|
| [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) (코드 · YOLO11 가중치) | 선수 탐지 · BoT-SORT 추적 | AGPL-3.0 |
| [PyTorch](https://github.com/pytorch/pytorch) | 추론 엔진 | BSD-3-Clause |
| [PySide6 / Qt for Python](https://www.qt.io/qt-for-python) | 데스크톱 UI | LGPL-3.0 (동적 링크로 사용) |
| [OpenCV](https://github.com/opencv/opencv-python) | 영상 처리 | Apache-2.0 (패키징: MIT) |
| [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg) | 영상 인코딩 · 오디오 먹싱 | BSD-2-Clause (번들된 FFmpeg 바이너리: LGPL/GPL) |
| [lap](https://github.com/gatagat/lap) | 추적 매칭(선형 할당) | BSD-2-Clause |
| [QtAwesome](https://github.com/spyder-ide/qtawesome) | 아이콘 | MIT |
| [Phosphor Icons](https://github.com/phosphor-icons/core) (QtAwesome 포함) | 아이콘 글꼴 | MIT |
| [NumPy](https://github.com/numpy/numpy) | 수치 연산 | BSD-3-Clause |
| [Pillow](https://github.com/python-pillow/Pillow) | 이름표 · 자막 렌더링 | MIT-CMU (HPND) |
| [fontTools](https://github.com/fonttools/fonttools) | 한글 지원 폰트 검사 | MIT |

폰트는 사용자의 macOS 시스템 폰트를 그대로 읽어 쓰며, 이 저장소에 포함하지 않습니다.
