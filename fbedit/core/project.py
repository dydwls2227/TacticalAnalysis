"""프로젝트 상태: 영상 + 추적 결과 + 선수별 표시 설정."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .formation import ROLE_TO_LINE, FormationStyle, draw_formation, groups_at
from .anim import Anim, ease_out_cubic, span_timing
from .markers import Marker, draw_markers
from .overlay import HighlightStyle, render_frame, ring_axes
from .text import draw_caption
from .timeline import FreezeCard, Timeline, TimeMap, freeze_progress, hold_frames
from .tracking import Box, TrackSet
from .video import probe

# 기본 테마 색(BGR). 강조·링·대형선이 모두 이 색을 쓰고,
# 선수별로 다르게 하고 싶을 때만 목록의 '색' 칸에서 바꾼다.
DEFAULT_THEME = (255, 255, 255)


@dataclass
class PlayerState:
    tid: int
    name: str = ""
    number: str = ""
    enabled: bool = False
    line: int = 0           # 대형선 그룹 (0 = 선 없음). 뷰어 우클릭으로 다음 라인
    order: int = 0          # 사용자가 고른 순서 — 대형선을 이 순서대로 잇는다
    # 표시 구간 목록. 비어 있으면 항상 표시. 끝이 -1 이면 "영상 끝까지".
    # 같은 선수를 서로 떨어진 여러 장면에서 강조할 수 있어야 해서 목록으로 둔다.
    spans: list[list[int]] = field(default_factory=list)
    style: HighlightStyle = field(default_factory=HighlightStyle)

    def visible_at(self, frame: int) -> bool:
        if not self.enabled:
            return False
        if not self.spans:
            return True
        return any(frame >= a and (b < 0 or frame <= b) for a, b in self.spans)

    # ---- 구간 편집 ----
    def span_begin(self, frame: int) -> None:
        """여기부터 — 새 구간을 연다(끝은 아직 미정)."""
        self.spans.append([frame, -1])

    def span_end(self, frame: int) -> None:
        """여기까지 — 열려 있는 마지막 구간을 닫는다.

        열린 구간이 없고 구간 자체가 없으면(= 제한 없이 항상 보이던 상태) 처음부터
        이 프레임까지로 본다. 이미 모든 구간이 닫혀 있으면 **아무것도 하지 않는다** —
        그 선수는 앞선 시퀀스에서 이미 끝났으므로, 0부터 다시 그으면 안 된다.
        """
        for sp in reversed(self.spans):
            if sp[1] < 0:
                sp[1] = frame
                return
        if not self.spans:
            self.spans = [[0, frame]]

    def span_stop(self, frame: int) -> None:
        """이 프레임에서 표시를 끊는다.

        연 프레임에서 바로 다시 껐다면 잘못 누른 것이므로 그 구간을 통째로 취소한다.
        """
        for sp in reversed(self.spans):
            if sp[0] <= frame and (sp[1] < 0 or frame <= sp[1]):
                if sp[0] >= frame:
                    self.spans.remove(sp)
                else:
                    sp[1] = frame
                return
        if not self.spans:          # 구간 제한 없이 항상 보이던 상태
            self.spans = [[0, frame]]

    def span_clear(self) -> None:
        self.spans = []

    @property
    def range_text(self) -> str:
        if not self.spans:
            return "전체"
        return ", ".join(f"{a}~{'끝' if b < 0 else b}" for a, b in self.spans)

    @property
    def label(self) -> str:
        if self.number and self.name:
            return f"{self.number}. {self.name}"
        return self.name or self.number

    @property
    def display(self) -> str:
        return self.label or f"ID {self.tid}"

    def to_dict(self) -> dict:
        return {"tid": self.tid, "name": self.name, "number": self.number,
                "enabled": self.enabled, "line": self.line, "order": self.order,
                "spans": [list(s) for s in self.spans],
                "style": self.style.to_dict()}

    @classmethod
    def from_dict(cls, d: dict) -> "PlayerState":
        # 예전 프로젝트는 역할 문자열(DF/MF/FW/GK)을 썼다 → 라인 번호로 옮긴다
        line = d.get("line")
        if line is None:
            line = ROLE_TO_LINE.get(d.get("role", ""), 0)
        # 예전에는 표시 구간이 하나뿐이었다(show_from/show_to) → 구간 목록으로 옮긴다
        spans = [list(s) for s in d.get("spans", [])]
        if not spans:
            a, b = d.get("show_from", -1), d.get("show_to", -1)
            if a >= 0 or b >= 0:
                spans = [[max(0, a), b]]
        return cls(tid=d["tid"], name=d.get("name", ""), number=d.get("number", ""),
                   enabled=d.get("enabled", False), line=line,
                   order=d.get("order", 0), spans=spans,
                   style=HighlightStyle.from_dict(d["style"]))


@dataclass
class Project:
    video_path: str = ""
    # trackset = raw_trackset 에 track_edits(합치기·맞바꾸기·끊기·팀 지정)를 적용한 결과.
    # 보정 기록만 되돌리기 스냅샷과 저장 파일에 들어가므로 큰 원본을 복사할 일이 없다.
    trackset: TrackSet | None = None
    raw_trackset: TrackSet | None = None
    track_edits: list[dict] = field(default_factory=list)
    players: dict[int, PlayerState] = field(default_factory=dict)
    formation: FormationStyle = field(default_factory=FormationStyle)
    timeline: Timeline = field(default_factory=Timeline)
    markers: list[Marker] = field(default_factory=list)
    theme_color: tuple[int, int, int] = DEFAULT_THEME
    anim_enter: bool = True     # 등장·퇴장 애니메이션 (솟아오름·페이드)
    anim_loop: bool = True      # 반복 효과 (링 회전·기둥 빛 흐름)
    export_start: int = 0
    export_end: int = 0
    path: str | None = None

    def add_marker(self, x: float, y: float) -> Marker:
        """(x, y) — 프레임 폭·높이 대비 비율 — 에 새 구역을 만든다.

        기본 테마 색(보통 흰색)을 따르지 않고 선명한 빨강으로 시작한다. 선수 강조와
        시각적으로 구분돼야 "공간 표시"라는 게 한눈에 들어온다. 물론 개별 색은
        선수와 마찬가지로 얼마든지 바꿀 수 있다.
        """
        mid = max((m.mid for m in self.markers), default=0) + 1
        m = Marker(mid=mid, x=x, y=y)
        self.markers.append(m)
        return m

    def remove_marker(self, mid: int) -> None:
        self.markers = [m for m in self.markers if m.mid != mid]

    # ---------- 추적 결과 연결 ----------
    def attach(self, ts: TrackSet) -> None:
        """새 추적 결과를 붙이되, 같은 ID의 기존 설정(이름/색)은 보존한다.

        다시 분석하면 ID 가 새로 매겨지므로 이전 보정 기록은 버린다.
        """
        self.raw_trackset = ts
        self.track_edits = []
        self.trackset = ts
        self.export_start, self.export_end = ts.start, ts.end
        for tid in ts.tracks:
            if tid not in self.players:
                self.players[tid] = PlayerState(
                    tid=tid, style=HighlightStyle(color=self.theme_color))
        for tid in list(self.players):
            if tid not in ts.tracks:
                del self.players[tid]

    # ---------- 추적 보정 ----------
    def _rebuild_tracks(self) -> None:
        if self.raw_trackset is None:
            return
        if not self.track_edits:
            self.trackset = self.raw_trackset
            return
        ts = self.raw_trackset.copy()
        for e in self.track_edits:
            try:
                ts.apply_edit(e)
            except KeyError:
                pass                # 대상 트랙이 없어진 기록은 건너뛴다
        self.trackset = ts

    def edit_tracks(self, e: dict) -> dict | None:
        """보정 하나를 적용하고 기록한다. 선수 설정도 함께 맞춘다."""
        if self.trackset is None:
            return
        if self.trackset is self.raw_trackset:
            self.trackset = self.raw_trackset.copy()   # 원본은 건드리지 않는다
        op = e["op"]
        if op == "split":
            e = {**e, "new": max(max(self.trackset.tracks), max(self.players, default=0)) + 1}
        self.trackset.apply_edit(e)
        self.track_edits.append(e)

        if op == "merge":
            self._merge_players(e["a"], e["b"])
        elif op == "split":
            a = self.players.get(e["a"])
            # 떼어낸 쪽은 다른 사람이므로 새 선수로 시작한다(강조 꺼짐)
            self.players[e["new"]] = PlayerState(
                tid=e["new"], style=HighlightStyle(color=self.theme_color))
            if a is not None and e["a"] not in self.trackset.tracks:
                del self.players[e["a"]]
        return e

    def _merge_players(self, a: int, b: int) -> None:
        pa, pb = self.players.get(a), self.players.pop(b, None)
        if pa is None or pb is None:
            return
        if pb.enabled and not pa.enabled:
            # 합쳐질 쪽만 강조돼 있었다 — 그 설정을 그대로 이어받는다
            pa.enabled, pa.line, pa.order, pa.spans, pa.style = (
                True, pb.line, pb.order, pb.spans, pb.style)
        elif pb.enabled:
            pa.spans = sorted(pa.spans + pb.spans) if pa.spans and pb.spans else []
        pa.name = pa.name or pb.name
        pa.number = pa.number or pb.number

    def team_of(self, tid: int) -> int:
        tr = self.trackset.tracks.get(tid) if self.trackset else None
        return tr.team if tr else 0

    def _anim(self, spans, o: int, fps: float, animate: bool) -> Anim:
        """o 는 결과 번호 — 정지 구간도 그대로 흐르는 시간이라 따로 셈할 게 없다."""
        clock = o / (fps or 30.0)
        if animate and self.anim_enter:
            age, remain = span_timing(spans, o, fps)
            return Anim(age, remain, clock, self.anim_loop)
        return Anim(clock=clock, loop=self.anim_loop)

    def items_at(self, o: int, fi: int, fps: float = 30.0,
                 animate: bool = True) -> list[tuple[Box, HighlightStyle, Anim]]:
        """결과 번호 o (원본 프레임 fi) 에서 강조가 켜진 선수들의 (박스, 스타일, 애니메이션)."""
        if self.trackset is None:
            return []
        items = []
        for tid, box in self.trackset.boxes_at(fi).items():
            ps = self.players.get(tid)
            if ps is None or not ps.visible_at(o):
                continue
            style = HighlightStyle(**{**ps.style.__dict__, "label": ps.label})
            if self.formation.unify_ring:
                style.ring_color = self.formation.color
            items.append((box, style, self._anim(ps.spans, o, fps, animate)))
        return items

    def render(self, frame, o: int, fi: int, copy: bool = True, fps: float = 30.0,
               animate: bool = True):
        """미리보기와 내보내기가 갈라지지 않도록 렌더 경로를 하나로 둔다.

        대형선을 먼저 깔고 그 위에 선수 강조를 얹는다(선이 선수를 가리지 않게).
        animate=False 면 등장·퇴장 없이 완전히 나타난 상태로 그린다(일시정지 미리보기).
        """
        out = frame.copy() if copy else frame
        if self.trackset is not None:
            items = self.items_at(o, fi, fps, animate)
            anims = {id(b): an for b, _, an in items}
            # 링을 켠 선수는 선을 링 경계에서 끊는다 (링끼리 이어지는 모양)
            ringed = {id(b) for b, st, _ in items if st.ring}
            ring_of = lambda b: ring_axes(b) if id(b) in ringed else None

            def anim_of(b):
                an = anims.get(id(b))
                if an is None:
                    return 1.0, 1.0, 99.0
                return ease_out_cubic(an.ph(0.18, 0.4)), an.out, an.age

            boxes = self.trackset.boxes_at(fi)
            draw_formation(out, groups_at(self.players, boxes, o), self.formation,
                           ring_of, anim_of)
            out = render_frame(out, items, copy=False)
        if self.markers:
            draw_markers(out, self.markers, o, fps, 0.0, animate and self.anim_enter)
        return out

    # ---------- 결과 시간 · 정지 카드 ----------
    def timemap(self, fps: float) -> TimeMap:
        return self.timeline.timemap(fps)

    def shift_time(self, o0: int, delta: int) -> None:
        """결과 번호 o0 부터 delta 만큼 시간을 넣거나(+) 뺀다(−).

        정지 카드를 넣으면 그 뒤 영상이 통째로 밀리므로, 뒤쪽에 걸린 표시 구간과
        자막도 같이 밀어야 같은 장면에 붙어 있는다. 빼는 구간에 걸린 끝점은 o0 로
        모으고, 통째로 빠진 구간은 지운다.
        """
        if delta == 0:
            return
        cut_end = o0 - delta if delta < 0 else o0

        def sh(v: int) -> int:
            if delta > 0:
                return v + delta if v >= o0 else v
            if v >= cut_end:
                return v + delta
            return o0 if v >= o0 else v

        gone_markers = []
        for obj in list(self.players.values()) + list(self.markers):
            spans = []
            for a, b in obj.spans:
                if delta < 0 and o0 <= a and 0 <= b < cut_end:
                    continue                    # 빠지는 구간 안에만 있던 표시
                spans.append([sh(a), sh(b) if b >= 0 else -1])
            if obj.spans and not spans:
                # 구간이 하나도 안 남았다 — 빈 목록은 '항상 표시'라 그대로 두면 안 된다
                if isinstance(obj, PlayerState):
                    self.set_enabled(obj.tid, False)
                else:
                    gone_markers.append(obj)
            obj.spans = spans
        self.markers = [m for m in self.markers if m not in gone_markers]
        keep = []
        for c in self.timeline.captions:
            if delta < 0 and o0 <= c.start and c.end <= cut_end:
                continue
            c.start, c.end = sh(c.start), sh(c.end)
            c.end = max(c.end, c.start + 1)
            keep.append(c)
        self.timeline.captions = keep

    def add_freeze(self, fi: int, fps: float, seconds: float = 3.0, **kw) -> FreezeCard:
        fz = FreezeCard(frame=fi, seconds=seconds, **kw)
        o0 = self.timemap(fps).out(fi) + 1
        self.timeline.freezes.append(fz)
        self.shift_time(o0, hold_frames(seconds, fps))
        return fz

    def set_freeze_seconds(self, fz: FreezeCard, seconds: float, fps: float) -> None:
        o0, o1 = self.timemap(fps).block(fz)
        new = hold_frames(seconds, fps)
        fz.seconds = seconds
        old = o1 - o0
        if new > old:
            self.shift_time(o1, new - old)
        elif new < old:
            self.shift_time(o0 + new, new - old)

    def remove_freeze(self, fz: FreezeCard, fps: float) -> None:
        o0, o1 = self.timemap(fps).block(fz)
        self.timeline.freezes.remove(fz)
        self.shift_time(o0, o0 - o1)

    def move_freeze(self, fz: FreezeCard, fi: int, fps: float) -> None:
        """멈추는 장면을 바꾼다 — 빼고 새 자리에 다시 넣는 것과 같다."""
        if fi == fz.frame:
            return
        seconds = fz.seconds
        self.remove_freeze(fz, fps)
        fz.frame = fi
        o0 = self.timemap(fps).out(fi) + 1
        self.timeline.freezes.append(fz)
        self.shift_time(o0, hold_frames(seconds, fps))

    def _to_output_time(self, fps: float) -> None:
        """예전 프로젝트(원본 프레임 기준 구간) → 결과 번호 기준."""
        tm = self.timemap(fps)
        if not tm.items:
            return
        for obj in list(self.players.values()) + list(self.markers):
            obj.spans = [[tm.out(a), tm.last_out(b) if b >= 0 else -1] for a, b in obj.spans]
        for c in self.timeline.captions:
            c.start, c.end = tm.out(c.start), tm.out(c.end)

    def reset_edits(self) -> None:
        """편집 내용을 모두 지운다. 영상과 추적 결과는 그대로 두므로 재분석은 불필요.

        되돌리기 스택을 거치므로 ⌘Z 로 복구할 수 있다.
        """
        for ps in self.players.values():
            ps.enabled = False
            ps.line = 0
            ps.order = 0
            ps.name = ""
            ps.number = ""
            ps.spans = []
            ps.style = HighlightStyle(color=self.theme_color)
        self.timeline = Timeline()
        self.formation = FormationStyle(color=self.theme_color)
        self.markers = []

    # ---------------------------------------------------------------- 되돌리기
    def snapshot(self) -> dict:
        """되돌리기용 상태 스냅샷.

        추적 결과 자체는 용량이 커서(수천 개 박스) 넣지 않고 **보정 기록만** 넣는다.
        되돌리면 원본에 기록을 다시 적용해 추적 결과를 복원한다.
        """
        return {
            "players": [p.to_dict() for p in self.players.values()],
            "formation": self.formation.to_dict(),
            "timeline": self.timeline.to_dict(),
            "markers": [m.to_dict() for m in self.markers],
            "theme_color": list(self.theme_color),
            "anim": [self.anim_enter, self.anim_loop],
            "export_start": self.export_start,
            "export_end": self.export_end,
            "track_edits": [dict(e) for e in self.track_edits],
        }

    def restore(self, snap: dict) -> None:
        edits = snap.get("track_edits", [])
        if edits != self.track_edits:
            self.track_edits = [dict(e) for e in edits]
            self._rebuild_tracks()
        self.players = {p["tid"]: PlayerState.from_dict(p) for p in snap["players"]}
        self.formation = FormationStyle.from_dict(snap["formation"])
        self.timeline = Timeline.from_dict(snap["timeline"])
        self.markers = [Marker.from_dict(m) for m in snap.get("markers", [])]
        self.theme_color = tuple(snap["theme_color"])
        self.anim_enter, self.anim_loop = snap.get("anim", [True, True])
        self.export_start = snap["export_start"]
        self.export_end = snap["export_end"]

    def set_enabled(self, tid: int, on: bool, line: int | None = None) -> None:
        """강조를 켜고 끈다.

        켤 때 선택 순서를 기록해 대형선 연결 순서로 쓴다. line 을 주면 그 라인에
        이어 붙인다(뷰어 좌클릭 경로). 끄면 라인에서도 빠진다.
        """
        ps = self.players.get(tid)
        if ps is None:
            return
        if ps.enabled == on and (line is None or ps.line == line):
            return
        ps.enabled = on
        ps.order = (max((p.order for p in self.players.values()), default=0) + 1
                    if on else 0)
        if not on:
            ps.line = 0
        elif line is not None:
            ps.line = line

    def apply_theme(self, color: tuple[int, int, int]) -> None:
        """테마 색을 모든 선수와 대형선에 적용한다(개별 커스텀은 덮어쓴다)."""
        self.theme_color = color
        self.formation.color = color
        for ps in self.players.values():
            ps.style.color = color

    def render_output(self, frame, o: int, fps: float, copy: bool = True,
                      animate: bool = True):
        """결과 번호 o 의 최종 한 프레임 — 선수 강조 + 대형선 + 자막/정지 카드.

        frame 은 o 에 해당하는 원본 프레임 이미지(`timemap(fps).locate(o)[0]`)다.
        """
        fi, freeze, elapsed = self.timemap(fps).locate(o)
        out = self.render(frame, o, fi, copy=copy, fps=fps, animate=animate)
        for cap, p, fade in self.timeline.captions_at(o, fps):
            draw_caption(out, cap.text, cap.style, p, fade)
        if freeze is not None and freeze.text.strip():
            p, fade = freeze_progress(freeze, elapsed)
            draw_caption(out, freeze.text, freeze.style, p, fade)
        return out

    @property
    def enabled_count(self) -> int:
        return sum(1 for p in self.players.values() if p.enabled)

    @property
    def role_count(self) -> int:
        return sum(1 for p in self.players.values() if p.enabled and p.line > 0)

    # ---------- 저장/로드 ----------
    def to_dict(self) -> dict:
        return {
            "video_path": self.video_path,
            "export_start": self.export_start,
            "export_end": self.export_end,
            "time_base": "output",
            "formation": self.formation.to_dict(),
            "timeline": self.timeline.to_dict(),
            "markers": [m.to_dict() for m in self.markers],
            "theme_color": list(self.theme_color),
            "anim": [self.anim_enter, self.anim_loop],
            "players": [p.to_dict() for p in self.players.values()],
            "trackset": self.raw_trackset.to_dict() if self.raw_trackset else None,
            "track_edits": self.track_edits,
        }

    def save(self, path: str | Path) -> None:
        data = {"version": 2, **self.to_dict()}
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        self.path = str(path)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        proj = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        proj.path = str(path)
        return proj

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        proj = cls(video_path=d["video_path"],
                   export_start=d.get("export_start", 0),
                   export_end=d.get("export_end", 0))
        if d.get("formation"):          # v1 프로젝트에는 없다
            proj.formation = FormationStyle.from_dict(d["formation"])
        if d.get("timeline"):
            proj.timeline = Timeline.from_dict(d["timeline"])
        if d.get("theme_color"):
            proj.theme_color = tuple(d["theme_color"])
        proj.anim_enter, proj.anim_loop = d.get("anim", [True, True])
        if d.get("trackset"):
            proj.raw_trackset = TrackSet.from_dict(d["trackset"])
            proj.track_edits = list(d.get("track_edits", []))
            proj._rebuild_tracks()
        proj.players = {p["tid"]: PlayerState.from_dict(p) for p in d.get("players", [])}
        proj.markers = [Marker.from_dict(m) for m in d.get("markers", [])]
        if d.get("time_base") != "output" and proj.timeline.freezes:
            try:
                fps = (proj.raw_trackset.info.fps if proj.raw_trackset
                       else probe(proj.video_path).fps)
                proj._to_output_time(fps)
            except Exception:
                pass
        return proj
