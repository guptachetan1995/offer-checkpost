"""Records the demo video: a real Playwright screen recording of the app on 127.0.0.1.

    python -m demo.record_demo capture --provider live|replay [--part film|cutaway]
        [--workdir DIR] [--max-usage N]
    python -m demo.record_demo render [--workdir DIR] [--out FILE] [--allow-unchecked]
    python -m demo.record_demo all --provider live|replay [--workdir DIR] [--out FILE]

``capture`` synthesizes each sentence of each beat's narration with macOS ``say`` (the default
system voice) and measures it, starts ``python -m offer_checkpost serve`` on a free port, opens
the single-use address it prints in Google Chrome, headless, at 1280x720 with Playwright's
``record_video_dir``, and plays the beats in ``demo.beats`` as a person's clicks. Each sentence
starts as the page reaches what it says, after the one before it has been spoken, and each beat
is held on screen until its last sentence has been. A caption goes up at the moment of the
on-screen change it describes, and comes down when the next sentence starts. It writes
``capture.webm`` and ``events.json`` (beat starts, the second each sentence was cued and each
caption shown, every interval the page waited on searches, the call log, the Account API's
counts before and after, what sample A's live search found, what the page showed against each
beat's ``expects``) into the work directory. ``live`` runs
the app on live SerpApi with ``OFFER_CHECKPOST_NO_CACHE=1``, so every search is real and
counted; before the first search it reads the searches used this month from the page's header
(the free Account API) and refuses when the worst case would pass ``--max-usage``. ``replay``
spends none. The app reads its own key from ``.env``; the recorder never does. A take that
stops part-way leaves ``events.aborted.json`` (the searches it had spent) and
``capture.partial.webm``.

``--part cutaway`` records the replay cutaway instead of the film: sample A again on the
recorded responses, which hold HCLTech's fraud notice. Only a live take whose sample A search
found no notice (``outcome`` ``unverified`` or ``unverified_retried`` in ``events.json``) needs
it: ``all --provider live``
captures it, into ``<workdir>/cutaway``, only then, and ``render`` splices it in, after the beat
named by ``demo.beats.CUTAWAY_AFTER``, only for such a take.

``render`` turns the capture into the mp4 without the browser or the app: it re-synthesizes any
narration whose words changed, lays out the timeline (``demo.timeline``: it leaves out the
stretches where the screen did not change and nobody was speaking, as ffmpeg's ``freezedetect``
finds them, and lists them in ``render.json``), draws each sped-up
wait's label as an image, and has ffmpeg cut, speed up, overlay and encode H.264 1280x720 at
30 fps with the AAC narration, faststart. It writes ``render.json`` beside the mp4 and fails
when ffprobe finds the video 179 s or longer, without sound, or a frame count more than one
off the plan. It refuses a capture where the page didn't show what a beat's narration says. It
also refuses when narration rewritten since names text the capture never checked, unless
``--allow-unchecked``; ``render.json`` lists those claims for a person to confirm.

The MCP beat runs ``python -m offer_checkpost mcp`` as a subprocess and speaks to it over
stdio as a scripted client, with no language model: it lists the tools and calls
``publish_verdict``, and shows the adapter's real answers in the page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from demo import timeline
from demo.beats import (
    CUTAWAY_AFTER,
    HACKATHON,
    OUTCOMES,
    PARTS,
    RECORDED,
    REPO_URL,
    SAMPLE_B,
    TRACK,
    UNVERIFIED,
    Beat,
    beats,
    judge,
)

ENTRY = Path(__file__).resolve().parents[1]
DEFAULT_WORKDIR = ENTRY / "out" / "demo-capture"
DEFAULT_OUT = ENTRY / "out" / "offer-checkpost-demo.mp4"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
SIZE = {"width": 1280, "height": 720}
# The most searches one take can spend: the per-case budget for each of A, C and B (C's
# remaining checks come out of its own budget). The app is started with that budget pinned, so a
# larger one in .env can't make the guard's arithmetic wrong.
PER_CASE = 6
WORST_CASE = 3 * PER_CASE
DEFAULT_MAX_USAGE = 54
SEARCH_TIMEOUT = 600
SETTLE = 0.4
# A smooth scroll to a finding takes about this long, and its caption goes up once most of it
# is done.
SCROLL = 0.9
CAPTION_AFTER_SCROLL = 0.6
NOTE = "Do not pay the fee."
FORWARDED = (ENTRY / "samples" / "offers" / "a-forwarded.txt").read_text("utf-8")
_URL = re.compile(r"(http://127\.0\.0\.1:\d+)/\?token=\S+")
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Refused(Exception):
    """The recorder stopped before it should go on; ``str()`` says why."""


# ---- the page's overlays ------------------------------------------------------------------

OVERLAY = (Path(__file__).parent / "overlay.js").read_text("utf-8")

LABEL_HTML = """<!doctype html><html><head><style>
html, body {{ margin: 0; background: transparent; }}
#l {{ display: inline-block; margin: 4px; padding: 10px 20px; border-radius: 10px;
  background: rgba(17, 17, 17, 0.92); color: #fff; border: 2px solid #ffb37a;
  font: 600 22px/1.3 system-ui, -apple-system, sans-serif; white-space: nowrap; }}
#l b {{ color: #ffb37a; }}
</style></head><body><div id="l"><b>&#9193; Sped up</b> &middot; {text}</div></body></html>"""


# ---- narration ----------------------------------------------------------------------------


def synthesize(plan_beats: Sequence[Beat], folder: Path) -> dict[str, list[dict[str, Any]]]:
    """Each narrated sentence's clip, ``<hash of its words>.wav`` in ``folder``, spoken by
    ``say``; a clip already there is reused. Returns ``{key: [{"text", "seconds", "path"}]}``,
    each beat's sentences in order."""
    folder.mkdir(parents=True, exist_ok=True)
    clips: dict[str, list[dict[str, Any]]] = {}
    for beat in plan_beats:
        for sentence in beat.narration:
            wav = folder / f"{hashlib.sha1(sentence.encode()).hexdigest()[:12]}.wav"
            if not wav.exists():
                aiff = wav.with_suffix(".aiff")
                subprocess.run(["say", "-o", str(aiff), sentence], check=True)
                ffmpeg("-i", str(aiff), "-ar", "48000", "-ac", "2", str(wav))
                aiff.unlink()
            clips.setdefault(beat.key, []).append(
                {"text": sentence, "seconds": probe_seconds(wav), "path": wav}
            )
    return clips


def ffmpeg(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.stdout)


