"""자유 배치 구역 표시 — 트래킹과 무관하게 화면 어디든 원으로 공간을 짚는다.

선수 강조와 똑같이 "구간"(spans) 방식으로 나타나고 사라진다: 여기부터/여기까지로
열고 닫으며, 여러 구간을 만들 수도, 구간 없이 항상 표시해 둘 수도 있다. 이 파일의
span 로직은 `project.PlayerState` 와 의도적으로 동일하다 — 사용자가 이미 익힌
"여기부터/여기까지" 조작을 그대로 재사용하기 위해서다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .anim import Anim, ease_out_back, span_timing
from .overlay import _draw_label

DEFAULT_MARKER_COLOR = (40, 40, 220)  # BGR, 선명한 빨강 — 레퍼런스의 공간 표시 원


@dataclass
class Marker:
    mid: int
    x: float = 0.5          # 프레임 폭 대비 비율(0~1) — 해상도가 달라도 상대 위치 유지
    y: float = 0.5          # 프레임 높이 대비 비율
    radius: float = 0.07    # 프레임 대각선 길이 대비 비율
    color: tuple[int, int, int] = DEFAULT_MARKER_COLOR
    alpha: float = 0.45
    ring: bool = False      # False = 꽉 찬 원, True = 테두리만
    label: str = ""
    # 세로 납작함. 잔디에 놓인 원을 중계 카메라 각도로 본 모양(타원)이 되게 한다.
    # 1.0 이면 화면상 정원.
    tilt: float = 0.6
    # 표시 구간 목록. PlayerState.spans 와 동일한 규칙: 비어 있으면 항상 표시,
    # 끝이 -1 이면 "영상 끝까지".
    spans: list[list[int]] = field(default_factory=list)

    def visible_at(self, frame: int) -> bool:
        if not self.spans:
            return True
        return any(frame >= a and (b < 0 or frame <= b) for a, b in self.spans)

    # ---- 구간 편집 (PlayerState 와 동일한 동작) ----
    def span_begin(self, frame: int) -> None:
        self.spans.append([frame, -1])

    def span_end(self, frame: int) -> None:
        for sp in reversed(self.spans):
            if sp[1] < 0:
                sp[1] = frame
                return
        if not self.spans:
            self.spans = [[0, frame]]

    def span_stop(self, frame: int) -> None:
        for sp in reversed(self.spans):
            if sp[0] <= frame and (sp[1] < 0 or frame <= sp[1]):
                if sp[0] >= frame:
                    self.spans.remove(sp)
                else:
                    sp[1] = frame
                return
        if not self.spans:
            self.spans = [[0, frame]]

    def span_clear(self) -> None:
        self.spans = []

    @property
    def range_text(self) -> str:
        if not self.spans:
            return "전체"
        return ", ".join(f"{a}~{'끝' if b < 0 else b}" for a, b in self.spans)

    @property
    def display(self) -> str:
        return self.label or f"구역 {self.mid}"

    def to_dict(self) -> dict:
        return {"mid": self.mid, "x": self.x, "y": self.y, "radius": self.radius,
                "color": list(self.color), "alpha": self.alpha, "ring": self.ring,
                "label": self.label, "tilt": self.tilt,
                "spans": [list(s) for s in self.spans]}

    @classmethod
    def from_dict(cls, d: dict) -> "Marker":
        return cls(mid=d["mid"], x=d.get("x", 0.5), y=d.get("y", 0.5),
                   radius=d.get("radius", 0.07),
                   color=tuple(d.get("color", DEFAULT_MARKER_COLOR)),
                   alpha=d.get("alpha", 0.45), ring=d.get("ring", False),
                   label=d.get("label", ""), tilt=d.get("tilt", 0.6),
                   spans=[list(s) for s in d.get("spans", [])])


def draw_markers(frame: np.ndarray, markers: list[Marker], frame_idx: int,
                 fps: float = 30.0, elapsed: float = 0.0, animate: bool = False,
                 holds: list[tuple[int, float]] | None = None) -> None:
    """켜져 있는 구역들을 프레임에 in-place 합성.

    레퍼런스처럼 반투명하게 채운 원 + 밝은 테두리. 등장할 때 작게 시작해 살짝 넘쳤다가
    자리 잡고, 구간이 끝날 때 옅어지며 줄어든다.

    대형선(`formation.draw_formation`)과 같은 이유로 그린 영역만 float32 로 왕복한다
    — 원 하나 그리자고 화면 전체를 변환하면 낭비다.
    """
    visible = [m for m in markers if m.visible_at(frame_idx)]
    if not visible:
        return
    H, W = frame.shape[:2]
    diag = float(np.hypot(W, H))
    for m in visible:
        age, remain = (span_timing(m.spans, frame_idx, fps, elapsed, holds) if animate
                       else (99.0, 99.0))
        an = Anim(age, remain)
        out = an.out
        k = (0.15 + 0.85 * ease_out_back(an.ph(0.0, 0.42), 1.3)) * (0.9 + 0.1 * out)
        fill_a = m.alpha * an.ph(0.0, 0.25) * out
        line_a = min(1.0, 0.55 + m.alpha) * an.ph(0.0, 0.15) * out
        if fill_a <= 0.004 and line_a <= 0.004:
            continue
        cx, cy = int(round(m.x * W)), int(round(m.y * H))
        r = max(4, int(round(m.radius * diag * k)))
        t = max(2, int(round(m.radius * diag / 24)))
        if m.ring:
            t = max(3, int(round(m.radius * diag / 12)))
        ry = max(2, int(round(r * m.tilt)))
        pad = r + t + 2
        x0, x1 = max(0, cx - pad), min(W, cx + pad + 1)
        y0, y1 = max(0, cy - ry - t - 2), min(H, cy + ry + t + 3)
        if x1 <= x0 or y1 <= y0:
            continue
        roi = frame[y0:y1, x0:x1]
        acc = roi.astype(np.float32)
        c = (cx - x0, cy - y0)
        if not m.ring:
            fill = np.zeros((y1 - y0, x1 - x0), np.uint8)
            cv2.ellipse(fill, c, (r, ry), 0, 0, 360, 255, -1, cv2.LINE_AA)
            a = (fill.astype(np.float32) / 255.0 * fill_a)[..., None]
            acc = acc * (1 - a) + np.array(m.color, np.float32) * a
        edge = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.ellipse(edge, c, (r, ry), 0, 0, 360, 255, t, cv2.LINE_AA)
        a = (edge.astype(np.float32) / 255.0 * line_a)[..., None]
        light = np.array([v + (255 - v) * 0.8 for v in m.color], np.float32)
        acc = acc * (1 - a) + light * a
        np.copyto(roi, np.clip(acc, 0, 255).astype(np.uint8))
        if m.label:
            _draw_label(frame, m.label, cx, cy - ry - 4, m.color, an.ph(0.2, 0.25) * out)
