"""The command line: ``investigate`` prints each sample's trace, band, draft verdict and activity
log through the same ``invoke`` the app uses; ``--as agent`` makes every call the agent's; replay
serves only what was recorded; ``serve`` waits for the web UI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from offer_checkpost import cli
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


def test_serve_waits_for_the_web_ui(capsys):
    assert cli.main(["serve"], {}) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "offer_checkpost: serve is not built yet: the web UI arrives in the next slice\n"


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
