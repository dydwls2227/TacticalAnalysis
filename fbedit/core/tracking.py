"""선수 탐지 + 추적.

YOLO(person) → BoT-SORT(카메라 모션 보정 포함) → 팀 색 구분 → 팀이 바뀐 트랙 자르기
→ 조각 이어붙이기(같은 팀끼리만) → 보간·평활화.

방송 영상은 컷이 잦으므로 샷 경계를 검출해 트래커를 리셋한다(리셋하지 않으면 컷 이후
전혀 다른 선수에게 같은 ID가 붙는다).
"""
from __future__ import annotations

import bisect
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .video import FrameReader, VideoInfo, probe
from .teams import SAMPLE_EVERY, assign_teams, jersey_feature, split_on_team_change

# YOLO 가중치 보관 위치 — iCloud 동기화 밖 (Analyzer.__init__ 주석 참고)
MODEL_DIR = Path.home() / ".cache" / "football-highlighter" / "models"

# 축구 중계용으로 튜닝한 BoT-SORT 설정 (근거는 파일 안 주석 참고)
TRACKER_CFG = str(Path(__file__).resolve().parent.parent / "assets"
                  / "botsort_football.yaml")

PERSON_CLASS = 0
BALL_CLASS = 32  # COCO sports ball


@dataclass
class Box:
    frame: int
    x1: float
    y1: float
    x2: float
    y2: float
    conf: float = 1.0

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    @property
    def feet(self) -> tuple[float, float]:
        """발밑 좌표 — 강조 표시와 대형선의 기준점."""
        return (self.cx, self.y2)

    def contains(self, x: float, y: float, pad: float = 0.0) -> bool:
        return (self.x1 - pad) <= x <= (self.x2 + pad) and (self.y1 - pad) <= y <= (self.y2 + pad)

    def lerp(self, other: "Box", t: float, frame: int) -> "Box":
        f = lambda a, b: a + (b - a) * t
        return Box(frame, f(self.x1, other.x1), f(self.y1, other.y1),
                   f(self.x2, other.x2), f(self.y2, other.y2),
                   f(self.conf, other.conf))


@dataclass
class Track:
    tid: int
    boxes: dict[int, Box] = field(default_factory=dict)
    # 팀 표본: 프레임 → 팀(0 기타, 1 팀 A, 2 팀 B). teams.assign_teams 가 채운다.
    samples: dict[int, int] = field(default_factory=dict)
    team_fixed: int | None = None       # 사용자가 직접 지정한 팀

    @property
    def team(self) -> int:
        """다수결 팀. 표본이 적거나 갈리면 0(모름)."""
        if self.team_fixed is not None:
            return self.team_fixed
        votes = [t for t in self.samples.values() if t]
        if len(votes) < 3:
            return 0
        best = max(set(votes), key=votes.count)
        return best if votes.count(best) >= 0.6 * len(votes) else 0

    def take_from(self, other: "Track", frames) -> None:
        """other 의 해당 프레임 박스·표본을 가져온다(덮어씀)."""
        for f in frames:
            if f in other.boxes:
                self.boxes[f] = other.boxes.pop(f)
                self.boxes[f].frame = f
            if f in other.samples:
                self.samples[f] = other.samples.pop(f)

    @property
    def first_frame(self) -> int:
        return min(self.boxes) if self.boxes else 0

    @property
    def last_frame(self) -> int:
        return max(self.boxes) if self.boxes else 0

    @property
    def length(self) -> int:
        return len(self.boxes)

    def at(self, frame: int) -> Box | None:
        return self.boxes.get(frame)


