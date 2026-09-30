"""Records the demo video: a real Playwright screen recording of the app on 127.0.0.1.

    python -m demo.record_demo capture --provider live|replay [--part film|cutaway]
        [--workdir DIR] [--max-usage N]
    python -m demo.record_demo render [--workdir DIR] [--out FILE] [--allow-unchecked]
    python -m demo.record_demo all --provider live|replay [--workdir DIR] [--out FILE]

``capture`` synthesizes each beat's narration with macOS ``say`` (the default system voice)
and measures it, starts ``python -m offer_checkpost serve`` on a free port, opens the
single-use address it prints in Google Chrome, headless, at 1280x720 with Playwright's
``record_video_dir``, and plays the beats in ``demo.beats`` as a person's clicks, holding each
on screen at least as long as its narration will last in the video. It injects each beat's
caption into the page, and writes ``capture.webm`` and ``events.json`` (beat starts, every
interval the page waited on searches, the call log, the Account API's counts before and after,
what the page showed against each beat's ``expects``) into the work directory. ``live`` runs
the app on live SerpApi with ``OFFER_CHECKPOST_NO_CACHE=1``, so every search is real and
counted; before the first search it reads the searches used this month from the page's header
(the free Account API) and refuses when the worst case would pass ``--max-usage``. ``replay``
spends none. The app reads its own key from ``.env``; the recorder never does. A take that
stops part-way leaves ``events.aborted.json`` (the searches it had spent) and
``capture.partial.webm``.

``--part cutaway`` records the replay cutaway instead of the film: sample A again on the
recorded responses, which hold HCLTech's fraud notice. ``all --provider live`` captures both,
the cutaway into ``<workdir>/cutaway``; ``render`` splices it in after the beat named by
``demo.beats.CUTAWAY_AFTER`` whenever that folder holds a capture.

``render`` turns the capture into the mp4 without the browser or the app: it re-synthesizes any
narration whose words changed, lays out the timeline (``demo.timeline``), draws each sped-up
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
    PARTS,
    RECORDED,
    REPO_URL,
    TRACK,
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
NOTE = "Do not pay the fee."
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


def synthesize(plan_beats: Sequence[Beat], folder: Path) -> dict[str, dict[str, Any]]:
    """Each narrated beat's clip, ``<key>.wav`` in ``folder``, spoken by ``say``; a clip whose
    words haven't changed is reused. Returns ``{key: {"text", "seconds", "path"}}``."""
    folder.mkdir(parents=True, exist_ok=True)
    clips = {}
    for beat in plan_beats:
        if not beat.narration:
            continue
        wav, words = folder / f"{beat.key}.wav", folder / f"{beat.key}.txt"
        if not (wav.exists() and words.exists() and words.read_text("utf-8") == beat.narration):
            aiff = folder / f"{beat.key}.aiff"
            subprocess.run(["say", "-o", str(aiff), beat.narration], check=True)
            ffmpeg("-i", str(aiff), "-ar", "48000", "-ac", "2", str(wav))
            aiff.unlink()
            words.write_text(beat.narration, "utf-8")
        clips[beat.key] = {"text": beat.narration, "seconds": probe_seconds(wav), "path": wav}
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
    """One capture: the page, the clock the video runs on, and the events logged against it."""

    def __init__(
        self,
        page: Any,
        app: App,
        workdir: Path,
        plan_beats: Sequence[Beat],
        narration: dict[str, dict[str, Any]],
    ):
        self.page, self.app, self.workdir = page, app, workdir
        self.beats = {b.key: b for b in plan_beats}
        self.narration = narration
        self.t0 = time.monotonic()
        self.marks: list[tuple[str, float]] = []
        self.waits: list[timeline.Wait] = []
        self.expects: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.caption = ""
        self.inflight = 0
        self.started = 0
        self.finished_at = 0.0
        page.on("request", self._request)
        page.on("requestfinished", self._settled)
        page.on("requestfailed", self._settled)

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
        self.set_caption(beat.caption)
        return beat

    def hold(self) -> None:
        """Holds the current beat until it lasts, in the video, as long as its narration."""
        key, start = self.marks[-1]
        spoken = self.narration.get(key, {}).get("seconds", 0.0)
        need = timeline.beat_minimum(spoken, self.beats[key].delay) if spoken else 1.5
        shown = timeline.output_elapsed(start, self.now(), self.waits)
        if need > shown:
            self.pause(need - shown)
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
        self.caption = text
        self.js("caption", text)

    def show(self, selector: str, dwell: float = 0.0) -> None:
        self.page.wait_for_selector(selector)
        self.js("show", selector)
        self.pause(0.9 + dwell)

    def center(self, selector: str, dwell: float = 0.0) -> None:
        self.page.wait_for_selector(selector)
        self.js("center", selector)
        self.pause(0.9 + dwell)

    def show_text(self, root: str, text: str, dwell: float = 0.0) -> None:
        if not self.js("showText", root, text):
            print(f"record_demo: {root} has no {text!r} to show", file=sys.stderr)
        self.pause(0.9 + dwell)

    def point(self, selector: str, dwell: float = 0.5) -> None:
        self.page.wait_for_selector(selector)
        if not self.js("visible", selector):
            self.js("center", selector)
            self.pause(0.9)
        self.js("point", selector)
        self.pause(dwell)

    def click(self, selector: str, dwell: float = 0.6) -> None:
        self.point(selector)
        self.page.click(selector)
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
        self.js("caption", self.caption)

    def top(self) -> None:
        self.page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
        self.pause(0.8)

    def open_sample(self, name: str) -> str:
        self.click("#sample", 0.2)
        self.page.select_option("#sample", name)
        self.pause(1.2)
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
    take.point("#bind", 1.4)
    take.point("#provider", 1.4)
    if app.provider == "live":
        take.point("#budget", 1.4)
    else:
        take.point("#replay", 1.4)
    take.point(".strip", 1.2)
    take.hold()

    take.beat("a-claims")
    a = facts["case_a"] = take.open_sample("a")
    take.point(".chips", 2.5)
    take.confirm(1.0)
    take.center(".claims .state.ok", 0.5)
    take.hold()

    take.beat("a-investigate")
    take.searching("[data-focus=investigate]")
    take.show("#trace", 4.5)
    take.show_text("#trace", "A fee was asked and step 1 named", 4.0)
    take.show_text("#trace", "not spent", 2.5)
    take.show("#evidence", 5.0)
    take.hold()

    take.beat("a-publish")
    take.show("#publish-h", 0.5)
    take.click("#pub-label", 0.2)
    page.select_option("#pub-label", "likely_impersonation")
    take.pause(0.8)
    take.click("#pub-note", 0.2)
    page.type("#pub-note", NOTE, delay=45)
    take.pause(0.4)
    take.click("#publish-btn")
    page.wait_for_selector(f"#post-{a}")
    take.show("#board", 0.5)
    take.point(f"[data-focus=wa-{a}]", 1.5)
    take.hold()

    take.beat("c-investigate")
    take.top()
    take.click("[data-focus=new-case]", 0.4)
    facts["case_c"] = take.open_sample("c")
    take.point(".chips", 1.8)
    take.confirm(0.5)
    take.searching("[data-focus=investigate]")
    take.show("#trace", 2.5)
    take.show_text("#trace", "Office not on Maps", 3.0)
    take.hold()

    take.beat("c-remaining")
    take.show("#remaining-h", 0.3)
    take.searching("[data-focus=remaining]")
    take.show_text("#trace", "No matching listing", 2.0)
    take.show_text("#trace", "Pay far above comparable listings", 3.0)
    take.show("#drafts-h", 0.3)
    take.click("[data-focus=draft-reply]", 0.3)
    take.center("#draft-reply", 3.0)
    take.hold()

    take.beat("b-investigate")
    take.top()
    take.click("[data-focus=new-case]", 0.4)
    facts["case_b"] = take.open_sample("b")
    take.pause(1.0)
    take.confirm(0.3)
    take.searching("[data-focus=investigate]")
    take.show_text("#trace", "No fee or sensitive documents were asked for", 2.5)
    take.show_text("#trace", "Listing applies on the official domain", 2.0)
    take.show_text("#trace", "Found: no news reports", 1.5)
    take.top()
    take.center("#verdict", 3.0)
    take.hold()

    take.beat("a-report")
    take.top()
    take.click(f"[data-focus=case-{a}]", 0.3)
    page.wait_for_selector(f"#case-h:text('{a}'), .case-meta:has-text('{a}')")
    take.show("#drafts-h", 0.3)
    take.click("[data-focus=draft-report]", 0.3)
    take.center("#draft-report", 3.5)
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
    take.pause(3.5)
    page.go_back()
    page.wait_for_selector("#bind:not(:text('…'))")
    take.js("caption", take.caption)
    # A reload opens on the newest case; the person goes back to the one they were on.
    take.click(f"[data-focus=case-{a}]", 0.3)
    take.hold()

    take.beat("mcp-refused")
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
    # The page reads the agent's call on its idle poll, every 2 s; the app's CSP rules out
    # wait_for_function, which evaluates a string.
    deadline = time.monotonic() + 10
    while page.locator("#log .log-row").count() <= shown:
        if time.monotonic() > deadline:
            raise Refused("the MCP client's call never showed in the page's activity log")
        take.pause(0.1)
    take.pause(0.6)
    take.center("#log .log-row:last-child", 0.3)
    take.point("#log .log-row:last-child", 1.5)
    take.hold()
    take.js("panel", "", [])

    facts["account_after"] = take.budget()
    facts["calls"] = app.get("/api/calls")["calls"]
    take.beat("end")
    take.js("card", end_card(app, facts))
    take.hold()
    return facts


