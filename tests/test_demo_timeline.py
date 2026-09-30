"""The demo recorder's pure parts, offline: the timeline that maps the capture's clock to the
video's (speed-ups, freezes, narration placement, label windows, the total), the filter graphs
it hands ffmpeg, the beat script, and the recorder's pacing against a stand-in page on a
virtual clock. The recorder itself drives a browser, the app and ffmpeg, and never runs in the
tests."""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

ENTRY = Path(__file__).resolve().parents[1]
# demo/ is the recorder's, beside the package rather than in it.
sys.path.insert(0, str(ENTRY))

from demo import record_demo, timeline  # noqa: E402
from demo.beats import (  # noqa: E402
    BEATS,
    CUTAWAY,
    CUTAWAY_AFTER,
    DIFFERENCES,
    MAX_CAPTION,
    OUTCOMES,
    PARTS,
    PROVIDERS,
    RECORDED_A,
    REPO_URL,
    SAMPLE_B,
    TRACK,
    Beat,
    beats,
    judge,
)
from demo.timeline import (  # noqa: E402
    CLIP_GAP,
    FPS,
    LABEL_MIN,
    NARRATION_LEAD,
    NARRATION_TAIL,
    SPED_TO,
    Wait,
    output_elapsed,
    place,
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
    narration = {KEYS[0]: [5.0], KEYS[1]: [20.0], KEYS[2]: [40.0]}
    p = plan(marks(0.0, 10.0, 100.0), 120.0, [live_search], narration)
    beat_b = 90.0 - 63.0 + SPED_TO
    freeze_c = NARRATION_LEAD + 40.0 + NARRATION_TAIL - 20.0
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
        {KEYS[0]: [3.0], KEYS[1]: [4.0, 5.0], KEYS[2]: [2.0]},
        {KEYS[0]: [12.0], KEYS[1]: [10.0, 10.0, 10.0], KEYS[2]: [25.0]},
        {KEYS[0]: [9.6], KEYS[1]: [], KEYS[2]: [9.0, 9.99]},
    ],
)
def test_narration_starts_in_its_beat_and_ends_before_the_next_one(narration):
    p = plan(marks(0.0, 10.0, 22.0), 40.0, [Wait(12.0, 20.0, 1)], narration)
    for beat in p.beats:
        for clip in beat.clips:
            assert clip.at >= beat.out_start + NARRATION_LEAD - 1e-9
        if beat.clips:
            assert beat.clips[-1].end + NARRATION_TAIL <= beat.out_end + 1e-9, beat
        for before, after in zip(beat.clips, beat.clips[1:], strict=False):
            assert after.at >= before.end + CLIP_GAP - 1e-9
    for before, after in zip(p.beats, p.beats[1:], strict=False):
        if before.clips and after.clips:
            assert before.clips[-1].end < after.clips[0].at


def test_a_narration_longer_than_its_footage_holds_the_beats_last_frame():
    p = plan(marks(0.0, 5.0), 10.0, [], {KEYS[0]: [4.0, 5.0], KEYS[1]: [1.0]})
    first, second = p.beats
    spoken = NARRATION_LEAD + 4.0 + CLIP_GAP + 5.0
    assert first.freeze == pytest.approx(spoken + NARRATION_TAIL - 5.0, abs=1 / FPS)
    assert first.freeze * FPS == pytest.approx(round(first.freeze * FPS))
    assert second.freeze == 0.0
    assert p.segments[0] == (first.segments[-1], first.freeze)


def test_a_sentence_starts_when_the_page_reaches_what_it_says():
    p = plan(marks(0.0, 30.0), 40.0, [], {KEYS[0]: [3.0, 3.0, 3.0]}, {KEYS[0]: [1.0, 8.0, 20.0]})
    assert [round(c.at, 3) for c in p.beats[0].clips] == [1.0, 8.0, 20.0]
    assert p.beats[0].late == 0.0
    assert p.beats[0].freeze == 0.0


def test_a_cue_after_a_speed_up_lands_where_the_video_reaches_it():
    wait = Wait(5.0, 65.0, 2)
    p = plan(marks(0.0, 80.0), 90.0, [wait], {KEYS[0]: [2.0, 2.0]}, {KEYS[0]: [1.0, 70.0]})
    assert p.beats[0].clips[1].at == pytest.approx(5.0 + SPED_TO + 5.0)


def test_a_sentence_still_being_spoken_holds_the_next_back_and_says_by_how_much():
    p = plan(marks(0.0, 30.0), 40.0, [], {KEYS[0]: [6.0, 2.0]}, {KEYS[0]: [1.0, 2.0]})
    first, second = p.beats[0].clips
    assert second.at == pytest.approx(first.end + CLIP_GAP)
    assert p.beats[0].late == pytest.approx(second.at - 2.0)


def test_a_sentence_with_no_cue_follows_the_one_before_and_cues_stay_inside_their_beat():
    p = plan(marks(0.0, 10.0), 30.0, [], {KEYS[0]: [2.0, 2.0], KEYS[1]: [1.0]}, {KEYS[0]: [1.0]})
    first, second = p.beats[0].clips
    assert second.at == pytest.approx(first.end + CLIP_GAP)
    # A cue logged before its beat started, or after it ended, can't put a sentence outside it.
    out = plan(marks(0.0, 10.0), 30.0, [], {KEYS[1]: [1.0]}, {KEYS[1]: [-5.0]})
    assert out.beats[1].clips[0].at == pytest.approx(10.0 + NARRATION_LEAD)
    late = plan(marks(0.0, 10.0), 30.0, [], {KEYS[0]: [1.0]}, {KEYS[0]: [99.0]})
    assert late.beats[0].clips[0].at >= 10.0 - 1e-9


