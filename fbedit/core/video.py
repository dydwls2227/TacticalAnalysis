"""비디오 입출력 유틸리티."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    n_frames: int

    @property
    def duration(self) -> float:
        return self.n_frames / self.fps if self.fps else 0.0

    def timecode(self, frame: int) -> str:
        t = frame / self.fps if self.fps else 0.0
        m, s = divmod(t, 60)
        return f"{int(m):02d}:{s:05.2f}"


def probe(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise IOError(f"영상을 열 수 없습니다: {path}")
    info = VideoInfo(
        path=str(path),
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        fps=cap.get(cv2.CAP_PROP_FPS) or 30.0,
        n_frames=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    )
    cap.release()
    return info


class FrameReader:
    """순차 읽기에 최적화된 리더. 임의 접근은 seek() 후 read()."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            raise IOError(f"영상을 열 수 없습니다: {path}")
        self.pos = 0

    def seek(self, frame: int) -> None:
        frame = max(0, int(frame))
        if frame == self.pos:
            return
        # 짧은 전진은 순차 디코딩이 seek보다 빠르고 정확하다
        if 0 < frame - self.pos <= 12:
            for _ in range(frame - self.pos):
                if not self.cap.grab():
                    break
                self.pos += 1
            return
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        self.pos = frame

    def read(self) -> np.ndarray | None:
        ok, frame = self.cap.read()
        if not ok:
            return None
        self.pos += 1
        return frame

    def frame_at(self, frame: int) -> np.ndarray | None:
        self.seek(frame)
        return self.read()

    def iter_range(self, start: int, end: int):
        """[start, end) 구간을 (frame_index, image) 로 순회."""
        self.seek(start)
        for i in range(start, end):
            img = self.read()
            if img is None:
                return
            yield i, img

    def close(self) -> None:
        self.cap.release()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class VideoWriter:
    """libx264 로 인코딩하는 BGR 프레임 라이터."""

    def __init__(self, path: str | Path, size: tuple[int, int], fps: float, crf: int = 18):
        self.path = str(path)
        self._w = imageio_ffmpeg.write_frames(
            self.path,
            size=size,  # (width, height)
            fps=fps,
            codec="libx264",
            pix_fmt_in="bgr24",
            pix_fmt_out="yuv420p",
            macro_block_size=2,
            quality=None,
            output_params=["-crf", str(crf), "-preset", "medium"],
        )
        self._w.send(None)

    def write(self, frame_bgr: np.ndarray) -> None:
        self._w.send(np.ascontiguousarray(frame_bgr))

    def close(self) -> None:
        try:
            self._w.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def fit_letterbox(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """비율을 유지한 채 (width, height) 안에 맞추고 남는 곳은 검게 채운다.

    클립마다 해상도가 다를 때 늘려 붙이면 선수가 홀쭉해지므로 여백을 넣는다.
    """
    h, w = frame.shape[:2]
    if (w, h) == (width, height):
        return frame
    scale = min(width / w, height / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
    small = cv2.resize(frame, (nw, nh), interpolation=interp)
    out = np.zeros((height, width, 3), dtype=frame.dtype)
    x0, y0 = (width - nw) // 2, (height - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


def has_audio(path: str) -> bool:
    """오디오 스트림이 있는지 확인. ffprobe 없이 ffmpeg 로 판별한다."""
    res = subprocess.run(
        [FFMPEG, "-hide_banner", "-i", str(path), "-f", "null", "-t", "0", "-"],
        capture_output=True, text=True, errors="ignore")
    return "Audio:" in (res.stderr or "")


def concat_audio(segments: list[tuple[str, float, float]], out_path: str) -> bool:
    """(원본, 시작초, 길이초) 목록의 오디오를 순서대로 이어 붙인다.

    오디오가 없는 클립 자리에는 같은 길이의 무음을 넣어야 뒤 클립이 밀리지 않는다.
    """
    if not segments:
        return False
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    parts, n = [], 0
    for src, start, dur in segments:
        if dur <= 0:
            continue
        if has_audio(src):
            cmd += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
        else:
            cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        parts.append(f"[{n}:a]aresample=48000,aformat="
                     f"sample_fmts=fltp:channel_layouts=stereo[a{n}]")
        n += 1
    if n == 0:
        return False
    graph = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(n))
    graph += f"concat=n={n}:v=0:a=1[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", "160k",
            str(out_path)]
    res = subprocess.run(cmd, capture_output=True)
    return res.returncode == 0 and Path(out_path).exists()


def mux_tracks(video_path: str, audio_path: str, out_path: str) -> bool:
    """이미 만들어 둔 오디오 파일을 영상에 붙인다."""
    res = subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(video_path),
         "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)],
        capture_output=True)
    return res.returncode == 0 and Path(out_path).exists()


def mux_audio(video_path: str, source_path: str, out_path: str,
              start_sec: float = 0.0, dur_sec: float | None = None) -> bool:
    """원본에서 해당 구간 오디오를 잘라 무음 영상에 붙인다. 오디오가 없으면 False."""
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", video_path,
           "-ss", f"{start_sec:.3f}"]
    if dur_sec is not None:
        cmd += ["-t", f"{dur_sec:.3f}"]
    cmd += ["-i", source_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-shortest", out_path]
    res = subprocess.run(cmd, capture_output=True)
    return res.returncode == 0 and Path(out_path).exists() and Path(out_path).stat().st_size > 0
