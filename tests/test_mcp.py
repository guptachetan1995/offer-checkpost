"""The MCP adapter as an MCP client runs it: ``python -m offer_checkpost mcp`` in a subprocess,
spoken to over its pipes by a scripted JSON-RPC client, in front of the real server on a
loopback socket with the synthetic fixtures.

It answers initialize with the version the client asked for when it speaks it, lists the
agent's tools and no human-only verb, and passes each tool call to the app's one ``invoke``,
where the server logs it as the agent's: a call to a verb comes back as a tool error with the
app's refusal, and the refusal is in the activity log. A notification gets no answer, a line
that isn't JSON or a method it doesn't have gets the JSON-RPC error for it, and with no app
running a call says how to start one. Its stdout carries JSON-RPC answers and nothing else, and
an ``http_proxy`` in its environment is never used."""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from offer_checkpost import __version__, cli
from offer_checkpost.invoke import invoke
from offer_checkpost.mcp_server import INSTRUCTIONS, not_running
from offer_checkpost.providers import FakeSearchProvider
from offer_checkpost.server import make_server
from offer_checkpost.store import Store
from offer_checkpost.tools import TOOLS, listing
from offer_checkpost.verbs import VERBS

pytestmark = pytest.mark.loopback

ENTRY = Path(__file__).resolve().parents[1]
# The synthetic stand-ins of the demo samples, which the fake provider's fixtures answer.
SAMPLES = ENTRY / "tests" / "fixtures" / "offers"
WAIT = 20