def test_place_starts_each_sentence_at_its_want_or_right_after_the_one_before():
    clips = place([3.0, 3.0, 3.0], [0.0, 10.0], origin=100.0)
    assert [c.at for c in clips] == [
        100.0 + NARRATION_LEAD,
        100.0 + NARRATION_LEAD + 3.0 + CLIP_GAP,
        100.0 + NARRATION_LEAD + 6.0 + 2 * CLIP_GAP,
    ]
    wanted = place([3.0, 3.0], [50.0, 60.0])
    assert [c.at for c in wanted] == [50.0, 60.0]


def test_a_beat_with_a_wait_in_the_middle_places_its_label_and_its_freeze_where_they_belong():
    wait = Wait(5.0, 20.0, 2)
    p = plan(marks(0.0, 30.0), 40.0, [wait], {KEYS[0]: [20.01]})
    beat = p.beats[0]
    assert [(s.start, s.end) for s in beat.segments] == [(0.0, 5.0), (5.0, 20.0), (20.0, 30.0)]
    [label] = p.labels
    assert label.start == pytest.approx(beat.out_start + (wait.start - beat.start))
    assert label.end == pytest.approx(label.start + SPED_TO)
    assert [freeze for _, freeze in p.segments[:3]] == [0.0, 0.0, beat.freeze]
    assert beat.freeze * FPS == pytest.approx(round(beat.freeze * FPS))
    assert beat.freeze >= NARRATION_LEAD + 20.01 + NARRATION_TAIL - (5.0 + SPED_TO + 10.0)


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


# ---- still, silent stretches are cut, and a beat can end early -------------------------------


def test_a_cut_leaves_out_its_stretch_and_the_pieces_either_side_play_on():
    cut = timeline.Cut(10.0, 14.0)
    pieces = segments(0.0, 30.0, [], cuts=[cut])
    assert [(p.start, p.end, p.factor) for p in pieces] == [(0.0, 10.0, 1.0), (14.0, 30.0, 1.0)]
    assert output_elapsed(0.0, 30.0, [], cuts=[cut]) == pytest.approx(26.0)
    # A moment after a cut lands where the video reaches it, and one inside it, at its start.
    assert output_elapsed(0.0, 20.0, [], cuts=[cut]) == pytest.approx(16.0)
    assert output_elapsed(0.0, 12.0, [], cuts=[cut]) == pytest.approx(10.0)


def test_a_cut_never_overlaps_a_wait_that_is_sped_up():
    with pytest.raises(ValueError, match="overlaps a sped-up wait"):
        segments(0.0, 100.0, [Wait(10.0, 70.0, 2)], cuts=[timeline.Cut(60.0, 80.0)])
    short = Wait(10.0, 12.0, 1)
    assert segments(0.0, 20.0, [short], cuts=[timeline.Cut(11.0, 13.0)])[-1].end == 20.0


def spoken_beat(sentences=(3.0, 3.0), cues=(1.0, 20.0), waits=(), **more):
    return plan(
        marks(0.0, 40.0),
        50.0,
        list(waits),
        {KEYS[0]: list(sentences)},
        {KEYS[0]: list(cues)},
        **more,
    )


def test_a_still_stretch_between_two_sentences_is_cut_down_to_the_margins():
    laid = spoken_beat()
    first = laid.beats[0].clips[0]
    cuts = timeline.idle_cuts(laid, [(2.0, 22.0)])
    [cut] = cuts
    assert cut.start == pytest.approx(
        round((first.end + timeline.KEEP_AFTER) * FPS) / FPS, abs=1 / FPS
    )
    assert cut.end == pytest.approx(20.0 - timeline.KEEP_BEFORE, abs=1 / FPS)
    cut_plan = spoken_beat(stills=[(2.0, 22.0)])
    assert cut_plan.cuts == tuple(cuts)
    assert cut_plan.duration == pytest.approx(laid.duration - cut.seconds, abs=1 / FPS)
    # The second sentence still starts when the page reaches what it says, now sooner.
    assert cut_plan.beats[0].clips[1].at == pytest.approx(20.0 - cut.seconds, abs=1 / FPS)
    assert cut_plan.beats[0].clips[0].at == laid.beats[0].clips[0].at


def test_nothing_is_cut_while_a_sentence_is_spoken_or_when_the_screen_moves():
    laid = spoken_beat()
    first, second = laid.beats[0].clips
    assert timeline.idle_cuts(laid, [(first.at, first.end)]) == []
    assert timeline.idle_cuts(laid, [(second.at - 0.3, second.end + 0.3)]) == []
    # A still shorter than the minimum is not worth a cut.
    assert timeline.idle_cuts(laid, [(10.0, 10.0 + timeline.CUT_MINIMUM - 0.1)]) == []
    # The stretch before the first sentence and after the last are silent too.
    head = timeline.idle_cuts(spoken_beat(cues=(9.0, 20.0)), [(0.0, 8.0)])
    assert head and head[0].end <= 9.0 - timeline.KEEP_BEFORE + 1 / FPS
    tail = timeline.idle_cuts(spoken_beat(), [(24.0, 39.0)])
    last = laid.beats[0].clips[1]
    assert tail and tail[0].start >= last.end + timeline.KEEP_AFTER - 1 / FPS


