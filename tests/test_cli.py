"""The command line: ``investigate`` prints each sample's trace, band, draft verdict and activity
log through the same ``invoke`` the app uses; ``--as agent`` makes every call the agent's; replay
serves only what was recorded; ``serve`` answers on 127.0.0.1 at the address it prints, whose
token makes the browser that opens it the person once and then prints the next address, and
stops cleanly on Ctrl-C; run as a program, it reads ``.env`` under the environment."""

import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from offer_checkpost import cli, server
from offer_checkpost.providers import (
    FIXTURES_DIR,
    ROUTES_FILE,
    ReplaySearchProvider,
    write_recording,
)

ENTRY = Path(__file__).resolve().parents[1]
SAMPLES = ENTRY / "samples" / "offers"
ROUTES = json.loads((FIXTURES_DIR / ROUTES_FILE).read_text(encoding="utf-8"))


def run(capsys, name, *flags, environ=None):
    code = cli.main(["investigate", str(SAMPLES / f"{name}.txt"), *flags], environ or {})
    out, err = capsys.readouterr()
    return code, out, err


def section(out, title):
    """The lines under a heading such as "Trace", up to the next blank line."""
    lines = out.split("\n")
    start = lines.index(title) + 1
    return lines[start : lines.index("", start)]


def headings(out):
    return [line for line in section(out, "Trace") if not line.startswith(" ")]


def test_sample_a_prints_its_trace_two_searches_and_four_saved(capsys):
    code, out, err = run(capsys, "a", "--provider", "fake")

    assert (code, err) == (0, "")
    assert out.startswith("Offer Checkpost · investigate ")
    assert out.split("\n")[0].endswith("a.txt · provider fake · as human")
    assert headings(out) == [
        "step 0   text_rules · agent",
        "step 1   lookup_official_site · google (fake, cache miss) · 1 search · agent",
        "step 2   find_fraud_notice · google (fake, cache miss) · 1 search · agent",
        "stop     investigate · agent",
    ]
    trace = section(out, "Trace")
    assert "         signal   sig_2 sender_lookalike (red, moderate)" in trace
    assert (
        "         because  a fee was asked and step 1 named the official domain brand.example"
        in trace
    )
    assert "         signal   sig_3 fee_contradicts_employer (red, strong)" in trace
    assert "band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive" in out
    verdict = section(out, "Draft verdict (a draft: only a person publishes)")
    assert verdict[0].startswith("  2 strong red signals, 1 of them from a search result")
    assert verdict[-1] == "  Searches: 2 spent, 4 not spent: the evidence was decisive."
    assert section(out, "Activity log") == [
        "  human  open_case                ok",
        "  human  update_claims            ok",
        "  human  investigate              ok",
        "  agent  lookup_official_site     ok",
        "  agent  find_fraud_notice        ok",
        "  human  draft_verdict            ok",
    ]


def test_sample_b_prints_the_r2_reorder_and_every_skip(capsys):
    code, out, _ = run(capsys, "b", "--provider", "fake")

    assert code == 0
    assert headings(out) == [
        "step 0   text_rules · agent",
        "step 1   lookup_official_site · google (fake, cache miss) · 1 search · agent",
        "reorder  check_job_listings · agent",
        "skip     find_fraud_notice · agent",
        "step 2   check_job_listings · google_jobs (fake, cache miss) · 1 search · agent",
        "skip     confirm_sender_domain · agent",
        "step 3   check_office · google_maps (fake, cache miss) · 1 search · agent",
        "skip     scan_office_reviews · agent",
        "step 4   check_scam_reports · google_news (fake, cache miss) · 1 search · agent",
        "skip     check_contact_footprint · agent",
        "stop     investigate · agent",
    ]
    assert "band consistent_with_genuine · 4 searches spent of 6 · stopped: done" in out


