"""유니폼 색으로 팀을 나눈다.

같은 팀 선수끼리는 외형이 거의 같아서 ReID 로도 구분이 안 되지만, **다른 팀**끼리는
유니폼 색이 확연히 다르다. 이걸로 두 가지를 막는다.

1. 겹쳤다 풀리면서 트래커가 상대 팀 선수에게 ID 를 넘겨주는 경우
   → 트랙 안에서 팀 색이 바뀌면 그 지점에서 트랙을 자른다(`split_on_team_change`)
2. 끊긴 조각을 이을 때 상대 팀 조각에 잘못 붙는 경우
   → `stitch_tracks` 가 팀이 다른 조각은 후보에서 뺀다

방법은 단순하다. 박스 상체(가슴~배) 부분을 잘라 잔디색 화소를 지우고 남은 화소의
중앙값을 Lab 색공간에서 구한다. 모든 표본을 k-means(k=2)로 두 무리로 나누고, 어느
쪽과도 먼 표본(심판·골키퍼 등)은 '기타'(0)로 둔다.
"""
from __future__ import annotations

import cv2
import numpy as np

SAMPLE_EVERY = 3          # 몇 프레임마다 색을 뽑나
MIN_BOX_H = 24            # 이보다 작은 박스는 색이 뭉개져 믿을 수 없다
MIN_SEPARATION = 18.0     # 두 무리 중심이 이보다 가까우면 한 팀만 화면에 있는 것
SPLIT_RUN = 6             # 팀이 바뀌었다고 볼 연속 표본 수 (6×3프레임 ≈ 0.6초)

TEAM_NAMES = {0: "기타", 1: "팀 A", 2: "팀 B"}


def jersey_feature(img: np.ndarray, box) -> np.ndarray | None:
    """상체 유니폼 색 특징 (0.5·L, a, b). 믿을 만한 화소가 적으면 None."""
    h = box.y2 - box.y1
    w = box.x2 - box.x1
    if h < MIN_BOX_H or w < 6:
        return None
    H, W = img.shape[:2]
    x0 = int(max(0, box.x1 + 0.25 * w))
    x1 = int(min(W, box.x2 - 0.25 * w))
    y0 = int(max(0, box.y1 + 0.15 * h))
    y1 = int(min(H, box.y1 + 0.50 * h))
    if x1 - x0 < 3 or y1 - y0 < 4:
        return None
    crop = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    grass = (hsv[..., 0] >= 30) & (hsv[..., 0] <= 90) & (hsv[..., 1] > 50) & (hsv[..., 2] > 40)
    keep = ~grass
    if keep.sum() < max(6, 0.2 * keep.size):
        return None
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)[keep].astype(np.float32)
    med = np.median(lab, axis=0)
    return np.array([med[0] * 0.5, med[1], med[2]], dtype=np.float32)


def feature_to_bgr(f: np.ndarray) -> tuple[int, int, int]:
    lab = np.array([[[min(255, f[0] * 2), f[1], f[2]]]], dtype=np.uint8)
    b, g, r = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)[0, 0]
    return int(b), int(g), int(r)


def assign_teams(ts, feats: dict[int, dict[int, np.ndarray]]) -> None:
    """표본을 두 팀으로 묶어 각 트랙의 `samples`(프레임→팀)와 `ts.team_colors` 를 채운다."""
    keys = [(tid, f) for tid, d in feats.items() if tid in ts.tracks for f in d]
    if len(keys) < 12:
        return
    data = np.stack([feats[t][f] for t, f in keys]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, labels, centers = cv2.kmeans(data, 2, None, crit, 5, cv2.KMEANS_PP_CENTERS)
    labels = labels.ravel()
    # 큰 무리를 팀 A 로 (실행할 때마다 A/B 가 뒤바뀌지 않게)
    if (labels == 1).sum() > (labels == 0).sum():
        labels = 1 - labels
        centers = centers[::-1].copy()

    if float(np.linalg.norm(centers[0] - centers[1])) < MIN_SEPARATION:
        # 한 팀만 화면에 있다 — 억지로 둘로 나누면 같은 팀을 잘라 버린다
        team = np.ones(len(keys), dtype=int)
        ts.team_colors = [feature_to_bgr(np.median(data, axis=0))]
    else:
        d = np.linalg.norm(data - centers[labels], axis=1)
        far = d > max(22.0, 3.0 * float(np.median(d)))
        team = labels + 1
        team[far] = 0                       # 심판·골키퍼·가려진 표본
        ts.team_colors = [feature_to_bgr(c) for c in centers]
    for (tid, f), t in zip(keys, team):
        ts.tracks[tid].samples[f] = int(t)


def split_on_team_change(ts, min_run: int = SPLIT_RUN) -> int:
    """트랙 안에서 팀 색이 바뀌면(= 상대 팀 선수에게 ID 가 넘어감) 거기서 자른다.

    겹치는 순간에는 두 유니폼이 섞여 표본이 잠깐 튄다. 그래서 바뀐 팀이
    `min_run` 표본 이상 이어질 때만 진짜로 본다. 반환값은 자른 횟수.
    """
    if len(ts.team_colors) < 2:
        return 0
    next_id = max(ts.tracks, default=0) + 1
    splits = 0
    work = list(ts.tracks)
    while work:
        tid = work.pop()
        tr = ts.tracks[tid]
        seq = sorted((f, t) for f, t in tr.samples.items() if t)
        runs: list[list[int]] = []          # [팀, 시작 표본, 끝 표본]
        for i, (_, t) in enumerate(seq):
            if runs and runs[-1][0] == t:
                runs[-1][2] = i
            else:
                runs.append([t, i, i])
        solid = [r for r in runs if r[2] - r[1] + 1 >= min_run]
        cut = next((seq[b[1]][0] for a, b in zip(solid, solid[1:]) if a[0] != b[0]), None)
        if cut is None:
            continue
        ts.split_at(tid, cut, next_id)
        work += [tid, next_id]
        next_id += 1
        splits += 1
    return splits
