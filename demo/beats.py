"""The demo video's beats: what each one says on screen and aloud, and what the page must show
for that to be true.

The narration and the caption say only what the screen shows. Each beat's ``expects`` names the
text the page has to carry, at the end of the beat, for its words to hold: the recorder checks
every one against the page it filmed and writes the result into ``events.json``, and the render
refuses a take where one failed, so a live search that found something else can't be narrated
as if it found what the recordings did. A replay take differs only where the words would be
false for it: it names the recorded responses instead of live SerpApi, and in case A says what
they found, which the filmed live search did not. The film also carries one replay cutaway
(``CUTAWAY``), spliced in after ``CUTAWAY_AFTER``, so the decisive path the recordings hold
is on screen next to what the live search found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROVIDERS = ("live", "replay")
PARTS = ("film", "cutaway")
REPO_URL = "https://github.com/guptachetan1995/offer-checkpost"
TRACK = "Knowledge & Public Interest"
HACKATHON = "SerpApi India Hackathon 2026"
RECORDED = "29 Sep 2026"
CUTAWAY_AFTER = "a-publish"


@dataclass(frozen=True)
class Beat:
    """One beat of the video. ``expects`` is ``(selector, text)`` pairs: the page's element at
    ``selector`` contains ``text``, compared case-insensitively. ``delay`` holds the narration
    back, in seconds, for a beat whose screen takes that long to reach what the first words say.
    ``replay`` overrides any of ``caption``, ``narration``, ``expects`` and ``delay`` for a
    replay take."""

    key: str
    caption: str
    narration: str
    expects: tuple[tuple[str, str], ...] = ()
    replay: dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0

    def resolved(self, provider: str) -> Beat:
        if provider not in PROVIDERS:
            raise ValueError(f"provider is one of {', '.join(PROVIDERS)}, not {provider!r}")
        if provider == "live" or not self.replay:
            return self
        return Beat(self.key, **{**_fields(self), **self.replay})


def _fields(beat: Beat) -> dict[str, Any]:
    return {
        "caption": beat.caption,
        "narration": beat.narration,
        "expects": beat.expects,
        "delay": beat.delay,
    }


BEATS: tuple[Beat, ...] = (
    Beat(
        "intro",
        "Running locally, on live SerpApi.",
        "Offer Checkpost, running locally, at local host, on live SerpApi. The header shows the "
        "searches left this month; SerpApi's count can trail the call log.",
        (("#provider", "Live SerpApi"), ("#budget", "searches left this month")),
        replay={
            "caption": f"Running locally, on SerpApi responses recorded {RECORDED} (replay).",
            "narration": "Offer Checkpost, running on this machine, at local host. This take "
            "replays SerpApi responses recorded on the 29th of September, and says so.",
            "expects": (("#provider", "Replay"), ("#replay", "not live")),
        },
    ),
    Beat(
        "a-claims",
        "The message alone proves nothing: verdict unverified.",
        "A placement officer opens a forwarded offer in HCLTech's name: data entry from home, "
        "and a refundable registration fee. Each claim becomes a chip, and the officer confirms "
        "them. The message alone proves nothing: unverified.",
        (
            ("#claims", "HCLTech"),
            ("#claims", "refundable registration fee"),
            ("#claims", "The message alone proves nothing"),
        ),
    ),
    Beat(
        "a-investigate",
        "Search 2 ran because search 1 named the employer's domain.",
        "Investigate. Search one finds HCLTech's official domain, and flags the sender's as a "
        "look-alike. Because a fee was asked, search two looks on HCLTech's own site for a "
        "fraud notice. Live today, that search finds none, so the planner keeps checking until "
        "its six searches run out. The message stays unverified.",
        (
            ("#trace", "Look-alike sender domain"),
            ("#trace", "A fee was asked and step 1 named the official domain"),
            ("#trace", "no recruitment-fraud notice on hcltech.com"),
            (".budget-line", "6 searches spent of 6"),
            ("#verdict .band-title", "Unverified"),
        ),
        delay=1.5,
        # The recorded responses carry HCLTech's notice; the filmed live take's site: search,
        # on 29 Sep 2026, found none.
        replay={
            "narration": "Investigate. Search one finds HCLTech's official domain, and flags the "
            "sender's as a look-alike. Because a fee was asked, and the domain is known, search "
            "two looks on HCLTech's own site, and finds its warning: it never asks for "
            "recruitment fees. Decisive, so four searches are never spent. High risk.",
            "expects": (
                ("#trace", "Look-alike sender domain"),
                ("#trace", "A fee was asked and step 1 named the official domain"),
                (".budget-line", "4 not spent"),
                ("#evidence", "never ask for recruitment fees"),
                ("#verdict .band-title", "High risk"),
            ),
        },
    ),
    Beat(
        "a-publish",
        "Only a person publishes a verdict.",
        "Only a person publishes. The draft stays unverified, but the officer chooses to warn "
        "students: likely impersonation, on the Offer Board, with a copy for WhatsApp.",
        (
            ("#verdict .band-title", "Unverified"),
            ("#board", "Likely impersonation"),
            ("#board", "Copy for WhatsApp"),
        ),
        # The recorded responses make the draft High risk, so there is no gap to speak of.
        replay={
            "narration": "Only a person publishes. The officer labels it likely impersonation, "
            "and posts it to the Offer Board, with a copy for WhatsApp.",
            "expects": (("#board", "Likely impersonation"), ("#board", "Copy for WhatsApp")),
        },
    ),
    Beat(
        "c-investigate",
        "No fee asked — the searches still find what doesn't add up.",
        "Sample C: an invented firm in Indore. 42,000 rupees a month for freshers, a "
        "Telegram-only interview, Aadhaar and bank photos up front, and no fee. No web "
        "footprint, so the planner checks Maps for an office before job listings. None. High "
        "risk.",
        (
            ("#claims", "Indore"),
            ("#trace", "No web footprint"),
            ("#trace", "Office not on Maps"),
            ("#verdict .band-title", "High risk"),
        ),
        delay=3.0,
    ),
    Beat(
        "c-remaining",
        "Only a person spends searches after a decisive result.",
        "Only a person spends searches past a decisive result. Google Jobs: no listing by this "
        "firm, and pay far above comparable listings nearby. Then a drafted reply asks the "
        "recruiter one question per red flag.",
        (
            ("#trace", "No matching listing"),
            ("#trace", "Pay far above comparable listings"),
            ("#draft-reply", "nothing is sent or filed"),
        ),
        delay=1.5,
    ),
    Beat(
        "b-investigate",
        "It never says 'genuine'. Its best is 'nothing contradicts this'.",
        "Sample B, a real Siemens opening in Bengaluru, links only to Siemens's own careers "
        "site and asks no fee, so the fraud-notice search is skipped. Jobs finds Siemens's "
        "listing on siemens dot com, Maps finds the office, and News, no fake-offer reports. "
        "Its best verdict: nothing found contradicts the offer. Never genuine.",
        (
            ("#trace", "no recruiter email or link is off siemens.com"),
            ("#trace", "Listing applies on the official domain"),
            ("#trace", "Office found on Maps"),
            ("#trace", "no news reports"),
            ("#verdict .band-title", "Nothing found contradicts the offer"),
        ),
        delay=4.0,
    ),
    Beat(
        "a-report",
        "Drafts only. Nothing is ever sent or filed.",
        "Back on case A: a drafted summary for the 1930 cybercrime helpline. Drafts only: "
        "nothing is sent or filed. And the Offer Board downloads as one page.",
        (("#draft-report", "1930"), ("#draft-report", "nothing is sent or filed")),
        delay=2.0,
    ),
    Beat(
        "mcp-refused",
        "An MCP client is the agent: publishing is refused.",
        "Now an agent: a scripted MCP client, no language model, calls publish verdict. It "
        "isn't in the agent's tool list; the app refuses it, and logs the refusal as the "
        "agent's.",
        (
            ("#log .log-row:last-child", "agent"),
            ("#log .log-row:last-child", "publish_verdict"),
            ("#log .log-row:last-child", "refused"),
        ),
    ),
    Beat(
        "end",
        "",
        "Offer Checkpost. Knowledge and Public Interest track. The code is on GitHub.",
    ),
)


CUTAWAY: tuple[Beat, ...] = (
    Beat(
        "replay-a",
        f"Replay of SerpApi responses recorded {RECORDED}: the same message.",
        "Replay, not live: the same message against responses recorded on the 29th of "
        "September, when HCLTech's notice was in the results. Search two finds it: high risk, "
        "and four searches never spent.",
        (
            ("#provider", "Replay"),
            ("#replay", "not live"),
            ("#trace", "A fee was asked and step 1 named the official domain"),
            (".budget-line", "4 not spent"),
            ("#evidence", "never ask for recruitment fees"),
            ("#verdict .band-title", "High risk"),
        ),
        delay=1.4,
    ),
)


def beats(provider: str, part: str = "film") -> tuple[Beat, ...]:
    if part not in PARTS:
        raise ValueError(f"the part is one of {', '.join(PARTS)}, not {part!r}")
    return tuple(beat.resolved(provider) for beat in (BEATS, CUTAWAY)[PARTS.index(part)])


def judge(
    logged: list[dict[str, Any]], plan_beats: tuple[Beat, ...]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What a capture's logged checks mean for the words the video says now: ``(failed,
    unchecked)``. A failed check counts only while a beat still claims its text, so narration
    rewritten after the capture to what the page did show no longer trips it; a claim the
    capture never checked is ``unchecked``, for a person to confirm against the footage."""
    seen = {(e["beat"], e["selector"], e["text"]): e["ok"] for e in logged}
    claims = [(b.key, selector, text) for b in plan_beats for selector, text in b.expects]
    failed = [
        {"beat": k, "selector": s, "text": t, "ok": False}
        for k, s, t in claims
        if seen.get((k, s, t)) is False
    ]
    unchecked = [
        {"beat": k, "selector": s, "text": t} for k, s, t in claims if (k, s, t) not in seen
    ]
    return failed, unchecked
