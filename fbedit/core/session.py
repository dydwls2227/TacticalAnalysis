"""여러 클립을 한 세션에서 편집하고 하나로 이어 붙이기.

클립 하나 = `Project` 하나다. 세션은 그 목록과 지금 편집 중인 클립을 들고 있을 뿐이라,
기존 편집 로직은 클립이 하나든 열이든 그대로 동작한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .project import Project
from .video import probe

FILE_VERSION = 3


@dataclass
class Session:
    clips: list[Project] = field(default_factory=list)
    current: int = 0
    path: str | None = None

    # ---------------------------------------------------------------- 목록
    @property
    def clip(self) -> Project | None:
        if 0 <= self.current < len(self.clips):
            return self.clips[self.current]
        return None

    def add_video(self, video_path: str) -> Project:
        proj = Project(video_path=video_path)
        try:
            info = probe(video_path)
            proj.export_start, proj.export_end = 0, info.n_frames
        except Exception:
            pass
        self.clips.append(proj)
        return proj

    def remove(self, index: int) -> None:
        if not (0 <= index < len(self.clips)):
            return
        del self.clips[index]
        self.current = min(self.current, max(0, len(self.clips) - 1))

    def move(self, index: int, delta: int) -> int:
        """클립 순서를 바꾸고 새 위치를 반환한다."""
        j = index + delta
        if not (0 <= index < len(self.clips)) or not (0 <= j < len(self.clips)):
            return index
        self.clips[index], self.clips[j] = self.clips[j], self.clips[index]
        return j

    def name_of(self, index: int) -> str:
        p = self.clips[index]
        return Path(p.video_path).name if p.video_path else "(영상 없음)"

    # ---------------------------------------------------------------- 출력 규격
    def output_spec(self) -> tuple[int, int, float]:
        """이어 붙일 때 맞출 (가로, 세로, fps).

        클립마다 해상도와 fps 가 다른 게 보통이라(사용자 영상: 506×183 / 864×480 /
        1280×658, 30fps 와 50fps 혼재) 기준을 하나 정해야 한다. **가장 큰 클립**에
        맞추면 큰 쪽의 화질 손실이 없고, 작은 클립만 비율을 유지한 채 여백이 붙는다.
        """
        best = None
        for p in self.clips:
            if not p.video_path:
                continue
            try:
                info = probe(p.video_path)
            except Exception:
                continue
            area = info.width * info.height
            if best is None or area > best[0]:
                best = (area, info.width, info.height, info.fps)
        if best is None:
            return 1280, 720, 30.0
        # 인코더가 짝수 크기를 요구한다
        return (best[1] // 2) * 2, (best[2] // 2) * 2, best[3] or 30.0

    def total_output_frames(self, clips: list[Project] | None = None) -> int:
        """이어 붙일 클립들의 예상 출력 프레임 수(진행률 표시용).

        분석 여부와 무관하게 계산한다 — 트랙셋이 없는(=분석 전) 클립도 원본 프레임
        수만 알면 되므로, 트랙셋의 fps 대신 항상 `probe()` 로 원본 fps 를 구한다.
        """
        _, _, fps = self.output_spec()
        n = 0
        for p in clips if clips is not None else self.clips:
            if not p.video_path:
                continue
            try:
                src_fps = probe(p.video_path).fps or fps
            except Exception:
                continue
            frames = p.timeline.output_length(p.export_start, p.export_end, src_fps)
            n += int(round(frames * fps / src_fps)) if src_fps else frames
        return n

    @property
    def ready_clips(self) -> list[Project]:
        """분석이 끝나 강조·대형선까지 포함해 내보낼 수 있는 클립."""
        return [p for p in self.clips if p.trackset is not None and p.video_path]

    @property
    def clips_with_video(self) -> list[Project]:
        """분석 여부와 무관하게, 원본 영상이 실제로 존재하는 클립.

        '분석 없이 그냥 이어붙이기' 는 이 목록을 쓴다 — 강조·대형선은 안 붙지만
        자막·정지 카드는 미리 넣어뒀다면 그대로 반영된다(둘 다 트랙셋과 무관하다).
        """
        return [p for p in self.clips
                if p.video_path and Path(p.video_path).exists()]

    # ---------------------------------------------------------------- 저장/로드
    def save(self, path: str | Path) -> None:
        data = {"version": FILE_VERSION, "current": self.current,
                "clips": [p.to_dict() for p in self.clips]}
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        self.path = str(path)

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if "clips" in d:                        # v3 — 여러 클립
            clips = [Project.from_dict(c) for c in d["clips"]]
            cur = d.get("current", 0)
        else:                                   # v1/v2 — 단일 클립 프로젝트
            clips = [Project.from_dict(d)]
            cur = 0
        s = cls(clips=clips, current=min(cur, max(0, len(clips) - 1)), path=str(path))
        return s