def test_a_wait_is_never_cut_and_a_held_last_frame_is_never_a_cut():
    wait = Wait(4.0, 64.0, 2)
    laid = plan(marks(0.0, 100.0), 110.0, [wait], {KEYS[0]: [2.0]}, {KEYS[0]: [1.0]})
    cuts = timeline.idle_cuts(laid, [(0.0, 100.0)])
    assert cuts and all(c.end <= wait.start or c.start >= wait.end for c in cuts)
    held = plan(marks(0.0, 10.0), 20.0, [], {KEYS[0]: [20.0]}, {KEYS[0]: [0.5]})
    assert held.beats[0].freeze > 0
    assert timeline.idle_cuts(held, [(0.0, 10.0)]) == []


def test_a_beat_can_end_early_and_the_footage_after_it_is_left_out():
    p = plan(
        marks(0.0, 30.0),
        40.0,
        [],
        {KEYS[0]: [3.0, 3.0]},
        {KEYS[0]: [1.0, 12.0]},
        trims={KEYS[0]: 16.0},
    )
    first, second = p.beats
    assert (first.start, first.end) == (0.0, 16.0)
    assert second.start == 30.0
    assert p.duration == pytest.approx(16.0 + 10.0, abs=1 / FPS)
    # No trim for a beat, or one past its end, changes nothing.
    assert plan(marks(0.0, 30.0), 40.0, [], {}, trims={KEYS[0]: 99.0}).duration == pytest.approx(
        40.0
    )
    with pytest.raises(ValueError, match="crosses a beat boundary"):
        plan(marks(0.0, 30.0), 40.0, [Wait(5.0, 50.0, 2)], {}, trims={KEYS[0]: 20.0})


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
    assert (
        p.labels[0].text
        == f"Live SerpApi: 2 searches took 63 s · shown {63 / SPED_TO:.0f}x faster"
    )
    assert (
        p.labels[1].text == f"Live SerpApi: 4 searches took 6 s · shown {6 / SPED_TO:.1f}x faster"
    )


def test_a_label_names_replay_as_replay():
    text = timeline.label_text(Wait(0.0, 9.0, 1, "replay"), 3.0)
    assert text == "Replayed SerpApi responses: 1 search took 9 s · shown 3.0x faster"
    assert "Live" not in text


def test_a_speed_up_too_short_to_read_its_label_is_refused():
    with pytest.raises(ValueError, match="to read its label"):
        plan(marks(0.0), 60.0, [Wait(1.0, 50.0)], {}, sped_to=LABEL_MIN - 0.5)


# ---- the filter graphs --------------------------------------------------------------------


def test_the_video_graph_cuts_each_segment_speeds_up_waits_freezes_and_overlays_labels():
    p = plan(marks(0.0, 10.0), 90.0, [Wait(20.0, 80.0, 2)], {KEYS[0]: [20.0]})
    graph = timeline.video_filter(p)
    assert graph.startswith(f"[0:v]fps={FPS},split=4")
    cuts = [
        (float(a), float(b)) for a, b in re.findall(r"trim=start=([\d.]+):end=([\d.]+)", graph)
    ]
    assert cuts == [(s.start, s.end) for s, _ in p.segments]
    assert f"setpts=(PTS-STARTPTS)/{60 / SPED_TO:g}," in graph
    [label] = p.labels
    [(a, b)] = re.findall(r"between\(t,([\d.]+),([\d.]+)\)", graph)
    assert (float(a), float(b)) == pytest.approx((label.start, label.end), abs=1e-3)
    assert "[0:v]" in graph and "[1:v]overlay" in graph
    assert graph.endswith("format=yuv420p[vout]")


def test_the_freeze_is_only_on_the_beats_last_segment_and_a_sped_one_is_cut_to_the_plans_frames():
    p = plan(marks(0.0, 30.0), 40.0, [Wait(5.0, 20.0, 2)], {KEYS[0]: [20.01]})
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


def test_the_audio_graph_places_each_sentence_where_the_plan_puts_it():
    p = plan(
        marks(0.0, 10.0, 20.0),
        30.0,
        [],
        {KEYS[0]: [4.0, 2.0], KEYS[2]: [5.0]},
        {KEYS[0]: [1.0, 6.0], KEYS[2]: [23.0]},
    )
    graph = timeline.audio_filter(p, first_input=3)
    delays = [int(ms) for ms in re.findall(r"adelay=delays=(\d+)", graph)]
    assert delays == [1000, 6000, 23000]
    assert "[3:a]" in graph and "[4:a]" in graph and "[5:a]" in graph and "[6:a]" not in graph
    assert f"apad=whole_dur={p.duration:g}" in graph
    assert graph.count(":all=1") == 3 and "normalize=0" in graph


# ---- the beat script ----------------------------------------------------------------------


def combos():
    for provider in PROVIDERS:
        for outcome in OUTCOMES:
            for sample_b in SAMPLE_B:
                if provider == "replay" and (outcome, sample_b) != ("decisive", "clean"):
                    continue
                yield provider, outcome, sample_b


def every_beat():
    for provider, outcome, sample_b in combos():
        for part in PARTS:
            for beat in beats(provider, part, outcome, sample_b):
                yield provider, f"{outcome}/{sample_b}", beat