@dataclass
class TrackSet:
    info: VideoInfo
    start: int
    end: int
    tracks: dict[int, Track] = field(default_factory=dict)
    cuts: list[int] = field(default_factory=list)  # 샷이 새로 시작하는 프레임 번호
    team_colors: list[tuple[int, int, int]] = field(default_factory=list)  # BGR, 팀 A·B

    # ---------- 수동 보정 ----------
    # 원본 추적 결과는 그대로 두고, Project 가 보정 기록을 사본에 다시 적용한다
    # (되돌리기·저장이 보정 기록만으로 끝난다).
    def copy(self) -> "TrackSet":
        out = TrackSet(info=self.info, start=self.start, end=self.end,
                       cuts=list(self.cuts), team_colors=list(self.team_colors))
        for tid, tr in self.tracks.items():
            out.tracks[tid] = Track(
                tid, {f: Box(b.frame, b.x1, b.y1, b.x2, b.y2, b.conf)
                      for f, b in tr.boxes.items()},
                dict(tr.samples), tr.team_fixed)
        return out

    def overlap(self, a: int, b: int) -> int:
        """두 트랙이 동시에 화면에 있는 프레임 수 — 크면 서로 다른 사람이다."""
        ta, tb = self.tracks.get(a), self.tracks.get(b)
        if not ta or not tb:
            return 0
        return len(ta.boxes.keys() & tb.boxes.keys())

    def merge_into(self, a: int, b: int) -> None:
        """b 를 a 에 합친다. b 가 있는 구간에서는 b 의 박스가 이긴다
        (끊겼다 다시 잡힌 조각을 원래 선수에 붙이는 용도)."""
        ta, tb = self.tracks[a], self.tracks.pop(b)
        lo, hi = tb.first_frame, tb.last_frame
        for f in [f for f in ta.boxes if lo <= f <= hi]:
            del ta.boxes[f]
        for f in [f for f in ta.samples if lo <= f <= hi]:
            del ta.samples[f]
        ta.take_from(tb, list(tb.boxes) + list(tb.samples))

    def swap_from(self, a: int, b: int, frame: int) -> None:
        """frame 부터 두 트랙의 박스를 맞바꾼다(엇갈리며 ID 가 뒤바뀐 경우)."""
        ta, tb = self.tracks[a], self.tracks[b]
        fa = [f for f in ta.boxes if f >= frame] + [f for f in ta.samples if f >= frame]
        fb = [f for f in tb.boxes if f >= frame] + [f for f in tb.samples if f >= frame]
        tmp = Track(-1)
        tmp.take_from(ta, fa)
        ta.take_from(tb, fb)
        tb.take_from(tmp, list(tmp.boxes) + list(tmp.samples))

    def split_at(self, a: int, frame: int, new_tid: int) -> None:
        """frame 부터를 새 트랙 new_tid 로 떼어낸다."""
        ta = self.tracks[a]
        nt = Track(new_tid)
        nt.take_from(ta, [f for f in ta.boxes if f >= frame]
                     + [f for f in ta.samples if f >= frame])
        self.tracks[new_tid] = nt
        if not ta.boxes:
            del self.tracks[a]

    def apply_edit(self, e: dict) -> None:
        op = e["op"]
        if op == "merge":
            self.merge_into(e["a"], e["b"])
        elif op == "swap":
            self.swap_from(e["a"], e["b"], e["frame"])
        elif op == "split":
            self.split_at(e["a"], e["frame"], e["new"])
        elif op == "team":
            self.tracks[e["a"]].team_fixed = e["team"]

    def boxes_at(self, frame: int) -> dict[int, Box]:
        out = {}
        for tid, tr in self.tracks.items():
            b = tr.at(frame)
            if b is not None:
                out[tid] = b
        return out

    def pick(self, frame: int, x: float, y: float) -> int | None:
        """클릭 지점의 트랙 ID. 겹칠 때는 가장 작은(=가까운/앞쪽) 박스를 고른다."""
        hits = [(b.w * b.h, tid) for tid, b in self.boxes_at(frame).items()
                if b.contains(x, y, pad=4)]
        if not hits:
            return None
        return min(hits)[1]

    def shot_bounds(self, frame: int) -> tuple[int, int]:
        """frame 이 속한 샷의 [시작, 끝) 구간."""
        lo, hi = self.start, self.end
        for c in self.cuts:
            if c <= frame:
                lo = max(lo, c)
            else:
                hi = min(hi, c)
        return lo, hi

    # ---------- 직렬화 ----------
    def to_dict(self) -> dict:
        return {
            "info": asdict(self.info),
            "start": self.start,
            "end": self.end,
            "cuts": self.cuts,
            "tracks": {
                str(tid): [[b.frame, round(b.x1, 2), round(b.y1, 2),
                            round(b.x2, 2), round(b.y2, 2), round(b.conf, 3)]
                           for b in sorted(tr.boxes.values(), key=lambda b: b.frame)]
                for tid, tr in self.tracks.items()
            },
            "team_colors": [list(c) for c in self.team_colors],
            "team_samples": {str(tid): sorted([f, t] for f, t in tr.samples.items())
                             for tid, tr in self.tracks.items() if tr.samples},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrackSet":
        ts = cls(info=VideoInfo(**d["info"]), start=d["start"], end=d["end"],
                 cuts=list(d.get("cuts", [])))
        for tid_s, rows in d["tracks"].items():
            tid = int(tid_s)
            tr = Track(tid)
            for r in rows:
                tr.boxes[int(r[0])] = Box(int(r[0]), *r[1:])
            ts.tracks[tid] = tr
        # 팀 구분 이전에 저장한 프로젝트에는 없다 → 모두 '모름'으로 둔다
        ts.team_colors = [tuple(c) for c in d.get("team_colors", [])]
        for tid_s, rows in d.get("team_samples", {}).items():
            if int(tid_s) in ts.tracks:
                ts.tracks[int(tid_s)].samples = {int(f): int(t) for f, t in rows}
        return ts

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict()), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "TrackSet":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class ShotDetector:
    """히스토그램 상관계수 기반 컷 검출. 방송 영상의 카메라 전환/리플레이 대응."""

    def __init__(self, threshold: float = 0.55):
        self.threshold = threshold
        self._prev: np.ndarray | None = None

    @staticmethod
    def _sig(frame: np.ndarray) -> np.ndarray:
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        return cv2.normalize(hist, hist).flatten()

    def is_cut(self, frame: np.ndarray) -> bool:
        sig = self._sig(frame)
        prev, self._prev = self._prev, sig
        if prev is None:
            return False
        return cv2.compareHist(prev, sig, cv2.HISTCMP_CORREL) < self.threshold


