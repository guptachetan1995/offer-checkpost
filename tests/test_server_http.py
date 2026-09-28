"""The real server on a loopback socket, spoken to with the standard library's http.client.

Every change goes through ``POST /api/invoke`` and so through ``invoke``: a missing or forged
actor, and each human-only verb called as the agent, come back refused and logged. The server
binds 127.0.0.1 alone, and refuses, before ``invoke``, what a page of another site open in the
same browser could send it: a foreign Host, Origin or Sec-Fetch-Site, and a POST that isn't
``application/json``. It serves three names from ``web/`` and nothing else, and no answer
carries an exception's text or anything key-shaped."""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import re
import socket
import threading
from pathlib import Path

import pytest
import requests

from offer_checkpost import server as server_module
from offer_checkpost.providers import (
    FakeSearchProvider,
    ReplaySearchProvider,
    SearchProvider,
    SerpApiSearchProvider,
    write_recording,
)
from offer_checkpost.server import CSP, MAX_BODY, MAX_DEPTH, make_server
from offer_checkpost.store import Store
from offer_checkpost.tools import TOOLS
from offer_checkpost.verbs import VERBS

pytestmark = pytest.mark.loopback

ENTRY = Path(__file__).resolve().parents[1]
SAMPLES = ENTRY / "samples" / "offers"
KEY_SHAPED = re.compile(r"[0-9a-fA-F]{64}")
OUTSIDE = "a file outside web/"
LIST = {"tool": "list_cases", "args": {}, "actor": "human"}


@dataclasses.dataclass
class Reply:
    status: int
    headers: http.client.HTTPMessage
    body: bytes

    def json(self):
        return json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