def test_sample_c_as_the_agent_makes_every_call_the_agents(capsys):
    code, out, _ = run(capsys, "c", "--provider", "fake", "--as", "agent")

    assert code == 0
    assert out.split("\n")[0].endswith("· as agent")
    assert headings(out) == [
        "step 0   text_rules · agent",
        "step 1   lookup_official_site · google (fake, cache miss) · 1 search · agent",
        "reorder  check_office · agent",
        "skip     find_fraud_notice · agent",
        "skip     confirm_sender_domain · agent",
        "step 2   check_office · google_maps (fake, cache miss) · 1 search · agent",
        "stop     investigate · agent",
    ]
    assert "band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive" in out
    assert {line.split()[0] for line in section(out, "Activity log")} == {"agent"}
    assert "  phone    +91 9XXXX XXXXX (a placeholder: never searched)" in out


def test_without_a_provider_flag_the_environment_names_it(capsys):
    code, out, _ = run(capsys, "a", environ={"OFFER_CHECKPOST_PROVIDER": "fake"})
    assert code == 0
    assert out.split("\n")[0].endswith("provider fake · as human")


def test_replay_serves_the_recorded_searches_and_names_their_date(capsys, monkeypatch, tmp_path):
    for route in (r for r in ROUTES if r["sample"] == "a"):
        response = json.loads((FIXTURES_DIR / route["fixture"]).read_text(encoding="utf-8"))
        write_recording(
            "a",
            route["params"],
            response,
            recorded_at="2026-10-03T10:02:11+05:30",
            recordings_dir=tmp_path,
        )
    monkeypatch.setitem(cli.PROVIDERS, "replay", lambda: ReplaySearchProvider(tmp_path))

    code, out, _ = run(capsys, "a", "--provider", "replay")

    assert code == 0
    assert "provider replay (recorded SerpApi responses: 2026-10-03; not live)" in out
    assert headings(out)[1:3] == [
        "step 1   lookup_official_site · google (replay, cache replay) · 1 search · agent",
        "step 2   find_fraud_notice · google (replay, cache replay) · 1 search · agent",
    ]
    assert "band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive" in out


def test_replay_with_nothing_recorded_fails_the_search_and_makes_nothing_up(
    capsys, monkeypatch, tmp_path
):
    monkeypatch.setitem(cli.PROVIDERS, "replay", lambda: ReplaySearchProvider(tmp_path))

    code, out, err = run(capsys, "a", "--provider", "replay")

    assert code == 1
    assert err == "offer_checkpost: the investigation is incomplete: a search failed\n"
    assert "nothing recorded yet; not live" in out
    assert headings(out) == [
        "step 0   text_rules · agent",
        "step 1   lookup_official_site · google · FAILED · agent",
        "stop     investigate · agent",
    ]
    assert "band unverified · 0 searches spent of 6 · stopped: search_error" in out
    assert section(out, "Activity log")[3].startswith(
        "  agent  lookup_official_site     error: no recorded google response"
    )