def smooth_tracks(ts: TrackSet, gap: int = 8, window: int = 5) -> None:
    """짧은 끊김을 보간하고 박스 좌표에 이동평균을 적용해 떨림을 줄인다.

    보간은 같은 샷 안에서만 수행한다 — 컷을 건너뛰며 이으면 화면을 가로지르는
    유령 박스가 생긴다.
    """
    for tr in ts.tracks.values():
        frames = sorted(tr.boxes)
        if len(frames) < 2:
            continue
        # 1) 갭 보간
        for a, b in zip(frames, frames[1:]):
            span = b - a
            if not (1 < span <= gap):
                continue
            if ts.shot_bounds(a) != ts.shot_bounds(b):
                continue
            ba, bb = tr.boxes[a], tr.boxes[b]
            for f in range(a + 1, b):
                tr.boxes[f] = ba.lerp(bb, (f - a) / span, f)

        # 2) 이동평균
        frames = sorted(tr.boxes)
        arr = np.array([[tr.boxes[f].x1, tr.boxes[f].y1,
                         tr.boxes[f].x2, tr.boxes[f].y2] for f in frames], dtype=np.float32)
        if len(frames) >= window:
            k = np.ones(window, dtype=np.float32) / window
            pad = window // 2
            sm = np.empty_like(arr)
            for c in range(4):
                padded = np.pad(arr[:, c], pad, mode="edge")
                sm[:, c] = np.convolve(padded, k, mode="valid")
            for i, f in enumerate(frames):
                b = tr.boxes[f]
                b.x1, b.y1, b.x2, b.y2 = (float(v) for v in sm[i])