def test_every_beat_is_named_once_and_says_something():
    assert len(set(KEYS)) == len(KEYS)
    for _, _, beat in every_beat():
        assert beat.narration and all(sentence.strip() for sentence in beat.narration), beat.key
        assert beat.captions or beat.key == "end", beat.key
        for caption in beat.captions:
            assert 0 < len(caption) <= MAX_CAPTION, (beat.key, caption)
        assert len(set(beat.captions)) == len(beat.captions), beat.key


def test_a_beat_says_the_same_number_of_sentences_and_captions_whatever_sample_a_found():
    """The recorder cues sentence ``i`` and shows caption ``i`` when the page reaches it, so
    every outcome's variant of a beat has the same slots to fill."""
    for base in (*BEATS, *CUTAWAY):
        for provider in PROVIDERS:
            shapes = {
                (len(v.narration), len(v.captions))
                for v in (base.resolved(p, o, b) for p, o, b in combos() if p == provider)
            }
            assert len(shapes) == 1, (base.key, provider)


def test_an_outcome_changes_only_what_sample_a_s_search_found():
    by = {o: {b.key: b for b in beats("live", outcome=o)} for o in OUTCOMES}

    def changed(outcome):
        return {k for k in by["decisive"] if by["decisive"][k] != by[outcome][k]}

    assert changed("retried") == {"a-investigate", "a-forwarded"}
    assert changed("unverified") == {"a-investigate", "a-publish", "a-forwarded"}
    assert changed("unverified_retried") == changed("unverified")
    # The retried film is still a decisive one from the officer's side: same publish beat.
    assert by["retried"]["a-publish"] == by["decisive"]["a-publish"]
    # An unretried and a retried miss share what the case did after the notice search.
    for key in ("a-publish", "a-forwarded"):
        assert by["unverified_retried"][key] == by["unverified"][key], key


def test_each_outcome_of_sample_a_says_what_the_screen_shows():
    by = {o: {b.key: b for b in beats("live", outcome=o)}["a-investigate"] for o in OUTCOMES}
    decisive, retried = by["decisive"], by["retried"]
    missed, both = by["unverified"], by["unverified_retried"]
    assert "never asks for any payment" in decisive.text
    assert "retr" not in decisive.text and "retr" not in missed.text
    assert "unverified" not in decisive.text.lower()
    assert ("#verdict .band-title", "High risk") in decisive.expects
    assert (".budget-line", "4 not spent") in decisive.expects
    assert ("#evidence", "never ask for any payment") in decisive.expects
    # The retry is narrated as what it was, from the first search's own words.
    for beat in (retried, both):
        assert "no page from hcltech.com" in beat.text
        assert "retries with the notice's usual titles" in beat.text
        assert ("#trace", "Inconclusive") in beat.expects
        assert ("#trace", "R3, inconclusive notice search") in beat.expects
    assert "three searches, three never spent" in retried.text
    assert (".budget-line", "3 not spent") in retried.expects
    assert ("#verdict .band-title", "High risk") in retried.expects
    assert not any("never ask" in text for _, text in retried.expects)
    assert "still no notice to quote" in both.text
    assert "finds it" not in both.text
    # A miss never claims the notice, and is checked to have spent everything.
    for beat in (missed, both):
        assert "finds no notice" in missed.text or beat is both
        assert (".budget-line", "6 searches spent of 6") in beat.expects
        assert ("#verdict .band-title", "Unverified") in beat.expects
        assert "high risk" not in beat.text.lower()
        assert not any(selector == "#evidence" for selector, _ in beat.expects)
    assert "finds no notice to quote" in missed.text
    assert ("#strip-count", "2 calls") in beats("live")[KEYS.index("a-forwarded")].expects
    forwarded = {o: {b.key: b for b in beats("live", outcome=o)}["a-forwarded"] for o in OUTCOMES}
    assert ("#strip-count", "3 calls") in forwarded["retried"].expects
    assert ("#strip-count", "6 calls") in forwarded["unverified"].expects
    assert ("#strip-count", "6 calls") in forwarded["unverified_retried"].expects
    # The recorded responses always hold the notice at the first search.
    assert beats("replay")[3].narration == beats("live")[3].narration
    with pytest.raises(ValueError, match="always find"):
        beats("replay", outcome="unverified")
    with pytest.raises(ValueError, match="always find"):
        beats("replay", outcome="retried")
    with pytest.raises(ValueError, match="outcome"):
        beats("live", outcome="maybe")


def test_the_film_opens_on_the_app_says_the_value_first_and_names_the_three_differences():
    assert KEYS[0] == "intro"
    intro = beats("live")[0]
    assert len(DIFFERENCES) == 3
    assert "before anyone pays" in intro.narration[0]
    said = " ".join(intro.narration).lower()
    for word in ("zero searches", "fraud notice", "only a person publishes", "provably cannot"):
        assert word in said, word
    # The three are named, as captions over the real screens, inside about 15 s at the voice's
    # measured pace (14.5 s of speech for these sentences, 3.2 to 4.4 s each).
    assert len(said.split()) / 2.85 < 15.5
    assert intro.captions[1:] == tuple(f"{i} · {t}" for i, t in enumerate(DIFFERENCES, 1))
    assert "live serpapi" in intro.captions[0].lower() and "lag" in intro.captions[0]
    assert KEYS.index("a-investigate") < KEYS.index("a-publish") < KEYS.index("a-forwarded")
    assert KEYS.index("a-forwarded") < KEYS.index("c-investigate")
    assert KEYS.index(CUTAWAY_AFTER) < KEYS.index("a-forwarded")
    forwarded = beats("live")[KEYS.index("a-forwarded")]
    assert "zero searches" in forwarded.text
    assert (".budget-line", "0 searches spent") in forwarded.expects
    assert ("#trace", "reused, 0 searches") in forwarded.expects


