"""The demo video's timing: from the capture's clock to the video's.

The capture runs in real time, and a live search can take a minute. The recorder logs when
each beat starts and each interval the page waits on searches in flight (a ``Wait``). In the
video, a wait longer than ``SPEED_THRESHOLD`` seconds plays in ``SPED_TO`` seconds, under a
label naming its real length and the factor, shown for exactly the sped-up stretch, so no
label ever sits over footage playing at real speed. Everything else plays at real speed.

Each beat's narration starts ``NARRATION_LEAD`` seconds into the beat, plus the beat's own
delay when its screen takes a while to reach what the first words say, and must end
``NARRATION_TAIL`` seconds before the next one starts. The recorder holds each beat on screen
long enough for that; when a narration is longer than the footage it was captured for (its
words changed after the capture), the beat's last frame is held (a ``freeze``) instead, so the
narration never runs into the next beat. Times are kept on the video's frame grid, so the
footage the render cuts is as long as the plan says.

Nothing here touches a browser, a file or ffmpeg: ``plan`` is arithmetic, and
``video_filter`` and ``audio_filter`` only write the filter graphs the render hands to ffmpeg.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

FPS = 30
SPEED_THRESHOLD = 4.0
SPED_TO = 3.0
LABEL_MIN = 2.0
NARRATION_LEAD = 0.4
NARRATION_TAIL = 0.6
CEILING = 179.0


def on_grid(t: float) -> float:
    return round(t * FPS) / FPS


@dataclass(frozen=True)
class Wait:
    """The page waiting on ``searches`` searches from ``provider``, capture seconds
    ``start`` to ``end``."""

    start: float
    end: float
    searches: int = 0
    provider: str = "live"

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Segment:
    """Capture seconds ``start`` to ``end``, played ``factor`` times faster."""

    start: float
    end: float
    factor: float = 1.0
    wait: Wait | None = None

    @property
    def output(self) -> float:
        return (self.end - self.start) / self.factor


@dataclass(frozen=True)
class Label:
    """The sped-up label for ``wait``, on screen from video second ``start`` to ``end``."""

    start: float
    end: float
    text: str
    wait: Wait
    factor: float


@dataclass(frozen=True)
class PlannedBeat:
    key: str
    start: float
    end: float
    segments: tuple[Segment, ...]
    out_start: float
    out_end: float
    freeze: float
    narration_at: float
    narration_seconds: float


@dataclass(frozen=True)
class Plan:
    beats: tuple[PlannedBeat, ...]
    labels: tuple[Label, ...]

    @property
    def duration(self) -> float:
        return self.beats[-1].out_end if self.beats else 0.0

    @property
    def segments(self) -> tuple[tuple[Segment, float], ...]:
        """Every segment in order, each with the freeze that follows it (0 but for a beat's
        last segment)."""
        out = []
        for beat in self.beats:
            for i, segment in enumerate(beat.segments):
                out.append((segment, beat.freeze if i == len(beat.segments) - 1 else 0.0))
        return tuple(out)


def sped(
    waits: Sequence[Wait], threshold: float = SPEED_THRESHOLD, sped_to: float = SPED_TO
) -> list[Wait]:
    """The waits the video speeds up, in order: those longer than ``threshold`` and than
    ``sped_to`` (speeding a wait up to last longer would slow it down)."""
    chosen = sorted(
        (w for w in waits if w.seconds > max(threshold, sped_to)), key=lambda w: w.start
    )
    for before, after in zip(chosen, chosen[1:], strict=False):
        if after.start < before.end:
            raise ValueError(f"waits overlap: {before} and {after}")
    return chosen


def segments(
    start: float,
    end: float,
    waits: Sequence[Wait],
    *,
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> list[Segment]:
    """Capture seconds ``start`` to ``end`` as real-speed and sped-up segments."""
    pieces: list[Segment] = []
    t = start
    for wait in sped(waits, threshold, sped_to):
        a, b = max(wait.start, start), min(wait.end, end)
        if b <= a:
            continue
        if a > t:
            pieces.append(Segment(t, a))
        pieces.append(Segment(a, b, wait.seconds / sped_to, wait))
        t = b
    if end > t:
        pieces.append(Segment(t, end))
    return pieces


def output_elapsed(
    start: float,
    now: float,
    waits: Sequence[Wait],
    *,
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> float:
    """How long capture seconds ``start`` to ``now`` last in the video."""
    return sum(s.output for s in segments(start, now, waits, threshold=threshold, sped_to=sped_to))


def beat_minimum(narration_seconds: float, delay: float = 0.0) -> float:
    """The shortest a beat can last in the video and still carry its narration."""
    return NARRATION_LEAD + delay + narration_seconds + NARRATION_TAIL


def label_text(wait: Wait, factor: float) -> str:
    shown = f"{factor:.0f}" if factor >= 10 else f"{factor:.1f}"
    searches = f"{wait.searches} search{'es' if wait.searches != 1 else ''}"
    source = "Live SerpApi" if wait.provider == "live" else "Replayed SerpApi responses"
    return f"{source}: {searches} took {wait.seconds:.0f} s · shown {shown}x faster"


def plan(
    marks: Sequence[tuple[str, float]],
    end: float,
    waits: Sequence[Wait],
    narration: Mapping[str, float],
    delays: Mapping[str, float] | None = None,
    *,
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> Plan:
    """The video's timeline: beat ``marks[i]`` runs from its capture second to the next
    beat's (the last to ``end``); the capture before the first beat is left out. ``delays``
    holds a beat's narration back, by seconds, from its usual start."""
    if sped_to < LABEL_MIN:
        raise ValueError(f"a sped-up wait lasts at least {LABEL_MIN} s, to read its label")
    if not marks:
        raise ValueError("the capture has no beats")
    starts = [on_grid(t) for _, t in marks]
    ends = [*starts[1:], on_grid(end)]
    for key, (a, b) in zip((k for k, _ in marks), zip(starts, ends, strict=True), strict=True):
        if b <= a:
            raise ValueError(f"beat {key} has no footage")
    grid = [Wait(on_grid(w.start), on_grid(w.end), w.searches, w.provider) for w in waits]
    for wait in sped(grid, threshold, sped_to):
        if any(wait.start < s < wait.end for s in starts[1:]):
            raise ValueError(f"a sped-up wait crosses a beat boundary: {wait}")

    planned, labels, clock = [], [], 0.0
    for (key, _), a, b in zip(marks, starts, ends, strict=True):
        pieces = segments(a, b, grid, threshold=threshold, sped_to=sped_to)
        footage = sum(s.output for s in pieces)
        spoken = narration.get(key, 0.0)
        delay = on_grid((delays or {}).get(key, 0.0))
        short = beat_minimum(spoken, delay) - footage if spoken else 0.0
        freeze = math.ceil(short * FPS) / FPS if short > 0 else 0.0
        t = clock
        for piece in pieces:
            if piece.wait is not None:
                text = label_text(piece.wait, piece.factor)
                labels.append(Label(t, t + piece.output, text, piece.wait, piece.factor))
            t += piece.output
        out_end = clock + footage + freeze
        planned.append(
            PlannedBeat(
                key,
                a,
                b,
                tuple(pieces),
                clock,
                out_end,
                freeze,
                clock + NARRATION_LEAD + delay,
                spoken,
            )
        )
        clock = out_end
    return Plan(tuple(planned), tuple(labels))