class Client:
    """A scripted MCP client: the adapter in a subprocess, one JSON-RPC message a line each
    way. ``lines`` keeps every line the adapter wrote to stdout."""

    def __init__(self, url: str, cwd: Path):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("SERPAPI_KEY", "PORT", "NO_PROXY", "no_proxy")
            and not k.startswith("OFFER_CHECKPOST_")
        }
        env["PYTHONPATH"] = str(ENTRY / "src")
        # Nothing listens on port 9: a request that went through this proxy would fail.
        env["http_proxy"] = env["HTTP_PROXY"] = "http://127.0.0.1:9"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "offer_checkpost", "mcp", "--url", url],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.lines: list[bytes] = []
        self._out: queue.Queue[bytes] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._next = 0

    def _read(self) -> None:
        for line in self.process.stdout:
            self.lines.append(line)
            self._out.put(line)

    def send(self, message: dict | bytes) -> None:
        line = message if isinstance(message, bytes) else json.dumps(message).encode()
        self.process.stdin.write(line + b"\n")
        self.process.stdin.flush()

    def receive(self) -> dict:
        return json.loads(self._out.get(timeout=WAIT))

    def request(self, method: str, params: dict | None = None) -> dict:
        """One request and its answer, whose id must be the request's."""
        self._next += 1
        message = {"jsonrpc": "2.0", "id": self._next, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        answer = self.receive()
        assert answer["id"] == self._next, answer
        return answer

    def result(self, method: str, params: dict | None = None) -> dict:
        answer = self.request(method, params)
        assert set(answer) == {"jsonrpc", "id", "result"}, answer
        return answer["result"]

    def call(self, name: str, arguments: dict | None = None, **extra) -> tuple[bool, str]:
        """``tools/call``: whether it was a tool error, and its one text block."""
        params = {"name": name, **extra}
        if arguments is not None:
            params["arguments"] = arguments
        result = self.result("tools/call", params)
        [block] = result["content"]
        assert set(result) == {"content", "isError"} and block["type"] == "text"
        return result["isError"], block["text"]

    def envelope(self, name: str, arguments: dict | None = None, **extra) -> tuple[bool, dict]:
        error, text = self.call(name, arguments, **extra)
        envelope = json.loads(text)
        assert text.startswith("{\n  "), "the envelope as indented JSON"
        assert error is (not envelope["ok"])
        return error, envelope

    def close(self) -> str:
        """Ends the session as a client does, by closing stdin; returns stderr. Every line on
        stdout was an answer this client read, and a JSON-RPC 2.0 response."""
        self.process.stdin.close()
        try:
            self.process.wait(WAIT)
        finally:
            if self.process.poll() is None:
                self.process.kill()
        self._reader.join(WAIT)
        err = self.process.stderr.read().decode("utf-8")
        self.process.stderr.close()
        assert self.process.returncode == 0, err
        assert self._out.empty(), f"an answer nobody asked for: {self._out.get()!r}"
        for line in self.lines:
            assert line.endswith(b"\n") and line.count(b"\n") == 1 and line.isascii(), line
            message = json.loads(line)
            assert message["jsonrpc"] == "2.0" and "id" in message, message
            assert len({"result", "error"} & set(message)) == 1, message
        return err


@pytest.fixture
def app():
    """The real server for a store on the synthetic fixtures, serving on a free port."""
    store = Store(FakeSearchProvider.from_fixtures())
    server = make_server(store, port=0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield store, f"http://127.0.0.1:{server.port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.fixture
def client(app, tmp_path):
    """The adapter pointed at the app, started outside the checkout so no .env is read."""
    started = Client(app[1], tmp_path)
    yield started
    if started.process.poll() is None:
        started.process.kill()
        started.process.wait(WAIT)


def entries(store) -> list[tuple[str, str, str]]:
    return [(e["actor"], e["tool"], e["result"]) for e in store.state()["activityLog"]]


def sample(name: str) -> str:
    return (SAMPLES / f"{name}.txt").read_text("utf-8")


# ---- the handshake ----------------------------------------------------------------------------


def test_initialize_answers_the_version_asked_for_when_it_is_spoken_else_the_newest(client):
    answered = {}
    for asked in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05", "1999-01-01"):
        result = client.result(
            "initialize",
            {"protocolVersion": asked, "capabilities": {}, "clientInfo": {"name": "script"}},
        )
        answered[asked] = result.pop("protocolVersion")
        assert result == {
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "offer-checkpost", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    assert answered == {
        "2025-11-25": "2025-11-25",
        "2025-06-18": "2025-06-18",
        "2025-03-26": "2025-03-26",
        "2024-11-05": "2025-11-25",
        "1999-01-01": "2025-11-25",
    }
    assert client.close() == ""


def test_the_instructions_say_what_stays_with_the_person():
    assert "\n" not in INSTRUCTIONS
    for words in (
        "Publishing a verdict to the Offer Board, retracting one, recording what the person "
        "decided and running checks past a decisive result are the person's, in the app",
        "a call that tries is refused and logged",
        "activity log",
    ):
        assert words in INSTRUCTIONS


def test_a_notification_gets_no_answer(app, client):
    store, _ = app
    client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    client.send(
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
    )
    client.send({"jsonrpc": "2.0", "method": "notifications/anything-else"})
    client.send({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "open_case"}})
    # A response to a request the adapter never sends is not answered either.
    client.send({"jsonrpc": "2.0", "id": 99, "result": {}})
    client.send(b"")

    # The first answer on stdout is the ping's: nothing came before it.
    assert client.result("ping") == {}
    assert client.result("ping", {}) == {}
    assert client.close() == ""
    assert store.state()["activityLog"] == [], "a tools/call without an id runs nothing"


# ---- the tools --------------------------------------------------------------------------------


def test_the_tool_list_is_the_agents_17_tools_and_no_human_only_verb(client):
    tools = client.result("tools/list")["tools"]

    names = [t["name"] for t in tools]
    assert len(names) == 17 and names == list(TOOLS)
    assert not set(names) & set(VERBS)
    assert not set(names) & {
        "publish_verdict",
        "retract_verdict",
        "record_outcome",
        "run_remaining_checks",
    }
    assert tools == listing()
    assert all(set(t) == {"name", "description", "inputSchema"} for t in tools)
    assert all(t["inputSchema"]["type"] == "object" for t in tools)
    assert client.result("tools/list", {"cursor": "ignored"})["tools"] == tools
    assert client.close() == ""


def test_open_case_runs_in_the_app_as_the_agent(app, client):
    store, _ = app

    error, envelope = client.envelope("open_case", {"text": sample("a")})

    assert error is False and envelope["ok"] is True
    assert envelope["result"]["id"] == "case_001"
    assert envelope["result"]["sourceText"] == sample("a")
    assert store.cases["case_001"]["sourceText"] == sample("a")
    assert entries(store) == [("agent", "open_case", "ok")]
    assert client.close() == ""


def test_update_claims_then_investigate_and_draft_run_as_the_agent(app, client):
    store, _ = app
    case_id = client.envelope("open_case", {"text": sample("a")})[1]["result"]["id"]

    _, unconfirmed = client.envelope("investigate", {"case_id": case_id})
    _, confirmed = client.envelope("update_claims", {"case_id": case_id, "confirm": True})
    _, investigated = client.envelope("investigate", {"case_id": case_id})
    _, drafted = client.envelope("draft_verdict", {"case_id": case_id})

    assert unconfirmed["outcome"] == "refused"
    assert confirmed["ok"] is True
    assert store.cases[case_id]["claims"]["company"]["confirmed"] is True
    assert investigated["ok"] is True
    assert investigated["result"]["band"] == "high_risk"
    assert investigated["result"]["budget"]["spent"] == 2
    assert investigated["result"]["budget"]["stoppedBecause"] == "decisive"
    assert drafted["result"]["band"] == "high_risk"
    assert store.cases[case_id]["draftVerdict"] == drafted["result"]
    assert entries(store) == [
        ("agent", "open_case", "ok"),
        ("agent", "investigate", "refused"),
        ("agent", "update_claims", "ok"),
        ("agent", "investigate", "ok"),
        ("agent", "lookup_official_site", "ok"),
        ("agent", "find_fraud_notice", "ok"),
        ("agent", "draft_verdict", "ok"),
    ]
    assert [c["provider"] for c in store.calls] == ["fake", "fake"]
    assert client.close() == ""


def test_a_human_only_verb_is_a_tool_error_carrying_the_apps_refusal_logged_as_the_agents(
    app, client
):
    store, _ = app
    case_id = invoke("open_case", {"text": sample("a")}, "human", store=store)["result"]["id"]
    invoke("update_claims", {"case_id": case_id, "confirm": True}, "human", store=store)
    invoke("investigate", {"case_id": case_id}, "human", store=store)
    invoke("draft_verdict", {"case_id": case_id}, "human", store=store)
    # Arguments a person's click would send, so only who is calling stands in the way.
    publish = {
        "case_id": case_id,
        "label": "likely_impersonation",
        "note": "",
        "revision": store.cases[case_id]["revision"],
    }
    verb_args = {
        "publish_verdict": publish,
        "retract_verdict": {"case_id": case_id, "reason": "the agent says so"},
        "record_outcome": {"case_id": case_id, "outcome": "walked_away"},
        "run_remaining_checks": {"case_id": case_id},
    }
    assert set(verb_args) == set(VERBS)
    before = store.state()
    logged = len(before["activityLog"])

    refused = {verb: client.envelope(verb, args) for verb, args in verb_args.items()}
    # Claiming to be the person, beside the call or inside its arguments, changes nothing.
    claimed = client.envelope("publish_verdict", publish, actor="human")
    inside = client.envelope("publish_verdict", {**publish, "actor": "human"})
    unknown = client.envelope("delete_case", {"case_id": case_id})

    for verb, (error, envelope) in refused.items():
        assert error is True
        assert (envelope["ok"], envelope["outcome"]) == (False, "refused")
        assert envelope["error"].startswith(f"{verb} is human-only: only a person can ")
        assert "It is not an agent tool, and nothing was changed" in envelope["error"]
    assert claimed == refused["publish_verdict"]
    assert inside[1]["error"].startswith("publish_verdict is human-only")
    assert unknown[0] is True
    assert unknown[1]["error"] == (
        "unknown tool 'delete_case': the agent's tools are the ones in its tool list"
    )
    after = store.state()
    assert after["board"] == [] and after["cases"] == before["cases"]
    assert entries(store)[logged:] == [
        ("agent", "publish_verdict", "refused"),
        ("agent", "retract_verdict", "refused"),
        ("agent", "record_outcome", "refused"),
        ("agent", "run_remaining_checks", "refused"),
        ("agent", "publish_verdict", "refused"),
        ("agent", "publish_verdict", "refused"),
        ("agent", "delete_case", "refused"),
    ]
    assert client.close() == ""


def test_a_refusal_before_invoke_comes_back_as_the_apps_own_envelope(app, client):
    store, _ = app

    # Half of an emoji: the server refuses the body before invoke, with the reason.
    error, envelope = client.envelope("open_case", {"text": "Pay the fee now \ud83d"})

    assert error is True
    assert envelope["outcome"] == "error"
    assert "the body holds text that is not valid Unicode" in envelope["error"]
    assert store.state()["activityLog"] == []
    assert client.close() == ""


# ---- errors -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [b"{not json", b"\xff\xfe{}", b"[" * 100_000, b'{"jsonrpc": "2.0", "id": 1'],
    ids=["not-json", "not-utf8", "too-deep", "cut-short"],
)
def test_a_line_that_is_not_json_is_a_parse_error_with_a_null_id(client, line):
    client.send(line)

    answer = client.receive()

    assert answer["id"] is None
    assert answer["error"]["code"] == -32700
    assert client.result("ping") == {}, "the adapter keeps answering"
    assert client.close() == ""


@pytest.mark.parametrize("method", ["resources/list", "prompts/list", "tools/nope", "Ping"])
def test_a_method_the_adapter_does_not_have_is_method_not_found(client, method):
    answer = client.request(method, {})

    assert answer["error"]["code"] == -32601
    assert answer["error"]["message"] == f"method not found: {method!r}"
    assert client.close() == ""


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("initialize", {}),
        ("initialize", {"protocolVersion": 20251125}),
        ("tools/call", {}),
        ("tools/call", {"name": 5}),
        ("tools/call", {"name": "list_cases", "arguments": ["status"]}),
        ("tools/call", {"name": "list_cases", "arguments": "{}"}),
        ("tools/list", ["cursor"]),
    ],
)
def test_params_that_are_not_what_the_method_takes_are_invalid_params(app, client, method, params):
    store, _ = app

    answer = client.request(method, params)

    assert answer["error"]["code"] == -32602
    assert store.state()["activityLog"] == []
    assert client.close() == ""