def test_the_sentences_a_beat_leaves_out_are_the_last_ones_and_nothing_kept_checks_them():
    left_out = {b.key: b.dropped for b in BEATS if b.dropped}
    assert left_out == {"c-remaining": 1, "a-report": 1}
    for _, _, beat in every_beat():
        assert 0 <= beat.dropped < len(beat.narration), beat.key
    reply = next(b for b in BEATS if b.key == "c-remaining")
    assert "drafted reply" in reply.narration[-1]
    assert not any(selector == "#draft-reply" for selector, _ in reply.expects)
    report = next(b for b in BEATS if b.key == "a-report")
    assert "Offer Board" in report.narration[-1]


def test_the_cutaway_is_spliced_in_after_a_beat_of_the_film_and_says_it_is_a_replay():
    assert CUTAWAY_AFTER in KEYS
    assert not {b.key for b in CUTAWAY} & set(KEYS)
    [beat] = beats("replay", "cutaway")
    assert "replay" in (beat.captions[0] + beat.narration[0]).lower()
    assert RECORDED_A in beat.captions[0]
    assert ("#provider", "Replay") in beat.expects
    with pytest.raises(ValueError, match="part"):
        beats("live", "trailer")


def test_a_replay_take_never_claims_to_be_live():
    for beat in beats("replay"):
        assert "live serpapi" not in (" ".join(beat.captions) + beat.text).lower(), beat.key
        assert "live searches" not in beat.text.lower(), beat.key
        assert not any("Live SerpApi" in text for _, text in beat.expects), beat.key
    assert [b.key for b in beats("replay")] == KEYS
    with pytest.raises(ValueError):
        beats("fake")


def test_what_the_beats_check_on_the_page_is_on_the_page():
    """Each selector's id or class exists in the page's markup or script, so a check can only
    fail on what the page shows, not on a selector that names nothing."""
    page = (ENTRY / "web" / "index.html").read_text("utf-8")
    script = (ENTRY / "web" / "app.js").read_text("utf-8")
    for _, _, beat in every_beat():
        for selector, _ in beat.expects:
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
        Beat("a", ("cap",), ("words",), (("#trace", "look-alike"), ("#trace", "none found"))),
        Beat("b", ("cap",), ("words",), (("#log", "refused"),)),
    )
    failed, unchecked = judge(logged, rewritten)
    assert failed == [{"beat": "b", "selector": "#log", "text": "refused", "ok": False}]
    assert unchecked == [{"beat": "a", "selector": "#trace", "text": "none found"}]
    assert judge(logged[1:2], rewritten[:1]) == ([], unchecked)


def test_the_end_card_names_the_repository_and_the_track():
    assert REPO_URL == "https://github.com/guptachetan1995/offer-checkpost"
    assert TRACK == "Knowledge & Public Interest"


# ---- the recorder's pacing, on a stand-in page and a virtual clock ------------------------


class Stage:
    """A page whose only clock is the pauses asked of it, and which logs what the overlay was
    told to say and when."""

    def __init__(self):
        self.clock = 0.0
        self.said = []

    def on(self, *_):
        pass

    def wait_for_timeout(self, ms):
        self.clock += ms / 1000

    def wait_for_selector(self, *_):
        pass

    def evaluate(self, script, args=()):
        if ".caption(" in script:
            self.said.append((round(self.clock, 3), args[0]))


def take_of(monkeypatch, *sentences, captions=("the caption",), waits=()):
    stage = Stage()
    monkeypatch.setattr(record_demo.time, "monotonic", lambda: stage.clock)
    words = tuple(f"sentence {i}" for i in range(len(sentences)))
    beat = Beat("scene", captions, words)
    clips = [
        {"text": w, "seconds": s, "path": None} for w, s in zip(words, sentences, strict=True)
    ]
    take = record_demo.Take(
        stage, None, None, {("decisive", "clean"): ((beat,), {"scene": clips})}
    )
    take.waits.extend(waits)
    take.beat("scene")
    return take, stage


def test_a_sentence_is_cued_after_the_lead_and_the_next_after_it_and_the_gap(monkeypatch):
    take, _ = take_of(monkeypatch, 5.0, 3.0)
    take.say(0)
    take.say(1)
    first, second = take.cues["scene"]
    assert first == pytest.approx(NARRATION_LEAD)
    assert second == pytest.approx(NARRATION_LEAD + 5.0 + CLIP_GAP)


def test_a_sentence_the_page_reaches_late_is_cued_when_it_does_not_before(monkeypatch):
    take, stage = take_of(monkeypatch, 2.0, 2.0)
    take.say(0)
    take.pause(9.0)
    take.say(1)
    assert take.cues["scene"][1] == pytest.approx(NARRATION_LEAD + 9.0)
    assert stage.clock == pytest.approx(NARRATION_LEAD + 9.0)


def test_a_caption_goes_up_when_asked_for_and_not_with_its_sentence(monkeypatch):
    take, stage = take_of(monkeypatch, 4.0, 4.0, captions=("first", "second"))
    assert stage.said == [(0.0, "")]
    take.say(0)
    assert stage.said == [(0.0, "")]
    take.pause(1.5)
    take.caption(0)
    at = NARRATION_LEAD + 1.5
    assert stage.said[-1] == (pytest.approx(at), "first")
    assert take.captions == [{"beat": "scene", "slot": 0, "t": pytest.approx(at), "text": "first"}]


