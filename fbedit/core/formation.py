"""대형(포메이션) 라인 표시.

사용자가 지정한 라인(수비/미드/공격/GK …)별로 선수를 묶어 폴리라인으로 잇는다.
뷰어에서 좌클릭하면 현재 라인에 이어 붙고, 우클릭하면 다음 라인으로 넘어간다.

라인을 **사용자가 고정**하기 때문에 경기장 호모그래피가 필요 없다. 자동 분류라면
매 프레임 위치로 라인을 다시 추정해야 하고, 오버래핑한 풀백이 미드필더로 재분류되며
선이 튀지만, 고정 속성으로 두면 그런 깜빡임이 없다.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

import cv2
import numpy as np

from .tracking import Box

# 라인은 정수 그룹이다. 뷰어에서 좌클릭하면 현재 라인에 이어 붙고, 우클릭하면
# 다음 라인으로 넘어간다. 축구에서 흔한 순서라 1~4 에는 이름을 붙여 둔다.
LINE_NAMES = {1: "수비", 2: "미드", 3: "공격", 4: "GK"}
NAMED_LINES = len(LINE_NAMES)   # 우클릭은 이 4개를 순환한다
MAX_LINES = 8                   # 목록에서 직접 고를 수 있는 최대 라인

# 예전 프로젝트의 역할 문자열 → 라인 번호
ROLE_TO_LINE = {"DF": 1, "MF": 2, "FW": 3, "GK": 4, "": 0}


def line_label(n: int) -> str:
    if n <= 0:
        return "—"
    return LINE_NAMES.get(n, f"라인 {n}")


@dataclass
class FormationStyle:
    show: bool = True
    color: tuple[int, int, int] = (255, 255, 255)   # BGR
    thickness: int = 2
    alpha: float = 0.85
    nodes: bool = True
    # 발밑 링을 대형선과 같은 색으로 그린다. 링과 선이 하나의 연결망으로 읽힌다.
    unify_ring: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        d["color"] = list(self.color)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FormationStyle":
        known = {f for f in cls.__dataclass_fields__}
        d = {k: v for k, v in d.items() if k in known}
        d["color"] = tuple(d["color"])
        return cls(**d)


def order_line(boxes: list[Box]) -> list[Box]:
    """한 라인 안에서 선수를 잇는 순서를 정한다.

    카메라 각도에 따라 라인이 화면에서 기울어지므로 x 좌표로만 정렬하면 선이 꼬인다.
    발밑 좌표의 주성분(PCA 1축)에 투영해 정렬하면 어느 각도에서도 자연스럽게 이어진다.
    """
    if len(boxes) <= 2:
        return list(boxes)
    pts = np.array([b.feet for b in boxes], dtype=np.float32)
    centered = pts - pts.mean(axis=0)
    axis = np.linalg.svd(centered, full_matrices=False)[2][0]
    return [boxes[i] for i in np.argsort(centered @ axis)]


def groups_at(players: dict, boxes: dict[int, Box], frame: int) -> dict[int, list[Box]]:
    """표시 중인 선수들을 라인 번호별로 묶는다.

    잇는 순서는 **사용자가 선수를 고른 순서**(PlayerState.order)를 따른다. 선이 어떻게
    이어질지 사용자가 직접 정할 수 있어야 하기 때문이다. 순서 정보가 없는 예전
    프로젝트에서는 주성분 정렬로 되돌아간다.
    """
    out: dict[int, list[tuple[int, Box]]] = {}
    for tid, box in boxes.items():
        ps = players.get(tid)
        if ps is None or not ps.visible_at(frame) or ps.line <= 0:
            continue
        out.setdefault(ps.line, []).append((ps.order, box))

    result: dict[int, list[Box]] = {}
    for line, items in out.items():
        if all(o > 0 for o, _ in items):
            result[line] = [b for _, b in sorted(items, key=lambda t: t[0])]
        else:
            result[line] = order_line([b for _, b in items])
    return result


def _radius_along(axes: tuple[float, float], u: np.ndarray) -> float:
    """중심에서 방향 u 로 나아갈 때 타원 경계까지의 거리."""
    a, b = axes
    denom = (u[0] / a) ** 2 + (u[1] / b) ** 2
    return 0.0 if denom <= 0 else float(1.0 / np.sqrt(denom))


def draw_formation(frame: np.ndarray, groups: dict[int, list[Box]],
                   style: FormationStyle,
                   ring_of: Callable[[Box], tuple[float, float] | None] | None = None,
                   anim_of: Callable[[Box], tuple[float, float, float]] | None = None
                   ) -> None:
    """라인별 폴리라인을 프레임에 in-place 합성.

    ring_of 가 주어지면 선을 해당 선수의 발밑 링 경계에서 끊는다. 링 안까지 선이
    파고들지 않아 "링끼리 이어진" 모양이 된다.

    anim_of(box) → (그어진 비율, 진하기, 등장 후 경과) 가 주어지면 선이 먼저 나타난
    선수 쪽에서 나중 선수 쪽으로 그어지듯 나타나고, 퇴장할 때 함께 옅어진다.
    """
    if not style.show or not groups:
        return

    t = max(1, style.thickness)
    node_r = t + 2

    # 1) 그릴 도형을 먼저 모은다 — 마스크와 합성을 실제로 그리는 영역으로만 한정하기
    #    위해서다. 선분 몇 개 때문에 화면 전체를 float32 로 왕복하면 1280×658 기준
    #    17.7ms 가 드는데, 그게 렌더 시간의 80% 였다.
    segs: list[tuple[int, int, int, int, int]] = []     # x1, y1, x2, y2, 진하기(0~255)
    nodes: list[tuple[int, int, int]] = []
    for line in sorted(groups):
        boxes = groups[line]
        for b1, b2 in zip(boxes, boxes[1:]):
            p1 = np.array(b1.feet, dtype=np.float32)
            p2 = np.array(b2.feet, dtype=np.float32)
            d = p2 - p1
            dist = float(np.hypot(*d))
            if dist < 1.0:
                continue
            u = d / dist
            s, e = p1, p2
            if ring_of:
                if (ax := ring_of(b1)) is not None:
                    s = p1 + u * _radius_along(ax, u)
                if (ax := ring_of(b2)) is not None:
                    e = p2 - u * _radius_along(ax, u)
            if np.dot(e - s, u) <= 0:      # 링이 겹칠 만큼 가까우면 선을 생략
                continue
            val = 255
            if anim_of:
                f1, o1, age1 = anim_of(b1)
                f2, o2, age2 = anim_of(b2)
                frac, val = min(f1, f2), int(255 * min(o1, o2))
                if frac <= 0.01 or val <= 2:
                    continue
                if age2 > age1:            # 먼저 나타난 쪽에서 출발한다
                    s, e = e, s
                e = s + (e - s) * frac
            segs.append((int(s[0]), int(s[1]), int(e[0]), int(e[1]), val))

        if style.nodes:
            for b in boxes:
                # 링이 이미 표식 역할을 하므로 점을 겹쳐 찍지 않는다
                if ring_of and ring_of(b) is not None:
                    continue
                val = int(255 * anim_of(b)[1]) if anim_of else 255
                if val > 2:
                    nodes.append((int(b.feet[0]), int(b.feet[1]), val))

    if not segs and not nodes:
        return

    # 2) 경계 상자를 좌표에서 바로 구한다. np.nonzero 로 찾으면 그 자체가 전체 화면
    #    스캔이라 이득이 절반으로 줄어든다.
    H, W = frame.shape[:2]
    xs = [v for x1, _, x2, _, _ in segs for v in (x1, x2)] + [n[0] for n in nodes]
    ys = [v for _, y1, _, y2, _ in segs for v in (y1, y2)] + [n[1] for n in nodes]
    pad = max(t, node_r) + 2
    x0 = max(0, min(xs) - pad); x1 = min(W, max(xs) + pad + 1)
    y0 = max(0, min(ys) - pad); y1 = min(H, max(ys) + pad + 1)
    if x1 <= x0 or y1 <= y0:
        return

    # 3) 선을 마스크에만 안티에일리어싱으로 그린 뒤 단색으로 합성한다.
    #    컬러 레이어에도 AA로 그리면 경계가 두 번 블렌딩돼 지저분해진다.
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    for sx, sy, ex, ey, val in segs:
        cv2.line(mask, (sx - x0, sy - y0), (ex - x0, ey - y0), val, t, cv2.LINE_AA)
    for nx, ny, val in nodes:
        cv2.circle(mask, (nx - x0, ny - y0), node_r, val, -1, cv2.LINE_AA)

    roi = frame[y0:y1, x0:x1]
    a = (mask.astype(np.float32) / 255.0 * style.alpha)[..., None]
    col = np.array(style.color, dtype=np.float32)
    np.copyto(roi, np.clip(roi.astype(np.float32) * (1 - a) + col * a, 0, 255)
              .astype(np.uint8))