@pytest.mark.parametrize(
    "message",
    [
        [{"jsonrpc": "2.0", "id": 1, "method": "ping"}],
        {"id": 1, "method": "ping"},
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": None, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1.5, "method": "ping"},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
        "ping",
    ],
    ids=[
        "batch",
        "no-jsonrpc",
        "old-jsonrpc",
        "no-method",
        "null-id",
        "float-id",
        "bool-id",
        "str",
    ],
)
def test_a_message_that_is_not_a_request_is_an_invalid_request(client, message):
    client.send(message)

    answer = client.receive()

    assert answer["error"]["code"] == -32600
    assert answer["id"] in (None, 1)
    assert client.close() == ""


def test_with_no_app_running_the_calls_say_how_to_start_it(tmp_path):
    # A port that was free a moment ago, as after the app stopped. (A socket bound but never
    # listening would do on Linux, but macOS drops the connection attempt instead of refusing it.)
    with socket.socket() as freed:
        freed.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{freed.getsockname()[1]}"
    client = Client(url, tmp_path)
    try:
        handshake = client.result("initialize", {"protocolVersion": "2025-11-25"})
        listed = client.request("tools/list")
        called = client.call("open_case", {"text": sample("a")})
    finally:
        err = client.close()

    message = (
        f"Offer Checkpost is not running at {url}: start it with python -m offer_checkpost serve"
    )
    assert message == not_running(url)
    assert handshake["serverInfo"]["name"] == "offer-checkpost"
    assert listed["error"] == {"code": -32000, "message": message}
    assert called == (True, message)
    assert err == ""


