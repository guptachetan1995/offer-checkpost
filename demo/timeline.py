"""The demo video's timing: from the capture's clock to the video's.

The capture runs in real time, and a live search can take a minute. The recorder logs when
each beat starts and each interval the page waits on searches in flight (a ``Wait``). In the
video, a wait longer than ``SPEED_THRESHOLD`` seconds plays in ``SPED_TO`` seconds, under a
label naming its real length and the factor, shown for exactly the sped-up stretch, so no
label ever sits over footage playing at real speed. Everything else plays at real speed.

A beat's narration is a list of sentences, one clip each. The recorder logs the capture
second at which the page reached what each sentence says (a cue), and a clip starts there, as
soon as the one before it has ended and ``CLIP_GAP`` has passed, and no sooner than
``NARRATION_LEAD`` into its beat. The last clip must end ``NARRATION_TAIL`` seconds before the
next beat starts. The recorder holds each beat on screen long enough for that; when a narration
is longer than the footage it was captured for (its words changed after the capture), the
beat's last frame is held (a ``freeze``) instead, so the narration never runs into the next
beat. Times are kept on the video's frame grid, so the footage the render cuts is as long as
the plan says.

A stretch of the capture where the screen did not change and nobody is speaking can be cut
(``idle_cuts``): the frames on either side of it are the same picture, so nothing shown is lost,
and ``KEEP_AFTER`` and ``KEEP_BEFORE`` seconds of it stay on either side of every sentence. A
wait on searches is never cut, only sped up, under its label.

Nothing here touches a browser, a file or ffmpeg: ``plan`` is arithmetic, and
``video_filter`` and ``audio_filter`` only write the filter graphs the render hands to ffmpeg.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

FPS = 30
SPEED_THRESHOLD = 4.0
SPED_TO = 2.5
LABEL_MIN = 2.0
NARRATION_LEAD = 0.4
NARRATION_TAIL = 0.6
CLIP_GAP = 0.25
KEEP_AFTER = 0.6
KEEP_BEFORE = 0.5
CUT_MINIMUM = 0.8
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
class Cut:
    """Capture seconds ``start`` to ``end``, left out of the video: a still, silent stretch."""

    start: float
    end: float

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class Label:
    """The sped-up label for ``wait``, on screen from video second ``start`` to ``end``."""

    start: float
    end: float
    text: str
    wait: Wait
    factor: float


@dataclass(frozen=True)
class Clip:
    """One narrated sentence, placed at video second ``at``."""

    at: float
    seconds: float

    @property
    def end(self) -> float:
        return self.at + self.seconds


@dataclass(frozen=True)
class PlannedBeat:
    """``late`` is how far the narration ran behind its cues: the most any clip started after
    the second its cue put it at, because the sentence before it was still being spoken."""

    key: str
    start: float
    end: float
    segments: tuple[Segment, ...]
    out_start: float
    out_end: float
    freeze: float
    clips: tuple[Clip, ...]
    late: float = 0.0


@dataclass(frozen=True)
class Plan:
    beats: tuple[PlannedBeat, ...]
    labels: tuple[Label, ...]
    cuts: tuple[Cut, ...] = ()

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
    cuts: Sequence[Cut] = (),
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> list[Segment]:
    """Capture seconds ``start`` to ``end`` as real-speed and sped-up segments, without the
    ``cuts``. A cut may not overlap a wait that is sped up."""
    pieces: list[Segment] = []
    t = start
    chosen = sped(waits, threshold, sped_to)
    for wait in chosen:
        a, b = max(wait.start, start), min(wait.end, end)
        if b <= a:
            continue
        if a > t:
            pieces.append(Segment(t, a))
        pieces.append(Segment(a, b, wait.seconds / sped_to, wait))
        t = b
    if end > t:
        pieces.append(Segment(t, end))
    if not cuts:
        return pieces
    for cut in cuts:
        for wait in chosen:
            if cut.start < wait.end and wait.start < cut.end:
                raise ValueError(f"a cut overlaps a sped-up wait: {cut} and {wait}")
    kept: list[Segment] = []
    for piece in pieces:
        if piece.wait is not None:
            kept.append(piece)
            continue
        t = piece.start
        for cut in sorted(cuts, key=lambda c: c.start):
            a, b = max(cut.start, piece.start), min(cut.end, piece.end)
            if b <= a:
                continue
            if a > t:
                kept.append(Segment(t, a))
            t = b
        if piece.end > t:
            kept.append(Segment(t, piece.end))
    return kept


def output_elapsed(
    start: float,
    now: float,
    waits: Sequence[Wait],
    *,
    cuts: Sequence[Cut] = (),
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> float:
    """How long capture seconds ``start`` to ``now`` last in the video."""
    pieces = segments(start, now, waits, cuts=cuts, threshold=threshold, sped_to=sped_to)
    return sum(s.output for s in pieces)


def place(seconds: Sequence[float], wants: Sequence[float], origin: float = 0.0) -> list[Clip]:
    """Where sentences of ``seconds`` each start, when sentence ``i`` wants to start at
    ``wants[i]`` (fewer wants than sentences leave the rest to follow on): at its want, or, when
    the sentence before is still being spoken, as soon as it and ``CLIP_GAP`` are over. No
    sentence starts before ``origin + NARRATION_LEAD``."""
    clips: list[Clip] = []
    earliest = origin + NARRATION_LEAD
    for i, length in enumerate(seconds):
        at = max(earliest, wants[i]) if i < len(wants) else earliest
        clips.append(Clip(at, length))
        earliest = at + length + CLIP_GAP
    return clips


def label_text(wait: Wait, factor: float) -> str:
    shown = f"{factor:.0f}" if factor >= 10 else f"{factor:.1f}"
    searches = f"{wait.searches} search{'es' if wait.searches != 1 else ''}"
    source = "Live SerpApi" if wait.provider == "live" else "Replayed SerpApi responses"
    return f"{source}: {searches} took {wait.seconds:.0f} s · shown {shown}x faster"


def plan(
    marks: Sequence[tuple[str, float]],
    end: float,
    waits: Sequence[Wait],
    narration: Mapping[str, Sequence[float]],
    cues: Mapping[str, Sequence[float]] | None = None,
    stills: Sequence[tuple[float, float]] = (),
    trims: Mapping[str, float] | None = None,
    *,
    threshold: float = SPEED_THRESHOLD,
    sped_to: float = SPED_TO,
) -> Plan:
    """The video's timeline: beat ``marks[i]`` runs from its capture second to the next
    beat's (the last to ``end``); the capture before the first beat is left out. ``narration``
    is each beat's sentences' lengths; ``cues`` the capture second at which the page reached
    what each said (a sentence with no cue follows the one before). ``stills`` are the capture
    intervals where the screen did not change: the ones that fall where nobody is speaking are
    cut. ``trims`` ends a beat's footage early, at a capture second."""
    args = (marks, end, waits, narration, cues, trims)
    laid = _lay_out(*args, cuts=(), threshold=threshold, sped_to=sped_to)
    cuts = idle_cuts(laid, stills)
    if not cuts:
        return laid
    return _lay_out(*args, cuts=cuts, threshold=threshold, sped_to=sped_to)