# ---- the render's filter graphs -----------------------------------------------------------


def _n(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def video_filter(plan: Plan, label_position: tuple[str, str] = ("(W-w)/2", "156")) -> str:
    """The ``-filter_complex`` video half: input 0 is the capture, inputs 1.. are the labels'
    images in ``plan.labels`` order. Ends in ``[vout]``."""
    pieces = plan.segments
    names = [f"s{i}" for i in range(len(pieces))]
    chains = [f"[0:v]fps={FPS},split={len(pieces)}" + "".join(f"[{n}]" for n in names)]
    for i, (segment, freeze) in enumerate(pieces):
        chain = f"[{names[i]}]trim=start={_n(segment.start)}:end={_n(segment.end)}"
        if segment.factor == 1:
            chain += ",setpts=PTS-STARTPTS"
        else:
            # Resampling thousands of frames onto the grid can drop the last tick, so the frame
            # count is fixed here: padded by a second of the last frame, then cut to the plan.
            frames = round(segment.output * FPS)
            chain += (
                f",setpts=(PTS-STARTPTS)/{_n(segment.factor)},fps={FPS}"
                f",tpad=stop_mode=clone:stop_duration=1,trim=end_frame={frames}"
                ",setpts=PTS-STARTPTS"
            )
        if freeze:
            chain += f",tpad=stop_mode=clone:stop_duration={_n(freeze)}"
        chains.append(chain + f"[v{i}]")
    chains.append(
        "".join(f"[v{i}]" for i in range(len(pieces)))
        + f"concat=n={len(pieces)}:v=1:a=0,fps={FPS}[c0]"
    )
    last = "c0"
    x, y = label_position
    for i, label in enumerate(plan.labels, 1):
        chains.append(
            f"[{last}][{i}:v]overlay=x={x}:y={y}:"
            f"enable='between(t,{_n(label.start)},{_n(label.end)})'[c{i}]"
        )
        last = f"c{i}"
    chains.append(f"[{last}]format=yuv420p[vout]")
    return ";".join(chains)


def audio_filter(plan: Plan, first_input: int) -> str:
    """The ``-filter_complex`` audio half: each narrated beat's clip is an input, in beat order
    from ``first_input``, placed at its ``narration_at``, over silence for the whole video.
    Ends in ``[aout]``."""
    spoken = [b for b in plan.beats if b.narration_seconds]
    chains = []
    for i, beat in enumerate(spoken):
        ms = round(beat.narration_at * 1000)
        chains.append(
            f"[{first_input + i}:a]aresample=48000,"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,adelay=delays={ms}:all=1[a{i}]"
        )
    mix = "".join(f"[a{i}]" for i in range(len(spoken)))
    chains.append(
        f"{mix}amix=inputs={len(spoken)}:normalize=0:duration=longest,"
        f"apad=whole_dur={_n(plan.duration)}[aout]"
    )
    return ";".join(chains)
