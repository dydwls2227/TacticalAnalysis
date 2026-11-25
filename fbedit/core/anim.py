"""오버레이 애니메이션 타이밍.

애니메이션 상태를 따로 저장하지 않는다. "표시 구간이 시작된 지 몇 초, 끝나기까지
몇 초"는 프레임 번호와 구간 목록만으로 언제든 다시 계산되므로, 미리보기에서 아무
프레임으로 건너뛰어도, 내보내기에서 순서대로 그려도 결과가 똑같다.

정지 카드로 영상이 멈춘 동안에는 `elapsed`(멈춘 뒤 흐른 시간)를 더한다. 그래서
정지 카드 프레임에서 켠 강조는 멈춘 화면 위에서 솟아오른다.
"""
from __future__ import annotations

from dataclasses import dataclass

IN_SEC = 0.55       # 등장 전체 길이
OUT_SEC = 0.30      # 퇴장(페이드) 길이
FAR = 99.0          # "애니메이션 끝난 지 오래" — 정지 상태


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_out_back(x: float, s: float = 1.6) -> float:
    """끝에서 살짝 넘쳤다가 돌아온다 — 레퍼런스의 '톡' 하고 자리 잡는 느낌."""
    x = clamp01(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


@dataclass
class Anim:
    age: float = FAR        # 등장한 뒤 흐른 시간(초)
    remain: float = FAR     # 사라지기까지 남은 시간(초)
    clock: float = 0.0      # 영상 시계(초) — 링 회전·빛 흐름 같은 반복 효과용
    loop: bool = True       # 반복 효과를 켤지

    def ph(self, start: float, dur: float) -> float:
        """등장 시작 후 start 초부터 dur 초 동안 0→1."""
        return clamp01((self.age - start) / dur)

    @property
    def out(self) -> float:
        """퇴장 배율 1→0."""
        return ease_out_cubic(self.remain / OUT_SEC)

    @property
    def settled(self) -> bool:
        return self.age >= IN_SEC and self.remain >= OUT_SEC


STATIC = Anim()


def held(holds: list[tuple[int, float]], lo: int, hi: int) -> float:
    """[lo, hi) 프레임 사이에 걸린 정지 카드들의 멈춤 시간 합."""
    return sum(sec for f, sec in holds if lo <= f < hi)


def span_timing(spans: list[list[int]], fi: int, fps: float, elapsed: float = 0.0,
                holds: list[tuple[int, float]] | None = None) -> tuple[float, float]:
    """(등장 후 경과, 퇴장까지 남은 시간). 구간이 없으면(항상 표시) 애니메이션 없음.

    holds = [(정지 카드 프레임, 멈춤 초)]. 지나온 정지 카드의 멈춤 시간도 흐른
    시간으로 친다 — 안 그러면 정지 카드가 끝나는 순간 등장 애니메이션이 다시 시작된다.
    """
    fps = fps or 30.0
    for a, b in spans:
        if fi >= a and (b < 0 or fi <= b):
            age = (fi - a) / fps + elapsed + (held(holds, a, fi) if holds else 0.0)
            remain = (b - fi + 1) / fps if b >= 0 else FAR
            return age, remain
    return FAR, FAR
