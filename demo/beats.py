"""The demo video's beats: what each one says on screen and aloud, and what the page must show
for that to be true.

The narration and the captions say only what the screen shows. A beat's narration is a list of
sentences; the recorder starts each as the page reaches what it says. A beat's captions are
short lines the recorder puts on screen one at a time, each at the moment of the on-screen
change it describes (a ring, a scroll to the finding), each replaced by the next sentence's
caption or by the next beat: none is up before what it describes or left over the line it
describes. Each beat's ``expects`` names the text the page has to carry, at the end of the
beat, for its words to hold: the recorder checks every one against the page it filmed and
writes the result into ``events.json``, and the render refuses a take where one failed, so a
live search that found something else can't be narrated as if it found what the recordings did.

Sample A's live search is the one result the film can't know beforehand: does HCLTech's own
fraud notice come back, and after how many searches? The words say what happened, decided from
the capture's events (``OUTCOMES``):

- ``decisive``: search two quotes the notice and the case stops at two searches.
- ``retried``: search two returned no page from HCLTech's domain, so it says nothing about a
  notice; the planner retried once with the notice's usual titles, and search three found it.
  The case stops decisive at three searches.
- ``unverified``: search two came back with pages but no notice to quote; the case runs out its
  budget.
- ``unverified_retried``: search two returned nothing from the domain, the retry found no
  notice either, and the case runs out its budget.

Sample B's live result is the other one the film can't know beforehand: whether Google Jobs
returns Siemens's own listing for the role (``clean``: nothing found contradicts the offer, the
best verdict there is) or none (``unverified``: one red signal, no matching listing, against the
green office on Maps, so the band stays unverified). Its words follow the capture too
(``SAMPLE_B``).

An unverified film carries one replay cutaway (``CUTAWAY``), spliced in after ``CUTAWAY_AFTER``,
so the decisive path the recordings hold is on screen, labelled a replay and dated, next to what
the live search found. A replay take is always decisive, and differs only where its words would
be false for it: it names the recorded responses instead of live SerpApi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROVIDERS = ("live", "replay")
PARTS = ("film", "cutaway")
OUTCOMES = ("decisive", "retried", "unverified", "unverified_retried")
UNVERIFIED = ("unverified", "unverified_retried")
SAMPLE_B = ("clean", "unverified")
# The variants a beat can override, in the order they apply to an outcome: an unretried
# ``unverified`` take's words also hold for the ``unverified_retried`` one unless it says more.
OVERRIDES = {
    "decisive": (),
    "retried": ("retried",),
    "unverified": ("unverified",),
    "unverified_retried": ("unverified", "unverified_retried"),
}
B_OVERRIDE = {"clean": (), "unverified": ("b_unverified",)}
REPO_URL = "https://github.com/guptachetan1995/offer-checkpost"
TRACK = "Knowledge & Public Interest"
HACKATHON = "SerpApi India Hackathon 2026"
RECORDED = "29 and 30 Sep 2026"
RECORDED_A = "30 Sep 2026"
CUTAWAY_AFTER = "a-publish"
MAX_CAPTION = 72

# The three differences the opening names, one caption each over the real app.
DIFFERENCES = (
    "A forwarded copy of a checked offer costs 0 searches",
    "The evidence is the employer's own fraud notice",
    "Only a person publishes; an agent provably cannot",
)


@dataclass(frozen=True)
class Beat:
    """One beat of the video. ``expects`` is ``(selector, text)`` pairs: the page's element at
    ``selector`` contains ``text``, compared case-insensitively. ``captions`` are the beat's
    caption slots: the recorder puts slot ``i`` on screen when the page reaches what it says,
    and every variant of a beat has the same slots. ``dropped`` is how many of the last
    sentences the video leaves out, with the footage from the moment the page reached them: the
    recorder films the whole beat, and the render keeps to the three-minute limit, so
    ``expects`` names only what the kept sentences say. ``variants`` maps ``replay``, each
    outcome of sample A but ``decisive`` and ``b_unverified`` to the fields (``captions``,
    ``narration``, ``expects``, ``dropped``) it overrides for a replay take, for a live take
    where sample A's search went that way and for one where sample B's ended unverified."""

    key: str
    captions: tuple[str, ...]
    narration: tuple[str, ...]
    expects: tuple[tuple[str, str], ...] = ()
    dropped: int = 0
    variants: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return " ".join(self.narration)

    def resolved(self, provider: str, outcome: str = "decisive", sample_b: str = "clean") -> Beat:
        if provider not in PROVIDERS:
            raise ValueError(f"provider is one of {', '.join(PROVIDERS)}, not {provider!r}")
        if outcome not in OUTCOMES:
            raise ValueError(f"the outcome is one of {', '.join(OUTCOMES)}, not {outcome!r}")
        if sample_b not in SAMPLE_B:
            raise ValueError(f"sample B is one of {', '.join(SAMPLE_B)}, not {sample_b!r}")
        if provider == "replay" and (outcome, sample_b) != ("decisive", "clean"):
            raise ValueError("the recorded responses always find the notice at the first search")
        names = (
            ("replay",) if provider == "replay" else (*OVERRIDES[outcome], *B_OVERRIDE[sample_b])
        )
        override: dict[str, Any] = {}
        for name in names:
            override |= self.variants.get(name, {})
        return Beat(self.key, **{**_fields(self), **override}) if override else self


def _fields(beat: Beat) -> dict[str, Any]:
    return {
        "captions": beat.captions,
        "narration": beat.narration,
        "expects": beat.expects,
        "dropped": beat.dropped,
    }


NOTICE_FOUND = ("#evidence", "never ask for any payment")
LOOKALIKE = ("#trace", "Look-alike sender domain")
STEP_TWO = ("#trace", "A fee was asked and step 1 named the official domain")
SEARCH_ONE = (
    "Investigate. Search one finds HCLTech's official domain, and flags the sender's as a "
    "look-alike."
)
NO_PAGE = "and gets no page from hcltech.com."
RETRY_CAPTION = "Search 3 retried with the notice's usual titles."
INCONCLUSIVE_CAPTION = "Search 2 is inconclusive: no page came from hcltech.com."

BEATS: tuple[Beat, ...] = (
    Beat(
        "intro",
        (
            "Running locally, on live SerpApi. Its searches-left count can lag.",
            *(f"{i} · {text}" for i, text in enumerate(DIFFERENCES, 1)),
        ),
        (
            "Offer Checkpost checks a job offer before anyone pays.",
            "Three differences. A forwarded copy of a checked offer costs zero searches.",
            "The evidence is the employer's own fraud notice, if it has one.",
            "Only a person publishes; an agent provably cannot.",
        ),
        (("#provider", "Live SerpApi"), ("#budget", "searches left this month")),
        variants={
            "replay": {
                "captions": (
                    f"Local app, on SerpApi responses recorded {RECORDED} (replay).",
                    *(f"{i} · {text}" for i, text in enumerate(DIFFERENCES, 1)),
                ),
                "narration": (
                    "Offer Checkpost checks a job offer before anyone pays; this take replays "
                    "recorded SerpApi responses, and says so.",
                    "Three differences. A forwarded copy of a checked offer costs zero searches.",
                    "The evidence is the employer's own fraud notice, if it has one.",
                    "Only a person publishes; an agent provably cannot.",
                ),
                "expects": (("#provider", "Replay"), ("#replay", "not live")),
            },
        },
    ),
    Beat(
        "a-claims",
        ("The message alone proves nothing: verdict unverified.",),
        (
            "A forwarded HCLTech offer, with a refundable registration fee.",
            "The officer confirms each claim.",
            "The message alone proves nothing: unverified.",
        ),
        (
            ("#claims", "HCLTech"),
            ("#claims", "refundable registration fee"),
            ("#claims", "The message alone proves nothing"),
        ),
    ),
    Beat(
        "a-investigate",
        (
            "Search 1 flagged the sender's domain as a look-alike.",
            "Search 2 ran because search 1 named the employer's domain.",
            "The employer's own notice says it never asks for any payment.",
            "Decisive: 4 searches never spent.",
        ),
        (
            SEARCH_ONE,
            "A fee was asked, so search two reads HCLTech's own site: it never asks for any "
            "payment.",
            "Decisive: four searches never spent. High risk.",
        ),
        (
            LOOKALIKE,
            STEP_TWO,
            (".budget-line", "4 not spent"),
            NOTICE_FOUND,
            ("#verdict .band-title", "High risk"),
        ),
        variants={
            "retried": {
                "captions": (
                    "Search 1 flagged the sender's domain as a look-alike.",
                    INCONCLUSIVE_CAPTION,
                    RETRY_CAPTION,
                    "Decisive after 3 searches: 3 never spent.",
                ),
                "narration": (
                    SEARCH_ONE,
                    f"A fee was asked, so search two looks on HCLTech's site for a fraud "
                    f"notice, {NO_PAGE}",
                    "So search three retries with the notice's usual titles, and finds it: "
                    "decisive after three searches, three never spent. High risk.",
                ),
                "expects": (
                    LOOKALIKE,
                    STEP_TWO,
                    ("#trace", "Inconclusive"),
                    ("#trace", "R3, inconclusive notice search"),
                    ("#trace", "no page from hcltech.com"),
                    (".budget-line", "3 not spent"),
                    ("#verdict .band-title", "High risk"),
                ),
            },
            "unverified": {
                "captions": (
                    "Search 1 flagged the sender's domain as a look-alike.",
                    "Search 2 ran because search 1 named the employer's domain.",
                    "Search 2 came back with no fraud notice to quote.",
                    "6 of 6 searches spent: still unverified.",
                ),
                "narration": (
                    SEARCH_ONE,
                    "A fee was asked, so search two looks on HCLTech's own site for a fraud "
                    "notice. Today it finds no notice to quote.",
                    "The planner keeps checking until its six searches run out: still unverified.",
                ),
                "expects": (
                    LOOKALIKE,
                    STEP_TWO,
                    ("#trace", "from hcltech.com"),
                    (".budget-line", "6 searches spent of 6"),
                    ("#verdict .band-title", "Unverified"),
                ),
            },
            "unverified_retried": {
                "captions": (
                    "Search 1 flagged the sender's domain as a look-alike.",
                    INCONCLUSIVE_CAPTION,
                    "Search 3 retried, and still found no notice to quote.",
                    "6 of 6 searches spent: still unverified.",
                ),
                "narration": (
                    SEARCH_ONE,
                    f"A fee was asked, so search two looks on HCLTech's site for a fraud "
                    f"notice, {NO_PAGE}",
                    "Search three retries with the notice's usual titles: still no notice to "
                    "quote. The planner runs its six searches out: unverified.",
                ),
                "expects": (
                    LOOKALIKE,
                    STEP_TWO,
                    ("#trace", "Inconclusive"),
                    ("#trace", "R3, inconclusive notice search"),
                    (".budget-line", "6 searches spent of 6"),
                    ("#verdict .band-title", "Unverified"),
                ),
            },
        },
    ),
    Beat(
        "a-publish",
        (
            "Only a person publishes a verdict.",
            "The officer posted it, with a copy for WhatsApp.",
        ),
        (
            "Only a person publishes.",
            "The officer labels it likely impersonation and posts it to the Offer Board, with a "
            "copy for WhatsApp.",
        ),
        (("#board", "Likely impersonation"), ("#board", "Copy for WhatsApp")),
        variants={
            "unverified": {
                "narration": (
                    "Only a person publishes. The draft stays unverified, but the officer "
                    "chooses to warn students.",
                    "Likely impersonation, on the Offer Board, with a copy for WhatsApp.",
                ),
                "expects": (
                    ("#verdict .band-title", "Unverified"),
                    ("#board", "Likely impersonation"),
                    ("#board", "Copy for WhatsApp"),
                ),
            },
        },
    ),
    Beat(
        "a-forwarded",
        (
            "The app notes it is the same message as case 1.",
            "0 searches: the call log does not grow.",
            "The trace says reused, 0 searches.",
        ),
        (
            "The same offer, forwarded by another student, pasted as a new case.",
            "Investigate: zero searches. It reuses the first check, and the call log does not "
            "grow.",
            "The trace says reused: same verdict, high risk.",
        ),
        (
            (".case-meta", "same message as"),
            ("#trace", "reused, 0 searches"),
            (".budget-line", "0 searches spent"),
            ("#strip-count", "2 calls"),
            ("#verdict .band-title", "High risk"),
        ),
        variants={
            "retried": {
                "expects": (
                    (".case-meta", "same message as"),
                    ("#trace", "reused, 0 searches"),
                    (".budget-line", "0 searches spent"),
                    ("#strip-count", "3 calls"),
                    ("#verdict .band-title", "High risk"),
                ),
            },
            "unverified": {
                "narration": (
                    "The same offer, forwarded by another student, pasted as a new case.",
                    "Investigate: zero searches. It reuses the first check; the call log stays "
                    "at six, and the header's count is still catching up.",
                    "The trace says reused: same verdict, unverified.",
                ),
                "expects": (
                    (".case-meta", "same message as"),
                    ("#trace", "reused, 0 searches"),
                    (".budget-line", "0 searches spent"),
                    ("#strip-count", "6 calls"),
                    ("#verdict .band-title", "Unverified"),
                ),
            },
        },
    ),
    Beat(
        "c-investigate",
        (
            "Sample C: no fee asked, an invented firm in Indore.",
            "No fee asked, yet the searches still find what doesn't add up.",
        ),
        (
            "Sample C: an invented firm in Indore. 42,000 rupees a month for freshers, and no "
            "fee.",
            "The flags: documents asked for before any interview, and a chat-only interview. "
            "No web footprint, so the planner checks Maps for an office first. None. High risk.",
        ),
        (
            ("#claims", "Indore"),
            ("#trace", "No web footprint"),
            ("#trace", "Office not on Maps"),
            ("#verdict .band-title", "High risk"),
        ),
    ),
    Beat(
        "c-remaining",
        (
            "Only a person spends searches after a decisive result.",
            "Google Jobs: no listing by this firm.",
            "Pay far above comparable listings.",
        ),
        (
            "Only a person spends searches past a decisive result.",
            "Google Jobs: no listing by this firm, and pay far above comparable listings.",
            "Then a drafted reply asks the recruiter one question per red flag.",
        ),
        (
            ("#trace", "No matching listing"),
            ("#trace", "Pay far above comparable listings"),
        ),
        dropped=1,
    ),
    Beat(
        "b-investigate",
        (
            "The fraud-notice search is skipped: nothing to contradict.",
            "Siemens's own listing, its office on Maps, no fake-offer news.",
            "It never says 'genuine'. Its best is 'nothing contradicts this'.",
        ),
        (
            "Sample B, a real Siemens opening in Bengaluru: links only to Siemens, and no fee.",
            "Investigate. The fraud-notice search is skipped: nothing to contradict.",
            "Jobs finds Siemens's own listing, Maps the office, and News no fake-offer reports.",
            "Its best verdict: nothing found contradicts the offer. Never genuine.",
        ),
        (
            ("#trace", "no recruiter email or link is off siemens.com"),
            ("#trace", "Listing applies on the official domain"),
            ("#trace", "Office found on Maps"),
            ("#trace", "no news reports"),
            ("#verdict .band-title", "Nothing found contradicts the offer"),
        ),
        variants={
            "b_unverified": {
                "captions": (
                    "The fraud-notice search is skipped: nothing to contradict.",
                    "Jobs found no Siemens listing today; Maps found the office.",
                    "Unverified: one red signal and one green are not enough.",
                ),
                "narration": (
                    "Sample B, a real Siemens opening in Bengaluru: links only to Siemens, and "
                    "no fee.",
                    "Investigate. The fraud-notice search is skipped: nothing to contradict.",
                    "Jobs finds no listing by Siemens for this role today, and Maps finds the "
                    "office.",
                    "The result: unverified. One red signal and one green are not enough to "
                    "move the band.",
                ),
                "expects": (
                    ("#trace", "no recruiter email or link is off siemens.com"),
                    ("#trace", "no listing by Siemens for this role"),
                    ("#trace", "Office found on Maps"),
                    ("#verdict .band-title", "Unverified"),
                ),
            },
        },
    ),
    Beat(
        "a-report",
        ("Drafts only. Nothing is ever sent or filed.",),
        (
            "Back on case A: a drafted summary for the 1930 cybercrime helpline. Nothing is "
            "sent or filed.",
            "And the Offer Board downloads as one page.",
        ),
        (("#draft-report", "1930"), ("#draft-report", "nothing is sent or filed")),
        dropped=1,
    ),
    Beat(
        "mcp-refused",
        (
            "A scripted MCP client, no language model, is the agent.",
            "The app refuses publish_verdict and logs it as the agent's.",
        ),
        (
            "Now an agent: a scripted MCP client, no language model, calls publish verdict.",
            "It isn't in the agent's tools. The app refuses it, and logs it as the agent's.",
        ),
        (
            ("#log .log-row:last-child", "agent"),
            ("#log .log-row:last-child", "publish_verdict"),
            ("#log .log-row:last-child", "refused"),
        ),
    ),
    Beat(
        "end",
        (),
        ("Offer Checkpost. Knowledge and Public Interest. Code on GitHub.",),
    ),
)


CUTAWAY: tuple[Beat, ...] = (
    Beat(
        "replay-a",
        (
            f"Replay of responses recorded earlier on {RECORDED_A}; live results move.",
            "Replay: search 2 finds HCLTech's notice, 4 searches never spent.",
        ),
        (
            "Replay, not live: the same offer, on responses recorded earlier on 30 September; "
            "live results move.",
            "Search two finds HCLTech's own notice: high risk, four searches never spent.",
        ),
        (
            ("#provider", "Replay"),
            ("#replay", "not live"),
            STEP_TWO,
            (".budget-line", "4 not spent"),
            NOTICE_FOUND,
            ("#verdict .band-title", "High risk"),
        ),
    ),
)


def beats(
    provider: str, part: str = "film", outcome: str = "decisive", sample_b: str = "clean"
) -> tuple[Beat, ...]:
    if part not in PARTS:
        raise ValueError(f"the part is one of {', '.join(PARTS)}, not {part!r}")
    return tuple(
        beat.resolved(provider, outcome, sample_b) for beat in (BEATS, CUTAWAY)[PARTS.index(part)]
    )


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