def _lay_out(
    marks: Sequence[tuple[str, float]],
    end: float,
    waits: Sequence[Wait],
    narration: Mapping[str, Sequence[float]],
    cues: Mapping[str, Sequence[float]] | None,
    trims: Mapping[str, float] | None,
    *,
    cuts: Sequence[Cut],
    threshold: float,
    sped_to: float,
) -> Plan:
    if sped_to < LABEL_MIN:
        raise ValueError(f"a sped-up wait lasts at least {LABEL_MIN} s, to read its label")
    if not marks:
        raise ValueError("the capture has no beats")
    starts = [on_grid(t) for _, t in marks]
    ends = [*starts[1:], on_grid(end)]
    ends = [
        min(e, on_grid((trims or {}).get(key, e))) for (key, _), e in zip(marks, ends, strict=True)
    ]
    for key, (a, b) in zip((k for k, _ in marks), zip(starts, ends, strict=True), strict=True):
        if b <= a:
            raise ValueError(f"beat {key} has no footage")
    grid = [Wait(on_grid(w.start), on_grid(w.end), w.searches, w.provider) for w in waits]
    for wait in sped(grid, threshold, sped_to):
        if any(wait.start < s < wait.end for s in [*starts[1:], *ends]):
            raise ValueError(f"a sped-up wait crosses a beat boundary: {wait}")

    planned, labels, clock = [], [], 0.0
    for (key, _), a, b in zip(marks, starts, ends, strict=True):
        pieces = segments(a, b, grid, cuts=cuts, threshold=threshold, sped_to=sped_to)
        footage = sum(s.output for s in pieces)
        spoken = list(narration.get(key, ()))
        heard = [min(max(on_grid(t), a), b) for t in (cues or {}).get(key, ())]
        wants = [
            clock + output_elapsed(a, t, grid, cuts=cuts, threshold=threshold, sped_to=sped_to)
            for t in heard
        ]
        clips = place(spoken, wants, clock)
        late = max((c.at - w for c, w in zip(clips, wants, strict=False)), default=0.0)
        short = clips[-1].end + NARRATION_TAIL - (clock + footage) if clips else 0.0
        freeze = math.ceil(short * FPS) / FPS if short > 0 else 0.0
        t = clock
        for piece in pieces:
            if piece.wait is not None:
                text = label_text(piece.wait, piece.factor)
                labels.append(Label(t, t + piece.output, text, piece.wait, piece.factor))
            t += piece.output
        out_end = clock + footage + freeze
        planned.append(
            PlannedBeat(key, a, b, tuple(pieces), clock, out_end, freeze, tuple(clips), late)
        )
        clock = out_end
    return Plan(tuple(planned), tuple(labels), tuple(cuts))