def end_card(app: App, facts: dict[str, Any]) -> list[list[str]]:
    port = app.origin.rsplit(":", 1)[1]
    searches = len(facts["calls"])
    if app.provider == "live":
        before, after = facts["account_before"], facts.get("account_after") or {}
        run = (
            f"This take: {searches} live SerpApi searches, from 127.0.0.1:{port} · "
            f"Account API: {before['left']} → {after.get('left')} searches left"
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
    the decisive path the filmed live search didn't reach."""
    page, app = take.page, take.app
    take.goto(app.url)
    page.wait_for_selector("#bind:not(:text('…'))")
    page.wait_for_selector("#sample-wrap:not([hidden])")

    take.beat("replay-a")
    take.point("#provider", 0.5)
    take.point("#replay", 0.5)
    take.click("#sample", 0.2)
    page.select_option("#sample", "a")
    take.pause(0.5)
    take.click("#open-case", 0.2)
    page.wait_for_selector("[data-focus=confirm-claims]")
    case = app.get("/api/state")["cases"][-1]["id"]
    take.point(".chips", 0.4)
    take.confirm(0.2)
    take.searching("[data-focus=investigate]")
    take.show("#trace", 1.0)
    take.show_text("#trace", "not spent", 0.8)
    take.show("#evidence", 1.6)
    take.hold()
    return {"case_a": case, "calls": app.get("/api/calls")["calls"]}


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
        "waits": [vars(w) for w in take.waits],
        "calls": calls,
    }
    (take.workdir / "events.aborted.json").write_text(json.dumps(record, indent=2), "utf-8")