def test_the_next_sentence_takes_the_caption_down_and_the_next_caption_replaces_it(monkeypatch):
    take, stage = take_of(monkeypatch, 2.0, 2.0, 2.0, captions=("first", "second"))
    take.say(0)
    take.caption(0)
    up = len(stage.said)
    take.say(1)
    assert stage.said[up:] == [(pytest.approx(NARRATION_LEAD + 2.0 + CLIP_GAP), "")]
    take.caption(1)
    assert stage.said[-1][1] == "second"
    take.say(2)
    assert stage.said[-1][1] == ""
    assert [c["text"] for c in take.captions] == ["first", "second"]


def test_a_caption_asked_of_a_scroll_goes_up_once_it_is_mostly_done_and_stays_for_the_dwell(
    monkeypatch,
):
    take, stage = take_of(monkeypatch, 1.0, captions=("finding",))
    take.say(0)
    start = stage.clock
    take.show("#trace", 1.5, caption=0)
    assert stage.said[-1] == (pytest.approx(start + record_demo.CAPTION_AFTER_SCROLL), "finding")
    assert stage.clock == pytest.approx(start + record_demo.SCROLL + 1.5)
    assert record_demo.CAPTION_AFTER_SCROLL < record_demo.SCROLL
    take.show_text("#trace", "x", 0.5)
    assert len(take.captions) == 1


def test_a_scroll_takes_the_caption_of_the_screen_it_leaves_down_before_it_moves(monkeypatch):
    take, stage = take_of(monkeypatch, 1.0, captions=("first",))
    take.say(0)
    for move in (
        lambda: take.show("#evidence"),
        lambda: take.center("#draft"),
        lambda: take.show_text("#trace", "text"),
        take.top,
    ):
        take.set_caption("up")
        started, up = stage.clock, len(stage.said)
        move()
        assert stage.said[up] == (pytest.approx(started), "")
    # Nothing is taken down that isn't up.
    before = len(stage.said)
    take.show("#evidence")
    assert len(stage.said) == before


def test_a_caption_asked_of_a_ring_goes_up_with_the_ring(monkeypatch):
    take, stage = take_of(monkeypatch, 1.0, captions=("finding",))
    take.say(0)
    start = stage.clock
    take.ring(".strip", 1.0, caption=0)
    assert stage.said[-1] == (pytest.approx(start), "finding")
    assert stage.clock == pytest.approx(start + 1.0)


def test_a_new_beat_starts_without_the_last_ones_caption(monkeypatch):
    take, stage = take_of(monkeypatch, 2.0)
    take.say(0)
    take.caption(0)
    assert stage.said[-1][1] == "the caption"
    take.beat("scene")
    assert stage.said[-1][1] == "" and take.showing == ""


def test_a_speed_up_counts_as_the_seconds_the_video_plays_it_in(monkeypatch):
    # The page waited 60 s on searches: SPED_TO s of video, so the next sentence is that much
    # nearer than the capture's clock says, rather than already due.
    take, stage = take_of(monkeypatch, 5.0, 3.0, waits=[Wait(1.0, 61.0, 2)])
    take.say(0)
    stage.clock = 61.0
    take.say(1)
    reached = 1.0 + SPED_TO
    assert take.cues["scene"][1] == pytest.approx(
        61.0 + (NARRATION_LEAD + 5.0 + CLIP_GAP - reached)
    )


def test_sentences_are_cued_in_order(monkeypatch):
    take, _ = take_of(monkeypatch, 1.0, 1.0)
    with pytest.raises(record_demo.Refused, match="out of order"):
        take.say(1)


def test_a_beat_is_held_until_its_last_sentence_and_the_tail_are_over(monkeypatch):
    take, stage = take_of(monkeypatch, 4.0, 6.0, 2.0)
    take.say(0)
    take.hold()
    third = NARRATION_LEAD + 4.0 + CLIP_GAP + 6.0 + CLIP_GAP
    assert take.cues["scene"][2] == pytest.approx(third)
    assert stage.clock == pytest.approx(third + 2.0 + NARRATION_TAIL)


def test_a_beat_already_past_its_narration_is_not_held_any_longer(monkeypatch):
    take, stage = take_of(monkeypatch, 1.0)
    take.say(0)
    take.pause(30.0)
    take.hold()
    assert stage.clock == pytest.approx(NARRATION_LEAD + 30.0)


def state_with(
    rule="fee_contradicts_employer", stale=False, superseded=False, stopped="decisive", searches=1
):
    signal = {"rule": rule, "stale": stale, "superseded": superseded}
    trace = [{"tool": "find_fraud_notice", "action": "ran", "note": "n"}] * searches
    trace.append({"tool": "find_fraud_notice", "action": "skipped", "note": ""})
    trace.append({"tool": "lookup_official_site", "action": "ran", "note": "official domain"})
    case = {
        "id": "case-1",
        "signals": [signal],
        "budget": {"stoppedBecause": stopped},
        "trace": trace,
    }

    class App:
        @staticmethod
        def get(path):
            if path == "/api/calls":
                return {"calls": [{}, {}]}
            assert path == "/api/state"
            return {"cases": [{"id": "case-0", "signals": [], "budget": {}}, case]}

    return App