class App:
    """The server on a free port in a thread, and a client that keeps every reply."""

    def __init__(self, store: Store, web_dir: Path):
        self.store = store
        self.server = make_server(store, port=0, web_dir=web_dir)
        self.port = self.server.port
        self.origin = f"http://127.0.0.1:{self.port}"
        self.replies: list[Reply] = []
        # A short poll: shutdown() waits for the loop's next look, and every test stops one.
        self._thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(5)

    def request(self, method, path, body=None, *, headers=None, timeout=10) -> Reply:
        """``body`` is sent as JSON unless it is bytes. A header set to None is not sent."""
        sent = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        data = body if body is None or isinstance(body, bytes) else json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for name, value in sent.items():
                if value is not None:
                    conn.putheader(name, value)
            if data is not None:
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            response = conn.getresponse()
            reply = Reply(response.status, response.headers, response.read())
        finally:
            conn.close()
        self.replies.append(reply)
        return reply

    def get(self, path, **kwargs) -> Reply:
        return self.request("GET", path, **kwargs)

    def call(self, tool, args, actor="human", **kwargs) -> Reply:
        body = {"tool": tool, "args": args, "actor": actor}
        return self.request("POST", "/api/invoke", body, **kwargs)

    def ok(self, tool, args, actor="human"):
        reply = self.call(tool, args, actor)
        assert reply.status == 200
        envelope = reply.json()
        assert envelope["ok"], envelope
        return envelope["result"]

    def opened(self, sample) -> str:
        case = self.ok("open_case", {"text": (SAMPLES / f"{sample}.txt").read_text("utf-8")})
        self.ok("update_claims", {"case_id": case["id"], "confirm": True})
        return case["id"]

    def investigated(self, sample) -> str:
        case_id = self.opened(sample)
        self.ok("investigate", {"case_id": case_id})
        return case_id

    def publish(self, case_id, label, note="") -> dict:
        """Drafts the verdict, reads the case, and publishes it as read."""
        self.ok("draft_verdict", {"case_id": case_id})
        revision = self.ok("get_case", {"case_id": case_id})["revision"]
        args = {"case_id": case_id, "label": label, "note": note, "revision": revision}
        return self.ok("publish_verdict", args)

    def log(self) -> list[dict]:
        return self.store.state()["activityLog"]

    def raw(self, request: bytes) -> tuple[str, bytes]:
        """Sends ``request`` as it is, for what http.client won't send; returns the answer's
        head and body."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(request)
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        return head.decode("latin-1"), body


@pytest.fixture
def web_dir(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>Offer Checkpost</title>\n", "utf-8")
    (web / "app.js").write_text("document.title;\n", "utf-8")
    (web / "app.css").write_text("body { margin: 0; }\n", "utf-8")
    for name in ("pyproject.toml", "README.md", ".env"):
        (tmp_path / name).write_text(OUTSIDE, "utf-8")
    return web


@pytest.fixture
def app(web_dir):
    served = App(Store(FakeSearchProvider.from_fixtures()), web_dir)
    yield served
    served.close()


# ---- the actor is a request field, and invoke still decides ------------------------------


def test_a_missing_or_forged_actor_reaches_invoke_and_is_refused_and_logged(app):
    bodies = [{"tool": "list_cases", "args": {}}] + [
        {**LIST, "actor": actor} for actor in ("admin", "", "Human", None)
    ]
    for body in bodies:
        reply = app.request("POST", "/api/invoke", body)
        assert reply.status == 200
        envelope = reply.json()
        assert (envelope["ok"], envelope["outcome"]) == (False, "refused")
        assert envelope["error"].startswith("unknown actor: a call comes from 'human'")

    assert [(e["actor"], e["tool"], e["result"]) for e in app.log()] == [
        (None, "list_cases", "refused"),
        ("admin", "list_cases", "refused"),
        ("", "list_cases", "refused"),
        ("Human", "list_cases", "refused"),
        (None, "list_cases", "refused"),
    ]


VERB_ARGS = {
    "publish_verdict": {"case_id": "case_001", "label": "likely_impersonation", "note": "agent"},
    "retract_verdict": {"case_id": "case_001", "reason": "the agent says so"},
    "record_outcome": {"case_id": "case_001", "outcome": "walked_away"},
    "run_remaining_checks": {"case_id": "case_001"},
}


def test_the_verb_arguments_cover_every_human_only_verb():
    assert set(VERB_ARGS) == set(VERBS)


@pytest.mark.parametrize("verb", sorted(VERB_ARGS))
def test_each_human_only_verb_is_refused_for_the_agent_and_changes_nothing(app, verb):
    app.investigated("a")
    before = app.store.state()

    reply = app.call(verb, VERB_ARGS[verb], "agent")

    assert reply.status == 200
    envelope = reply.json()
    assert (envelope["ok"], envelope["outcome"]) == (False, "refused")
    assert envelope["error"].startswith(f"{verb} is human-only: only a person can ")
    after = app.store.state()
    for part in ("cases", "board", "calls"):
        assert after[part] == before[part]
    last = after["activityLog"][-1]
    assert (last["actor"], last["tool"], last["result"]) == ("agent", verb, "refused")


def test_the_same_verbs_run_for_a_person(app):
    case_id = app.investigated("a")
    post = app.publish(case_id, "likely_impersonation", "Checked.")
    assert (post["caseId"], post["by"]) == (case_id, "human")
    assert app.ok("record_outcome", {"case_id": case_id, "outcome": "walked_away"})["by"] == (
        "human"
    )
    assert app.ok("retract_verdict", {"case_id": case_id, "reason": "wrong batch"})["by"] == (
        "human"
    )


# ---- loopback only --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "localhost", "", "::", "::1", "127.0.0.2", "192.0.2.1", None]
)
def test_binding_anything_but_127_0_0_1_raises_before_anything_is_bound(host, monkeypatch):
    built = []
    monkeypatch.setattr(server_module, "OfferServer", lambda *args: built.append(args))
    store = Store(FakeSearchProvider.from_fixtures())

    with pytest.raises(ValueError, match=r"listens on 127\.0\.0\.1 only"):
        make_server(store, host, 0)

    assert built == []
    assert store.server["bind"] is None


def test_state_carries_the_call_log_and_the_bound_address(app):
    app.investigated("a")

    reply = app.get("/api/state")

    assert reply.status == 200
    assert reply.headers["Content-Type"] == "application/json; charset=utf-8"
    state = reply.json()
    assert state["server"] == {
        "bind": f"127.0.0.1:{app.port}",
        "provider": "fake",
        "key": "missing",
    }
    assert [(c["engine"], c["provider"], c["cache"]) for c in state["calls"]] == [
        ("google", "fake", "miss"),
        ("google", "fake", "miss"),
    ]
    assert [(e["actor"], e["tool"], e["result"]) for e in state["activityLog"]] == [
        ("human", "open_case", "ok"),
        ("human", "update_claims", "ok"),
        ("human", "investigate", "ok"),
        ("agent", "lookup_official_site", "ok"),
        ("agent", "find_fraud_notice", "ok"),
    ]
    assert state["cases"][0]["budget"]["stoppedBecause"] == "decisive"


def test_the_call_log_answers_while_the_store_is_locked(app):
    app.investigated("a")

    with app.store.lock:
        calls = app.get("/api/calls", timeout=5)
        with pytest.raises(TimeoutError):
            app.get("/api/state", timeout=0.5)

    assert calls.status == 200
    state = app.store.state()
    assert calls.json() == {"server": state["server"], "calls": state["calls"]}


class Held(SearchProvider):
    """The fake provider, holding the second search until ``go`` is set."""

    name = "fake"

    def __init__(self):
        self.inner = FakeSearchProvider.from_fixtures()
        self.second = threading.Event()
        self.go = threading.Event()
        self.count = 0

    def search(self, params):
        self.count += 1
        if self.count == 2:
            self.second.set()
            assert self.go.wait(10)
        return self.inner.search(params)

    def account(self):
        return self.inner.account()


def test_the_call_log_grows_while_an_investigation_is_running(web_dir):
    provider = Held()
    app = App(Store(provider), web_dir)
    done = []
    try:
        case_id = app.opened("a")
        running = threading.Thread(
            target=lambda: done.append(app.call("investigate", {"case_id": case_id}))
        )
        running.start()
        assert provider.second.wait(10)
        during = app.get("/api/calls", timeout=5).json()["calls"]
        provider.go.set()
        running.join(10)
        after = app.get("/api/calls").json()["calls"]
    finally:
        provider.go.set()
        app.close()

    assert [c["engine"] for c in during] == ["google"]
    assert [c["engine"] for c in after] == ["google", "google"]
    assert done[0].json()["ok"] is True


def test_the_tool_list_is_the_agents_and_names_no_human_only_verb(app):
    listed = app.get("/api/tools").json()

    assert [t["name"] for t in listed] == list(TOOLS)
    assert not {t["name"] for t in listed} & set(VERBS)
    assert all(set(t) == {"name", "description", "inputSchema"} for t in listed)


def test_samples_are_the_three_demo_messages_described_without_a_verdict(app):
    samples = app.get("/api/samples").json()["samples"]

    assert [s["id"] for s in samples] == ["a", "b", "c"]
    for sample in samples:
        assert sample["text"] == (SAMPLES / f"{sample['id']}.txt").read_text("utf-8")
        assert not re.search(r"genuine|safe|scam|fake|fraud|risk", sample["label"], re.I)
    assert app.store.state()["cases"] == [] and app.log() == []


def test_an_agent_cannot_rewrite_a_published_post_or_the_board_download(app):
    case_id = app.investigated("a")
    post = app.publish(case_id, "likely_impersonation", "Do not pay the fee.")
    page = app.get("/api/board.html").text
    before = app.store.state()

    attempts = [
        ("update_claims", {"fields": {"company": "Northwind Bank of India Limited"}}),
        ("update_claims", {"confirm": True}),
        ("investigate", {}),
        ("lookup_official_site", {}),
    ]
    replies = [app.call(tool, {"case_id": case_id, **args}, "agent") for tool, args in attempts]

    for reply in replies:
        assert reply.json()["outcome"] == "refused"
        assert "on the Offer Board" in reply.json()["error"]
    after = app.store.state()
    assert after["board"] == [post] and after["cases"] == before["cases"]
    article = re.compile(r"<article.*</article>", re.S)
    assert article.search(app.get("/api/board.html").text)[0] == article.search(page)[0]
    assert "in the name of Brand ·" in page and "Northwind" not in page


def test_the_board_download_has_only_published_verdicts(app):
    assert "No verdicts published yet." in app.get("/api/board.html").text
    a, b = app.investigated("a"), app.investigated("b")
    app.publish(a, "likely_impersonation", "Checked.")
    app.publish(b, "no_contradictions_found")
    app.ok("retract_verdict", {"case_id": b, "reason": "posted to the wrong batch"})

    reply = app.get("/api/board.html")

    assert reply.status == 200
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"
    assert reply.headers["Content-Disposition"] == 'attachment; filename="offer-board.html"'
    page = reply.text
    assert f'id="{a}"' in page and f'id="{b}"' not in page
    assert "· 1 published verdict<" in page
    assert "Likely impersonation" in page and "No contradictions found" not in page


# ---- what a page of another site could send ----------------------------------------------


@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "text/plain",
        "text/plain;charset=UTF-8",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/jsonp",
    ],
)
def test_a_post_that_is_not_json_is_forbidden_before_invoke(app, content_type):
    reply = app.request(
        "POST", "/api/invoke", json.dumps(LIST).encode(), headers={"Content-Type": content_type}
    )

    assert reply.status == 403
    assert reply.json() == {
        "ok": False,
        "outcome": "refused",
        "error": "a call must be sent as application/json",
    }
    assert app.log() == []


def test_json_with_a_charset_is_json(app):
    reply = app.request(
        "POST", "/api/invoke", LIST, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    assert (reply.status, reply.json()) == (200, {"ok": True, "result": []})


@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "null",
        "https://127.0.0.1:{port}",
        "http://127.0.0.1:1",
        "http://127.0.0.1.evil.example:{port}",
        "http://[::1]:{port}",
    ],
)
def test_a_foreign_origin_is_forbidden_on_get_and_post(app, origin):
    headers = {"Origin": origin.format(port=app.port)}

    replies = [app.call("list_cases", {}, headers=headers), app.get("/api/state", headers=headers)]

    assert [r.status for r in replies] == [403, 403]
    assert replies[0].json()["error"] == "a request from another site's page is refused"
    assert app.log() == []


def test_this_servers_own_origins_are_accepted(app):
    for origin in (app.origin, f"http://localhost:{app.port}"):
        assert app.call("list_cases", {}, headers={"Origin": origin}).json()["ok"] is True


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:{port}",
        "127.0.0.1",
        "127.0.0.1:1",
        "localhost",
        "0.0.0.0:{port}",
        "127.0.0.1:{port}.evil.example",
        None,
    ],
)
def test_a_foreign_or_missing_host_is_forbidden_on_every_request(app, host):
    headers = {"Host": host and host.format(port=app.port)}

    replies = [
        app.get("/", headers=headers),
        app.get("/api/state", headers=headers),
        app.get("/api/calls", headers=headers),
        app.call("list_cases", {}, headers=headers),
    ]

    assert [r.status for r in replies] == [403] * 4
    assert replies[0].json()["error"] == (
        f"this server answers only requests addressed to 127.0.0.1:{app.port}"
    )
    assert b"Offer Checkpost" not in replies[0].body
    assert app.log() == []


def test_localhost_with_the_port_is_this_server(app):
    assert app.get("/", headers={"Host": f"localhost:{app.port}"}).status == 200


def test_two_host_headers_are_forbidden(app):
    head, _ = app.raw(
        f"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\nHost: evil.example\r\n"
        "Connection: close\r\n\r\n".encode()
    )
    assert head.startswith("HTTP/1.0 403 ")


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_fetch_from_another_site_is_forbidden(app, site):
    headers = {"Sec-Fetch-Site": site}
    replies = [app.call("list_cases", {}, headers=headers), app.get("/", headers=headers)]
    assert [r.status for r in replies] == [403, 403]
    assert app.log() == []


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_fetch_from_this_page_or_the_address_bar_is_accepted(app, site):
    headers = {"Sec-Fetch-Site": site}
    assert app.call("list_cases", {}, headers=headers).json()["ok"] is True
    assert app.get("/", headers=headers).status == 200


def test_a_preflight_is_never_approved(app):
    preflight = {
        "Origin": "http://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }

    foreign = app.request("OPTIONS", "/api/invoke", headers=preflight)
    own = app.request("OPTIONS", "/api/invoke", headers={**preflight, "Origin": app.origin})
    plain = app.request("OPTIONS", "/api/invoke")

    assert [foreign.status, own.status, plain.status] == [403, 405, 405]
    for reply in app.replies:
        assert not [h for h in reply.headers if h.lower().startswith("access-control-")]
    assert app.log() == []


def test_a_wrong_method_is_405_and_an_unknown_path_404(app):
    wrong = [
        (app.get("/api/invoke"), "POST"),
        (app.request("POST", "/api/state", LIST), "GET"),
        (app.request("POST", "/", LIST), "GET"),
        (app.request("PUT", "/api/invoke", LIST), "POST"),
        (app.request("DELETE", "/api/state"), "GET"),
        (app.request("HEAD", "/"), "GET"),
    ]
    for reply, allowed in wrong:
        assert (reply.status, reply.headers["Allow"]) == (405, allowed)
    for reply in (app.get("/api/nope"), app.request("POST", "/api/nope", LIST), app.get("/api")):
        assert reply.status == 404
    assert app.log() == []


# ---- bodies ---------------------------------------------------------------------------------


def test_a_body_over_the_cap_is_413_and_never_reaches_invoke(app):
    body = json.dumps({**LIST, "tool": "open_case", "args": {"text": "x" * MAX_BODY}}).encode()

    reply = app.request("POST", "/api/invoke", body)

    assert reply.status == 413
    assert reply.json()["error"] == f"the request is larger than {MAX_BODY} bytes"
    assert app.log() == []


def test_the_cap_admits_the_longest_message_open_case_takes_however_it_is_encoded(app):
    longest = TOOLS["open_case"].schema["properties"]["text"]["maxLength"]
    # Twelve bytes each once escaped (a surrogate pair), the most a character can take.
    text = "\N{GRINNING FACE}" * longest
    body = json.dumps({**LIST, "tool": "open_case", "args": {"text": text}}).encode()
    too_long = json.dumps({**LIST, "tool": "open_case", "args": {"text": text + "x"}}).encode()
    assert len(body) > 12 * longest > 64 * 1024

    opened = app.request("POST", "/api/invoke", body)
    refused = app.request("POST", "/api/invoke", too_long)

    assert opened.status == 200 and opened.json()["ok"] is True
    assert refused.status == 200
    assert refused.json()["outcome"] == "refused"
    assert f"args.text is longer than {longest} characters" in refused.json()["error"]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{not json",
        b"[1, 2]",
        b'"list_cases"',
        b"null",
        b"\xff\xfe{}",
        b'{"tool": NaN}',
        b"[" * 100_000,
    ],
)
def test_a_body_that_is_not_a_json_object_is_400(app, body):
    reply = app.request("POST", "/api/invoke", body)

    assert reply.status == 400
    assert reply.json()["ok"] is False
    assert app.log() == []


def strict(reply: Reply):
    """The answer's JSON as a browser's parser reads it: no NaN or Infinity."""

    def refuse(name):
        raise ValueError(f"{name} is not JSON")

    return json.loads(reply.body.decode("utf-8"), parse_constant=refuse)


def nested(depth: int, inner: str = '"agent"') -> str:
    return "[" * depth + inner + "]" * depth


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            b'{"tool": "list_cases", "args": {}, "actor": "\\ud800"}',
            "the body holds text that is not valid Unicode",
        ),
        (b'{"tool": "x\\udc00", "args": {}, "actor": "human"}', "not valid Unicode"),
        (
            b'{"tool": "open_case", "args": {"text": "Pay now \\ud83d"}, "actor": "human"}',
            "half of a character, such as an emoji cut in two",
        ),
        (b'{"tool": "list_cases", "args": {"\\udfff": 1}, "actor": "human"}', "not valid Unicode"),
        (
            f'{{"tool": "list_cases", "args": {{}}, "actor": {nested(MAX_DEPTH)}}}'.encode(),
            f"the body is nested more than {MAX_DEPTH} levels deep",
        ),
        (f'{{"a": {nested(500)}}}'.encode(), "nested more than"),
        (
            b'{"tool": "investigate", "args": {"case_id": "case_001", "max_searches": 1e400}, '
            b'"actor": "agent"}',
            "the body holds a number too large to be finite",
        ),
        (b'{"tool": "list_cases", "args": {"n": -1E999}}', "too large to be finite"),
        (b'{"tool": "list_cases", "args": {"n": Infinity}}', "Infinity, which is not a JSON"),
    ],
    ids=[
        "actor",
        "tool",
        "cut-emoji",
        "key",
        "too-deep",
        "far-too-deep",
        "1e400",
        "-1e999",
        "inf",
    ],
)
def test_a_body_that_could_not_go_back_out_as_json_is_400_and_never_kept(app, body, error):
    reply = app.request("POST", "/api/invoke", body)

    assert reply.status == 400
    assert error in reply.json()["error"]
    assert app.log() == [] and app.store.state()["cases"] == []
    for path in ("/api/state", "/api/calls"):
        answer = app.get(path)
        assert answer.status == 200 and strict(answer)


def test_a_call_nested_as_deep_as_allowed_is_refused_by_invoke_and_the_state_still_reads(app):
    body = f'{{"tool": "list_cases", "args": {{}}, "actor": {nested(MAX_DEPTH - 1)}}}'.encode()

    reply = app.request("POST", "/api/invoke", body)

    assert (reply.status, reply.json()["outcome"]) == (200, "refused")
    assert app.get("/api/state").status == 200


class Garbled(SearchProvider):
    """The fake provider, with a lone surrogate on every text and a NaN and an infinity added
    to every response, the way a truncated or odd search result could arrive."""

    name = "fake"

    def __init__(self):
        self.inner = FakeSearchProvider.from_fixtures()

    def search(self, params):
        result = self.inner.search(params)
        data = {**garble(result.data), "rating": float("nan"), "reviews": float("inf")}
        return dataclasses.replace(result, data=data)

    def account(self):
        return {**self.inner.account(), "this_hour_searches": float("inf")}


def garble(value):
    if isinstance(value, str):
        return value + " \ud83d"
    if isinstance(value, dict):
        return {k: garble(v) for k, v in value.items()}
    if isinstance(value, list):
        return [garble(v) for v in value]
    return value


def test_a_search_result_that_is_not_fit_for_json_is_made_fit_before_it_is_kept(web_dir):
    app = App(Store(Garbled()), web_dir)
    try:
        case_id = app.investigated("a")
        budget = app.call("search_budget", {})
        drafted = app.call("draft_verdict", {"case_id": case_id})
        state, board = app.get("/api/state"), app.get("/api/board.html")
    finally:
        app.close()

    assert [r.status for r in (budget, drafted, state, board)] == [200] * 4
    assert strict(budget)["result"]["account"]["this_hour_searches"] is None
    kept = json.dumps(strict(state), ensure_ascii=False)
    assert "\ufffd" in kept and not re.search("[\ud800-\udfff]", kept)
    assert strict(drafted)["result"]["band"] == "high_risk"


def test_every_json_answer_is_ascii(app):
    case_id = app.investigated("a")
    for reply in (app.get("/api/state"), app.call("get_case", {"case_id": case_id})):
        assert reply.body.isascii() and "₹2,499" in json.dumps(strict(reply), ensure_ascii=False)


def test_replay_names_the_dates_its_responses_were_recorded_on(tmp_path, web_dir):
    empty = ReplaySearchProvider(tmp_path / "none")
    recordings = tmp_path / "recordings"
    params = {"engine": "google", "q": "Brand official site"}
    write_recording("a", params, {"organic_results": []}, recorded_at="2026-09-28T11:00:00+05:30",
                    recordings_dir=recordings)  # fmt: skip
    for provider, recorded in [(empty, []), (ReplaySearchProvider(recordings), ["2026-09-28"])]:
        app = App(Store(provider), web_dir)
        try:
            server = app.get("/api/state").json()["server"]
        finally:
            app.close()
        assert server == {
            "bind": f"127.0.0.1:{app.port}",
            "provider": "replay",
            "key": "missing",
            "recorded": recorded,
        }


def test_a_content_length_that_is_not_a_number_is_400(app):
    head, body = app.raw(
        f"POST /api/invoke HTTP/1.1\r\nHost: 127.0.0.1:{app.port}\r\n"
        "Content-Type: application/json\r\nContent-Length: ten\r\n\r\n{}".encode()
    )
    assert head.startswith("HTTP/1.0 400 ")
    assert json.loads(body)["error"] == "the Content-Length is not a number"


# ---- static files ---------------------------------------------------------------------------


def test_the_page_and_its_two_files_are_served_from_web(app, web_dir):
    for path, name, kind in [
        ("/", "index.html", "text/html; charset=utf-8"),
        ("/?sample=a", "index.html", "text/html; charset=utf-8"),
        ("/app.js", "app.js", "text/javascript; charset=utf-8"),
        ("/app.css", "app.css", "text/css; charset=utf-8"),
    ]:
        reply = app.get(path)
        assert (reply.status, reply.headers["Content-Type"]) == (200, kind)
        assert reply.body == (web_dir / name).read_bytes()


def test_a_missing_page_file_is_404(app, web_dir):
    (web_dir / "app.css").unlink()
    assert app.get("/app.css").status == 404


@pytest.mark.parametrize(
    "path",
    [
        "/../pyproject.toml",
        "/web/../README.md",
        "/%2e%2e/pyproject.toml",
        "/%2e%2e%2fpyproject.toml",
        "/..%2f..%2fpyproject.toml",
        "/app.js/../../pyproject.toml",
        "//pyproject.toml",
        "/.env",
        "/../.env",
        "/index.html",
        "/web/app.js",
        "/web/",
        "/APP.JS",
        "/app.js%00.css",
        "/app.js/",
    ],
)
def test_nothing_but_the_three_names_is_served(app, path):
    reply = app.get(path)
    assert reply.status == 404
    assert OUTSIDE.encode() not in reply.body


# ---- every answer ---------------------------------------------------------------------------


def test_every_answer_carries_the_security_headers_and_no_cors_grant(app, monkeypatch):
    assert CSP == (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    app.get("/")
    app.get("/app.js")
    app.get("/api/state")
    app.get("/api/calls")
    app.get("/api/board.html")
    app.call("list_cases", {})
    app.call("list_cases", {}, headers={"Origin": "http://evil.example"})
    app.get("/nope")
    app.get("/api/invoke")
    app.request("POST", "/api/invoke", b"[")
    app.request("POST", "/api/invoke", b"x" * (MAX_BODY + 1))
    monkeypatch.setattr(app.store, "state", lambda: 1 / 0)
    app.get("/api/state")
    assert sorted({r.status for r in app.replies}) == [200, 400, 403, 404, 405, 413, 500]

    for reply in app.replies:
        assert reply.headers["Content-Security-Policy"] == CSP
        assert reply.headers["X-Content-Type-Options"] == "nosniff"
        assert reply.headers["Referrer-Policy"] == "no-referrer"
        assert reply.headers["Cache-Control"] == "no-store"
        assert reply.headers["Server"] == "OfferCheckpost"
        assert not [h for h in reply.headers if h.lower().startswith("access-control-")]


@pytest.mark.parametrize(
    ("request_line", "status"),
    [
        (b"GARBAGE\r\n\r\n", 400),
        (b"GET / HTTP/9.9\r\n\r\n", 505),
        (b"BREW / HTTP/1.0\r\n\r\n", 501),
    ],
)
def test_a_request_the_parser_rejects_still_gets_json_and_the_headers(app, request_line, status):
    head, body = app.raw(request_line)

    assert head.startswith(f"HTTP/1.0 {status} ")
    assert f"Content-Security-Policy: {CSP}" in head.split("\r\n")
    assert "X-Content-Type-Options: nosniff" in head.split("\r\n")
    assert json.loads(body)["ok"] is False
    assert b"GARBAGE" not in body and b"BREW" not in body


def test_an_unexpected_failure_is_a_500_that_carries_no_exception_text(app, monkeypatch, capsys):
    key = hashlib.sha256(b"offer-checkpost server test").hexdigest()

    def boom(call, args):
        raise RuntimeError(f"GET https://serpapi.com/search?api_key={key} failed")

    monkeypatch.setitem(TOOLS, "get_case", dataclasses.replace(TOOLS["get_case"], handler=boom))

    reply = app.call("get_case", {"case_id": "case_001"})

    assert reply.status == 500
    assert reply.json() == {"ok": False, "outcome": "error", "error": "internal error"}
    assert key not in reply.text and "serpapi.com" not in reply.text
    last = app.log()[-1]
    assert (last["tool"], last["result"], last["reason"]) == ("get_case", "error", "RuntimeError")
    err = capsys.readouterr().err
    assert err == "offer_checkpost: internal error on POST /api/invoke: RuntimeError\n"


def test_no_answer_ever_carries_a_key_shaped_string(tmp_path, web_dir, serpapi_stub):
    """The live provider on a real ``serpapi.Client`` whose every request URL carries a 64-hex
    key (built at runtime): an Account API reply that echoes the key and the account email, a
    timeout whose text holds the URL, a 401 and a 400 that echo the key. Every answer the
    server gives, headers included, is then free of it and of anything shaped like it."""
    key = serpapi_stub.api_key
    account = {
        "api_key": key,
        "account_email": "owner@mail.example",
        "plan_searches_left": 240,
        "searches_per_month": 250,
        "this_month_usage": 10,
        "this_hour_searches": 1,
        "account_rate_limit_per_hour": 50,
    }

    def timeout(url):
        return requests.exceptions.ReadTimeout(f"Read timed out. (url: {url})")

    (
        serpapi_stub.reply(account)  # search_budget
        .reply(account)  # the first investigation's quota read
        .fail(timeout)  # its first search
        .reply({"error": f"Invalid API key {key}."}, status=401)  # the second's quota read
        .reply({"error": f"Unsupported value {key}."}, status=400)  # search_budget again
    )
    provider = SerpApiSearchProvider(
        client=serpapi_stub.client, cache_dir=tmp_path / "cache", no_cache=False
    )
    app = App(Store(provider, key_set=True), web_dir)
    try:
        budget = app.ok("search_budget", {})
        case_id = app.opened("a")
        first = app.ok("investigate", {"case_id": case_id})
        second = app.ok("investigate", {"case_id": case_id})
        refused_budget = app.call("search_budget", {}).json()
        for path in ("/", "/app.js", "/app.css", "/api/state", "/api/calls", "/api/tools"):
            app.get(path)
        app.get("/api/samples")
        app.get("/api/board.html")
        app.call("get_case", {"case_id": case_id})
        app.call("list_cases", {})
    finally:
        app.close()

    assert budget == {"provider": "live", "account": {k: account[k] for k in list(account)[2:]}}
    assert first["budget"]["stoppedBecause"] == "search_error"
    assert second["budget"]["stoppedBecause"] == "search_error"
    assert refused_budget == {
        "ok": False,
        "outcome": "error",
        "error": "bad query: Unsupported value [key].",
    }
    assert len(serpapi_stub.urls) == 5
    assert all(f"api_key={key}" in url for url in serpapi_stub.urls), "the key went to SerpApi"
    assert len(app.replies) == 16
    for reply in app.replies:
        head = "".join(f"{name}: {value}\n" for name, value in reply.headers.items())
        seen = head + reply.body.decode("utf-8")
        assert key not in seen
        assert not KEY_SHAPED.search(seen)
