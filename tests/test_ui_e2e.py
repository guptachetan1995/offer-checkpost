"""The page in a real browser against the real server: Playwright drives Google Chrome through
paste, confirm, investigate and publish for sample A, on the synthetic fixtures.

Nothing else checks that the two halves fit: the page's own JavaScript, the Content-Security-
Policy it runs under, the clicks going out as ``human`` through ``invoke``, the planner's checks
coming back as ``agent``, and the call-log strip counting the same calls as the store. The test
skips, saying why, where Playwright or Chrome is missing, so a fresh clone still passes; it never
downloads a browser.

The page mirrors two tables it can't read from the server (the rule labels, which the draft's
"Also found" lines are matched on, and the Offer Board labels); the first tests hold them to the
server's, without a browser.

The later tests set most of their cases up through ``invoke`` as the person, as the page's own
clicks would, then load the page and check what it shows: what a correction makes out of date,
the labels a draft verdict allows, the call-log strip, the replay banner, focus on a narrow
window, and contrast."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from offer_checkpost import drafts
from offer_checkpost.invoke import invoke
from offer_checkpost.providers import FakeSearchProvider, ReplaySearchProvider, write_recording
from offer_checkpost.rules import RULES
from offer_checkpost.server import make_server
from offer_checkpost.store import Store

ENTRY = Path(__file__).resolve().parents[1]
WEB = ENTRY / "web"
SAMPLES = ENTRY / "samples" / "offers"
SAMPLE_A = SAMPLES / "a.txt"

# The clicks in the walk below, each a person's; the planner's checks inside investigate are
# the agent's.
CLICKS = {"search_budget", "open_case", "update_claims", "investigate", "draft_verdict"}
PLANNER_CHECKS = {"lookup_official_site", "find_fraud_notice"}


def js_table(name: str) -> dict[str, str]:
    """The ``const <name> = { key: 'text', ... };`` object literal in app.js."""
    source = (WEB / "app.js").read_text("utf-8")
    body = re.search(rf"^const {name} = \{{\n(.*?)^\}};", source, re.M | re.S)
    assert body, f"app.js has no {name} table"
    return {m[1]: m[3] for m in re.finditer(r"^\s*(\w+): (['\"])(.*)\2,$", body[1], re.M)}


def test_the_pages_rule_labels_are_the_rule_tables():
    assert js_table("RULE_LABELS") == {name: rule.label for name, rule in RULES.items()}


def test_the_pages_board_labels_are_the_drafts():
    assert js_table("BOARD_LABELS") == drafts.BOARD_LABELS


def test_the_page_names_nothing_outside_its_own_repository():
    # The monorepo's name is split so this file doesn't itself contain it.
    outside = re.compile(r"\bSPEC\b|entries/|hackathons" + r"-2026|\bbin/")
    for path in sorted(WEB.iterdir()):
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            assert not outside.search(line), f"{path.name}:{n}: {line.strip()}"


# ---- the browser walk ---------------------------------------------------------------------


@contextmanager
def serving(store):
    server = make_server(store, port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield f"http://{store.server['bind']}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def app():
    store = Store(FakeSearchProvider.from_fixtures())
    with serving(store) as origin:
        yield store, origin


@pytest.fixture(scope="module")
def chrome():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("Playwright is not installed: make setup installs it from the dev lock")
    # Any failure to start is a reason to skip, not to fail: a machine without Chrome, or
    # where Playwright's driver can't run, can still run every other test.
    try:
        playwright = sync_playwright().start()
    except Exception as e:
        pytest.skip(f"Playwright could not start: {first_line(e)}")
    try:
        try:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception as e:
            pytest.skip(f"Google Chrome could not be started by Playwright: {first_line(e)}")
        try:
            yield browser
        finally:
            browser.close()
    finally:
        playwright.stop()


def first_line(error: Exception) -> str:
    return (str(error).strip().splitlines() or [type(error).__name__])[0]


@pytest.mark.e2e
@pytest.mark.loopback
def test_paste_confirm_investigate_publish_in_chrome(app, chrome):
    store, origin = app
    context = chrome.new_context(viewport={"width": 1280, "height": 720})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    context.set_default_timeout(10_000)
    page = context.new_page()
    problems = []
    page.on(
        "console", lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

    page.goto(origin + "/")
    page.wait_for_selector("#sample-wrap:not([hidden])")
    assert page.inner_text("#bind") == store.server["bind"]
    assert page.inner_text("#provider") == "Fake fixtures (tests)"

    page.select_option("#sample", "a")
    assert page.input_value("#offer-text") == SAMPLE_A.read_text("utf-8")
    page.click("#open-case")
    page.wait_for_selector("[data-focus=confirm-claims]")
    assert page.is_disabled("[data-focus=investigate]")
    chips = page.locator(".chips .chip")
    assert chips.count() == 6
    assert "The message alone proves nothing" in page.inner_text("#claims")

    page.click("[data-focus=confirm-claims]")
    page.wait_for_selector(".claims .state.ok")
    page.click("[data-focus=investigate]")
    page.wait_for_selector("#evidence")
    # Investigate, then the draft and the budget read: the button is back once all three ran.
    page.wait_for_selector("[data-focus=investigate]:enabled")

    assert page.inner_text("#verdict .band-title") == "High risk"
    assert page.inner_text(".budget-line").startswith("2 searches spent of 6 · 4 not spent")
    because = page.locator("#trace .because").all_inner_texts()
    assert any("fee was asked" in line for line in because), because

    page.select_option("#pub-label", "likely_impersonation")
    page.fill("#pub-note", "Do not pay the fee.")
    page.click("#publish-btn")
    page.wait_for_selector("#post-case_001")
    page.click("[data-focus=wa-case_001]")
    page.wait_for_selector("[data-focus=wa-case_001]:text('Copied')")
    copied = page.evaluate("() => navigator.clipboard.readText()")

    state = store.state()
    [post] = state["board"]
    assert (post["caseId"], post["label"], post["by"]) == (
        "case_001",
        "likely_impersonation",
        "human",
    )
    assert page.inner_text("#post-case_001 .post-label") == "Likely impersonation"
    assert copied == post["whatsapp"]

    log = state["activityLog"]
    assert {e["tool"] for e in log} == CLICKS | PLANNER_CHECKS | {"publish_verdict"}
    assert all(e["actor"] == "human" for e in log if e["tool"] not in PLANNER_CHECKS)
    assert all(e["actor"] == "agent" for e in log if e["tool"] in PLANNER_CHECKS)
    assert page.locator("#log .log-row").count() == len(log)
    assert page.locator("#log .actor.agent").count() == len(PLANNER_CHECKS)

    assert len(state["calls"]) == 2
    assert page.locator("#calls .call:not(.idle)").count() == len(state["calls"])

    # The post shows what was published, whatever reaches the case later; and the case stays
    # as published until a person retracts it.
    assert page.inner_text("#post-case_001 .post-offer") == post["offer"]
    assert page.is_disabled("[data-focus=investigate]")
    store.cases["case_001"]["claims"]["company"]["value"] = "Northwind Bank of India Limited"
    page.reload()
    page.wait_for_selector("#post-case_001")
    assert page.inner_text("#post-case_001 .post-offer") == post["offer"]
    assert "Northwind" not in page.inner_text("#board")
    assert problems == []


# ---- what the page shows, case by case ----------------------------------------------------------


def human(store, tool, args):
    out = invoke(tool, args, "human", store=store)
    assert out["ok"], out
    return out["result"]


def investigated(store, name, *, remaining=False):
    """Opens, confirms, investigates and drafts one sample the way the page's clicks do."""
    case_id = human(store, "open_case", {"text": (SAMPLES / f"{name}.txt").read_text("utf-8")})[
        "id"
    ]
    human(store, "update_claims", {"case_id": case_id, "confirm": True})
    human(store, "investigate", {"case_id": case_id})
    if remaining:
        human(store, "run_remaining_checks", {"case_id": case_id})
    human(store, "draft_verdict", {"case_id": case_id})
    return case_id