def capture(provider: str, workdir: Path, max_usage: int, part: str = "film") -> Path:
    from playwright.sync_api import sync_playwright

    if part == "cutaway" and provider != "replay":
        raise Refused("the cutaway is a replay of the recordings: run it with --provider replay")
    workdir.mkdir(parents=True, exist_ok=True)
    plan_beats = beats(provider, part)
    narration = synthesize(plan_beats, workdir / "narration")
    spoken = sum(n["seconds"] for n in narration.values())
    print(f"record_demo: narration {spoken:.1f} s over {len(narration)} beats")
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
            take = Take(page, app, workdir, plan_beats, narration)
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
    events = {
        "provider": provider,
        "part": part,
        "recorded_at": recorded_at,
        "bind": app.origin.removeprefix("http://"),
        "video": webm.name,
        "end": end,
        "marks": [{"beat": k, "t": t} for k, t in take.marks],
        "waits": [vars(w) for w in take.waits],
        "expects": take.expects,
        "checks": take.checks,
        "narration": {
            k: {"text": n["text"], "seconds": n["seconds"]} for k, n in narration.items()
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


def render_part(
    workdir: Path, out: Path, allow_unchecked: bool, crf_preset: tuple[str, str]
) -> Part:
    """One capture as an mp4: its narration laid over the timeline, its waits sped up under
    labels. Refuses a check that failed for a claim the beat still makes; a claim the capture
    never checked is refused unless ``allow_unchecked``."""
    events = json.loads((workdir / "events.json").read_text("utf-8"))
    plan_beats = beats(events["provider"], events.get("part", "film"))
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
    waits = [timeline.Wait(**w) for w in events["waits"]]
    marks = [(m["beat"], m["t"]) for m in events["marks"]]
    plan = timeline.plan(
        marks,
        events["end"],
        waits,
        {k: c["seconds"] for k, c in clips.items()},
        {b.key: b.delay for b in plan_beats},
    )
    images = label_images(plan.labels, workdir / "labels")

    spoken = [b for b in plan.beats if b.narration_seconds]
    inputs = ["-i", str(workdir / events["video"])]
    for image in images:
        inputs += ["-i", str(image)]
    for beat in spoken:
        inputs += ["-i", str(clips[beat.key]["path"])]
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
    return [
        {
            "beat": b.key,
            "out_start": round(b.out_start + offset(b.out_start), 2),
            "out_end": round(b.out_end + offset(b.out_start), 2),
            "capture_start": round(b.start, 2),
            "capture_end": round(b.end, 2),
            "freeze": round(b.freeze, 2),
            "narration_at": round(b.narration_at + offset(b.out_start), 2),
            "narration_seconds": round(b.narration_seconds, 2),
            "caption": texts[b.key].caption,
            "narration": texts[b.key].narration,
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
    """The film as an mp4 at ``out``, with ``render.json`` beside it. When ``workdir/cutaway``
    holds a capture, its replay is spliced in after the ``CUTAWAY_AFTER`` beat."""
    cutaway_dir = workdir / "cutaway"
    spliced = (cutaway_dir / "events.json").exists()
    if spliced:
        parts = workdir / "parts"
        film = render_part(workdir, parts / "film.mp4", allow_unchecked, PART_ENCODE)
        cut = render_part(cutaway_dir, parts / "cutaway.mp4", allow_unchecked, PART_ENCODE)
        at = next(b.out_end for b in film.plan.beats if b.key == CUTAWAY_AFTER)
        planned = film.plan.duration + cut.plan.duration
        if planned >= timeline.CEILING:
            raise Refused(f"the plan runs {planned:.1f} s, not under {timeline.CEILING:.0f} s")
        out.parent.mkdir(parents=True, exist_ok=True)
        join(parts / "film.mp4", parts / "cutaway.mp4", at, out, planned)
    else:
        film = render_part(workdir, out, allow_unchecked, FINAL_ENCODE)
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo.record_demo", description=__doc__.split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("capture", "render", "all"):
        command = commands.add_parser(name)
        command.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
        if name != "render":
            command.add_argument("--provider", choices=("live", "replay"), required=True)
            command.add_argument("--max-usage", type=int, default=DEFAULT_MAX_USAGE)
        if name == "capture":
            command.add_argument("--part", choices=PARTS, default="film")
        if name != "capture":
            command.add_argument("--out", type=Path, default=DEFAULT_OUT)
            command.add_argument("--allow-unchecked", action="store_true")
    args = parser.parse_args(argv)
    steps: list[Callable[[], Any]] = []
    if args.command == "capture":
        steps.append(lambda: capture(args.provider, args.workdir, args.max_usage, args.part))
    if args.command == "all":
        steps.append(lambda: capture(args.provider, args.workdir, args.max_usage))
        if args.provider == "live":
            steps.append(
                lambda: capture("replay", args.workdir / "cutaway", args.max_usage, "cutaway")
            )
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
