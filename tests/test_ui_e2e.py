"""The page in a real browser against the real server: Playwright drives Google Chrome through
paste, confirm, investigate and publish for sample A, on the synthetic fixtures.

Nothing else checks that the two halves fit: the page's own JavaScript, the Content-Security-
Policy it runs under, the session the address ``serve`` prints opens, the clicks going out
through ``invoke`` as ``human`` because of it, the planner's checks coming back as ``agent``,
and the call-log strip counting the same calls as the store. The test skips, saying why, where
Playwright or Chrome is missing, so a fresh clone still passes; it never downloads a browser.

The page mirrors two tables it can't read from the server (the rule labels, which the draft's
"Also found" lines are matched on, and the Offer Board labels); the first tests hold them to the
server's, without a browser.

The later tests set most of their cases up through ``invoke`` as the person, as the page's own
clicks would, then load the page and check what it shows: what a correction makes out of date,
the labels a draft verdict allows, the call-log strip, the replay banner, focus on a narrow
window, and contrast; what a browser without the session gets, what an address already opened
opens, and what a program on another port of 127.0.0.1 gets from the browser (the cookie, never
the key); the agent's calls appearing while the person is mid-sentence, without costing them
their text, caret, open disclosure or scroll; a label chosen before the agent changed the case
never publishing the changed case; claims the agent confirmed saying so; and a page whose
session ended saying so."""

from __future__ import annotations

import http.client
import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from offer_checkpost import drafts
from offer_checkpost.invoke import invoke
from offer_checkpost.providers import FakeSearchProvider, ReplaySearchProvider, write_recording
from offer_checkpost.rules import RULES
from offer_checkpost.server import SESSION_HEADER, make_server
from offer_checkpost.store import Store
from offer_checkpost.tools import TOOLS

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


def js_set(name: str) -> set[str]:
    """The names in the ``const <name> = new Set([...]);`` in app.js."""
    source = (WEB / "app.js").read_text("utf-8")
    body = re.search(rf"^const {name} = new Set\(\[\n(.*?)^\]\);", source, re.M | re.S)
    assert body, f"app.js has no {name} set"
    return set(re.findall(r"'(\w+)'", body[1]))


def test_the_tools_the_page_counts_on_to_keep_a_revision_keep_it():
    """The page tells a case the agent changed from one it only read by these."""
    keeps = js_set("KEEPS_REVISION")
    assert set(TOOLS) - keeps == {
        "update_claims",
        "investigate",
        "lookup_official_site",
        "find_fraud_notice",
        "confirm_sender_domain",
        "check_job_listings",
        "check_office",
        "scan_office_reviews",
        "check_scam_reports",
        "check_contact_footprint",
    }
    store = Store(FakeSearchProvider.from_fixtures())
    case_id = investigated(store, "a")
    special = {"open_case": {"text": SAMPLE_A.read_text("utf-8")}, "list_cases": {}}
    special["search_budget"] = {}
    for tool in sorted(keeps):
        before = store.cases[case_id]["revision"]
        out = invoke(tool, special.get(tool, {"case_id": case_id}), "agent", store=store)
        assert out["ok"], (tool, out)
        assert store.cases[case_id]["revision"] == before, tool


def test_the_page_names_nothing_outside_its_own_repository():
    # The monorepo's name is split so this file doesn't itself contain it.
    outside = re.compile(r"\bSPEC\b|entries/|hackathons" + r"-2026|\bbin/")
    for path in sorted(WEB.iterdir()):
        for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
            assert not outside.search(line), f"{path.name}:{n}: {line.strip()}"


# ---- the browser walk ---------------------------------------------------------------------