def opened(chrome, origin, *, width=1280, height=720):
    context = chrome.new_context(
        viewport={"width": width, "height": height}, reduced_motion="reduce"
    )
    context.set_default_timeout(10_000)
    page = context.new_page()
    problems = []
    page.on(
        "console", lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    page.goto(origin + "/")
    page.wait_for_selector("#bind:not(:text('…'))")
    return page, problems


def pick(page, case_id):
    page.click(f"[data-focus=case-{case_id}]")
    page.wait_for_selector(f"#case-h:text('{case_id}'), .case-meta:has-text('{case_id}')")


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_correction_marks_out_of_date_everything_that_relied_on_it(app, chrome):
    store, origin = app
    case_id = investigated(store, "a")
    human(store, "draft_recruiter_reply", {"case_id": case_id})
    human(store, "draft_cybercrime_report", {"case_id": case_id})
    page, problems = opened(chrome, origin)

    for block in ("#draft-reply", "#draft-report"):
        assert page.is_enabled(f"{block} [data-focus^=copy]")
    page.click(".fix summary")
    # Pay and the fee are corrected like the rest: a fee the message never asked is removed.
    assert page.input_value("#fix-pay") == "38000" and page.input_value("#fix-fee") == "2499"
    page.fill("#fix-fee", "")
    page.fill("#fix-company", "Brand Logistics")
    page.click("[data-focus=save-fixes]")
    page.wait_for_selector("#draft-reply.stale")

    case = store.cases[case_id]
    assert (
        case["claims"]["fee"] is None and case["claims"]["company"]["value"] == "Brand Logistics"
    )
    stale = {s["rule"] for s in case["signals"] if s["stale"]}
    assert stale == {"fee_requested", "sender_lookalike", "fee_contradicts_employer"}
    for block in ("#draft-reply", "#draft-report"):
        assert page.is_disabled(f"{block} [data-focus^=copy]")
        assert page.inner_text(f"{block} .stale-note").startswith("Out of date")
    assert page.is_disabled("#publish-btn")

    page.click("[data-focus=redraft]")
    page.wait_for_selector("#verdict .band-title:text('Unverified')")
    # The trace's own band stood only while its findings did.
    assert page.inner_text(".trace-sum .state") == "Out of date"
    assert page.locator(".trace-sum .pill").count() == 0

    # A rupee field takes whole rupees only, and says so before anything is sent.
    page.fill("#fix-pay", "38k")
    page.click("[data-focus=save-fixes]")
    assert "must be a whole number of rupees" in page.inner_text("#claims .slot-msg")
    assert problems == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_the_label_follows_the_draft_verdict_and_a_text_only_case_says_why_it_cannot_post(
    app, chrome
):
    store, origin = app
    a, b, task = (
        investigated(store, "a"),
        investigated(store, "b"),
        investigated(store, "task-per-like"),
    )
    page, problems = opened(chrome, origin)

    def options():
        return dict(
            page.eval_on_selector_all(
                "#pub-label option[value]:not([value=''])",
                "els => els.map(e => [e.value, e.disabled])",
            )
        )

    pick(page, a)
    assert page.input_value("#pub-label") == ""
    assert options() == {
        "likely_impersonation": False,
        "pay_to_apply_red_flag": False,
        "unverified_ask_questions": False,
        "no_contradictions_found": True,
    }
    pick(page, b)
    assert page.input_value("#pub-label") == "no_contradictions_found"
    assert options() == {
        "likely_impersonation": True,
        "pay_to_apply_red_flag": True,
        "unverified_ask_questions": False,
        "no_contradictions_found": False,
    }
    pick(page, task)
    assert page.input_value("#pub-label") == "unverified_ask_questions"
    assert page.is_disabled("#publish-btn")
    reason = page.inner_text("#publish-blocked")
    assert "names no company" in reason and "investigate first" not in reason
    assert store.board == [] and problems == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_cut_emoji_in_a_pasted_message_still_opens_a_case(app, chrome):
    store, origin = app
    page, problems = opened(chrome, origin)

    page.eval_on_selector(
        "#offer-text",
        "el => { el.value = 'Pay the registration fee now \\ud83d'; "
        "el.dispatchEvent(new Event('input')); }",
    )
    page.click("#open-case")
    page.wait_for_selector("[data-focus=confirm-claims]")

    assert store.cases["case_001"]["sourceText"] == "Pay the registration fee now \ufffd"
    assert page.locator("#notice").is_hidden() and problems == []


def rect_inside(page, selector):
    """For each item under ``selector`` that is shown: whether it lies whole inside ``#calls``."""
    return page.eval_on_selector_all(
        selector,
        """els => {
          const box = document.getElementById('calls').getBoundingClientRect();
          return els.filter((e) => !e.hidden).map((e) => {
            const r = e.getBoundingClientRect();
            return r.left >= box.left - 1 && r.right <= box.right + 1;
          });
        }""",
    )


@pytest.mark.e2e
@pytest.mark.loopback
def test_the_strip_shows_whole_columns_of_the_newest_calls(app, chrome):
    store, origin = app
    page, problems = opened(chrome, origin)
    assert ratio(*colours(page, "#calls .call.idle")) >= 4.5

    investigated(store, "a")
    investigated(store, "c", remaining=True)
    investigated(store, "b")
    assert len(store.calls) == 10
    page.reload()
    page.wait_for_selector("#calls .call:not(.idle):last-child")

    for width in (1280, 1000):
        page.set_viewport_size({"width": width, "height": 720})
        page.wait_for_timeout(100)
        shown = rect_inside(page, "#calls .call")
        assert shown and all(shown), (width, shown)
        count = page.inner_text("#strip-count")
        assert count == f"10 calls · the latest {len(shown)} shown", count
        # The newest call is always among them.
        assert page.locator("#calls .call").last.is_visible()
    assert problems == []


@pytest.mark.e2e
@pytest.mark.loopback
@pytest.mark.parametrize(("width", "height"), [(800, 900), (375, 812)])
def test_confirming_the_claims_brings_the_next_step_into_view(app, chrome, width, height):
    store, origin = app
    page, problems = opened(chrome, origin, width=width, height=height)
    page.select_option("#sample", "a")
    page.click("#open-case")
    page.wait_for_selector("[data-focus=confirm-claims]")

    page.click("[data-focus=confirm-claims]")
    page.wait_for_selector(".claims .state.ok")
    page.wait_for_selector("[data-focus=investigate]:focus")

    box = page.eval_on_selector(
        "[data-focus=investigate]",
        "e => { const r = e.getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; }",
    )
    top, bottom, view = box
    assert 0 <= top and bottom <= view, box
    assert problems == []


def colours(page, selector):
    """The text colour of ``selector`` and the first opaque background behind it, as RGB."""
    return page.eval_on_selector(
        selector,
        """el => {
          const rgb = (s) => s.match(/[\\d.]+/g).map(Number);
          let at = el;
          let back = null;
          while (at && !back) {
            const c = rgb(getComputedStyle(at).backgroundColor);
            if (c.length === 3 || c[3] === 1) back = c.slice(0, 3);
            at = at.parentElement;
          }
          return [rgb(getComputedStyle(el).color).slice(0, 3), back || [255, 255, 255]];
        }""",
    )


def ratio(fg, bg):
    def luminance(rgb):
        channels = [c / 255 for c in rgb]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.e2e
@pytest.mark.loopback
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_smallest_text_meets_contrast_in_both_schemes(app, chrome, scheme):
    store, origin = app
    investigated(store, "a")
    context = chrome.new_context(viewport={"width": 1280, "height": 720}, color_scheme=scheme)
    page = context.new_page()
    page.goto(origin + "/")
    page.wait_for_selector("#trace .after-k")

    for selector in ("#log .log-time", "#trace .after-k", ".budget-line", ".case-meta"):
        assert ratio(*colours(page, selector)) >= 4.5, (scheme, selector)
    page.click("[data-focus=new-case]")
    assert ratio(*colours(page, "#offer-count")) >= 4.5, (scheme, "#offer-count")
    context.close()


@pytest.mark.e2e
@pytest.mark.loopback
@pytest.mark.parametrize(
    ("recorded", "badge", "banner"),
    [
        (
            [],
            "Replay: nothing recorded yet",
            "Replay mode, and nothing is recorded yet: every search fails and says so",
        ),
        (
            ["2026-09-28T11:00:00+05:30"],
            "Replay: recorded 28 Sep 2026, not live",
            "Replaying SerpApi responses recorded on 28 Sep 2026, not live.",
        ),
    ],
)
def test_replay_says_when_its_responses_were_recorded(chrome, tmp_path, recorded, badge, banner):
    for n, when in enumerate(recorded):
        params = {"engine": "google", "q": f"query {n}"}
        write_recording("a", params, {}, recorded_at=when, recordings_dir=tmp_path)
    with serving(Store(ReplaySearchProvider(tmp_path))) as origin:
        page, problems = opened(chrome, origin)
        page.wait_for_selector("#replay:not([hidden])")
        assert page.inner_text("#provider") == badge
        assert page.inner_text("#replay").startswith(banner)
        assert problems == []