def test_localhost_reaches_the_app_and_never_a_program_on_the_same_port_of_ipv6(app, tmp_path):
    """The app listens on 127.0.0.1 alone, which leaves its port free on ::1, where
    ``localhost`` can resolve first. A program there would get the pasted messages and could
    serve its own tool descriptions; the adapter asks 127.0.0.1 whatever ``--url`` names."""
    store, url = app
    port = int(url.rsplit(":", 1)[1])
    heard = []
    try:
        squatter = socket.socket(socket.AF_INET6)
        squatter.bind(("::1", port))
    except OSError as e:
        pytest.skip(f"no IPv6 loopback to listen on: {e.strerror}")
    with squatter:
        squatter.listen()
        squatter.settimeout(0.2)
        stop = threading.Event()

        def listen():
            while not stop.is_set():
                try:
                    conn, _ = squatter.accept()
                except TimeoutError:
                    continue
                with conn:
                    heard.append(conn.recv(65536))

        listening = threading.Thread(target=listen, daemon=True)
        listening.start()
        client = Client(f"http://localhost:{port}", tmp_path)
        try:
            listed = client.result("tools/list")
            error, opened = client.envelope("open_case", {"text": sample("a")})
        finally:
            err = client.close()
            stop.set()
            listening.join(5)

    assert [t["name"] for t in listed["tools"]] == list(TOOLS)
    assert (error, opened["result"]["id"]) == (False, "case_001")
    assert entries(store) == [("agent", "open_case", "ok")]
    assert heard == [] and err == ""