def probe_seconds(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


# ---- the app ------------------------------------------------------------------------------


class App:
    """``python -m offer_checkpost serve`` on a free port of 127.0.0.1, with ``provider``.
    ``url`` is the single-use address it prints; ``origin`` the bare one."""

    def __init__(self, provider: str, log: Path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("OFFER_CHECKPOST_")}
        env["PYTHONPATH"] = str(ENTRY / "src")
        env["OFFER_CHECKPOST_PROVIDER"] = provider
        env["OFFER_CHECKPOST_MAX_SEARCHES"] = str(PER_CASE)
        if provider == "live":
            env["OFFER_CHECKPOST_NO_CACHE"] = "1"
        else:
            env.pop("SERPAPI_KEY", None)
        self.provider = provider
        self._err = log.open("w")
        # The app runs from the entry, where it reads its own settings, key included, from .env.
        self.process = subprocess.Popen(
            [sys.executable, "-u", "-m", "offer_checkpost", "serve", "--port", "0"],
            cwd=ENTRY,
            env=env,
            stdout=subprocess.PIPE,
            stderr=self._err,
            text=True,
        )
        first = self.process.stdout.readline()
        found = _URL.search(first)
        if not found:
            self.stop()
            raise Refused(f"the app did not print its address; its errors are in {log}")
        self.url, self.origin = found[0], found[1]
        # The later lines carry the next single-use address: read and dropped, never kept.
        threading.Thread(target=self.process.stdout.read, daemon=True).start()

    def get(self, path: str) -> Any:
        with _OPENER.open(self.origin + path, timeout=10) as answer:
            return json.loads(answer.read())

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._err.close()


def mcp_exchange(origin: str, cwd: Path, case_id: str) -> dict[str, Any]:
    """A scripted MCP client, no model: initialize, tools/list, then tools/call
    publish_verdict on ``case_id``, through ``python -m offer_checkpost mcp``."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "SERPAPI_KEY" and k != "PORT" and not k.startswith("OFFER_CHECKPOST_")
    }
    env["PYTHONPATH"] = str(ENTRY / "src")
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "scripted-demo-client", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "publish_verdict",
                "arguments": {"case_id": case_id, "label": "likely_impersonation"},
            },
        },
    ]
    # Its working directory is the capture's, so no .env is read by it.
    out = subprocess.run(
        [sys.executable, "-m", "offer_checkpost", "mcp", "--url", origin],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=60,
        check=True,
    )
    answers = {a["id"]: a for a in map(json.loads, out.stdout.splitlines())}
    tools = [t["name"] for t in answers[2]["result"]["tools"]]
    called = answers[3]["result"]
    envelope = json.loads(called["content"][0]["text"])
    return {
        "tools": tools,
        "isError": called["isError"],
        "outcome": envelope.get("outcome"),
        "error": envelope.get("error"),
    }


# ---- the capture --------------------------------------------------------------------------


class Take:
    """One capture: the page, the clock the video runs on, and the events logged against it.
    ``variants`` is each ``(outcome of sample A, outcome of sample B)``'s beats and their
    narration clips; the take paces itself by the ``decisive``, ``clean`` ones until ``decided``
    and ``decided_b`` say what the live searches found."""

    def __init__(
        self,
        page: Any,
        app: App,
        workdir: Path,
        variants: dict[tuple[str, str], tuple[Sequence[Beat], dict[str, list[dict[str, Any]]]]],
    ):
        self.page, self.app, self.workdir = page, app, workdir
        self.variants = variants
        self.outcome, self.sample_b = "decisive", "clean"
        self.beats: dict[str, Beat] = {}
        self.narration: dict[str, list[dict[str, Any]]] = {}
        self.decided(self.outcome)
        self.t0 = time.monotonic()
        self.marks: list[tuple[str, float]] = []
        self.cues: dict[str, list[float]] = {}
        self.waits: list[timeline.Wait] = []
        self.expects: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.showing = ""
        self.captions: list[dict[str, Any]] = []
        self.inflight = 0
        self.started = 0
        self.finished_at = 0.0
        page.on("request", self._request)
        page.on("requestfinished", self._settled)
        page.on("requestfailed", self._settled)

    def decided(self, outcome: str) -> None:
        """What sample A's live search found: the beats from here on say that."""
        self.outcome = outcome
        self._speak()

    def decided_b(self, sample_b: str) -> None:
        """What sample B's live searches found: the beats from here on say that."""
        self.sample_b = sample_b
        self._speak()

    def _speak(self) -> None:
        plan_beats, clips = self.variants[(self.outcome, self.sample_b)]
        self.beats = {b.key: b for b in plan_beats}
        self.narration = clips

    def now(self) -> float:
        return time.monotonic() - self.t0

    @staticmethod
    def _invoke(request: Any) -> bool:
        return request.method == "POST" and request.url.endswith("/api/invoke")

    def _request(self, request: Any) -> None:
        if self._invoke(request):
            self.inflight += 1
            self.started += 1

    def _settled(self, request: Any) -> None:
        if self._invoke(request):
            self.inflight -= 1
            self.finished_at = self.now()

    # -- pacing

    def pause(self, seconds: float) -> None:
        self.page.wait_for_timeout(seconds * 1000)

    def beat(self, key: str) -> Beat:
        beat = self.beats[key]
        self.marks.append((key, self.now()))
        self.set_caption("")
        return beat

    def elapsed(self) -> float:
        """Video seconds since the current beat started."""
        _, start = self.marks[-1]
        return timeline.output_elapsed(start, self.now(), self.waits)

    def say(self, i: int) -> None:
        """Starts sentence ``i`` of the current beat: as soon as the page has reached what it
        says, but not before the sentence before it, and the gap after it, are over. The
        caption of the sentence before is taken down: the next one goes up when the page shows
        what it describes."""
        key, start = self.marks[-1]
        clips, heard = self.narration[key], self.cues.setdefault(key, [])
        if i != len(heard):
            raise Refused(f"{key}: sentence {i} was cued out of order")
        earliest = timeline.NARRATION_LEAD
        if heard:
            before = timeline.output_elapsed(start, heard[-1], self.waits)
            earliest = before + clips[i - 1]["seconds"] + timeline.CLIP_GAP
        if earliest > self.elapsed():
            self.pause(earliest - self.elapsed())
        heard.append(self.now())
        if i:
            self.set_caption("")

    def caption(self, i: int) -> None:
        """Puts caption slot ``i`` of the current beat on screen, in place of any other: call it
        the moment the page shows what the caption describes."""
        key, _ = self.marks[-1]
        text = self.beats[key].captions[i]
        self.set_caption(text)
        self.captions.append({"beat": key, "slot": i, "t": self.now(), "text": text})

    def hold(self) -> None:
        """Speaks any sentence not yet cued, then holds the beat until its last has been spoken
        and the tail after it is over."""
        key, start = self.marks[-1]
        clips, heard = self.narration[key], self.cues.setdefault(key, [])
        while len(heard) < len(clips):
            self.say(len(heard))
        last = timeline.output_elapsed(start, heard[-1], self.waits) + clips[-1]["seconds"]
        need = last + timeline.NARRATION_TAIL
        if need > self.elapsed():
            self.pause(need - self.elapsed())
        self.check_expects(key)

    def check_expects(self, key: str) -> None:
        for selector, text in self.beats[key].expects:
            element = self.page.locator(selector).first
            found = element.inner_text() if element.count() else ""
            ok = text.lower() in found.lower()
            self.expects.append({"beat": key, "selector": selector, "text": text, "ok": ok})
            if not ok:
                print(f"record_demo: {key}: {selector} does not show {text!r}", file=sys.stderr)

    # -- the page

    def js(self, call: str, *args: Any) -> Any:
        return self.page.evaluate(f"(a) => window.__oc.{call}(...a)", list(args))

    def set_caption(self, text: str) -> None:
        self.showing = text
        self.js("caption", text)

    def scrolled(self, dwell: float, slot: int | None) -> None:
        """Waits out a scroll and ``dwell``. Caption ``slot``, if any, goes up once the page has
        mostly reached what it describes, and stays for the rest of the wait."""
        total = SCROLL + dwell
        if slot is None:
            self.pause(total)
            return
        self.pause(CAPTION_AFTER_SCROLL)
        self.caption(slot)
        self.pause(total - CAPTION_AFTER_SCROLL)

    def clear(self) -> None:
        """Takes down the caption before the page moves on: it described the screen that is
        being scrolled away."""
        if self.showing:
            self.set_caption("")

    def show(self, selector: str, dwell: float = 0.0, caption: int | None = None) -> None:
        self.page.wait_for_selector(selector)
        self.clear()
        self.js("show", selector)
        self.scrolled(dwell, caption)

    def center(self, selector: str, dwell: float = 0.0, caption: int | None = None) -> None:
        self.page.wait_for_selector(selector)
        self.clear()
        self.js("center", selector)
        self.scrolled(dwell, caption)

    def show_text(
        self, root: str, text: str, dwell: float = 0.0, caption: int | None = None
    ) -> None:
        self.clear()
        if not self.js("showText", root, text):
            print(f"record_demo: {root} has no {text!r} to show", file=sys.stderr)
        self.scrolled(dwell, caption)

    def point(self, selector: str, dwell: float = 0.5, caption: int | None = None) -> None:
        self.page.wait_for_selector(selector)
        if not self.js("visible", selector):
            self.clear()
            self.js("center", selector)
            self.pause(SCROLL)
        self.js("point", selector)
        if caption is not None:
            self.caption(caption)
        self.pause(dwell)

    def click(self, selector: str, dwell: float = 0.6) -> None:
        self.point(selector)
        self.page.click(selector)
        self.pause(dwell)

    def ring(self, selector: str, dwell: float = 1.0, caption: int | None = None) -> None:
        """Rings ``selector`` where it is, without scrolling: for the sticky header."""
        self.page.wait_for_selector(selector)
        self.js("point", selector)
        if caption is not None:
            self.caption(caption)
        self.pause(dwell)

    def searching(self, selector: str) -> None:
        """Clicks ``selector``, then waits out every call it set off: a ``Wait`` in the log,
        counting the searches the call log gained."""
        before = len(self.app.get("/api/calls")["calls"])
        self.point(selector)
        started, start = self.started, self.now()
        self.page.click(selector)
        deadline = time.monotonic() + SEARCH_TIMEOUT
        while True:
            self.page.wait_for_timeout(50)
            quiet = self.now() - self.finished_at
            if self.started > started and self.inflight == 0 and quiet >= SETTLE:
                break
            if time.monotonic() > deadline:
                raise Refused(f"{selector} was still waiting after {SEARCH_TIMEOUT} s")
        calls = self.app.get("/api/calls")["calls"][before:]
        self.waits.append(timeline.Wait(start, self.finished_at, len(calls), self.app.provider))
        if notice := self.page.locator("#notice:not([hidden])").all_inner_texts():
            print(f"record_demo: the page says: {notice}", file=sys.stderr)

    def goto(self, url: str) -> None:
        self.page.goto(url)
        self.js("caption", self.showing)

    def top(self) -> None:
        self.clear()
        self.page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        self.pause(0.8)

    def open_sample(self, name: str) -> str:
        self.click("#sample", 0.2)
        self.page.select_option("#sample", name)
        self.pause(1.2)
        self.click("#open-case")
        self.page.wait_for_selector("[data-focus=confirm-claims]")
        return self.app.get("/api/state")["cases"][-1]["id"]

    def paste(self, text: str) -> str:
        """Pastes ``text`` into the offer box and opens the case; returns its id."""
        self.point("#offer-text", 0.3)
        self.page.fill("#offer-text", text)
        self.pause(0.6)
        self.click("#open-case")
        self.page.wait_for_selector("[data-focus=confirm-claims]")
        return self.app.get("/api/state")["cases"][-1]["id"]

    def confirm(self, dwell: float) -> None:
        self.pause(dwell)
        self.click("[data-focus=confirm-claims]", 0.3)
        self.page.wait_for_selector(".claims .state.ok")

    def budget(self) -> dict[str, Any] | None:
        """The header's searches-left badge, which the page fills from the Account API."""
        badge = self.page.locator("#budget")
        if badge.is_hidden():
            return None
        text, title = badge.inner_text(), badge.get_attribute("title") or ""
        left = re.match(r"([\d,]+) searches left", text)
        used = re.search(r"(\d+) used of (\d+)", title)
        return {
            "text": text,
            "title": title,
            "left": int(left[1].replace(",", "")) if left else None,
            "usage": int(used[1]) if used else None,
            "per_month": int(used[2]) if used else None,
        }


def _case(app: App, case_id: str) -> dict[str, Any]:
    return next(c for c in app.get("/api/state")["cases"] if c["id"] == case_id)


def notice_found(app: App, case_id: str) -> bool:
    """Whether case ``case_id``'s investigation stopped decisive on the employer's own fraud
    notice contradicting the fee: what the ``decisive`` beats say."""
    case = _case(app, case_id)
    fired = any(
        s["rule"] == "fee_contradicts_employer" and not s["stale"] and not s.get("superseded")
        for s in case["signals"]
    )
    return fired and case["budget"]["stoppedBecause"] == "decisive"


def notice_runs(app: App, case_id: str) -> list[dict[str, Any]]:
    """The fraud-notice searches case ``case_id`` ran, in order: one, or two when the first
    returned no page from the employer's domain and the planner retried."""
    trace = _case(app, case_id)["trace"]
    return [x for x in trace if x["tool"] == "find_fraud_notice" and x["action"] == "ran"]


def notice_note(app: App, case_id: str) -> str:
    """What the last fraud-notice search of case ``case_id`` found, as the trace shows it: an
    inconclusive search (no page from the employer's domain) leads with its own label there,
    so the page does not repeat the word."""
    [*_, last] = [
        x for x in _case(app, case_id)["trace"] if x["tool"] == "find_fraud_notice" and x["note"]
    ]
    return last["note"].removeprefix("inconclusive: ")


def sample_a_outcome(app: App, case_id: str) -> str:
    """What sample A's live investigation found, for the beats to say (``demo.beats.OUTCOMES``):
    ``decisive`` when the notice was found by the first fraud-notice search, ``retried`` when by
    the planner's second (the first returned no page from the domain), ``unverified`` and
    ``unverified_retried`` when the checks ran to their end without it. A search that failed (a
    timeout, the quota) is none of them, and stops the take: the forwarded copy after it could
    not be answered from a check that never finished, and every search spent going on would be
    wasted."""
    retried = len(notice_runs(app, case_id)) > 1
    if notice_found(app, case_id):
        return "retried" if retried else "decisive"
    why = _case(app, case_id)["budget"]["stoppedBecause"]
    if why in ("budget", "done"):
        return "unverified_retried" if retried else "unverified"
    raise Refused(
        f"sample A's investigation stopped on {why!r}, not on a finished check "
        f"({len(app.get('/api/calls')['calls'])} searches logged); nothing more was searched"
    )


def sample_b_outcome(app: App, case_id: str) -> str:
    """What sample B's live investigation ended on, for the beats to say (``demo.beats.SAMPLE_B``):
    ``clean`` when nothing found contradicts the offer, ``unverified`` when the band did not
    move. Any other band is not one the film has words for."""
    bands = [x["band"] for x in _case(app, case_id)["trace"] if x.get("band")]
    if bands[-1:] == ["consistent_with_genuine"]:
        return "clean"
    if bands[-1:] == ["unverified"]:
        return "unverified"
    raise Refused(f"sample B ended on band {bands[-1:]}, which the film has no words for")


def play(take: Take, max_usage: int) -> dict[str, Any]:
    """The beats, as a person's clicks. Returns what the events log needs beyond the take."""
    page, app = take.page, take.app
    facts: dict[str, Any] = {}

    take.goto(app.url)
    page.wait_for_selector("#bind:not(:text('…'))")
    page.wait_for_selector("#sample-wrap:not([hidden])")
    if app.provider == "live":
        page.wait_for_selector("#budget:not([hidden])", timeout=30_000)
        before = facts["account_before"] = take.budget()
        if not before or before["usage"] is None:
            raise Refused("the header shows no Account API count, so the spend can't be guarded")
        if before["usage"] + WORST_CASE > max_usage:
            raise Refused(
                f"{before['usage']} searches used this month, and a take can spend "
                f"{WORST_CASE}: that could pass --max-usage {max_usage}; nothing was searched"
            )
        print(f"record_demo: Account API before: {before['title']}")

    take.beat("intro")
    take.say(0)
    take.ring("#provider", 0.9, caption=0)
    take.ring("#budget" if app.provider == "live" else "#replay", 0.9)
    take.ring(".tagline", 0.9)
    take.say(1)
    take.ring(".strip", 1.0, caption=1)
    take.say(2)
    take.ring(".lede", 1.0, caption=2)
    take.say(3)
    take.center("#board", 0.2, caption=3)
    take.ring("#board", 1.0)
    take.top()
    take.hold()

    take.beat("a-claims")
    take.say(0)
    a = facts["case_a"] = take.open_sample("a")
    take.point(".chips", 1.5)
    take.say(1)
    take.confirm(0.2)
    take.say(2)
    take.center(".claims .state.ok", 0.5, caption=0)
    take.hold()

    take.beat("a-investigate")
    take.say(0)
    take.searching("[data-focus=investigate]")
    outcome = sample_a_outcome(app, a) if app.provider == "live" else "decisive"
    take.decided(outcome)
    facts["outcome"] = take.outcome
    print(f"record_demo: sample A's live search: {take.outcome}")
    take.show("#trace", 0.6, caption=0)
    take.say(1)
    take.show_text("#trace", "A fee was asked and step 1 named", 1.0, caption=1)
    found = outcome in ("decisive", "retried")
    if outcome == "decisive":
        take.show("#evidence", 2.0, caption=2)
    elif outcome == "unverified":
        take.show_text("#trace", notice_note(app, a), 1.0, caption=2)
    else:
        take.say(2)
        take.show_text("#trace", "R3, inconclusive notice search", 1.0, caption=2)
        if found:
            take.show("#evidence", 2.0)
        else:
            take.show_text("#trace", notice_note(app, a), 1.0)
    if outcome in ("decisive", "unverified"):
        take.say(2)
    take.show_text("#trace", "not spent" if found else "6 searches spent of 6", 1.0, caption=3)
    take.hold()

    take.beat("a-publish")
    take.say(0)
    take.show("#publish-h", 0.3, caption=0)
    take.click("#pub-label", 0.2)
    page.select_option("#pub-label", "likely_impersonation")
    take.pause(0.5)
    take.say(1)
    take.click("#pub-note", 0.2)
    page.type("#pub-note", NOTE, delay=45)
    take.pause(0.3)
    take.click("#publish-btn")
    page.wait_for_selector(f"#post-{a}")
    take.show("#board", 0.4)
    take.point(f"[data-focus=wa-{a}]", 1.0, caption=1)
    take.hold()

    take.beat("a-forwarded")
    take.top()
    take.say(0)
    take.click("[data-focus=new-case]", 0.3)
    facts["case_forwarded"] = take.paste(FORWARDED)
    take.point(".case-meta", 0.8, caption=0)
    take.confirm(0.3)
    take.say(1)
    take.searching("[data-focus=investigate]")
    take.ring(".strip", 1.0, caption=1)
    take.say(2)
    take.show("#trace", 1.5, caption=2)
    take.hold()

    take.beat("c-investigate")
    take.top()
    take.say(0)
    take.click("[data-focus=new-case]", 0.3)
    facts["case_c"] = take.open_sample("c")
    take.point(".chips", 1.8, caption=0)
    take.confirm(0.5)
    take.say(1)
    take.searching("[data-focus=investigate]")
    take.show("#trace", 1.5)
    take.show_text("#trace", "Office not on Maps", 2.0, caption=1)
    take.hold()

    take.beat("c-remaining")
    take.show("#remaining-h", 0.3, caption=0)
    take.say(0)
    take.searching("[data-focus=remaining]")
    take.say(1)
    take.show_text("#trace", "No matching listing", 1.5, caption=1)
    take.show_text("#trace", "Pay far above comparable listings", 1.5, caption=2)
    take.say(2)
    take.show("#drafts-h", 0.3)
    take.click("[data-focus=draft-reply]", 0.3)
    take.center("#draft-reply", 2.0)
    take.hold()

    take.beat("b-investigate")
    take.top()
    take.say(0)
    take.click("[data-focus=new-case]", 0.3)
    facts["case_b"] = take.open_sample("b")
    take.pause(0.6)
    take.confirm(0.3)
    take.say(1)
    take.searching("[data-focus=investigate]")
    sample_b = sample_b_outcome(app, facts["case_b"]) if app.provider == "live" else "clean"
    take.decided_b(sample_b)
    facts["sample_b"] = sample_b
    print(f"record_demo: sample B's live searches: {sample_b}")
    take.show_text("#trace", "No fee or sensitive documents were asked for", 1.0, caption=0)
    take.say(2)
    if sample_b == "clean":
        take.show_text("#trace", "Listing applies on the official domain", 1.5)
        take.show_text("#trace", "Found: no news reports", 1.0, caption=1)
    else:
        take.show_text("#trace", "no listing by Siemens for this role", 1.5)
        take.show_text("#trace", "Office found on Maps", 1.0, caption=1)
    take.say(3)
    take.top()
    take.center("#verdict", 2.0, caption=2)
    take.hold()

    take.beat("a-report")
    take.top()
    take.say(0)
    take.click(f"[data-focus=case-{a}]", 0.3)
    page.wait_for_selector(f"#case-h:text('{a}'), .case-meta:has-text('{a}')")
    take.show("#drafts-h", 0.3)
    take.click("[data-focus=draft-report]", 0.3)
    take.center("#draft-report", 2.5, caption=0)
    take.say(1)
    take.show("#board", 0.3)
    take.point("#board a[download]", 0.3)
    with page.expect_download() as download:
        page.click("#board a[download]")
    saved = take.workdir / "offer-board.html"
    download.value.save_as(saved)
    board_ok = "Likely impersonation" in saved.read_text("utf-8")
    take.checks.append({"what": "board download", "ok": board_ok, "file": saved.name})
    take.pause(0.6)
    take.goto(saved.as_uri())
    take.pause(2.5)
    page.go_back()
    page.wait_for_selector("#bind:not(:text('…'))")
    take.js("caption", take.showing)
    # A reload opens on the newest case; the person goes back to the one they were on.
    take.click(f"[data-focus=case-{a}]", 0.3)
    take.hold()

    take.beat("mcp-refused")
    take.say(0)
    take.show("#activity-h", 0.3)
    shown = page.locator("#log .log-row").count()
    mcp = facts["mcp"] = mcp_exchange(app.origin, take.workdir, a)
    listed = "publish_verdict" in mcp["tools"]
    take.checks.append(
        {
            "what": "MCP publish_verdict refused, and not in tools/list",
            "ok": mcp["isError"] and mcp["outcome"] == "refused" and not listed,
        }
    )
    take.js(
        "panel",
        "Scripted MCP client (no LLM) · python -m offer_checkpost mcp · stdio",
        [
            [
                "dim",
                f"→ tools/list: {len(mcp['tools'])} tools; publish_verdict "
                f"{'is' if listed else 'is not'} one of them",
            ],
            [
                "text",
                f'→ tools/call publish_verdict {{"case_id": "{a}", '
                '"label": "likely_impersonation"}',
            ],
            [
                "error",
                f"← isError: {str(mcp['isError']).lower()} · {mcp['outcome']}: {mcp['error']}",
            ],
        ],
    )
    take.caption(0)
    take.say(1)
    # The page reads the agent's call on its idle poll, every 2 s; the app's CSP rules out
    # wait_for_function, which evaluates a string.
    deadline = time.monotonic() + 10
    while page.locator("#log .log-row").count() <= shown:
        if time.monotonic() > deadline:
            raise Refused("the MCP client's call never showed in the page's activity log")
        take.pause(0.1)
    take.pause(0.4)
    take.center("#log .log-row:last-child", 0.3)
    take.point("#log .log-row:last-child", 1.2, caption=1)
    take.hold()
    take.js("panel", "", [])

    facts["account_after"] = take.budget()
    facts["calls"] = app.get("/api/calls")["calls"]
    take.beat("end")
    take.say(0)
    take.js("card", end_card(app, facts))
    take.hold()
    return facts


def end_card(app: App, facts: dict[str, Any]) -> list[list[str]]:
    port = app.origin.rsplit(":", 1)[1]
    searches = len(facts["calls"])
    if app.provider == "live":
        before, after = facts["account_before"], facts.get("account_after") or {}
        run = (
            f"This take: {searches} live SerpApi searches, from 127.0.0.1:{port} · Account API "
            f"searches left: {before['left']} before, {after.get('left')} after (it can trail)"
        )
    else:
        run = (
            f"This take: {searches} searches replayed from SerpApi responses recorded "
            f"{RECORDED}, on 127.0.0.1:{port}"
        )
    return [
        ["title", "Offer Checkpost"],
        ["sub", "Checks a job offer's claims against SerpApi results before anyone pays"],
        ["url", REPO_URL],
        ["line", f"{HACKATHON} · {TRACK} track"],
        ["fine", run],
    ]


def play_cutaway(take: Take) -> dict[str, Any]:
    """Sample A again on the replay provider, whose recorded responses carry HCLTech's notice:
    the decisive path a live search that found none didn't reach."""
    page, app = take.page, take.app
    take.goto(app.url)
    page.wait_for_selector("#bind:not(:text('…'))")
    page.wait_for_selector("#sample-wrap:not([hidden])")

    take.beat("replay-a")
    take.say(0)
    take.ring("#provider", 0.7, caption=0)
    take.ring("#replay", 0.7)
    page.select_option("#sample", "a")
    take.pause(0.3)
    take.click("#open-case", 0.2)
    page.wait_for_selector("[data-focus=confirm-claims]")
    case = app.get("/api/state")["cases"][-1]["id"]
    take.confirm(0.1)
    take.say(1)
    take.searching("[data-focus=investigate]")
    take.show("#trace", 0.5)
    take.show_text("#trace", "not spent", 0.6)
    take.show("#evidence", 1.0, caption=1)
    take.hold()
    return {"case_a": case, "calls": app.get("/api/calls")["calls"]}


def needs_cutaway(outcome: str) -> bool:
    """Only a film whose live search found no notice carries the replay of the decisive path."""
    return outcome in UNVERIFIED


def cutaway_if_needed(workdir: Path, max_usage: int) -> None:
    """Captures the replay cutaway, when the film's live search found no notice."""
    outcome = json.loads((workdir / "events.json").read_text("utf-8"))["outcome"]
    if needs_cutaway(outcome):
        capture("replay", workdir / "cutaway", max_usage, "cutaway")


def keep_partial(take: Take, failure: BaseException) -> None:
    """What a take that stopped part-way leaves behind: the searches it had spent by then, so
    the month's count can still be reconciled."""
    try:
        calls = take.app.get("/api/calls")["calls"]
    except OSError:
        calls = None
    record = {
        "provider": take.app.provider,
        "aborted": f"{type(failure).__name__}: {failure}",
        "marks": [{"beat": k, "t": t} for k, t in take.marks],
        "cues": take.cues,
        "waits": [vars(w) for w in take.waits],
        "calls": calls,
    }
    (take.workdir / "events.aborted.json").write_text(json.dumps(record, indent=2), "utf-8")


def capture(provider: str, workdir: Path, max_usage: int, part: str = "film") -> Path:
    from playwright.sync_api import sync_playwright

    if part == "cutaway" and provider != "replay":
        raise Refused("the cutaway is a replay of the recordings: run it with --provider replay")
    workdir.mkdir(parents=True, exist_ok=True)
    live_film = provider == "live" and part == "film"
    combos = [(o, b) for o in OUTCOMES for b in SAMPLE_B] if live_film else [("decisive", "clean")]
    variants = {}
    for outcome, sample_b in combos:
        plan_beats = beats(provider, part, outcome, sample_b)
        variants[(outcome, sample_b)] = (plan_beats, synthesize(plan_beats, workdir / "narration"))
    first = variants[("decisive", "clean")][1]
    spoken = sum(c["seconds"] for v in first.values() for c in v)
    print(f"record_demo: narration {spoken:.1f} s over {len(first)} beats")
    video_dir = workdir / "video"
    shutil.rmtree(video_dir, ignore_errors=True)

    app = App(provider, workdir / "server.stderr.log")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(
                viewport=SIZE,
                record_video_dir=str(video_dir),
                record_video_size=SIZE,
                accept_downloads=True,
            )
            context.add_init_script(OVERLAY)
            context.set_default_timeout(20_000)
            page = context.new_page()
            take = Take(page, app, workdir, variants)
            recorded_at = datetime.now().astimezone().isoformat(timespec="seconds")
            aborted = False
            try:
                facts = play(take, max_usage) if part == "film" else play_cutaway(take)
                end = take.now()
            except BaseException as failure:
                aborted = True
                keep_partial(take, failure)
                raise
            finally:
                context.close()
                browser.close()
                if aborted:
                    page.video.save_as(str(workdir / "capture.partial.webm"))
            video = Path(page.video.path())
    finally:
        app.stop()

    webm = workdir / "capture.webm"
    video.replace(webm)
    shutil.rmtree(video_dir, ignore_errors=True)
    outcome = facts.get("outcome", "decisive")
    sample_b = facts.get("sample_b", "clean")
    narration = variants[(outcome, sample_b)][1]
    events = {
        "provider": provider,
        "part": part,
        "outcome": outcome,
        "sample_b": sample_b,
        "recorded_at": recorded_at,
        "bind": app.origin.removeprefix("http://"),
        "video": webm.name,
        "end": end,
        "marks": [{"beat": k, "t": t} for k, t in take.marks],
        "cues": take.cues,
        "waits": [vars(w) for w in take.waits],
        "captions": take.captions,
        "expects": take.expects,
        "checks": take.checks,
        "narration": {
            k: [{"text": n["text"], "seconds": n["seconds"]} for n in clips]
            for k, clips in narration.items()
        },
        **facts,
    }
    (workdir / "events.json").write_text(json.dumps(events, indent=2, ensure_ascii=False), "utf-8")
    searches = len(facts["calls"])
    print(f"record_demo: captured {end:.1f} s, {searches} searches ({provider}) → {webm}")
    if provider == "live":
        after = facts.get("account_after") or {}
        print(f"record_demo: Account API after: {after.get('title')}")
    return workdir / "events.json"


# ---- the render ---------------------------------------------------------------------------


def label_images(labels: Sequence[timeline.Label], folder: Path) -> list[Path]:
    if not labels:
        return []
    from playwright.sync_api import sync_playwright

    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 200})
        for i, label in enumerate(labels):
            page.set_content(LABEL_HTML.format(text=_escape(label.text)))
            path = folder / f"label-{i}.png"
            page.locator("#l").screenshot(path=str(path), omit_background=True)
            paths.append(path)
        browser.close()
    return paths


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@dataclass(frozen=True)
class Part:
    """One capture, rendered: its events, its plan and what its checks found."""

    events: dict[str, Any]
    plan: timeline.Plan
    beats: tuple[Beat, ...]
    failed: list[dict[str, Any]]
    unchecked: list[dict[str, Any]]


