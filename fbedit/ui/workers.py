"""백그라운드 작업 스레드 — 분석과 내보내기가 UI를 멈추지 않게 한다."""
from __future__ import annotations

import shutil
import tempfile
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..core.project import Project
from ..core.tracking import Analyzer
from ..core.video import (FrameReader, VideoWriter, concat_audio,
                          fit_letterbox, mux_audio, mux_tracks, probe)

# 표시 이름 → (가중치, 입력 해상도)
PRESETS: dict[str, tuple[str, int]] = {
    "빠름 (yolo11s · 960)": ("yolo11s.pt", 960),
    "균형 (yolo11m · 1280)": ("yolo11m.pt", 1280),
    "정밀 (yolo11x · 1280)": ("yolo11x.pt", 1280),
}
# 실제 중계 클립 측정 결과 s→x 의 탐지 이득이 6% 남짓인데 속도는 15배 차이라
# 빠름을 기본값으로 둔다 (README 성능표 참고)
DEFAULT_PRESET = "빠름 (yolo11s · 960)"


def best_device() -> str:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


class AnalyzeWorker(QThread):
    progress = Signal(int, int)     # done, total
    status = Signal(str)
    done = Signal(object)           # TrackSet | None(취소)
    failed = Signal(str)

    # 모델 로딩이 느리므로 프리셋별로 캐시해 재분석 시 재사용한다
    _cache: dict[tuple[str, int], Analyzer] = {}

    def __init__(self, video: str, preset: str, start: int, end: int, parent=None):
        super().__init__(parent)
        # start/end 라는 이름은 쓰지 말 것 — self.start 는 QThread.start() 메서드다
        self.video, self.preset = video, preset
        self.start_frame, self.end_frame = start, end
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            weights, imgsz = PRESETS[self.preset]
            key = (weights, imgsz)
            if key not in self._cache:
                self.status.emit(f"모델 로딩 중… ({weights})")
                self._cache[key] = Analyzer(weights=weights, imgsz=imgsz,
                                            device=best_device())
            self.status.emit("선수 탐지 및 추적 중…")
            ts = self._cache[key].run(
                self.video, self.start_frame, self.end_frame,
                progress=lambda d, t: self.progress.emit(d, t),
                should_cancel=self._cancel.is_set)
            self.done.emit(ts)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ExportWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, project: Project, out_path: str, with_audio: bool = True,
                 parent=None):
        super().__init__(parent)
        self.project, self.out_path, self.with_audio = project, out_path, with_audio
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            proj = self.project
            info = probe(proj.video_path)
            start, end = proj.export_start, proj.export_end
            src_frames = max(1, end - start)      # 원본 구간 길이 (오디오 자를 때 씀)

            fps = info.fps
            total = max(1, proj.timeline.output_length(start, end, fps))
            tmp = str(Path(tempfile.mkdtemp()) / "video.mp4")
            self.status.emit("오버레이 렌더링 중…")

            # 정지 카드가 있으면 같은 원본 프레임을 여러 번 쓴다. 매번 디코딩하지
            # 않도록 현재 프레임을 들고 있는다.
            with FrameReader(proj.video_path) as reader, \
                    VideoWriter(tmp, (info.width, info.height), fps) as writer:
                cur_idx, cur_img = -1, None
                done = 0
                for o, fi, _fz, _el in proj.timeline.plan(start, end, fps):
                    if self._cancel.is_set():
                        self.done.emit("")
                        return
                    if fi != cur_idx:
                        cur_img = reader.frame_at(fi)
                        cur_idx = fi
                        if cur_img is None:
                            break
                    writer.write(proj.render_output(cur_img, o, fps))
                    done += 1
                    self.progress.emit(done, total)

            final = self.out_path
            # 정지 카드가 들어가면 영상이 원본보다 길어져 오디오가 어긋난다.
            if self.with_audio and proj.timeline.has_freezes:
                self.status.emit("정지 카드가 있어 오디오 없이 내보냅니다")
                self.with_audio = False
            if self.with_audio:
                self.status.emit("오디오 병합 중…")
                ok = mux_audio(tmp, proj.video_path, final,
                               start_sec=start / fps, dur_sec=src_frames / fps)
                if not ok:  # 원본에 오디오 트랙이 없는 경우
                    Path(tmp).replace(final)
            else:
                Path(tmp).replace(final)
            self.done.emit(final)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ConcatExportWorker(QThread):
    """여러 클립을 순서대로 렌더해 하나의 영상으로 이어 붙인다.

    클립마다 해상도와 fps 가 다르므로 가장 큰 클립 기준으로 규격을 맞추고, 작은
    클립은 비율을 유지한 채 여백을 넣는다(늘려 붙이면 선수가 홀쭉해진다).

    require_analysis=False 로 두면 분석 여부와 무관하게 원본 영상이 있는 클립을
    전부 이어 붙인다("일단 이어붙이기"). 트랙셋이 없는 클립은 `proj.render_output`
    이 알아서 강조·대형선을 건너뛰므로 별도 분기가 필요 없다 — 자막·정지 카드는
    트랙셋과 무관하게 그대로 반영된다.
    """

    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, session, out_path: str, with_audio: bool = True,
                 require_analysis: bool = True, parent=None):
        super().__init__(parent)
        self.session, self.out_path, self.with_audio = session, out_path, with_audio
        self.require_analysis = require_analysis
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        tmpdir = None
        try:
            clips = (self.session.ready_clips if self.require_analysis
                     else self.session.clips_with_video)
            if not clips:
                msg = ("내보낼 클립이 없습니다. 각 클립을 먼저 분석하세요."
                       if self.require_analysis else
                       "내보낼 영상이 없습니다. 먼저 클립을 추가하세요.")
                self.failed.emit(msg)
                return

            W, H, FPS = self.session.output_spec()
            total = max(1, self.session.total_output_frames(clips))
            tmpdir = Path(tempfile.mkdtemp())
            tmp = str(tmpdir / "video.mp4")
            done = 0
            audio_segs: list[tuple[str, float, float]] = []

            with VideoWriter(tmp, (W, H), FPS) as writer:
                for idx, proj in enumerate(clips, 1):
                    if self._cancel.is_set():
                        self.done.emit("")
                        return
                    info = probe(proj.video_path)
                    src_fps = info.fps or FPS
                    start, end = proj.export_start, proj.export_end
                    self.status.emit(
                        f"[{idx}/{len(clips)}] {Path(proj.video_path).name} 렌더링 중…")

                    # 출력 fps 가 원본과 다르면 시간축을 맞춰 프레임을 재배치한다.
                    out_n = proj.timeline.output_length(start, end, src_fps)
                    n_out = max(1, int(round(out_n * FPS / src_fps)))
                    plan = list(proj.timeline.plan(start, end, src_fps))

                    reader = FrameReader(proj.video_path)
                    try:
                        cur_idx, cur_img, rendered = -1, None, None
                        last_k = -1
                        for k in range(n_out):
                            if self._cancel.is_set():
                                self.done.emit("")
                                return
                            j = min(len(plan) - 1, int(k * len(plan) / n_out))
                            if j != last_k:
                                o, fi, _fz, _el = plan[j]
                                if fi != cur_idx:
                                    cur_img = reader.frame_at(fi)
                                    cur_idx = fi
                                if cur_img is None:
                                    break
                                rendered = fit_letterbox(
                                    proj.render_output(cur_img, o, src_fps), W, H)
                                last_k = j
                            writer.write(rendered)
                            done += 1
                            self.progress.emit(done, total)
                    finally:
                        reader.close()

                    audio_segs.append((proj.video_path, start / src_fps,
                                       n_out / FPS))

            final = self.out_path
            # 정지 카드가 있으면 출력이 원본보다 길어져 소리가 어긋난다
            if self.with_audio and any(p.timeline.has_freezes for p in clips):
                self.status.emit("정지 카드가 있어 오디오 없이 내보냅니다")
                self.with_audio = False

            if self.with_audio:
                self.status.emit("오디오 이어 붙이는 중…")
                atmp = str(tmpdir / "audio.m4a")
                if concat_audio(audio_segs, atmp) and mux_tracks(tmp, atmp, final):
                    self.done.emit(final)
                    return
                self.status.emit("오디오를 붙이지 못해 영상만 내보냅니다")
            Path(tmp).replace(final)
            self.done.emit(final)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if tmpdir is not None:
                shutil.rmtree(tmpdir, ignore_errors=True)