def test_an_empty_message_is_refused_and_nothing_is_investigated(capsys, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    code = cli.main(["investigate", str(empty), "--provider", "fake"], {})
    out, err = capsys.readouterr()
    assert code == 1
    assert err.startswith("offer_checkpost: open_case refused: open_case was refused: ")
    assert "Trace" not in out


def test_a_missing_file_is_a_usage_error(capsys, tmp_path):
    with pytest.raises(SystemExit) as exit_:
        cli.main(["investigate", str(tmp_path / "nope.txt"), "--provider", "fake"], {})
    assert exit_.value.code == 2
    assert "cannot read" in capsys.readouterr().err


def get(port, path, session=None):
    """The status, the Set-Cookie header and the body of a GET, sent with ``session``'s
    headers."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=session or {})
        response = conn.getresponse()
        return response.status, response.getheader("Set-Cookie"), response.read()
    finally:
        conn.close()


def signed_in(port, url):
    """Opens ``url``, the address serve printed, as a browser and its page would: the headers
    that then make a request the person's (the cookie it sets, the key its redirect hands on)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", url.removeprefix(f"http://127.0.0.1:{port}"))
        response = conn.getresponse()
        assert (response.status, response.read()) == (303, b"")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        key = response.getheader("Location").removeprefix("/#session=")
    finally:
        conn.close()
    return {"Cookie": cookie, server.SESSION_HEADER: key}


@pytest.mark.loopback
def test_serve_answers_at_the_address_it_prints_and_stops_cleanly(capsys, monkeypatch, tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    page = b"<!doctype html><title>Offer Checkpost</title>\n"
    (web / "index.html").write_bytes(page)
    (web / "session.html").write_bytes(b"<!doctype html><title>Open the address</title>\n")
    monkeypatch.setattr(server, "WEB_DIR", web)
    started, ready, codes = [], threading.Event(), []

    def make_server(*args, **kwargs):
        started.append(server.make_server(*args, **kwargs))
        ready.set()
        return started[-1]

    monkeypatch.setattr(cli, "make_server", make_server)
    serving = threading.Thread(
        target=lambda: codes.append(cli.main(["serve", "--port", "0", "--provider", "fake"], {}))
    )
    serving.start()
    try:
        assert ready.wait(10)
        port, token = started[0].port, started[0].token
        without = get(port, "/")
        session = signed_in(port, f"http://127.0.0.1:{port}/?token={token}")
        answered = get(port, "/", session)
        following = started[0].url
    finally:
        if started:
            started[0].shutdown()
        serving.join(10)

    assert not serving.is_alive()
    assert codes == [0]
    assert without[::2] == (200, b"<!doctype html><title>Open the address</title>\n")
    assert answered[::2] == (200, page)
    out, err = capsys.readouterr()
    assert (out, err) == (
        f"Offer Checkpost on http://127.0.0.1:{port}/?token={token} · provider fake\n"
        f"{cli.SESSION_LINE}\n"
        f"{cli.OPENED_LINE.format(url=following)}\n",
        "",
    )
    assert token not in following
    assert cli.SESSION_LINE == (
        "That address works once, and makes the browser that opens it the person; anything "
        "else talking to this server is the agent."
    )
    assert cli.OPENED_LINE.format(url=following) == (
        "The address was opened: that browser is the person. To make another browser the "
        f"person instead, open {following}"
    )


@pytest.mark.loopback
@pytest.mark.skipif(
    sys.platform == "win32", reason="Ctrl-C on Windows is a console event, not a signal to send"
)
def test_serve_from_python_dash_m_stops_on_ctrl_c_with_exit_code_0():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SERPAPI_KEY", "PORT") and not k.startswith("OFFER_CHECKPOST_")
    }
    env["PYTHONPATH"] = str(ENTRY / "src")
    serving = subprocess.Popen(
        [sys.executable, "-m", "offer_checkpost", "serve", "--port", "0", "--provider", "fake"],
        cwd=ENTRY,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        # A runner started in the background can pass SIGINT on ignored; Python then never
        # turns it into KeyboardInterrupt.
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    try:
        line = serving.stdout.readline()
        match = re.fullmatch(
            r"Offer Checkpost on (http://127\.0\.0\.1:(\d+)/\?token=[A-Za-z0-9_-]{32}) · "
            r"provider fake\n",
            line,
        )
        assert match, line
        assert serving.stdout.readline() == f"{cli.SESSION_LINE}\n"
        port = int(match[2])
        status, _, body = get(port, "/api/tools")
        session = signed_in(port, match[1])
        calls = json.loads(get(port, "/api/calls", session)[2])
        following = serving.stdout.readline()
        serving.send_signal(signal.SIGINT)
        out, err = serving.communicate(timeout=10)
    finally:
        if serving.poll() is None:
            serving.kill()
            serving.communicate()

    assert status == 200 and json.loads(body)[0]["name"] == "open_case"
    assert calls["caller"] == "human"
    next_url = re.fullmatch(
        r".* open (http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]{32})\n", following
    )
    assert next_url and next_url[1] != match[1], following
    assert (serving.returncode, out, err) == (0, "", "")


def test_serve_says_so_when_the_port_is_taken(capsys):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        code = cli.main(["serve", "--port", str(port), "--provider", "fake"], {})
    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert err.startswith(f"offer_checkpost: cannot listen on 127.0.0.1:{port}: ")


def test_the_port_comes_from_the_flag_then_port_then_8741():
    parser = cli._parser()
    assert cli._port(parser, 9000, {"PORT": "9100"}) == 9000
    assert cli._port(parser, None, {"PORT": " 9100 "}) == 9100
    assert cli._port(parser, None, {"PORT": ""}) == 8741
    assert cli._port(parser, None, {}) == 8741


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (["serve", "--port", "70000"], {}),
        (["serve", "--port", "-1"], {}),
        (["serve"], {"PORT": "web"}),
    ],
)
def test_a_port_that_is_not_one_is_a_usage_error(capsys, argv, environ):
    with pytest.raises(SystemExit) as exit_:
        cli.main(argv, environ)
    assert exit_.value.code == 2
    assert "the port must be a whole number from 0 to 65535" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("environ", "says"),
    [
        ({"OFFER_CHECKPOST_PROVIDER": "live"}, "SERPAPI_KEY is not set"),
        ({"OFFER_CHECKPOST_PROVIDER": "bogus"}, "must be live, replay or fake, not 'bogus'"),
        ({"OFFER_CHECKPOST_MAX_SEARCHES": "six"}, "OFFER_CHECKPOST_MAX_SEARCHES must be a whole"),
        ({"OFFER_CHECKPOST_QUOTA_RESERVE": "-1"}, "OFFER_CHECKPOST_QUOTA_RESERVE must be a whole"),
    ],
)
@pytest.mark.parametrize(
    "command", [["serve", "--port", "0"], ["investigate", str(SAMPLES / "a.txt")]]
)
def test_a_bad_setting_is_a_one_line_usage_error_not_a_traceback(capsys, command, environ, says):
    with pytest.raises(SystemExit) as exit_:
        cli.main(command, environ)
    assert exit_.value.code == 2
    err = capsys.readouterr().err
    assert says in err
    assert "Traceback" not in err