# ---- the command line -------------------------------------------------------------------------


def test_the_app_address_comes_from_url_then_port_then_8741():
    parser = cli._parser()
    assert cli._app_url(parser, None, {}) == "http://127.0.0.1:8741"
    assert cli._app_url(parser, None, {"PORT": " 9100 "}) == "http://127.0.0.1:9100"
    assert cli._app_url(parser, "http://127.0.0.1:8800", {"PORT": "9100"}) == (
        "http://127.0.0.1:8800"
    )
    # localhost can resolve to ::1 first, where the app doesn't listen and anything else may.
    assert cli._app_url(parser, "http://localhost:8800/", {}) == "http://127.0.0.1:8800"


def test_port_0_says_to_give_the_bare_address_the_adapter_takes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["mcp"], {"PORT": "0"})
    assert (
        "give --url http://127.0.0.1:<port>, with the port in the address serve printed and "
        "without the token" in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (["mcp", "--url", "http://127.0.0.1:8741/?token=abc"], {}),
        (["mcp", "--url", "http://127.0.0.1:8741/api"], {}),
        (["mcp", "--url", "https://127.0.0.1:8741"], {}),
        (["mcp", "--url", "http://192.0.2.1:8741"], {}),
        (["mcp", "--url", "http://[::1]:8741"], {}),
        (["mcp", "--url", "http://127.0.0.1"], {}),
        (["mcp", "--url", "http://127.0.0.1:0"], {}),
        (["mcp", "--url", "http://127.0.0.1:port"], {}),
        (["mcp", "--url", "http://someone@127.0.0.1:8741"], {}),
        (["mcp", "--url", "file:///etc/hosts"], {}),
        (["mcp"], {"PORT": "0"}),
        (["mcp"], {"PORT": "web"}),
    ],
)
def test_anything_but_the_apps_bare_address_is_a_usage_error(capsys, argv, environ):
    with pytest.raises(SystemExit) as exit_:
        cli.main(argv, environ)

    assert exit_.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and "Traceback" not in err
    assert "abc" not in err, "the token given is not echoed"


def test_help_names_the_adapter(capsys):
    with pytest.raises(SystemExit):
        cli.main(["-h"], {})
    assert "mcp" in capsys.readouterr().out
