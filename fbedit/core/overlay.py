"""강조 표시 오버레이 렌더링.

선수 발밑에서 솟는 유리관 기둥(bar), 발밑 원판(ellipse), HUD 링(ring), 말풍선
이름표(label)를 합성한다. 등장·퇴장·반복 효과는 `anim.Anim` 이 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .anim import STATIC, Anim, ease_out_back, ease_out_cubic
from .tracking import Box

# BGR. 방송 그래픽에서 잘 읽히는 고채도 색들
PALETTE: list[tuple[int, int, int]] = [
    (30, 110, 255),    # 주황
    (255, 190, 40),    # 시안블루
    (60, 230, 90),     # 초록
    (230, 80, 230),    # 자홍
    (40, 240, 250),    # 노랑
    (255, 90, 90),     # 파랑
    (80, 80, 250),     # 빨강
    (240, 240, 240),   # 흰색
]

_FONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
]


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont:
    """한글 지원 볼드 폰트. .ttc 컬렉션에서 Bold 페이스를 찾아 쓴다."""
    for path in _FONT_CANDIDATES:
        if not Path(path).exists():
            continue
        if path.endswith(".ttc"):
            for idx in range(12):
                try:
                    f = ImageFont.truetype(path, size, index=idx)
                except Exception:
                    break
                if "Bold" in " ".join(f.getname()):
                    return f
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


@dataclass
class HighlightStyle:
    color: tuple[int, int, int] = PALETTE[0]  # BGR
    alpha: float = 0.55
    bar: bool = True
    ellipse: bool = True
    ring: bool = False        # 발밑 타원 테두리 (대형선이 여기에 붙는다)
    # 링만 별도 색을 쓸 수 있다. None 이면 color 를 따른다.
    # 기본 동작은 Project 가 대형선 색을 여기에 넣어 링과 선을 한 색으로 통일하는 것.
    ring_color: tuple[int, int, int] | None = None
    label: str = ""
    show_label: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["color"] = list(self.color)
        d["ring_color"] = list(self.ring_color) if self.ring_color else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HighlightStyle":
        # 예전 버전 프로젝트에는 없는 필드가 있으므로 아는 것만 취한다
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        d["color"] = tuple(d["color"])
        if d.get("ring_color"):
            d["ring_color"] = tuple(d["ring_color"])
        return cls(**d)


def color_for(tid: int) -> tuple[int, int, int]:
    return PALETTE[tid % len(PALETTE)]


def _blend(frame: np.ndarray, x0: int, y0: int, alpha: np.ndarray,
           color: tuple[int, int, int], strength: float) -> None:
    """알파 마스크를 프레임 ROI에 in-place 합성. 화면 밖은 잘라낸다."""
    H, W = frame.shape[:2]
    h, w = alpha.shape
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    a = alpha[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)][..., None] * strength
    roi = frame[dy0:dy1, dx0:dx1].astype(np.float32)
    col = np.array(color, dtype=np.float32)
    frame[dy0:dy1, dx0:dx1] = np.clip(roi * (1.0 - a) + col * a, 0, 255).astype(np.uint8)


@lru_cache(maxsize=256)
def _label_chip(text: str, size: int, color: tuple[int, int, int]) -> np.ndarray:
    """이름표 칩 이미지(RGBA) — 아래쪽 가운데에 말풍선 꼬리가 달린 둥근 사각형.

    모양은 (글자, 크기, 색)에만 의존하고 위치는 매 프레임 달라질 뿐이라 캐시한다."""
    font = _font(size)
    pad_x, pad_y = int(size * 0.6), int(size * 0.34)
    tail = max(4, int(size * 0.38))

    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    l, t, r, b = tmp.textbbox((0, 0), text, font=font)
    tw, th = r - l, b - t
    cw, ch = tw + pad_x * 2, th + pad_y * 2

    # 2배로 그린 뒤 줄여 가장자리를 매끈하게 만든다(PIL 도형은 안티에일리어싱이 없다)
    k = 2
    chip = Image.new("RGBA", (cw * k, (ch + tail) * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(chip)
    b_, g, r_ = color
    fill = (r_, g, b_, 238)
    d.rounded_rectangle([0, 0, cw * k - 1, ch * k - 1], radius=int(ch * k * 0.22), fill=fill)
    mx = cw * k // 2
    d.polygon([(mx - tail * k, ch * k - 2), (mx + tail * k, ch * k - 2),
               (mx, (ch + tail) * k - 1)], fill=fill)
    # 위쪽 절반에 옅은 하이라이트 — 납작한 색 덩어리보다 입체적으로 보인다.
    # 같은 이미지에 반투명으로 그리면 합성이 아니라 덮어쓰기라 구멍이 나므로 따로 합친다.
    gloss = Image.new("RGBA", chip.size, (0, 0, 0, 0))
    ImageDraw.Draw(gloss).rounded_rectangle(
        [k, k, cw * k - 1 - k, int(ch * k * 0.5)], radius=int(ch * k * 0.2),
        fill=(255, 255, 255, 30))
    mask = chip.getchannel("A")
    chip = Image.alpha_composite(chip, gloss)
    chip.putalpha(mask)
    chip = chip.resize((cw, ch + tail), Image.LANCZOS)
    d = ImageDraw.Draw(chip)
    # 밝은 칩(흰색/노랑 팀컬러) 위에서는 흰 글씨가 묻히므로 글자색을 반전
    lum = 0.114 * b_ + 0.587 * g + 0.299 * r_
    fg = (20, 20, 20, 255) if lum > 165 else (255, 255, 255, 255)
    d.text((pad_x - l, pad_y - t), text, font=font, fill=fg)

    arr = np.array(chip)
    arr.flags.writeable = False     # 캐시된 배열이 실수로 변형되는 것을 막는다
    return arr


def _draw_label(frame: np.ndarray, text: str, cx: float, bottom_y: float,
                color: tuple[int, int, int], alpha: float = 1.0) -> None:
    """색 말풍선 칩 + 흰 글씨. (cx, bottom_y)가 꼬리 끝."""
    if not text or alpha <= 0.01:
        return
    size = max(13, int(frame.shape[0] * 0.022))
    chip = _label_chip(text, size, tuple(color))
    ch, cw = chip.shape[:2]

    # 선수가 화면 가장자리에 있어도 이름표는 항상 프레임 안에 남긴다
    H, W = frame.shape[:2]
    x0 = int(np.clip(cx - cw / 2, 2, max(2, W - cw - 2)))
    y0 = int(np.clip(bottom_y - ch, 2, max(2, H - ch - 2)))
    _blend_rgba(frame, x0, y0, chip, alpha)


def _blend_rgba(frame: np.ndarray, x0: int, y0: int, rgba: np.ndarray,
                alpha: float = 1.0) -> None:
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    patch = rgba[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    a = patch[..., 3:4].astype(np.float32) * (alpha / 255.0)
    bgr = patch[..., :3][..., ::-1].astype(np.float32)
    roi = frame[dy0:dy1, dx0:dx1].astype(np.float32)
    frame[dy0:dy1, dx0:dx1] = np.clip(roi * (1.0 - a) + bgr * a, 0, 255).astype(np.uint8)


def ring_axes(box: Box) -> tuple[float, float]:
    """발밑 링의 반축 (a=가로, b=세로). 대형선을 링 경계에서 끊을 때도 쓴다.

    잔디에 놓인 원을 비스듬히 본 모양이라 세로를 납작하게 눌러준다.
    """
    a = max(6.0, box.w * 0.80)
    return a, a * 0.36




# ================================================================ 모던 그래픽
# 레퍼런스(방송 전술 분석 그래픽) 기준:
#   - 기둥: 유리관 — 가장자리가 밝고 가운데는 비치며, 위로 갈수록 옅어진다.
#     바닥에 밝은 테두리 타원. 위로 흐르는 빛 띠가 가끔 지나간다.
#   - 원판: 반투명하게 채운 타원 + 안쪽의 밝은 테두리
#   - 링: 흰 링 + 천천히 도는 바깥 호(HUD) + 등장할 때 한 번 퍼지는 파동
#   - 등장 순서: 원판이 톡 커짐 → 링·파동 → 기둥이 아래에서 솟음 → 이름표가 떠오름

def _lighter(c: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    """흰색 쪽으로 k 만큼 섞은 색 — 테두리·하이라이트용."""
    return tuple(int(v + (255 - v) * k) for v in c)


def _gauss(x: np.ndarray, c: float, s: float) -> np.ndarray:
    return np.exp(-((x - c) / s) ** 2)


@lru_cache(maxsize=512)
def _pillar_masks(w: int, h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(몸통, 하이라이트, 가로 단면) 알파. 행 0 = 위."""
    v = np.linspace(0.0, 1.0, h, dtype=np.float32)              # 0 위 → 1 아래
    x = np.linspace(-1.0, 1.0, w, dtype=np.float32)
    ax = np.abs(x)
    aa = np.clip((1.0 - ax) * w * 0.5, 0.0, 1.0)                # 좌우 경계 안티에일리어싱
    top = np.clip(v / 0.10, 0.0, 1.0) ** 1.3                    # 꼭대기는 녹아 사라진다
    # 몸통: 가장자리가 진한 원통 단면 × 아래가 진한 세로 그라데이션
    across = (0.55 + 0.45 * ax ** 2.5) * aa
    body = ((0.32 + 0.68 * v ** 1.3) * top)[:, None] * across[None, :]
    # 하이라이트: 양쪽 가장자리의 가는 빛 + 왼쪽의 넓은 반사
    hl = (0.85 * (_gauss(x, -0.88, 0.07) + _gauss(x, 0.88, 0.07))
          + 0.30 * _gauss(x, -0.45, 0.18)) * aa
    light = ((0.25 + 0.75 * v) * top)[:, None] * hl[None, :]
    for m in (body, light, across):
        m.flags.writeable = False
    return body, light, across