def idle_cuts(
    laid: Plan,
    stills: Sequence[tuple[float, float]],
    *,
    keep_after: float = KEEP_AFTER,
    keep_before: float = KEEP_BEFORE,
    minimum: float = CUT_MINIMUM,
) -> list[Cut]:
    """The capture stretches to leave out of ``laid``: where nobody speaks (``keep_after`` s
    after a sentence ends, until ``keep_before`` s before the next starts, or the beat's end)
    and the screen is one of the ``stills``, at real speed, and at least ``minimum`` long."""
    cuts: list[Cut] = []
    for beat in laid.beats:
        footage_end = beat.out_end - beat.freeze
        first = beat.clips[0].at - keep_before if beat.clips else footage_end
        windows = [(beat.out_start + keep_after, first)]
        for before, after in zip(beat.clips, beat.clips[1:], strict=False):
            windows.append((before.end + keep_after, after.at - keep_before))
        if beat.clips:
            windows.append((beat.clips[-1].end + keep_after, footage_end))
        v = beat.out_start
        for piece in beat.segments:
            if piece.wait is None:
                for lo, hi in windows:
                    a, b = max(lo, v), min(hi, v + piece.output)
                    for sa, sb in stills:
                        x = on_grid(max(piece.start + (a - v), sa))
                        y = on_grid(min(piece.start + (b - v), sb))
                        if b > a and y - x >= minimum:
                            cuts.append(Cut(x, y))
            v += piece.output
    return sorted(cuts, key=lambda c: c.start)


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
    """The ``-filter_complex`` audio half: each sentence's clip is an input, in beat then
    sentence order from ``first_input``, placed at its start, over silence for the whole video.
    Ends in ``[aout]``."""
    spoken = [clip for beat in plan.beats for clip in beat.clips]
    chains = []
    for i, clip in enumerate(spoken):
        ms = round(clip.at * 1000)
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