def stitch_tracks(ts: "TrackSet", max_gap: int = 45, dist_factor: float = 1.4,
                  size_tol: float = 0.45) -> int:
    """끊긴 트랙 조각을 이어 붙인다. 반환값은 병합한 조각 수.

    유니폼 색으로 팀이 갈린 조각끼리는 잇지 않는다(teams.py).

    트래커가 놓친 선수는 잠시 뒤 새 ID 로 되살아난다. 사람 눈에는 같은 선수인데
    이름표와 대형선은 끊긴다. 앞 트랙이 끝난 지점에서 마지막 속도로 이어졌다고 보고,
    그 예상 위치 근처에서 시작하는 트랙을 같은 선수로 본다.

    거리 문턱은 박스 높이에 비례시킨다. 원근 때문에 먼 선수는 화면상 조금만 움직여도
    실제로는 많이 움직인 것이기 때문이다. 샷 경계는 넘지 않는다.
    """
    cuts = sorted(ts.cuts)

    def blocked(a: int, b: int) -> bool:      # 두 프레임 사이에 컷이 있나
        i = bisect.bisect_right(cuts, a)
        return i < len(cuts) and cuts[i] <= b

    def summarize(tr: Track) -> dict:
        """판정에 필요한 값만 뽑아 둔다.

        예전에는 후보 쌍마다 min()/max() 전체 스캔과 sorted(boxes)[-4:] 를 다시
        했다. 트랙 400개에서 6.4초가 걸리던 원인이다.
        """
        frames = sorted(tr.boxes)
        tail = frames[-4:]
        if len(tail) >= 2:
            b0, b1 = tr.boxes[tail[0]], tr.boxes[tail[-1]]
            span = max(1, tail[-1] - tail[0])
            vx = (b1.feet[0] - b0.feet[0]) / span
            vy = (b1.feet[1] - b0.feet[1]) / span
        else:
            vx = vy = 0.0
        return {"first": frames[0], "last": frames[-1],
                "fbox": tr.boxes[frames[0]], "lbox": tr.boxes[frames[-1]],
                "vx": vx, "vy": vy, "team": tr.team}

    info = {tid: summarize(tr) for tid, tr in ts.tracks.items() if tr.boxes}
    merged = 0
    changed = True
    while changed:                            # 이어 붙인 뒤 또 이어질 수 있다
        changed = False
        order = sorted(info, key=lambda t: info[t]["first"])
        by_last = sorted(info, key=lambda t: info[t]["last"])
        lasts = [info[t]["last"] for t in by_last]

        for tid in order:
            s = info[tid]
            first = s["first"]
            # 후보는 자기 시작 직전 max_gap 안에서 끝난 트랙뿐이다. 시간 창으로
            # 잘라내면 전체 쌍을 훑지 않아도 된다.
            lo = bisect.bisect_left(lasts, first - max_gap)
            hi = bisect.bisect_left(lasts, first)
            window = by_last[lo:hi]
            if not window:
                continue
            # 동점일 때 먼저 만난 쪽을 고르는 기존 동작을 유지하려면 시작 순서로 본다
            window.sort(key=lambda t: info[t]["first"])

            best, best_cost = None, None
            for ptid in window:
                if ptid == tid:
                    continue
                ps = info[ptid]
                gap = first - ps["last"]
                if not (0 < gap <= max_gap) or blocked(ps["last"], first):
                    continue
                if ps["team"] and s["team"] and ps["team"] != s["team"]:
                    continue                  # 상대 팀 조각에는 붙이지 않는다
                pb, nb = ps["lbox"], s["fbox"]
                if min(pb.h, nb.h) <= 0:
                    continue
                if abs(pb.h - nb.h) / min(pb.h, nb.h) > size_tol:
                    continue
                # 말단 속도로 예상 위치를 구한다
                px = pb.feet[0] + ps["vx"] * gap
                py = pb.feet[1] + ps["vy"] * gap
                dist = float(np.hypot(nb.feet[0] - px, nb.feet[1] - py))
                limit = dist_factor * pb.h * (1 + gap / 30.0)
                if dist > limit:
                    continue
                cost = dist / max(1e-6, limit)
                if best_cost is None or cost < best_cost:
                    best, best_cost = ptid, cost
            if best is not None:
                ts.tracks[best].boxes.update(ts.tracks[tid].boxes)
                ts.tracks[best].samples.update(ts.tracks[tid].samples)
                del ts.tracks[tid]
                del info[tid]
                info[best] = summarize(ts.tracks[best])
                merged += 1
                changed = True
                break
    return merged