@contextmanager
def serving(store):
    """The server for ``store``, serving; ``url`` is the address ``serve`` would print."""
    server = make_server(store, port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def origin(server):
    return f"http://127.0.0.1:{server.port}"


@pytest.fixture
def app():
    store = Store(FakeSearchProvider.from_fixtures())
    with serving(store) as server:
        yield store, server


def agent_call(server, tool, args, **headers):
    """One call as anything but the person's page makes it: over HTTP, without the session's
    key (and, unless ``headers`` gives one, its cookie)."""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        body = json.dumps({"tool": tool, "args": args})
        headers = {
            "Content-Type": "application/json",
            "Host": f"127.0.0.1:{server.port}",
            **headers,
        }
        conn.request("POST", "/api/invoke", body, headers)
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


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


@pytest.fixture
def browser(chrome):
    """New Chrome contexts for one test, closed after it, so no page keeps polling a server
    that has stopped."""
    contexts = []

    def new_context(**kwargs):
        contexts.append(chrome.new_context(**kwargs))
        return contexts[-1]

    yield new_context
    for context in contexts:
        context.close()


@pytest.mark.e2e
@pytest.mark.loopback
def test_paste_confirm_investigate_publish_in_chrome(app, browser):
    store, server = app
    context = browser(viewport={"width": 1280, "height": 720})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin(server))
    context.set_default_timeout(10_000)
    page = context.new_page()
    problems = []
    page.on(
        "console", lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))

    # The address serve prints: it opens the session and leaves the address bar bare.
    token = server.token
    page.goto(server.url)
    assert page.url == origin(server) + "/"
    session = server.session
    [cookie] = context.cookies()
    assert (cookie["name"], cookie["value"], cookie["httpOnly"], cookie["sameSite"]) == (
        f"oc_session_{server.port}",
        session.cookie,
        True,
        "Strict",
    )
    assert token not in (session.cookie, session.key, server.token)
    assert cookie["expires"] == -1, "a session cookie, gone with the browser session"
    assert page.evaluate("document.cookie") == ""
    # The key came in the fragment, which is gone from the address, and is kept for this tab.
    assert page.evaluate("[location.hash, sessionStorage.getItem('session')]") == ["", session.key]
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
    # The planner's checks are the agent's, but the person started them: nothing to point out.
    assert page.locator("#agent-note").is_hidden()
    assert page.locator("#session-lost").is_hidden()
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


def opened(browser, server, *, width=1280, height=720):
    """A page opened at the address ``serve`` prints, as the person, once it has drawn."""
    context = browser(viewport={"width": width, "height": height}, reduced_motion="reduce")
    context.set_default_timeout(10_000)
    page = context.new_page()
    problems = []
    page.on(
        "console", lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None
    )
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    page.goto(server.url)
    page.wait_for_selector("#bind:not(:text('…'))")
    return page, problems


def pick(page, case_id):
    page.click(f"[data-focus=case-{case_id}]")
    page.wait_for_selector(f"#case-h:text('{case_id}'), .case-meta:has-text('{case_id}')")


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_correction_marks_out_of_date_everything_that_relied_on_it(app, browser):
    store, server = app
    case_id = investigated(store, "a")
    human(store, "draft_recruiter_reply", {"case_id": case_id})
    human(store, "draft_cybercrime_report", {"case_id": case_id})
    page, problems = opened(browser, server)

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
    app, browser
):
    store, server = app
    a, b, task = (
        investigated(store, "a"),
        investigated(store, "b"),
        investigated(store, "task-per-like"),
    )
    page, problems = opened(browser, server)

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
def test_a_cut_emoji_in_a_pasted_message_still_opens_a_case(app, browser):
    store, server = app
    page, problems = opened(browser, server)

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
def test_the_strip_shows_whole_columns_of_the_newest_calls(app, browser):
    store, server = app
    page, problems = opened(browser, server)
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
def test_confirming_the_claims_brings_the_next_step_into_view(app, browser, width, height):
    store, server = app
    page, problems = opened(browser, server, width=width, height=height)
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
def test_the_smallest_text_meets_contrast_in_both_schemes(app, browser, scheme):
    store, server = app
    investigated(store, "a")
    context = browser(viewport={"width": 1280, "height": 720}, color_scheme=scheme)
    page = context.new_page()
    page.goto(server.url)
    page.wait_for_selector("#trace .after-k")

    for selector in ("#log .log-time", "#trace .after-k", ".budget-line", ".case-meta"):
        assert ratio(*colours(page, selector)) >= 4.5, (scheme, selector)
    page.click("[data-focus=new-case]")
    assert ratio(*colours(page, "#offer-count")) >= 4.5, (scheme, "#offer-count")


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
def test_replay_says_when_its_responses_were_recorded(browser, tmp_path, recorded, badge, banner):
    for n, when in enumerate(recorded):
        params = {"engine": "google", "q": f"query {n}"}
        write_recording("a", params, {}, recorded_at=when, recordings_dir=tmp_path)
    with serving(Store(ReplaySearchProvider(tmp_path))) as server:
        page, problems = opened(browser, server)
        page.wait_for_selector("#replay:not([hidden])")
        assert page.inner_text("#provider") == badge
        assert page.inner_text("#replay").startswith(banner)
        assert problems == []


