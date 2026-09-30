"""The demo recorder's pure parts, offline: the timeline that maps the capture's clock to the
video's (speed-ups, freezes, narration placement, label windows, the total), the filter graphs
it hands ffmpeg, and the beat script. The recorder itself drives a browser, the app and ffmpeg,
and never runs in the tests."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parents[1]
# demo/ is the recorder's, beside the package rather than in it.
sys.path.insert(0, str(ENTRY))

from demo import timeline  # noqa: E402
from demo.beats import (  # noqa: E402
    BEATS,
    CUTAWAY,
    CUTAWAY_AFTER,
    PARTS,
    PROVIDERS,
    REPO_URL,
    TRACK,
    Beat,
    beats,
    judge,
)
from demo.timeline import (  # noqa: E402
    FPS,
    LABEL_MIN,
    NARRATION_LEAD,
    NARRATION_TAIL,
    SPED_TO,
    Wait,
    output_elapsed,
    plan,
    segments,
)

KEYS = [b.key for b in BEATS]


def marks(*starts):
    return list(zip(KEYS, starts, strict=False))


# ---- speed-ups ----------------------------------------------------------------------------


def test_a_capture_without_long_waits_plays_at_real_speed():
    short = [Wait(10.0, 13.5, 2)]
    assert segments(0.0, 60.0, short) == [timeline.Segment(0.0, 60.0)]
    assert output_elapsed(0.0, 60.0, short) == 60.0


def test_a_long_wait_plays_in_sped_to_seconds_and_the_rest_at_real_speed():
    minute = Wait(10.0, 73.0, 2)
    pieces = segments(0.0, 100.0, [minute])
    assert [(p.start, p.end, p.factor) for p in pieces] == [
        (0.0, 10.0, 1.0),
        (10.0, 73.0, 63.0 / SPED_TO),
        (73.0, 100.0, 1.0),
    ]
    assert pieces[1].output == pytest.approx(SPED_TO)
    assert output_elapsed(0.0, 100.0, [minute]) == pytest.approx(100.0 - 63.0 + SPED_TO)


def test_timestamps_after_a_speed_up_move_earlier_by_what_it_saved():
    waits = [Wait(5.0, 45.0, 2), Wait(60.0, 70.0, 4)]
    saved = (40.0 - SPED_TO) + (10.0 - SPED_TO)
    assert output_elapsed(0.0, 80.0, waits) == pytest.approx(80.0 - saved)
    # A moment inside the first wait lands inside its sped-up stretch.
    assert output_elapsed(0.0, 25.0, waits) == pytest.approx(5.0 + 20.0 / (40.0 / SPED_TO))


def test_a_wait_barely_longer_than_the_speed_up_is_never_slowed_down():
    assert segments(0.0, 10.0, [Wait(1.0, 3.5)], threshold=2.0) == [timeline.Segment(0.0, 10.0)]


def test_overlapping_waits_are_refused():
    with pytest.raises(ValueError, match="overlap"):
        timeline.sped([Wait(0.0, 10.0), Wait(5.0, 20.0)])


# ---- the plan -----------------------------------------------------------------------------


def test_the_video_starts_at_the_first_beat_and_its_beats_follow_on_without_gaps():
    p = plan(marks(1.5, 11.5, 30.0), 40.0, [], {})
    assert [b.key for b in p.beats] == KEYS[:3]
    assert p.beats[0].out_start == 0.0
    for before, after in zip(p.beats, p.beats[1:], strict=False):
        assert after.out_start == before.out_end
    assert p.duration == pytest.approx(40.0 - 1.5)


def test_the_total_is_real_time_less_what_speed_ups_saved_plus_what_freezes_added():
    live_search = Wait(22.0, 85.0, 2)
    narration = {KEYS[0]: 5.0, KEYS[1]: 20.0, KEYS[2]: 40.0}
    p = plan(marks(0.0, 10.0, 100.0), 120.0, [live_search], narration)
    beat_b = 90.0 - 63.0 + SPED_TO
    freeze_c = timeline.beat_minimum(40.0) - 20.0
    assert [round(b.out_end - b.out_start, 3) for b in p.beats] == [
        10.0,
        pytest.approx(beat_b, abs=1 / FPS),
        pytest.approx(20.0 + freeze_c, abs=1 / FPS),
    ]
    assert p.duration == pytest.approx(10.0 + beat_b + 20.0 + freeze_c, abs=2 / FPS)
    assert p.duration < timeline.CEILING


@pytest.mark.parametrize(
    "narration",
    [
        {KEYS[0]: 3.0, KEYS[1]: 9.0, KEYS[2]: 2.0},
        {KEYS[0]: 12.0, KEYS[1]: 30.0, KEYS[2]: 25.0},
        {KEYS[0]: 9.6, KEYS[1]: 0.0, KEYS[2]: 18.99},
    ],
)
def test_narration_starts_in_its_beat_and_ends_before_the_next_one(narration):
    p = plan(marks(0.0, 10.0, 22.0), 40.0, [Wait(12.0, 20.0, 1)], narration)
    for beat in p.beats:
        assert beat.narration_at == pytest.approx(beat.out_start + NARRATION_LEAD)
        if beat.narration_seconds:
            end = beat.narration_at + beat.narration_seconds + NARRATION_TAIL
            assert end <= beat.out_end + 1e-9, beat
    for before, after in zip(p.beats, p.beats[1:], strict=False):
        assert before.narration_at + before.narration_seconds < after.narration_at


def test_a_narration_longer_than_its_footage_holds_the_beats_last_frame():
    p = plan(marks(0.0, 5.0), 10.0, [], {KEYS[0]: 9.0, KEYS[1]: 1.0})
    first, second = p.beats
    assert first.freeze == pytest.approx(timeline.beat_minimum(9.0) - 5.0, abs=1 / FPS)
    assert first.freeze * FPS == pytest.approx(round(first.freeze * FPS))
    assert second.freeze == 0.0
    assert p.segments[0] == (first.segments[-1], first.freeze)


def test_a_delay_holds_the_narration_back_and_counts_toward_the_beat_being_long_enough():
    p = plan(marks(0.0, 20.0), 30.0, [], {KEYS[0]: 12.0, KEYS[1]: 3.0}, {KEYS[0]: 4.0})
    first, second = p.beats
    assert first.narration_at == pytest.approx(NARRATION_LEAD + 4.0)
    assert second.narration_at == pytest.approx(first.out_end + NARRATION_LEAD)
    assert first.freeze == 0.0
    late = plan(marks(0.0, 15.0), 30.0, [], {KEYS[0]: 12.0}, {KEYS[0]: 4.0})
    assert late.beats[0].freeze == pytest.approx(
        timeline.beat_minimum(12.0, 4.0) - 15.0, abs=1 / FPS
    )
    assert late.beats[0].narration_at + 12.0 + NARRATION_TAIL <= late.beats[0].out_end + 1e-9


def test_a_beat_with_a_wait_in_the_middle_places_its_label_and_its_freeze_where_they_belong():
    wait = Wait(5.0, 20.0, 2)
    p = plan(marks(0.0, 30.0), 40.0, [wait], {KEYS[0]: 20.01})
    beat = p.beats[0]
    assert [(s.start, s.end) for s in beat.segments] == [(0.0, 5.0), (5.0, 20.0), (20.0, 30.0)]
    [label] = p.labels
    assert label.start == pytest.approx(beat.out_start + (wait.start - beat.start))
    assert label.end == pytest.approx(label.start + SPED_TO)
    assert [freeze for _, freeze in p.segments[:3]] == [0.0, 0.0, beat.freeze]
    assert beat.freeze * FPS == pytest.approx(round(beat.freeze * FPS))
    assert beat.freeze >= timeline.beat_minimum(20.01) - (5.0 + SPED_TO + 10.0)


def test_times_are_kept_on_the_frame_grid():
    p = plan(marks(0.013, 7.71), 15.049, [Wait(8.004, 14.2, 3)], {})
    for segment, _ in p.segments:
        for t in (segment.start, segment.end):
            assert t * FPS == pytest.approx(round(t * FPS))


def test_a_sped_up_wait_that_crosses_a_beat_boundary_is_refused():
    with pytest.raises(ValueError, match="crosses a beat boundary"):
        plan(marks(0.0, 10.0), 30.0, [Wait(5.0, 20.0)], {})


def test_a_capture_without_beats_or_with_an_empty_beat_is_refused():
    with pytest.raises(ValueError, match="no beats"):
        plan([], 10.0, [], {})
    with pytest.raises(ValueError, match="no footage"):
        plan(marks(0.0, 5.0, 5.0), 10.0, [], {})


# ---- labels -------------------------------------------------------------------------------


def test_each_speed_up_is_labelled_for_exactly_its_sped_up_stretch_and_long_enough_to_read():
    waits = [Wait(12.0, 75.0, 2, "live"), Wait(80.0, 86.0, 4, "live"), Wait(90.0, 92.0, 1)]
    p = plan(marks(0.0, 10.0, 78.0), 100.0, waits, {})
    assert len(p.labels) == 2
    sped_up = [(s, f) for b in p.beats for s in b.segments if s.wait for f in [s.factor]]
    for label, (segment, _) in zip(p.labels, sped_up, strict=True):
        assert label.end - label.start == pytest.approx(segment.output)
        assert label.end - label.start >= LABEL_MIN
        beat = next(b for b in p.beats if segment in b.segments)
        assert beat.out_start <= label.start < label.end <= beat.out_end
    assert p.labels[0].text == "Live SerpApi: 2 searches took 63 s · shown 21x faster"
    assert p.labels[1].text == "Live SerpApi: 4 searches took 6 s · shown 2.0x faster"


def test_a_label_names_replay_as_replay():
    text = timeline.label_text(Wait(0.0, 9.0, 1, "replay"), 3.0)
    assert text == "Replayed SerpApi responses: 1 search took 9 s · shown 3.0x faster"
    assert "Live" not in text


def test_a_speed_up_too_short_to_read_its_label_is_refused():
    with pytest.raises(ValueError, match="to read its label"):
        plan(marks(0.0), 60.0, [Wait(1.0, 50.0)], {}, sped_to=LABEL_MIN - 0.5)


# ---- the filter graphs --------------------------------------------------------------------


def test_the_video_graph_cuts_each_segment_speeds_up_waits_freezes_and_overlays_labels():
    p = plan(marks(0.0, 10.0), 90.0, [Wait(20.0, 80.0, 2)], {KEYS[0]: 20.0})
    graph = timeline.video_filter(p)
    assert graph.startswith(f"[0:v]fps={FPS},split=4")
    cuts = [
        (float(a), float(b)) for a, b in re.findall(r"trim=start=([\d.]+):end=([\d.]+)", graph)
    ]
    assert cuts == [(s.start, s.end) for s, _ in p.segments]
    assert "setpts=(PTS-STARTPTS)/20," in graph
    [label] = p.labels
    [(a, b)] = re.findall(r"between\(t,([\d.]+),([\d.]+)\)", graph)
    assert (float(a), float(b)) == pytest.approx((label.start, label.end), abs=1e-3)
    assert "[0:v]" in graph and "[1:v]overlay" in graph
    assert graph.endswith("format=yuv420p[vout]")


def test_the_freeze_is_only_on_the_beats_last_segment_and_a_sped_one_is_cut_to_the_plans_frames():
    p = plan(marks(0.0, 30.0), 40.0, [Wait(5.0, 20.0, 2)], {KEYS[0]: 20.01})
    freeze = p.beats[0].freeze
    chains = {
        c.rsplit("[", 1)[1].rstrip("]"): c
        for c in timeline.video_filter(p).split(";")
        if re.search(r"\[v\d+\]$", c)
    }
    padded = [name for name, chain in chains.items() if f"stop_duration={freeze:.4f}" in chain]
    assert padded == ["v2"]
    assert f"trim=end_frame={round(SPED_TO * FPS)}" in chains["v1"]
    assert "trim=end_frame" not in chains["v0"] + chains["v2"]


def test_the_audio_graph_places_each_narration_at_its_beat():
    p = plan(marks(0.0, 10.0, 20.0), 30.0, [], {KEYS[0]: 4.0, KEYS[2]: 5.0})
    graph = timeline.audio_filter(p, first_input=3)
    delays = [int(ms) for ms in re.findall(r"adelay=delays=(\d+)", graph)]
    assert delays == [round(p.beats[0].narration_at * 1000), round(p.beats[2].narration_at * 1000)]
    assert "[3:a]" in graph and "[4:a]" in graph and "[5:a]" not in graph
    assert f"apad=whole_dur={p.duration:g}" in graph
    assert graph.count(":all=1") == 2 and "normalize=0" in graph


# ---- the beat script ----------------------------------------------------------------------


def test_every_beat_is_named_once_and_says_something():
    assert len(set(KEYS)) == len(KEYS)
    for provider in PROVIDERS:
        for part in PARTS:
            for beat in beats(provider, part):
                assert beat.narration.strip(), beat.key
                assert beat.caption or beat.key == "end", beat.key
                assert beat.delay >= 0, beat.key


def test_the_cutaway_is_spliced_in_after_a_beat_of_the_film_and_says_it_is_a_replay():
    assert CUTAWAY_AFTER in KEYS
    assert not {b.key for b in CUTAWAY} & set(KEYS)
    for beat in beats("replay", "cutaway"):
        assert "replay" in (beat.caption + beat.narration).lower(), beat.key
        assert ("#provider", "Replay") in beat.expects
    with pytest.raises(ValueError, match="part"):
        beats("live", "trailer")


def test_a_replay_take_never_claims_to_be_live():
    for beat in beats("replay"):
        assert "live serpapi" not in (beat.caption + beat.narration).lower(), beat.key
        assert not any("Live SerpApi" in text for _, text in beat.expects), beat.key
    assert [b.key for b in beats("replay")] == KEYS
    with pytest.raises(ValueError):
        beats("fake")


def test_what_the_beats_check_on_the_page_is_on_the_page():
    """Each selector's id or class exists in the page's markup or script, so a check can only
    fail on what the page shows, not on a selector that names nothing."""
    page = (ENTRY / "web" / "index.html").read_text("utf-8")
    script = (ENTRY / "web" / "app.js").read_text("utf-8")
    for beat in (*BEATS, *CUTAWAY):
        for provider in PROVIDERS:
            for selector, _ in beat.resolved(provider).expects:
                kind, name = re.match(r"([#.])([\w-]+)", selector).groups()
                if kind == ".":
                    known = f"class: '{name}'" in script or f'class="{name}"' in page
                else:
                    known = (
                        f'id="{name}"' in page
                        or f"id: '{name}'" in script
                        or name.startswith("draft-")
                        and "id: `draft-${id}`" in script
                    )
                assert known, (beat.key, selector)


def test_a_failed_check_counts_only_while_a_beat_still_claims_it():
    logged = [
        {"beat": "a", "selector": "#trace", "text": "decisive", "ok": False},
        {"beat": "a", "selector": "#trace", "text": "look-alike", "ok": True},
        {"beat": "b", "selector": "#log", "text": "refused", "ok": False},
    ]
    rewritten = (
        Beat("a", "cap", "words", (("#trace", "look-alike"), ("#trace", "none found"))),
        Beat("b", "cap", "words", (("#log", "refused"),)),
    )
    failed, unchecked = judge(logged, rewritten)
    assert failed == [{"beat": "b", "selector": "#log", "text": "refused", "ok": False}]
    assert unchecked == [{"beat": "a", "selector": "#trace", "text": "none found"}]
    assert judge(logged[1:2], rewritten[:1]) == ([], unchecked)


def test_the_end_card_names_the_repository_and_the_track():
    assert REPO_URL == "https://github.com/guptachetan1995/offer-checkpost"
    assert TRACK == "Knowledge & Public Interest"
