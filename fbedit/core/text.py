"""한글 자막 렌더링 — 폰트 탐색, 텍스트 그리기, 등장 이펙트."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path.home() / "Library/Fonts",
]
CACHE = Path.home() / ".cache" / "football-highlighter" / "fonts.json"

# 한글 지원 판정용 (가, 한, 글)
_PROBE = (0xAC00, 0xD55C, 0xAE00)

# 목록 상단에 올릴 폰트. 자막용으로 가독성이 좋은 순서.
_PREFERRED = [
    "Apple SD Gothic Neo Bold", "Apple SD Gothic Neo ExtraBold",
    "Apple SD Gothic Neo Heavy", "Apple SD Gothic Neo SemiBold",
    "Apple SD Gothic Neo Medium", "Apple SD Gothic Neo Regular",
    "S-Core Dream 6 Bold", "S-Core Dream 7 ExtraBold", "S-Core Dream 5 Medium",
    "AppleMyungjo", "AppleGothic",
]

EFFECTS = {
    "none": "없음",
    "fade": "페이드",
    "slide_up": "아래에서 위로",
    "pop": "팝(확대)",
    "typewriter": "타자기",
    "wipe": "좌→우 와이프",
}
POSITIONS = {"top": "상단", "center": "중앙", "bottom": "하단"}


@dataclass(frozen=True)
class FontFace:
    name: str
    path: str
    index: int = 0


# ---------------------------------------------------------------- 폰트 탐색
def _face_name(font) -> str:
    best = ""
    for rec in font["name"].names:
        if rec.nameID != 4:
            continue
        try:
            best = rec.toUnicode()
            break
        except Exception:
            continue
    return best


def _scan() -> list[FontFace]:
    from fontTools.ttLib import TTCollection, TTFont

    out: list[FontFace] = []
    seen: set[str] = set()
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*")):
            if p.suffix.lower() not in (".ttf", ".ttc", ".otf"):
                continue
            try:
                if p.suffix.lower() == ".ttc":
                    faces = list(enumerate(TTCollection(str(p)).fonts))
                else:
                    faces = [(0, TTFont(str(p), fontNumber=0, lazy=True))]
            except Exception:
                continue
            for idx, f in faces:
                try:
                    cmap = f.getBestCmap()
                    if not all(c in cmap for c in _PROBE):
                        continue
                    name = _face_name(f)
                    # 점으로 시작하는 이름은 시스템 내부용 숨김 페이스
                    if not name or name.startswith(".") or name in seen:
                        continue
                    if "LastResort" in name:
                        continue
                    seen.add(name)
                    out.append(FontFace(name, str(p), idx))
                except Exception:
                    continue
                finally:
                    try:
                        f.close()
                    except Exception:
                        pass

    rank = {n: i for i, n in enumerate(_PREFERRED)}
    out.sort(key=lambda f: (rank.get(f.name, len(_PREFERRED)), f.name))
    return out


def available_fonts(refresh: bool = False) -> list[FontFace]:
    """한글을 지원하는 폰트 목록. 스캔이 몇 초 걸리므로 결과를 캐시한다."""
    if not refresh and CACHE.exists():
        try:
            data = json.loads(CACHE.read_text(encoding="utf-8"))
            faces = [FontFace(**f) for f in data["fonts"]]
            if faces and all(Path(f.path).exists() for f in faces[:5]):
                return faces
        except Exception:
            pass
    faces = _scan()
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(
            {"scanned": time.time(), "fonts": [asdict(f) for f in faces]},
            ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return faces


def font_by_name(name: str) -> FontFace | None:
    for f in available_fonts():
        if f.name == name:
            return f
    return None


def default_font_name() -> str:
    faces = available_fonts()
    return faces[0].name if faces else ""


@lru_cache(maxsize=64)
def _pil_font(path: str, index: int, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        return ImageFont.load_default()


# ---------------------------------------------------------------- 스타일
@dataclass
class TextStyle:
    font: str = ""                                  # 빈 값이면 기본 폰트
    size_pct: float = 3.0                           # 프레임 높이 대비 %
    color: tuple[int, int, int] = (255, 255, 255)   # BGR
    outline: float = 0.14                           # 글자 크기 대비 외곽선 두께 (0=없음)
    outline_color: tuple[int, int, int] = (20, 20, 20)
    shadow: bool = True
    box: bool = False                               # 반투명 배경 박스
    box_color: tuple[int, int, int] = (0, 0, 0)
    box_alpha: float = 0.55
    position: str = "bottom"                        # top | center | bottom
    margin_pct: float = 8.0                         # 위/아래 여백 (프레임 높이 대비 %)
    effect: str = "fade"
    effect_sec: float = 0.45                        # 등장 이펙트 길이

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("color", "outline_color", "box_color"):
            d[k] = list(getattr(self, k))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TextStyle":
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        for k in ("color", "outline_color", "box_color"):
            if k in d:
                d[k] = tuple(d[k])
        return cls(**d)


def _ease_out(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


# ---------------------------------------------------------------- 렌더링
def _text_layer(text: str, style: TextStyle, frame_h: int,
                reveal: float = 1.0) -> Image.Image | None:
    """자막을 RGBA 레이어로 그린다. reveal 은 타자기 효과용 글자 비율."""
    if not text.strip():
        return None
    if style.effect == "typewriter" and reveal < 1.0:
        shown = ""
        for line in text.split("\n"):
            keep = int(len(line) * reveal + 0.999)
            shown += line[:keep] + "\n"
        text = shown.rstrip("\n")
        if not text.strip():
            return None

    size = max(10, int(frame_h * style.size_pct / 100.0))
    face = font_by_name(style.font) or (available_fonts() or [None])[0]
    if face is None:
        return None
    font = _pil_font(face.path, face.index, size)

    stroke = int(size * style.outline) if style.outline > 0 else 0
    pad = stroke + int(size * 0.35)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke,
                                          align="center", spacing=int(size * 0.25))
    # Pillow 는 textbbox 를 float 로 준다 — Image.new 는 정수만 받는다
    w, h = int(round(r - l)), int(round(b - t))
    layer = Image.new("RGBA", (max(1, w + pad * 2), max(1, h + pad * 2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    if style.box:
        bc = style.box_color
        d.rounded_rectangle([0, 0, layer.width - 1, layer.height - 1],
                            radius=int(size * 0.28),
                            fill=(bc[2], bc[1], bc[0], int(255 * style.box_alpha)))

    ox, oy = pad - l, pad - t
    if style.shadow:
        d.multiline_text((ox + max(2, size // 22), oy + max(2, size // 22)), text,
                         font=font, fill=(0, 0, 0, 150), align="center",
                         spacing=int(size * 0.25), stroke_width=stroke,
                         stroke_fill=(0, 0, 0, 150))
    c, oc = style.color, style.outline_color
    d.multiline_text((ox, oy), text, font=font, fill=(c[2], c[1], c[0], 255),
                     align="center", spacing=int(size * 0.25), stroke_width=stroke,
                     stroke_fill=(oc[2], oc[1], oc[0], 255) if stroke else None)
    return layer


def draw_caption(frame: np.ndarray, text: str, style: TextStyle,
                 progress: float = 1.0, fade_out: float = 1.0) -> None:
    """자막을 프레임에 in-place 합성.

    progress: 등장 진행도 0→1.  fade_out: 1이면 그대로, 0이면 완전히 사라짐.
    """
    H, W = frame.shape[:2]
    p = _ease_out(progress)
    reveal = progress if style.effect == "typewriter" else 1.0

    layer = _text_layer(text, style, H, reveal)
    if layer is None:
        return

    alpha = fade_out
    dy = 0
    if style.effect in ("fade", "slide_up", "pop", "wipe"):
        alpha *= p
    if style.effect == "slide_up":
        dy = int((1.0 - p) * H * 0.05)
    if style.effect == "pop":
        s = 0.82 + 0.18 * p
        layer = layer.resize((max(1, int(layer.width * s)),
                              max(1, int(layer.height * s))), Image.LANCZOS)

    arr = np.array(layer)
    if style.effect == "wipe" and p < 1.0:
        cut = int(arr.shape[1] * p)
        arr = arr.copy()
        arr[:, cut:, 3] = 0

    lw, lh = arr.shape[1], arr.shape[0]
    margin = int(H * style.margin_pct / 100.0)
    x = (W - lw) // 2
    if style.position == "top":
        y = margin
    elif style.position == "center":
        y = (H - lh) // 2
    else:
        y = H - lh - margin
    y += dy

    _composite(frame, arr, x, y, alpha)


def _composite(frame: np.ndarray, rgba: np.ndarray, x0: int, y0: int,
               alpha: float) -> None:
    if alpha <= 0.001:
        return
    H, W = frame.shape[:2]
    h, w = rgba.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + w), min(H, y0 + h)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    patch = rgba[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
    a = (patch[..., 3:4].astype(np.float32) / 255.0) * alpha
    bgr = patch[..., :3][..., ::-1].astype(np.float32)
    roi = frame[dy0:dy1, dx0:dx1].astype(np.float32)
    frame[dy0:dy1, dx0:dx1] = np.clip(roi * (1 - a) + bgr * a, 0, 255).astype(np.uint8)