# The cutaway and the film are encoded near-losslessly when they are to be joined, then once
# more for the file that ships.
PART_ENCODE = ("12", "fast")
FINAL_ENCODE = ("18", "slow")


def encode(out: Path, seconds: float, crf_preset: tuple[str, str]) -> list[str]:
    crf, preset = crf_preset
    return [
        "-c:v", "libx264", "-preset", preset, "-crf", crf, "-r", str(timeline.FPS),
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-t", f"{seconds:.3f}",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip


STILL_NOISE = "-55dB"
STILL_SECONDS = 0.8


def find_stills(capture: Path, cache: Path) -> list[tuple[float, float]]:
    """The capture seconds during which the screen did not change, as ffmpeg's ``freezedetect``
    finds them (``STILL_SECONDS`` or longer, frames within ``STILL_NOISE`` of each other).
    Kept in ``cache`` while the capture is no newer than it."""
    if cache.exists() and cache.stat().st_mtime >= capture.stat().st_mtime:
        return [tuple(pair) for pair in json.loads(cache.read_text("utf-8"))]
    out = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-i", str(capture), "-map", "0:v:0",
            "-vf", f"freezedetect=n={STILL_NOISE}:d={STILL_SECONDS}", "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stderr  # fmt: skip
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", out)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", out)]
    stills = list(zip(starts, ends, strict=False))
    cache.write_text(json.dumps(stills), "utf-8")
    return stills