def _ellipse_mask(a: float, b: float, pad: float, draw) -> tuple[np.ndarray, int, int]:
    """(a,b) 타원 둘레를 그릴 작은 마스크. draw(mask, center) 가 그린다."""
    w, h = int(2 * (a + pad)) + 2, int(2 * (b + pad)) + 2
    m = np.zeros((h, w), np.uint8)
    draw(m, (w // 2, h // 2))
    return m, w, h


def _blend_mask(frame, cx, cy, m, w, h, color, strength) -> None:
    if strength > 0.004:
        _blend(frame, int(round(cx - w // 2)), int(round(cy - h // 2)),
               m.astype(np.float32) / 255.0, color, strength)


def draw_disc(frame, cx, cy, a, b, color, strength) -> None:
    """반투명 원판 + 안쪽 밝은 테두리."""
    if a < 2 or strength <= 0.004:
        return
    ai, bi = int(round(a)), max(1, int(round(b)))
    m, w, h = _ellipse_mask(a, b, 2, lambda m, c: cv2.ellipse(
        m, c, (ai, bi), 0, 0, 360, 255, -1, cv2.LINE_AA))
    _blend_mask(frame, cx, cy, m, w, h, color, strength * 0.78)
    t = max(1, int(round(a * 0.045)))
    m, w, h = _ellipse_mask(a, b, 2, lambda m, c: cv2.ellipse(
        m, c, (max(1, int(a * 0.84)), max(1, int(b * 0.84))), 0, 0, 360, 255, t, cv2.LINE_AA))
    _blend_mask(frame, cx, cy, m, w, h, _lighter(color, 0.65), strength * 0.9)


def draw_rings(frame, cx, cy, a, b, color, strength, anim: Anim) -> None:
    """흰 링 + 도는 바깥 호 + 등장 파동."""
    if a < 2 or strength <= 0.004:
        return
    t = max(2, int(round(a * 0.085)))
    rip = anim.ph(0.08, 0.6)
    rot = (anim.clock * 75.0) % 360 if anim.loop else 25.0
    reach = 1.9 if 0 < rip < 1 else 1.3

    def paint(m, c):
        cv2.ellipse(m, c, (int(a), max(1, int(b))), 0, 0, 360, 255, t, cv2.LINE_AA)
        oa, ob = int(a * 1.22), max(1, int(b * 1.22))
        ta = max(1, int(t * 0.6))
        for start in (rot, rot + 180):
            cv2.ellipse(m, c, (oa, ob), 0, start, start + 95, 205, ta, cv2.LINE_AA)
        if 0 < rip < 1:
            k = 1.0 + 0.8 * ease_out_cubic(rip)
            cv2.ellipse(m, c, (int(a * k), max(1, int(b * k))), 0, 0, 360,
                        int(230 * (1 - rip)), max(1, int(t * 0.7)), cv2.LINE_AA)

    m, w, h = _ellipse_mask(a * reach, b * reach, t + 2, paint)
    _blend_mask(frame, cx, cy, m, w, h, color, strength)


def draw_pillar(frame, cx, feet_y, w, full_h, grow, color, strength, anim: Anim) -> None:
    """유리관 기둥. grow(0~1) 만큼 아래에서 솟아 있다."""
    h = int(full_h * grow)
    w = int(w)
    if h < 3 or w < 4 or strength <= 0.004:
        return
    body, light, across = _pillar_masks(w, h)
    x0, y0 = int(round(cx - w / 2)), int(round(feet_y - h))
    _blend(frame, x0, y0, body, color, strength)
    hl = light
    if anim.loop:
        # 위로 흐르는 빛 띠 — 2.6초마다 한 번, 바닥에서 꼭대기로
        pos = 1.15 - ((anim.clock / 2.6) % 1.0) * 1.5           # 1=바닥, 0=꼭대기
        v = np.linspace(0.0, 1.0, h, dtype=np.float32)
        band = _gauss(v, pos, 0.08) * 0.45
        if band.max() > 0.01:
            hl = light + band[:, None] * across[None, :]
    _blend(frame, x0, y0, hl, _lighter(color, 0.75), strength)
    # 바닥 테두리 타원 — 원통 밑면
    ra, rb = w / 2, w / 2 * 0.34
    t = max(1, int(round(w * 0.04)))
    m, mw, mh = _ellipse_mask(ra, rb, t + 1, lambda m, c: cv2.ellipse(
        m, c, (int(ra), max(1, int(rb))), 0, 0, 360, 255, t, cv2.LINE_AA))
    _blend_mask(frame, cx, feet_y, m, mw, mh, _lighter(color, 0.6), strength * 1.1)


PILLAR_W = 1.45     # 기둥 폭 / 박스 폭
PILLAR_H = 1.50     # 기둥 높이 / 박스 높이


def draw_highlight(frame: np.ndarray, box: Box, style: HighlightStyle,
                   anim: Anim = STATIC) -> None:
    cx, feet_y = box.feet
    bw, bh = box.w, box.h
    if bw <= 1 or bh <= 1:
        return
    out = anim.out
    if out <= 0.01:
        return
    a, b = ring_axes(box)
    base = min(1.0, style.alpha)

    if style.ellipse:
        k = (0.3 + 0.7 * ease_out_back(anim.ph(0.0, 0.38))) * (0.85 + 0.15 * out)
        draw_disc(frame, cx, feet_y, a * k, b * k, style.color,
                  base * anim.ph(0.0, 0.18) * out)

    if style.ring:
        k = (0.55 + 0.45 * ease_out_cubic(anim.ph(0.04, 0.35))) * (0.85 + 0.15 * out)
        draw_rings(frame, cx, feet_y, a * k, b * k, style.ring_color or style.color,
                   min(1.0, base * 1.6) * anim.ph(0.04, 0.2) * out, anim)

    full_h = bh * PILLAR_H
    if style.bar:
        grow = ease_out_cubic(anim.ph(0.10, 0.42)) * (0.25 + 0.75 * out)
        draw_pillar(frame, cx, feet_y, max(8, bw * PILLAR_W), full_h, grow, style.color,
                    base * anim.ph(0.10, 0.2) * out, anim)
        label_y = feet_y - full_h + bh * 0.06     # 꼬리가 기둥 꼭대기에 닿게
    else:
        label_y = box.y1 - bh * 0.08

    if style.show_label and style.label:
        p = anim.ph(0.30, 0.28)
        lift = (1 - ease_out_cubic(p)) * bh * 0.18
        _draw_label(frame, style.label, cx, label_y + lift, style.color, p * out)


def render_frame(frame: np.ndarray, items: list, copy: bool = True) -> np.ndarray:
    """items: (박스, 스타일[, 애니메이션]). 원근 순서(먼 선수 먼저)로 합성한다."""
    out = frame.copy() if copy else frame
    for it in sorted(items, key=lambda it: it[0].y2):
        draw_highlight(out, it[0], it[1], it[2] if len(it) > 2 else STATIC)
    return out
