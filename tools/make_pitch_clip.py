"""축구 중계와 유사한 조건의 테스트 클립 생성기.

실제 중계 클립이 없어도 파이프라인을 현실적인 난이도로 검증하기 위한 도구.
  - 105x68m 경기장을 탑다운으로 그린 뒤 호모그래피로 중계 카메라 시점으로 왜곡
  - 실제 인물 사진에서 세그먼테이션으로 오려낸 스프라이트를 원근 크기로 합성
  - 카메라 팬 + 두 팀의 4-4-2 / 4-3-3 대형 이동

여기서 쓰는 호모그래피(H)와 선수별 라인(DF/MF/FW)은 그대로 기능②(대형 표시)의
정답 데이터로 쓸 수 있다.

    python tools/make_pitch_clip.py out.mp4 --frames 150
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fbedit.core.video import VideoWriter  # noqa: E402

PPM = 10                      # px per meter (탑다운)
PITCH_L, PITCH_W = 105, 68    # m
TOP_W, TOP_H = PITCH_L * PPM, PITCH_W * PPM

TEAM_A = (60, 60, 235)        # BGR 빨강
TEAM_B = (235, 235, 235)      # 흰색


# ---------------------------------------------------------------- 경기장
def draw_topdown() -> np.ndarray:
    img = np.zeros((TOP_H, TOP_W, 3), np.uint8)
    for i in range(0, TOP_W, PPM * 6):  # 잔디 줄무늬
        shade = 8 if (i // (PPM * 6)) % 2 else -8
        img[:, i:i + PPM * 6] = np.clip(np.array([52, 124, 58]) + shade, 0, 255)

    W, L = (255, 255, 255), 2
    line = lambda a, b: cv2.line(img, a, b, W, L, cv2.LINE_AA)
    m = lambda x, y: (int(x * PPM), int(y * PPM))

    cv2.rectangle(img, m(0, 0), m(PITCH_L - 0.2, PITCH_W - 0.2), W, L)
    line(m(PITCH_L / 2, 0), m(PITCH_L / 2, PITCH_W))
    cv2.circle(img, m(PITCH_L / 2, PITCH_W / 2), int(9.15 * PPM), W, L, cv2.LINE_AA)
    cv2.circle(img, m(PITCH_L / 2, PITCH_W / 2), 4, W, -1, cv2.LINE_AA)

    for side in (0, 1):
        x0 = 0 if side == 0 else PITCH_L
        sgn = 1 if side == 0 else -1
        cy = PITCH_W / 2
        cv2.rectangle(img, m(x0, cy - 20.16), m(x0 + sgn * 16.5, cy + 20.16), W, L)
        cv2.rectangle(img, m(x0, cy - 9.16), m(x0 + sgn * 5.5, cy + 9.16), W, L)
        cv2.circle(img, m(x0 + sgn * 11, cy), 4, W, -1, cv2.LINE_AA)
    return img


def homography(pan: float = 0.0) -> np.ndarray:
    """탑다운 → 중계 카메라 시점. pan 은 좌우 이동(px)."""
    src = np.float32([[0, 0], [TOP_W, 0], [TOP_W, TOP_H], [0, TOP_H]])
    dst = np.float32([[330 + pan, 158], [952 + pan, 158],
                      [1428 + pan, 700], [-146 + pan, 700]])
    return cv2.getPerspectiveTransform(src, dst)


def project(H: np.ndarray, x_m: float, y_m: float) -> tuple[float, float]:
    p = H @ np.array([x_m * PPM, y_m * PPM, 1.0])
    return float(p[0] / p[2]), float(p[1] / p[2])


# ---------------------------------------------------------------- 스프라이트
def person_sprites(min_h: int = 90) -> list[np.ndarray]:
    """번들 이미지에서 사람 마스크를 뽑아 RGBA 컷아웃으로 만든다."""
    from ultralytics import YOLO

    seg = YOLO("yolo11n-seg.pt")
    root = Path(__import__("ultralytics").__file__).parent / "assets"
    out: list[np.ndarray] = []
    for name in ("bus.jpg", "zidane.jpg"):
        img = cv2.imread(str(root / name))
        r = seg(img, classes=[0], conf=0.4, verbose=False)[0]
        if r.masks is None:
            continue
        for mask, box in zip(r.masks.data.cpu().numpy(), r.boxes.xyxy.cpu().numpy()):
            m = cv2.resize(mask, (img.shape[1], img.shape[0]))
            x1, y1, x2, y2 = (int(v) for v in box)
            if y2 - y1 < min_h or x2 - x1 < 20:
                continue
            crop = img[y1:y2, x1:x2]
            a = (np.clip(m[y1:y2, x1:x2], 0, 1) * 255).astype(np.uint8)
            a = cv2.erode(a, np.ones((3, 3), np.uint8))
            out.append(np.dstack([crop, a]))
    if not out:
        raise RuntimeError("스프라이트를 추출하지 못했습니다")
    return out


def tint(sprite: np.ndarray, color, amount: float = 0.5) -> np.ndarray:
    s = sprite.copy()
    s[..., :3] = np.clip(s[..., :3] * (1 - amount) + np.array(color) * amount, 0, 255)
    return s


def paste(dst: np.ndarray, sprite: np.ndarray, cx: float, feet_y: float, h: int) -> None:
    if h < 8:
        return
    w = max(4, int(h * sprite.shape[1] / sprite.shape[0]))
    s = cv2.resize(sprite, (w, h), interpolation=cv2.INTER_AREA)
    x0, y0 = int(cx - w / 2), int(feet_y - h)
    H, W = dst.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0, dx1, dy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    p = s[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0]
    a = p[..., 3:4].astype(np.float32) / 255.0
    roi = dst[dy0:dy1, dx0:dx1].astype(np.float32)
    dst[dy0:dy1, dx0:dx1] = (roi * (1 - a) + p[..., :3] * a).astype(np.uint8)


# ---------------------------------------------------------------- 대형
# (x_m, y_m, 라인) — 라인은 기능② 검증용 정답
def formation_442(flip: bool) -> list[tuple[float, float, str]]:
    rows = [(6, [34], "GK"), (22, [12, 26, 42, 56], "DF"),
            (42, [12, 26, 42, 56], "MF"), (58, [26, 42], "FW")]
    out = []
    for x, ys, role in rows:
        for y in ys:
            out.append((PITCH_L - x if flip else x, y, role))
    return out


def formation_433(flip: bool) -> list[tuple[float, float, str]]:
    rows = [(6, [34], "GK"), (24, [12, 26, 42, 56], "DF"),
            (44, [20, 34, 48], "MF"), (62, [16, 34, 52], "FW")]
    out = []
    for x, ys, role in rows:
        for y in ys:
            out.append((PITCH_L - x if flip else x, y, role))
    return out


def make(out_path: str, frames: int = 150, fps: float = 25.0,
         size: tuple[int, int] = (1280, 720)) -> str:
    W, H = size
    top = draw_topdown()
    sprites = person_sprites()
    print(f"스프라이트 {len(sprites)}개 추출")

    team_a = [(p, TEAM_A, i) for i, p in enumerate(formation_442(False))]
    team_b = [(p, TEAM_B, i) for i, p in enumerate(formation_433(True))]
    players = team_a + team_b

    stands = np.full((H, W, 3), (58, 52, 48), np.uint8)
    cv2.randn(noise := np.zeros((H, W, 3), np.int16), 0, 26)
    stands = np.clip(stands.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    with VideoWriter(out_path, size=(W, H), fps=fps) as writer:
        for fi in range(frames):
            t = fi / fps
            pan = 110 * math.sin(2 * math.pi * t / 6.0)
            Hm = homography(pan)

            frame = stands.copy()
            cv2.warpPerspective(top, Hm, (W, H), dst=frame,
                                borderMode=cv2.BORDER_TRANSPARENT)

            drawn = []
            for (px, py, role), color, idx in players:
                # 공을 향한 완만한 전진 + 개인별 흔들림
                ox = 5.0 * math.sin(2 * math.pi * t / 7.0 + idx * 0.4)
                oy = 3.0 * math.sin(2 * math.pi * t / 4.5 + idx * 0.9)
                ix, iy = project(Hm, px + ox, py + oy)
                ph = int(26 + (iy - 158) / (700 - 158) * 62)   # 원근에 따른 키
                drawn.append((iy, ix, ph, tint(sprites[idx % len(sprites)], color)))

            for iy, ix, ph, spr in sorted(drawn):   # 먼 선수부터
                paste(frame, spr, ix, iy, ph)

            writer.write(frame)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--frames", type=int, default=150)
    ap.add_argument("--fps", type=float, default=25.0)
    a = ap.parse_args()
    print("생성:", make(a.out, a.frames, a.fps))