def dropped_sentences(
    plan_beats: Sequence[Beat], cues: dict[str, list[float]]
) -> tuple[dict[str, int], dict[str, float]]:
    """How many sentences of each beat the video keeps, and, for a beat that drops its last
    ones, the capture second at which the page reached the first of them: where the beat ends."""
    kept = {b.key: len(b.narration) - b.dropped for b in plan_beats}
    trims = {b.key: cues[b.key][kept[b.key]] for b in plan_beats if b.dropped}
    return kept, trims


def render_part(
    workdir: Path,
    out: Path,
    allow_unchecked: bool,
    crf_preset: tuple[str, str],
    cut_stills: bool = False,
) -> Part:
    """One capture as an mp4: its narration laid over the timeline, its waits sped up under
    labels. Refuses a check that failed for a claim the beat still makes; a claim the capture
    never checked is refused unless ``allow_unchecked``."""
    events = json.loads((workdir / "events.json").read_text("utf-8"))
    plan_beats = beats(
        events["provider"],
        events.get("part", "film"),
        events["outcome"],
        events.get("sample_b", "clean"),
    )
    failed, unchecked = judge(events["expects"], plan_beats)
    failed += [c for c in events["checks"] if not c["ok"]]
    if failed or (unchecked and not allow_unchecked):
        lines = "\n".join(f"  {json.dumps(f, ensure_ascii=False)}" for f in failed + unchecked)
        raise Refused(
            "the capture didn't show, or never checked, what the narration says"
            f"{'' if failed else ' (--allow-unchecked renders it once a person has looked)'}:\n"
            f"{lines}"
        )
    clips = synthesize(plan_beats, workdir / "narration")
    # A beat's dropped sentences, and its footage from the second the page reached the first of
    # them, are left out of the video.
    kept, trims = dropped_sentences(plan_beats, events["cues"])
    clips = {key: cs[: kept[key]] for key, cs in clips.items()}
    waits = [timeline.Wait(**w) for w in events["waits"]]
    marks = [(m["beat"], m["t"]) for m in events["marks"]]
    plan = timeline.plan(
        marks,
        events["end"],
        waits,
        {k: [c["seconds"] for c in cs] for k, cs in clips.items()},
        events["cues"],
        find_stills(workdir / events["video"], workdir / "stills.json") if cut_stills else (),
        trims,
    )
    images = label_images(plan.labels, workdir / "labels")

    inputs = ["-i", str(workdir / events["video"])]
    for image in images:
        inputs += ["-i", str(image)]
    for beat in plan.beats:
        for clip in clips.get(beat.key, ()):
            inputs += ["-i", str(clip["path"])]
    graph = ";".join([timeline.video_filter(plan), timeline.audio_filter(plan, 1 + len(images))])
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg(
        *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[aout]",
        *encode(out, plan.duration, crf_preset),
    )  # fmt: skip
    return Part(events, plan, plan_beats, failed, unchecked)