# ---- the session ------------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_browser_without_the_session_address_is_told_where_it_is(app, browser):
    store, server = app
    context = browser(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    problems = []
    page.on(
        "console", lambda m: problems.append(f"{m.type}: {m.text}") if m.type == "error" else None
    )

    plain = page.goto(origin(server) + "/")
    assert plain.status == 200
    assert page.inner_text("h1") == "Open the address printed in the terminal"
    assert "acts as the agent" in page.inner_text("main")
    # Styled by app.css, and nothing runs.
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(246, 245, 241)"
    assert page.locator("script").count() == 0

    stale = page.goto(server.url + "x")
    assert stale.status == 403
    assert page.inner_text("h1") == "Open the address printed in the terminal"
    assert context.cookies() == [] and store.activity_log == []
    # The console notes the 403 itself; nothing else may go wrong.
    assert [p for p in problems if "403" not in p] == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_the_agents_calls_appear_without_costing_the_person_what_they_were_doing(app, browser):
    store, server = app
    case_id = investigated(store, "a")
    page, problems = opened(browser, server)
    page.click(".full summary")
    page.click("#pub-note")
    page.keyboard.type("Do not pay the fee.")
    page.eval_on_selector("#pub-note", "n => n.setSelectionRange(3, 6)")
    scrolled = page.evaluate("scrollY")
    assert scrolled > 0

    reply = agent_call(server, "open_case", {"text": (SAMPLES / "b.txt").read_text("utf-8")})
    assert reply["ok"], reply
    page.wait_for_selector("#agent-note:not([hidden])")

    assert page.inner_text("#agent-note") == "The agent called open_case."
    assert page.locator("[data-focus=case-case_002]").count() == 1
    assert case_id in page.inner_text(".case-meta")
    assert page.inner_text("#log .log-row:last-child .actor") == "agent"
    # The person's sentence, caret, open disclosure and place on the page are as they left them.
    assert page.input_value("#pub-note") == "Do not pay the fee."
    assert page.evaluate(
        "[document.activeElement.id, document.activeElement.selectionStart, "
        "document.activeElement.selectionEnd]"
    ) == ["pub-note", 3, 6]
    assert page.eval_on_selector(".full", "d => d.open") is True
    assert page.evaluate("scrollY") == scrolled

    refused = agent_call(
        server,
        "publish_verdict",
        {"case_id": case_id, "label": "likely_impersonation", "note": "", "revision": 0},
    )
    assert refused["outcome"] == "refused"
    page.wait_for_selector("#agent-note:text('publish_verdict (refused)')")
    assert page.inner_text("#agent-note") == "The agent called publish_verdict (refused)."
    assert store.board == [] and problems == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_page_whose_session_ended_says_so_and_turns_off_what_only_a_person_does(app, browser):
    store, server = app
    investigated(store, "a")
    page, problems = opened(browser, server)
    page.select_option("#pub-label", "likely_impersonation")
    human_only = ("#publish-btn", "[data-focus=remaining]", "[data-focus=outcome-walked_away]")
    for control in human_only:
        assert page.is_enabled(control), control

    page.context.clear_cookies()
    page.wait_for_selector("#session-lost:not([hidden])")

    assert page.inner_text("#session-lost") == (
        "This page is not signed in as the person: open the newest address printed in the "
        "terminal."
    )
    for control in human_only:
        assert page.is_disabled(control), control
    # What the page still sends, the server counts as the agent's.
    page.click("[data-focus=draft-reply]")
    page.wait_for_selector("#draft-reply")
    last = store.state()["activityLog"][-1]
    assert (last["actor"], last["tool"], last["result"]) == (
        "agent",
        "draft_recruiter_reply",
        "ok",
    )

    page.context.add_cookies(
        [
            {
                "name": server.cookie_name,
                "value": server.session.cookie,
                "url": origin(server),
                "httpOnly": True,
                "sameSite": "Strict",
            }
        ]
    )
    page.wait_for_selector("#session-lost", state="hidden")
    for control in human_only:
        assert page.is_enabled(control), control
    assert problems == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_the_address_opens_the_person_once_and_the_next_one_moves_them(app, browser):
    store, server = app
    used = server.url
    page, problems = opened(browser, server)

    # Another browser opening the address already used, as a copy from the first browser's
    # history would be, opens nothing.
    other = browser(viewport={"width": 1280, "height": 720}).new_page()
    assert other.goto(used).status == 403
    assert other.inner_text("h1") == "Open the address printed in the terminal"
    assert other.context.cookies() == []
    assert page.locator("#session-lost").is_hidden()

    # The next address the terminal prints makes that browser the person instead.
    other.goto(server.url)
    other.wait_for_selector("#bind:not(:text('…'))")
    page.wait_for_selector("#session-lost:not([hidden])")
    assert other.locator("#session-lost").is_hidden()
    assert [p for p in problems if "403" not in p] == []


class Listener(BaseHTTPRequestHandler):
    """Another program on 127.0.0.1, on a port of its own: it keeps what each request sent."""

    heard: list[tuple[str, str | None, str | None]] = []

    def do_GET(self):
        self.heard.append((self.path, self.headers["Cookie"], self.headers[SESSION_HEADER]))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_program_on_another_port_gets_the_cookie_but_never_the_key(app, browser):
    store, server = app
    case_id = investigated(store, "a")
    revision = store.cases[case_id]["revision"]
    page, problems = opened(browser, server)
    Listener.heard = []
    other = ThreadingHTTPServer(("127.0.0.1", 0), Listener)
    thread = threading.Thread(target=other.serve_forever, kwargs={"poll_interval": 0.02})
    thread.start()
    try:
        # Opened from the address bar, as the person might open another local app.
        page.goto(f"http://127.0.0.1:{other.server_port}/typed")
    finally:
        other.shutdown()
        other.server_close()
        thread.join(5)

    [(_, cookie, key)] = [h for h in Listener.heard if h[0] == "/typed"]
    # Browsers don't scope cookies by port: it has the cookie, and nothing else.
    assert cookie == f"{server.cookie_name}={server.session.cookie}"
    assert key is None
    args = {"case_id": case_id, "label": "likely_impersonation", "note": "", "revision": revision}
    refused = agent_call(server, "publish_verdict", args, Cookie=cookie)
    assert refused["error"].startswith("publish_verdict is human-only")
    last = store.state()["activityLog"][-1]
    assert (last["actor"], last["tool"], last["result"]) == ("agent", "publish_verdict", "refused")
    assert store.board == [] and problems == []


# ---- the case as the person read it ------------------------------------------------------------


AGENT_CHANGES = (
    ("update_claims", {"fields": {"pay": 95000, "fee": 49999}}),
    ("update_claims", {"confirm": True}),
    ("investigate", {}),
    ("draft_verdict", {}),
)


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_label_chosen_before_the_agent_changed_the_case_never_publishes_the_changed_case(
    app, browser
):
    store, server = app
    case_id = agent_call(server, "open_case", {"text": SAMPLE_A.read_text("utf-8")})["result"][
        "id"
    ]
    for tool, args in AGENT_CHANGES[1:]:
        assert agent_call(server, tool, {"case_id": case_id, **args})["ok"], tool
    page, problems = opened(browser, server)
    page.select_option("#pub-label", "likely_impersonation")
    page.fill("#pub-note", "Do not pay the fee.")
    assert page.is_enabled("#publish-btn")

    for tool, args in AGENT_CHANGES:
        assert agent_call(server, tool, {"case_id": case_id, **args})["ok"], tool
    page.wait_for_selector("#publish-changed:has-text('draft_verdict')")

    assert page.inner_text("#publish-changed") == (
        "The agent changed this case after you read it: it called update_claims, investigate "
        "and draft_verdict. Read it again, then choose the label."
    )
    assert page.input_value("#pub-label") == "" and page.is_disabled("#publish-btn")
    assert page.input_value("#pub-note") == "Do not pay the fee."
    # Sent anyway, the form asks for a label first.
    page.eval_on_selector(".publish-form", "f => f.requestSubmit()")
    page.wait_for_selector(".publish .slot-msg")
    assert store.board == []

    # Choosing the label again is reading the case as it is now.
    page.select_option("#pub-label", "likely_impersonation")
    assert page.locator("#publish-changed").count() == 0
    page.click("#publish-btn")
    page.wait_for_selector(f"#post-{case_id}")
    [post] = store.board
    assert post["by"] == "human" and "₹95000/month" in post["offer"]
    assert problems == []


@pytest.mark.e2e
@pytest.mark.loopback
def test_a_click_before_the_page_saw_the_agents_change_is_refused_and_says_what_changed(
    app, browser
):
    store, server = app
    case_id = investigated(store, "a")
    page, problems = opened(browser, server)
    page.select_option("#pub-label", "likely_impersonation")
    # The page doesn't look again while the person composes text with an input method: the
    # agent changes the case in that moment, and the click comes before the page has seen it.
    page.dispatch_event("#pub-note", "compositionstart")
    changed = agent_call(server, "update_claims", {"case_id": case_id, "fields": {"pay": 95000}})
    assert changed["ok"]

    page.click("#publish-btn")
    page.wait_for_selector(".publish .slot-msg")

    assert "has changed since it was read" in page.inner_text(".publish .slot-msg")
    assert store.board == []
    assert page.inner_text("#publish-changed").startswith(
        "The agent changed this case after you read it: it called update_claims."
    )
    assert page.input_value("#pub-label") == ""
    assert problems == []


class Held(FakeSearchProvider):
    """The synthetic fixtures, holding the second search until ``go`` is set."""

    def __init__(self):
        fixtures = FakeSearchProvider.from_fixtures()
        self.__dict__.update(fixtures.__dict__)
        self.second = threading.Event()
        self.go = threading.Event()
        self.count = 0

    def search(self, params):
        self.count += 1
        if self.count == 2:
            self.second.set()
            assert self.go.wait(10)
        return super().search(params)


@pytest.mark.e2e
@pytest.mark.loopback
def test_the_agents_call_during_the_persons_own_is_said_and_unsettles_the_label(browser):
    provider = Held()
    with serving(Store(provider)) as server:
        page, problems = opened(browser, server)
        page.select_option("#sample", "a")
        page.click("#open-case")
        page.click("[data-focus=confirm-claims]")
        page.wait_for_selector(".claims .state.ok")
        page.click("[data-focus=investigate]")
        assert provider.second.wait(10)
        # The agent's call waits behind the person's investigation, and lands before the page
        # reads the state again.
        replies = []
        agent = threading.Thread(
            target=lambda: replies.append(
                agent_call(
                    server, "update_claims", {"case_id": "case_001", "fields": {"pay": 95000}}
                )
            )
        )
        agent.start()
        page.wait_for_timeout(300)
        provider.go.set()
        agent.join(10)
        page.wait_for_selector("#publish-changed")

        assert replies[0]["ok"]
        assert page.inner_text("#agent-note") == "The agent called update_claims."
        assert page.inner_text("#publish-changed").startswith(
            "The agent changed this case after you read it: it called update_claims."
        )
        assert page.input_value("#pub-label") == ""
        assert problems == []


# ---- who confirmed ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.loopback
def test_claims_the_agent_confirmed_say_so_until_a_person_confirms_them(app, browser):
    store, server = app
    case_id = agent_call(server, "open_case", {"text": SAMPLE_A.read_text("utf-8")})["result"][
        "id"
    ]
    assert agent_call(server, "update_claims", {"case_id": case_id, "confirm": True})["ok"]
    page, problems = opened(browser, server)

    assert page.inner_text(".claims .state") == "Confirmed by the agent"
    assert page.inner_text(".claims .sub").startswith(
        "The agent confirmed the claims marked so, not a person."
    )
    assert page.locator(".chips .t.ok").count() == 0
    assert page.locator(".chips .t:text-is('confirmed by the agent')").count() == 5
    # The agent's confirmation is what investigate needs; the page just won't call it a person's.
    assert page.is_enabled("[data-focus=investigate]")

    page.click("[data-focus=confirm-claims]")
    page.wait_for_selector(".claims .state.ok")

    assert page.inner_text(".claims .state") == "Confirmed"
    assert page.inner_text(".claims .sub").startswith(
        "A person checked these against the message."
    )
    assert page.locator(".chips .t:text-is('✓ confirmed')").count() == 5
    assert page.locator("[data-focus=confirm-claims]").count() == 0
    claims = store.cases[case_id]["claims"]
    assert {claims[f]["confirmedBy"] for f in ("company", "role", "city", "pay", "fee")} == {
        "human"
    }
    assert problems == []