class Analyzer:
    """모델을 한 번 로드해 재사용한다(앱 실행 중 반복 분석 대비)."""

    def __init__(self, weights: str = "yolo11m.pt", device: str = "mps",
                 imgsz: int = 1280, conf: float = 0.25):
        from ultralytics import YOLO

        # 가중치를 프로젝트 폴더(작업 디렉터리)가 아니라 MODEL_DIR 에 둔다. 프로젝트가
        # iCloud 동기화 폴더(~/Desktop) 안에 있으면 100MB급 .pt 파일이 클라우드로
        # 오프로드돼, 읽으려는 순간 앱이 아무 말 없이 멈춘다 — 실제로 겪었다.
        if os.sep not in weights:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            weights = str(MODEL_DIR / weights)
        self.model = YOLO(weights)
        self.device = device
        self.imgsz = imgsz
        self.conf = conf

    def _reset_tracker(self) -> None:
        pred = getattr(self.model, "predictor", None)
        for t in getattr(pred, "trackers", None) or []:
            if hasattr(t, "reset"):
                t.reset()

    def run(self, video: str | Path, start: int = 0, end: int | None = None,
            min_track_len: int = 6,
            progress: Callable[[int, int], None] | None = None,
            should_cancel: Callable[[], bool] | None = None) -> TrackSet | None:
        info = probe(video)
        end = info.n_frames if end is None else min(end, info.n_frames)
        ts = TrackSet(info=info, start=start, end=end)
        shots = ShotDetector()
        total = max(1, end - start)

        # 트래커는 reset() 시 ID 카운터도 1로 되돌린다. 그대로 두면 컷 이후의
        # 다른 선수가 컷 이전 ID를 물려받아 한 트랙으로 합쳐지므로 오프셋을 준다.
        id_offset = 0
        max_tid = 0
        feats: dict[int, dict[int, np.ndarray]] = {}   # 트랙 → 프레임 → 유니폼 색

        # Analyzer 는 프리셋별로 캐시돼 여러 영상에 재사용된다("전체 분석"이 대표적
        # 사용처). 여기서 리셋하지 않으면 BoT-SORT 의 광류 기반 모션 보정이 이전
        # 영상의 마지막 프레임을 기준으로 이번 영상 첫 프레임과 매칭을 시도하다가
        # 전혀 다른 화면끼리 비교하며 값이 발산해, 심하면 사실상 멈춘 것처럼 오래
        # 걸린다. 실제로 클립 3개를 연달아 분석했을 때 세 번째 클립에서 재현됐다.
        self._reset_tracker()

        with FrameReader(video) as reader:
            for fi, img in reader.iter_range(start, end):
                if should_cancel and should_cancel():
                    return None
                if shots.is_cut(img):
                    ts.cuts.append(fi)
                    self._reset_tracker()
                    id_offset = max_tid

                res = self.model.track(
                    img, persist=True, tracker=TRACKER_CFG,
                    classes=[PERSON_CLASS], conf=self.conf, imgsz=self.imgsz,
                    device=self.device, verbose=False,
                )[0]

                b = res.boxes
                if b is not None and b.id is not None:
                    xyxy = b.xyxy.cpu().numpy()
                    ids = b.id.cpu().numpy().astype(int)
                    confs = b.conf.cpu().numpy()
                    for (x1, y1, x2, y2), tid, cf in zip(xyxy, ids, confs):
                        gid = int(tid) + id_offset
                        max_tid = max(max_tid, gid)
                        box = Box(fi, float(x1), float(y1), float(x2), float(y2), float(cf))
                        ts.tracks.setdefault(gid, Track(gid)).boxes[fi] = box
                        if (fi - start) % SAMPLE_EVERY == 0:
                            f = jersey_feature(img, box)
                            if f is not None:
                                feats.setdefault(gid, {})[fi] = f

                if progress:
                    progress(fi - start + 1, total)

        # 스쳐 지나간 오탐(관중, 벤치 등)을 걸러낸다
        ts.tracks = {tid: tr for tid, tr in ts.tracks.items() if tr.length >= min_track_len}
        # 팀 구분 → 팀이 바뀐 트랙 자르기 → 같은 팀끼리만 조각 잇기
        assign_teams(ts, feats)
        split_on_team_change(ts)
        ts.tracks = {tid: tr for tid, tr in ts.tracks.items() if tr.length >= min_track_len}
        stitch_tracks(ts)
        smooth_tracks(ts)
        return ts