def test_the_notice_was_found_only_when_its_rule_stands_and_the_case_stopped_decisive():
    assert record_demo.notice_found(state_with(), "case-1")
    assert not record_demo.notice_found(state_with(stopped="budget"), "case-1")
    assert not record_demo.notice_found(state_with(rule="lookalike_sender"), "case-1")
    assert not record_demo.notice_found(state_with(stale=True), "case-1")
    assert not record_demo.notice_found(state_with(superseded=True), "case-1")


def test_the_outcome_is_decided_from_the_notice_searches_the_case_ran_and_how_it_stopped():
    outcome = record_demo.sample_a_outcome
    assert outcome(state_with(), "case-1") == "decisive"
    assert outcome(state_with(searches=2), "case-1") == "retried"
    for finished in ("budget", "done"):
        assert outcome(state_with(stopped=finished), "case-1") == "unverified"
        both = outcome(state_with(stopped=finished, searches=2), "case-1")
        assert both == "unverified_retried"
    # A rule that was superseded by a later answer is not the notice having been found.
    assert outcome(state_with(superseded=True, stopped="budget", searches=2), "case-1") == (
        "unverified_retried"
    )
    assert (
        record_demo.notice_runs(state_with(searches=2), "case-1")
        == [{"tool": "find_fraud_notice", "action": "ran", "note": "n"}] * 2
    )


def test_a_search_that_failed_stops_the_take_instead_of_being_filmed_as_an_outcome():
    for failed in ("search_error", "quota"):
        for searches in (1, 2):
            with pytest.raises(record_demo.Refused, match=failed):
                record_demo.sample_a_outcome(
                    state_with(stopped=failed, searches=searches), "case-1"
                )


def test_the_notice_note_is_what_the_trace_shows_of_the_last_fraud_notice_search():
    def app(*notes):
        trace = [{"tool": "find_fraud_notice", "note": n} for n in notes]
        trace.append({"tool": "lookup_official_site", "note": "official domain hcltech.com"})

        class App:
            @staticmethod
            def get(path):
                return {"cases": [{"id": "case-1", "trace": trace}]}

        return App

    inconclusive = "inconclusive: the search returned no page from hcltech.com (no results at all)"
    assert record_demo.notice_note(app(inconclusive), "case-1") == (
        "the search returned no page from hcltech.com (no results at all)"
    )
    finding = "2 pages from hcltech.com came back, none is a recruitment-fraud notice"
    assert record_demo.notice_note(app(inconclusive, finding), "case-1") == finding


# ---- the honesty checks and the splice, which the render trusts ---------------------------


class Screen:
    """A page whose elements carry fixed text."""

    def __init__(self, texts):
        self.texts = texts
        self.clock = 0.0

    def on(self, *_):
        pass

    def evaluate(self, *_):
        pass

    def locator(self, selector):
        text = self.texts.get(selector)
        return type(
            "Element",
            (),
            {
                "first": property(lambda self: self),
                "count": lambda self: 0 if text is None else 1,
                "inner_text": lambda self: text,
            },
        )()


def checked(texts, expects):
    beat = Beat("scene", "caption", ("said",), expects)
    take = record_demo.Take(
        Screen(texts), None, None, {("decisive", "clean"): ((beat,), {"scene": []})}
    )
    take.check_expects("scene")
    return [(e["text"], e["ok"]) for e in take.expects]


def test_a_check_passes_only_when_the_page_carries_the_text_whatever_its_case():
    found = checked({"#trace": "Office FOUND on Maps"}, (("#trace", "office found"),))
    assert found == [("office found", True)]
    assert checked({"#trace": "Office found"}, (("#trace", "no listing"),)) == [
        ("no listing", False)
    ]


def test_a_check_fails_when_the_element_is_not_on_the_page_at_all():
    assert checked({}, (("#trace", "office found"),)) == [("office found", False)]


def test_only_a_film_whose_live_search_found_no_notice_gets_the_replay_cutaway():
    assert record_demo.needs_cutaway("unverified")
    assert record_demo.needs_cutaway("unverified_retried")
    assert not record_demo.needs_cutaway("decisive")
    assert not record_demo.needs_cutaway("retried")


def test_the_cutaway_is_captured_only_for_an_unverified_film(tmp_path, monkeypatch):
    taken = []
    monkeypatch.setattr(record_demo, "capture", lambda *args: taken.append(args))
    for outcome, captured in (
        ("decisive", 0),
        ("retried", 0),
        ("unverified", 1),
        ("unverified_retried", 1),
    ):
        taken.clear()
        (tmp_path / "events.json").write_text(f'{{"outcome": "{outcome}"}}', "utf-8")
        record_demo.cutaway_if_needed(tmp_path, 10)
        assert len(taken) == captured
    assert taken == [("replay", tmp_path / "cutaway", 10, "cutaway")]


def test_an_unverified_film_without_its_cutaway_is_not_rendered(tmp_path):
    (tmp_path / "events.json").write_text('{"outcome": "unverified"}', "utf-8")
    with pytest.raises(record_demo.Refused, match="cutaway"):
        record_demo.render(tmp_path, tmp_path / "out.mp4")


def test_a_beat_keeps_all_its_sentences_unless_it_drops_the_last_ones():
    whole = Beat("whole", "c", ("one", "two", "three"))
    cut = Beat("cut", "c", ("one", "two", "three"), dropped=1)
    cues = {"whole": [1.0, 2.0, 3.0], "cut": [5.0, 6.0, 7.0]}
    kept, trims = record_demo.dropped_sentences((whole, cut), cues)
    assert kept == {"whole": 3, "cut": 2}
    assert trims == {"cut": 7.0}