def test_a_command_is_required_and_help_names_both(capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.main([], {})
    assert exit_.value.code == 2
    with pytest.raises(SystemExit) as exit_:
        cli.main(["-h"], {})
    assert exit_.value.code == 0
    help_text = capsys.readouterr().out
    assert "investigate" in help_text and "serve" in help_text


def test_python_dash_m_runs_the_cli():
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "SERPAPI_KEY" and not k.startswith("OFFER_CHECKPOST_")
    }
    env["PYTHONPATH"] = str(ENTRY / "src")
    done = subprocess.run(
        [sys.executable, "-m", "offer_checkpost", "investigate", "samples/offers/a.txt"]
        + ["--provider", "fake"],
        cwd=ENTRY,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "step 2   find_fraud_notice · google (fake, cache miss) · 1 search · agent" in (
        done.stdout
    )


def test_settings_are_the_environment_over_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "# Offer Checkpost settings\n"
        "\n"
        "SERPAPI_KEY=\n"
        'OFFER_CHECKPOST_PROVIDER="fake"\n'
        "export OFFER_CHECKPOST_MAX_SEARCHES = 4\n"
        "PORT=9001\n"
        "# PORT=9002\n"
        "not a setting\n",
        encoding="utf-8",
    )
    got = cli.settings({"PORT": "9100", "HOME": "/home/student"}, dotenv)
    assert got == {
        "SERPAPI_KEY": "",
        "OFFER_CHECKPOST_PROVIDER": "fake",
        "OFFER_CHECKPOST_MAX_SEARCHES": "4",
        "PORT": "9100",
        "HOME": "/home/student",
    }
    assert cli.settings({"PORT": "9100"}, tmp_path / "absent") == {"PORT": "9100"}


def test_python_dash_m_reads_dotenv_in_the_working_directory(tmp_path):
    (tmp_path / ".env").write_text("OFFER_CHECKPOST_PROVIDER=fake\n", encoding="utf-8")
    (tmp_path / "a.txt").write_bytes((SAMPLES / "a.txt").read_bytes())
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "SERPAPI_KEY" and not k.startswith("OFFER_CHECKPOST_")
    }
    env["PYTHONPATH"] = str(ENTRY / "src")
    done = subprocess.run(
        [sys.executable, "-m", "offer_checkpost", "investigate", "a.txt"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("Offer Checkpost · investigate a.txt · provider fake · as human")
