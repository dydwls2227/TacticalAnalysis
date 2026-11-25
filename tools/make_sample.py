"""테스트용 샘플 클립 생성기.

실제 중계 클립이 없을 때 파이프라인(탐지→추적→렌더→내보내기)을 점검하기 위한 도구.
ultralytics 번들 이미지 위를 카메라가 팬/줌 하는 영상으로 만들어, 방송 영상의
카메라 이동 상황을 흉내낸다.

    python tools/make_sample.py out.mp4 --frames 100
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fbedit.core.video import VideoWriter  # noqa: E402


def find_asset(name: str = "zidane.jpg") -> Path:
    import ultralytics

    p = Path(ultralytics.__file__).parent / "assets" / name
    if not p.exists():
        raise FileNotFoundError(f"샘플 이미지를 찾을 수 없습니다: {p}")
    return p


def make_clip(out: str, frames: int = 100, fps: float = 25.0,
              size: tuple[int, int] = (1280, 720), asset: str = "zidane.jpg",
              cut_at: int | None = None) -> str:
    img = cv2.imread(str(find_asset(asset)))
    alt = cv2.imread(str(find_asset("bus.jpg")))
    W, H = size

    with VideoWriter(out, size=(W, H), fps=fps) as w:
        for i in range(frames):
            t = i / max(1, frames - 1)
            src = alt if (cut_at is not None and i >= cut_at) else img
            h, w_ = src.shape[:2]

            # 팬 + 완만한 줌 (방송 카메라 흉내)
            zoom = 1.0 + 0.18 * math.sin(math.pi * t)
            ch, cw = int(h / zoom), int(w_ / zoom)
            max_dx, max_dy = w_ - cw, h - ch
            x0 = int(max_dx * (0.5 + 0.5 * math.sin(2 * math.pi * t)))
            y0 = int(max_dy * 0.5)
            crop = src[y0:y0 + ch, x0:x0 + cw]
            w.write(cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--frames", type=int, default=100)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("--cut-at", type=int, default=None, help="이 프레임에서 다른 장면으로 컷")
    a = ap.parse_args()
    print("생성:", make_clip(a.out, a.frames, a.fps, cut_at=a.cut_at))