# ---- the script the recorder plays -------------------------------------------------------


def shown_slots(function):
    """Each beat's caption slots the function puts on screen, read from its source: the
    recorder drives a browser and never runs in the tests, so what it plays is checked against
    what the beats define."""
    parts = re.split(r'take\.beat\("([\w-]+)"\)', inspect.getsource(function))
    return {
        key: [
            int(direct or keyword)
            for direct, keyword in re.findall(r"take\.caption\((\d+)\)|, caption=(\d+)\)", body)
        ]
        for key, body in zip(parts[1::2], parts[2::2], strict=True)
    }


def test_the_recorder_puts_up_every_caption_slot_of_every_beat_once_and_no_other():
    played = {**shown_slots(record_demo.play), **shown_slots(record_demo.play_cutaway)}
    for _, outcome, beat in every_beat():
        slots = played[beat.key]
        assert sorted(set(slots)) == list(range(len(beat.captions))), (beat.key, outcome)
        # A slot in a branch of the flow that only one outcome takes may repeat; an unbranched
        # beat shows each slot exactly once.
        if beat.key not in ("a-investigate", "b-investigate"):
            assert slots == sorted(slots) and len(slots) == len(set(slots)), beat.key
    assert set(played) == set(KEYS) | {b.key for b in CUTAWAY}


def test_each_outcome_of_sample_a_fills_the_same_four_caption_slots_in_its_own_words():
    texts = {o: beats("live", outcome=o)[KEYS.index("a-investigate")].captions for o in OUTCOMES}
    assert all(len(t) == 4 for t in texts.values())
    assert len({t for t in texts.values()}) == 4
    assert (
        "inconclusive" in texts["retried"][1] and "inconclusive" in texts["unverified_retried"][1]
    )
    assert "retried" in texts["retried"][2] and "retried" in texts["unverified_retried"][2]
    assert (
        "3 never spent" in texts["retried"][3] and "4 searches never spent" in texts["decisive"][3]
    )
    assert "still unverified" in texts["unverified"][3]


def test_sample_b_is_narrated_as_the_live_jobs_search_left_it():
    clean = {b.key: b for b in beats("live")}["b-investigate"]
    missed = {b.key: b for b in beats("live", sample_b="unverified")}["b-investigate"]
    others = {
        b.key
        for b in beats("live")
        if b != beats("live", sample_b="unverified")[KEYS.index(b.key)]
    }
    assert others == {"b-investigate"}
    assert "never genuine" in clean.text.lower()
    assert ("#verdict .band-title", "Nothing found contradicts the offer") in clean.expects
    # A miss never claims a listing or the best verdict, and is checked against the page.
    assert "listing by Siemens" in missed.text and "no listing" in missed.text
    assert "nothing found contradicts" not in missed.text.lower()
    assert "genuine" not in missed.text.lower()
    assert ("#verdict .band-title", "Unverified") in missed.expects
    assert ("#trace", "no listing by Siemens for this role") in missed.expects
    assert not any("Listing applies" in text for _, text in missed.expects)
    assert not any("contradicts the offer" in c for c in missed.captions)
    assert (len(missed.narration), len(missed.captions)) == (
        len(clean.narration),
        len(clean.captions),
    )
    both = {b.key: b for b in beats("live", "film", "unverified", "unverified")}
    assert (
        both["b-investigate"] == missed
        and both["a-investigate"]
        == beats("live", outcome="unverified")[KEYS.index("a-investigate")]
    )
    with pytest.raises(ValueError, match="sample B"):
        beats("live", sample_b="maybe")
    with pytest.raises(ValueError, match="always find"):
        beats("replay", sample_b="unverified")


def band_state(*bands):
    trace = [{"band": b} for b in bands]

    class App:
        @staticmethod
        def get(path):
            return {"cases": [{"id": "case-1", "trace": trace}]}

    return App


def test_sample_b_is_told_from_the_last_band_of_its_trace_and_nothing_else_is_filmed():
    outcome = record_demo.sample_b_outcome
    assert (
        outcome(band_state(None, "unverified", "consistent_with_genuine", None), "case-1")
        == "clean"
    )
    assert outcome(band_state("consistent_with_genuine", "unverified"), "case-1") == "unverified"
    for other in (("high_risk",), ()):
        with pytest.raises(record_demo.Refused, match="no words"):
            outcome(band_state(*other), "case-1")


def test_a_take_speaks_the_variant_of_both_samples_from_the_moment_each_is_decided(monkeypatch):
    stage = Stage()
    monkeypatch.setattr(record_demo.time, "monotonic", lambda: stage.clock)
    say = {}
    variants = {}
    for outcome in ("decisive", "unverified"):
        for sample_b in ("clean", "unverified"):
            beat = Beat("scene", (f"{outcome}/{sample_b}",), (f"{outcome}/{sample_b} words",))
            variants[(outcome, sample_b)] = ((beat,), {"scene": [{"seconds": 1.0}]})
            say[(outcome, sample_b)] = beat
    take = record_demo.Take(stage, None, None, variants)
    assert take.beats["scene"] is say[("decisive", "clean")]
    take.decided("unverified")
    assert take.beats["scene"] is say[("unverified", "clean")]
    take.decided_b("unverified")
    assert take.beats["scene"] is say[("unverified", "unverified")]
    assert (take.outcome, take.sample_b) == ("unverified", "unverified")