def join(film: Path, cutaway: Path, at: float, out: Path, seconds: float) -> None:
    """The film with the cutaway spliced in at video second ``at``."""
    cut = f"{at:.4f}"
    graph = (
        f"[0:v]trim=end={cut},setpts=PTS-STARTPTS[v0];"
        f"[0:v]trim=start={cut},setpts=PTS-STARTPTS[v2];"
        f"[0:a]atrim=end={cut},asetpts=PTS-STARTPTS[a0];"
        f"[0:a]atrim=start={cut},asetpts=PTS-STARTPTS[a2];"
        "[v0][a0][1:v][1:a][v2][a2]concat=n=3:v=1:a=1[vout][aout]"
    )
    ffmpeg(
        "-i", str(film), "-i", str(cutaway),
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[aout]",
        *encode(out, seconds, FINAL_ENCODE),
    )  # fmt: skip


def video_frames(path: Path) -> int:
    out = subprocess.run(
        [
            FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )  # fmt: skip
    return int(out.stdout.strip())


def _beat_rows(part: Part, offset: Callable[[float], float]) -> list[dict[str, Any]]:
    """``part``'s beats in the final's seconds: ``offset(start)`` is what a beat starting at
    ``start`` is moved by."""
    texts = {b.key: b for b in part.beats}
    waits = [timeline.Wait(**w) for w in part.events["waits"]]
    return [
        {
            "beat": b.key,
            "out_start": round(b.out_start + offset(b.out_start), 2),
            "out_end": round(b.out_end + offset(b.out_start), 2),
            "capture_start": round(b.start, 2),
            "capture_end": round(b.end, 2),
            "freeze": round(b.freeze, 2),
            "narration_late": round(b.late, 2),
            "narration": [
                {"at": round(c.at + offset(b.out_start), 2), "seconds": round(c.seconds, 2)}
                for c in b.clips
            ],
            "captions": [
                {
                    "at": round(
                        b.out_start
                        + timeline.output_elapsed(b.start, c["t"], waits, cuts=part.plan.cuts)
                        + offset(b.out_start),
                        2,
                    ),
                    "slot": c["slot"],
                    "text": c["text"],
                }
                for c in part.events.get("captions", [])
                if c["beat"] == b.key and b.start <= c["t"] <= b.end
            ],
            "sentences": list(
                texts[b.key].narration[: len(texts[b.key].narration) - texts[b.key].dropped]
            ),
        }
        for b in part.plan.beats
    ]


def _speedups(part: Part, offset: Callable[[float], float]) -> list[dict[str, Any]]:
    return [
        {
            "capture_start": round(label.wait.start, 2),
            "capture_seconds": round(label.wait.seconds, 2),
            "searches": label.wait.searches,
            "factor": round(label.factor, 2),
            "out_start": round(label.start + offset(label.start), 2),
            "out_end": round(label.end + offset(label.start), 2),
            "label": label.text,
        }
        for label in part.plan.labels
    ]


def render(workdir: Path, out: Path, allow_unchecked: bool = False) -> dict[str, Any]:
    """The film as an mp4 at ``out``, with ``render.json`` beside it. When sample A's live search
    found no notice, the replay in ``workdir/cutaway`` is spliced in after the ``CUTAWAY_AFTER``
    beat."""
    cutaway_dir = workdir / "cutaway"
    outcome = json.loads((workdir / "events.json").read_text("utf-8"))["outcome"]
    spliced = needs_cutaway(outcome)
    if spliced and not (cutaway_dir / "events.json").exists():
        raise Refused(
            "sample A's live search found no notice: capture the replay cutaway "
            f"(--part cutaway --provider replay --workdir {cutaway_dir}) to splice in"
        )
    if spliced:
        parts = workdir / "parts"
        film = render_part(workdir, parts / "film.mp4", allow_unchecked, PART_ENCODE, True)
        cut = render_part(cutaway_dir, parts / "cutaway.mp4", allow_unchecked, PART_ENCODE)
        at = next(b.out_end for b in film.plan.beats if b.key == CUTAWAY_AFTER)
        planned = film.plan.duration + cut.plan.duration
        if planned >= timeline.CEILING:
            raise Refused(f"the plan runs {planned:.1f} s, not under {timeline.CEILING:.0f} s")
        out.parent.mkdir(parents=True, exist_ok=True)
        join(parts / "film.mp4", parts / "cutaway.mp4", at, out, planned)
    else:
        film = render_part(workdir, out, allow_unchecked, FINAL_ENCODE, True)
        cut, at, planned = None, film.plan.duration, film.plan.duration
        if planned >= timeline.CEILING:
            raise Refused(f"the plan runs {planned:.1f} s, not under {timeline.CEILING:.0f} s")

    found = probe(out)
    streams = found["streams"]
    duration = float(found["format"]["duration"])
    video = next((s for s in streams if s["codec_type"] == "video"), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)
    frames = video_frames(out)
    problems = []
    if duration >= timeline.CEILING:
        problems.append(f"it runs {duration:.2f} s, not under {timeline.CEILING:.0f} s")
    if audio is None:
        problems.append("it has no audio stream")
    if not video or (video["width"], video["height"]) != (SIZE["width"], SIZE["height"]):
        problems.append("its video is not 1280x720")
    if abs(frames - round(planned * timeline.FPS)) > 1:
        problems.append(f"it has {frames} frames, the plan {round(planned * timeline.FPS)}")
    if problems:
        raise Refused(f"{out}: " + "; ".join(problems))

    gap = cut.plan.duration if cut else 0.0

    def later(start: float) -> float:
        return gap if start >= at - 1e-6 else 0.0

    def within(_: float) -> float:
        return at

    events = film.events
    calls = events.get("calls", [])
    rows = _beat_rows(film, later)
    speedups = _speedups(film, later)
    if cut:
        rows += _beat_rows(cut, within)
        speedups += _speedups(cut, within)
    report = {
        "file": out.name,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "duration": round(duration, 3),
        "planned_duration": round(planned, 3),
        "frames": frames,
        "video": {
            k: video.get(k) for k in ("codec_name", "width", "height", "r_frame_rate", "pix_fmt")
        },
        "audio": {k: audio.get(k) for k in ("codec_name", "sample_rate", "channels")},
        "provider": events["provider"],
        "outcome": events["outcome"],
        "sample_b": events.get("sample_b", "clean"),
        "recorded_at": events["recorded_at"],
        "bind": events["bind"],
        "searches_spent": sum(1 for c in calls if c.get("provider") == "live"),
        "searches_replayed": sum(1 for c in calls if c.get("provider") == "replay"),
        "calls": calls,
        "account_before": events.get("account_before"),
        "account_after": events.get("account_after"),
        "cutaway": None
        if not cut
        else {
            "provider": cut.events["provider"],
            "recorded_at": cut.events["recorded_at"],
            "bind": cut.events["bind"],
            "after": CUTAWAY_AFTER,
            "out_start": round(at, 2),
            "out_end": round(at + gap, 2),
            "searches_replayed": len(cut.events["calls"]),
            "calls": cut.events["calls"],
        },
        "beats": sorted(rows, key=lambda r: r["out_start"]),
        "speedups": sorted(speedups, key=lambda r: r["out_start"]),
        "cuts": {
            "what": "stretches where the screen did not change and nobody was speaking",
            "seconds": round(sum(c.seconds for c in film.plan.cuts), 2),
            "capture": [
                {"start": round(c.start, 2), "seconds": round(c.seconds, 2)}
                for c in film.plan.cuts
            ],
        },
        "expects": events["expects"] + (cut.events["expects"] if cut else []),
        "checks": events["checks"],
        "failed": film.failed + (cut.failed if cut else []),
        "unchecked": film.unchecked + (cut.unchecked if cut else []),
    }
    report["unchecked_allowed"] = bool(report["unchecked"])
    report_path = out.parent / "render.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), "utf-8")
    print(f"record_demo: {out} · {duration:.2f} s · sha256 {report['sha256']}")
    for b in report["beats"]:
        print(f"  {b['out_start']:7.2f}–{b['out_end']:7.2f}  {b['beat']}")
    return report


# ---- the command line ---------------------------------------------------------------------


def _path(text: str) -> Path:
    """Absolute, since the page opens the saved board as a ``file:`` address."""
    return Path(text).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo.record_demo", description=__doc__.split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "render", "all"):
        command = commands.add_parser(name)
        command.add_argument("--workdir", type=_path, default=DEFAULT_WORKDIR)
        if name != "render":
            command.add_argument("--provider", choices=("live", "replay"), required=True)
            command.add_argument("--max-usage", type=int, default=DEFAULT_MAX_USAGE)
        if name == "capture":
            command.add_argument("--part", choices=PARTS, default="film")
        if name != "capture":
            command.add_argument("--out", type=_path, default=DEFAULT_OUT)
            command.add_argument("--allow-unchecked", action="store_true")
    args = parser.parse_args(argv)
    steps: list[Callable[[], Any]] = []
    if args.command == "capture":
        steps.append(lambda: capture(args.provider, args.workdir, args.max_usage, args.part))
    if args.command == "all":
        steps.append(lambda: capture(args.provider, args.workdir, args.max_usage))
        if args.provider == "live":
            steps.append(lambda: cutaway_if_needed(args.workdir, args.max_usage))
    if args.command in ("render", "all"):
        steps.append(lambda: render(args.workdir, args.out, args.allow_unchecked))
    try:
        for step in steps:
            step()
    except Refused as refusal:
        print(f"record_demo: {refusal}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
