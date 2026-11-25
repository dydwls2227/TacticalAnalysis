"""편집 타임라인 — 구간 자막과 정지 카드, 그리고 '결과 시간' 축.

정지 카드는 특정 프레임에서 영상을 멈추고 그 장면을 N초 유지하면서 자막을 띄운다.
그래서 결과 영상은 원본보다 길다. 편집도 **결과 영상의 시간**으로 한다:

    원본 프레임  0 1 2 3 [3에서 멈춤 ×3] 4 5
    결과 번호   0 1 2 3   4  5  6        7 8

선수·구역의 표시 구간, 자막 시작/끝은 모두 이 결과 번호로 저장한다. 그래야 정지
구간 안에서도 "멈춘 지 1초 뒤 구역이 나타남" 같은 편집이 된다. `TimeMap` 이 결과
번호 ↔ (원본 프레임, 정지 카드, 정지 경과초)를 바꿔 준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

from .text import TextStyle

FADE_OUT_SEC = 0.3


def hold_frames(seconds: float, fps: float) -> int:
    return max(1, int(round(seconds * fps))) if seconds > 0 else 0


@dataclass
class Caption:
    """움직이는 영상 위에 [start, end) 구간 동안 떠 있는 자막."""
    text: str = ""
    start: int = 0
    end: int = 0
    style: TextStyle = field(default_factory=TextStyle)

    def to_dict(self) -> dict:
        return {"text": self.text, "start": self.start, "end": self.end,
                "style": self.style.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "Caption":
        return cls(text=d.get("text", ""), start=d.get("start", 0),
                   end=d.get("end", 0),
                   style=TextStyle.from_dict(d.get("style", {})))


@dataclass
class FreezeCard:
    """frame 에서 정지해 seconds 초 동안 유지하며 자막을 띄운다."""
    frame: int = 0
    seconds: float = 3.0
    text: str = ""
    style: TextStyle = field(default_factory=lambda: TextStyle(position="bottom",
                                                              size_pct=3.5))

    def to_dict(self) -> dict:
        return {"frame": self.frame, "seconds": self.seconds, "text": self.text,
                "style": self.style.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "FreezeCard":
        return cls(frame=d.get("frame", 0), seconds=d.get("seconds", 3.0),
                   text=d.get("text", ""),
                   style=TextStyle.from_dict(d.get("style", {})))


@dataclass
class Timeline:
    captions: list[Caption] = field(default_factory=list)
    freezes: list[FreezeCard] = field(default_factory=list)

    # ---------- 조회 ----------
    def captions_at(self, frame: int, fps: float) -> list[tuple[Caption, float, float]]:
        """(자막, 등장 진행도, 사라짐 계수) 목록."""
        out = []
        for c in self.captions:
            if not (c.start <= frame < c.end) or not c.text.strip():
                continue
            elapsed = (frame - c.start) / max(1e-6, fps)
            remain = (c.end - frame) / max(1e-6, fps)
            p = 1.0 if c.style.effect_sec <= 0 else min(1.0, elapsed / c.style.effect_sec)
            fade = min(1.0, remain / FADE_OUT_SEC) if FADE_OUT_SEC > 0 else 1.0
            out.append((c, p, fade))
        return out

    def freeze_at(self, frame: int) -> FreezeCard | None:
        for f in self.freezes:
            if f.frame == frame:
                return f
        return None

    def sorted_freezes(self) -> list[FreezeCard]:
        return sorted(self.freezes, key=lambda f: f.frame)

    # ---------- 결과 시간 ----------
    def timemap(self, fps: float) -> "TimeMap":
        return TimeMap(self.freezes, fps)

    def plan(self, start: int, end: int, fps: float
             ) -> Iterator[tuple[int, int, FreezeCard | None, float]]:
        """원본 [start, end) 구간의 (결과 번호, 원본 프레임, 정지카드|None, 정지 경과초)."""
        tm = self.timemap(fps)
        for o in range(tm.out(start), tm.out(end)):
            fi, fz, el = tm.locate(o)
            yield o, fi, fz, el

    def output_length(self, start: int, end: int, fps: float) -> int:
        tm = self.timemap(fps)
        return max(0, tm.out(end) - tm.out(start))

    @property
    def has_freezes(self) -> bool:
        return any(f.seconds > 0 for f in self.freezes)

    # ---------- 직렬화 ----------
    def to_dict(self) -> dict:
        return {"captions": [c.to_dict() for c in self.captions],
                "freezes": [f.to_dict() for f in self.freezes]}

    @classmethod
    def from_dict(cls, d: dict) -> "Timeline":
        return cls(
            captions=[Caption.from_dict(c) for c in d.get("captions", [])],
            freezes=[FreezeCard.from_dict(f) for f in d.get("freezes", [])],
        )


def freeze_progress(card: FreezeCard, elapsed: float) -> tuple[float, float]:
    """정지 카드의 (등장 진행도, 사라짐 계수)."""
    p = 1.0 if card.style.effect_sec <= 0 else min(1.0, elapsed / card.style.effect_sec)
    remain = card.seconds - elapsed
    fade = min(1.0, remain / FADE_OUT_SEC) if FADE_OUT_SEC > 0 else 1.0
    return p, max(0.0, fade)


class TimeMap:
    """결과 번호(o) ↔ 원본 프레임(f). 정지 카드 f 는 결과에서

        out(f)              — 원본 f 그대로
        out(f)+1 … out(f)+H — 멈춘 화면 H 프레임 (정지 경과 0, 1/fps, …)

    을 차지한다. 기존 내보내기 순서(원본 프레임 다음에 멈춤)와 같다.
    """

    def __init__(self, freezes: list[FreezeCard], fps: float):
        self.fps = fps or 30.0
        self.items: list[tuple[int, int, FreezeCard]] = sorted(
            ((fz.frame, hold_frames(fz.seconds, self.fps), fz) for fz in freezes
             if fz.seconds > 0), key=lambda t: t[0])

    def out(self, f: int) -> int:
        """원본 프레임 f 가 결과에서 놓이는 번호(멈춤 앞)."""
        return f + sum(h for fk, h, _ in self.items if fk < f)

    def last_out(self, f: int) -> int:
        """원본 프레임 f 가 결과에서 보이는 마지막 번호(멈춤 포함)."""
        return self.out(f + 1) - 1

    def block(self, fz: FreezeCard) -> tuple[int, int]:
        """정지 구간 [시작, 끝) 결과 번호."""
        o0 = self.out(fz.frame) + 1
        return o0, o0 + hold_frames(fz.seconds, self.fps)

    def locate(self, o: int) -> tuple[int, FreezeCard | None, float]:
        """결과 번호 → (원본 프레임, 정지카드|None, 정지 경과초)."""
        offset = 0
        for fk, h, fz in self.items:
            ofk = fk + offset
            if o <= ofk:
                break
            if o <= ofk + h:
                return fk, fz, (o - ofk - 1) / self.fps
            offset += h
        return o - offset, None, 0.0

    def total(self, n_src: int) -> int:
        return self.out(n_src)
