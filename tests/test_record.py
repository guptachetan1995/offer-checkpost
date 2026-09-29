"""``record``: runs the sample offers on the live provider once and writes every response,
scrubbed and dated, where replay finds it by the planner's own key-less params.

No test here reaches SerpApi. The live provider is ``StubLive`` (the synthetic fixtures, sent
the way SerpApi sends a response: with its copy of the params and the key, and with personal
data around the fields the app reads) or the real ``SerpApiSearchProvider`` over canned replies
(``serpapi_stub``), and the recordings go to a temporary folder."""

import copy
import dataclasses
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from offer_checkpost import cli
from offer_checkpost.providers import (
    FAKE_ACCOUNT,
    FIXTURES_DIR,
    ROUTES_FILE,
    FakeSearchProvider,
    ReplaySearchProvider,
    SerpApiSearchProvider,
    cache_key,
)
from offer_checkpost.store import Store
from test_no_secrets import key_shaped, personal_data, unmasked

ENTRY = Path(__file__).resolve().parents[1]
# The synthetic stand-ins of the demo samples, which the fixtures StubLive serves answer.
SAMPLES = ENTRY / "tests" / "fixtures" / "offers"
ROUTES = json.loads((FIXTURES_DIR / ROUTES_FILE).read_text(encoding="utf-8"))
# Built at runtime: the repository must never hold a key-shaped literal.
KEY = hashlib.sha256(b"offer-checkpost record test key").hexdigest()
PHONE = unmasked("+91 9XXXX XXXXX")
EMAIL = "a.person@" + "freemail.test"
ACCOUNT = {**FAKE_ACCOUNT, "api_key": KEY, "account_email": "owner@mail.example"}
FIVE = sorted(FAKE_ACCOUNT)
WITH_KEY = {"SERPAPI_KEY": "set-for-the-test"}


def sample(name: str) -> cli.Sample:
    path = SAMPLES / f"{name}.txt"
    return cli.Sample(name, path, path.read_text(encoding="utf-8"))


ABC = [sample(name) for name in "abc"]


def fixture(route: dict) -> dict:
    return json.loads((FIXTURES_DIR / route["fixture"]).read_text(encoding="utf-8"))


def as_sent(engine: str, data: dict) -> dict:
    """``data`` as SerpApi sends it: with its own copy of the params, key included, a link
    carrying the key, and personal data the scrub must take out. The personal data sits where
    no reader looks (an extra result, a field the whitelist drops, the end of a snippet), so
    the reading, and the trace, are the same with or without it."""
    data = copy.deepcopy(data)
    data["search_parameters"] = {"engine": engine, "api_key": KEY}
    data["search_metadata"] = {
        "json_endpoint": f"https://serpapi.example/{KEY}.json?api_key={KEY}"
    }
    for result in data.get("organic_results", []):
        if "snippet" in result:
            result["snippet"] += f" Call {PHONE} or mail {EMAIL}."
    if "knowledge_graph" in data:
        # Only beside a knowledge graph: without one, the reader counts the results.
        data.setdefault("organic_results", []).append(
            {"title": "A Person - Recruiter | LinkedIn", "link": "https://in.linkedin.com/in/a-p"}
        )
    for job in data.get("jobs_results", []):
        job["thumbnail"] = "https://img.example/logo.png"
        job.setdefault("apply_options", []).append({"link": "https://www.facebook.com/jobs/1"})
        if "description" in job:
            job["description"] += f" Mail {EMAIL} to apply."
    for place in data.get("local_results", []):
        place["phone"] = PHONE
    if "news_results" in data:
        data["news_results"].append(
            {"title": "Hiring update", "link": "https://x.com/someone/status/1"}
        )
    for review in data.get("reviews", []):
        review["user"] = {"name": "A Reviewer", "thumbnail": "https://img.example/r.png"}
    return data


class StubLive(FakeSearchProvider):
    """The fixtures served as the live provider would serve them: provider "live", each
    response as SerpApi sends it, and a stub Account API whose counts move with each search."""

    name = "live"

    def search(self, params):
        result = super().search(params)
        return dataclasses.replace(result, data=as_sent(result.engine, result.data))


def stub_live(**account) -> StubLive:
    return StubLive.from_fixtures(account={**ACCOUNT, **account})


def blocks(out: str) -> dict[str, list[str]]:
    """The record output's per-sample blocks, by sample name."""
    found = {}
    for block in out.split("\n== ")[1:]:
        lines = block.split("\n")
        found[lines[0].split(" · ")[0]] = lines
    return found


def trace(lines: list[str]) -> list[str]:
    start = lines.index("Trace") + 1
    return lines[start : lines.index("", start)]


def as_replayed(lines: list[str]) -> list[str]:
    return [line.replace("(live, cache miss)", "(replay, cache replay)") for line in lines]


def account_lines(out: str) -> list[dict[str, int]]:
    counts = []
    for line in out.split("\n"):
        if line.startswith("Account API: "):
            pairs = (
                pair.rsplit(" ", 1) for pair in line.removeprefix("Account API: ").split(", ")
            )
            counts.append({name: int(value) for name, value in pairs})
    return counts


# ---- the whole recording --------------------------------------------------------------------


def test_record_writes_scrubbed_dated_files_and_the_notice(capsys, monkeypatch, tmp_path):
    recordings = tmp_path / "recordings"
    live = stub_live(this_month_usage=10)
    monkeypatch.setattr(cli, "RECORDINGS_DIR", recordings)
    monkeypatch.setattr(cli, "provider_from_env", lambda environ: live)

    code = cli.main(["record", *(f"{s.name}={s.source}" for s in ABC)], WITH_KEY)
    out, err = capsys.readouterr()

    assert (code, err) == (0, "")
    assert out.startswith("Offer Checkpost · record a, b, c · provider live · into recordings/\n")
    # Worst case 30 (6 each, and 6 more for the remaining checks of a and of c): 10 + 30 is
    # exactly 40.
    assert (
        "Worst case: 30 searches (6 for each of 3 samples, and 6 more for the remaining checks "
        "of each of a, c)\n"
    ) in out
    before, after = account_lines(out)
    assert sorted(before) == sorted(after) == FIVE
    assert (before["this_month_usage"], after["this_month_usage"]) == (10, 24)
    assert "Spent: 14 searches\n" in out
    assert KEY not in out and "owner@" not in out and "api_key" not in out

    written = sorted(p.relative_to(recordings).as_posix() for p in recordings.rglob("*.json"))
    assert [p.split("/")[0] for p in written] == ["a"] * 6 + ["b"] * 4 + ["c"] * 4
    assert len(live.calls) == 14
    for path in recordings.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert KEY not in text and "api_key" not in text and "search_metadata" not in text
        assert key_shaped(path) == [] and personal_data(path) == [], path
        record = json.loads(text)
        assert record["recordedAt"].endswith("+05:30")
        assert path.name == f"{record['engine']}-{cache_key(record['params'])[:12]}.json"

    shown = blocks(out)
    assert shown["a"][0].endswith("a.txt · case_001")
    assert [line for line in shown["a"] if ": band " in line] == [
        "investigate: band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive",
        "run_remaining_checks: band high_risk · 6 searches spent of 6 · stopped: done",
    ]
    assert [line for line in shown["b"] if ": band " in line] == [
        "investigate: band consistent_with_genuine · 4 searches spent of 6 · stopped: done"
    ]
    assert [line.split(": ")[0] for line in shown["c"] if ": band " in line] == [
        "investigate",
        "run_remaining_checks",
    ]
    for name in "ac":
        listed = [line.split(" · ")[0].strip() for line in shown[name] if line.startswith("  rec")]
        assert sorted(listed) == [f"recordings/{p}" for p in written if p.startswith(f"{name}/")]

    notice = " ".join((recordings / "NOTICE.md").read_text(encoding="utf-8").split())
    for says in (
        "**Third-party material.** The search content is Google's results as SerpApi returned",
        "not covered by this repository's MIT licence",
        "keeps only the fields the app reads",
        "Results that tend to name people are dropped",
        "brand pages included",
        "everything on a video site",
        "every news headline without both a fraud term and a job word",
        "phone numbers and email addresses are masked",
        "`recordedAt` is when SerpApi returned the response, in IST",
        "replay the demo without a SerpApi key",
        "and for the tests",
    ):
        assert says in notice, says
    assert personal_data(recordings / "NOTICE.md") == []
    assert "Wrote recordings/NOTICE.md" in out


def test_the_notice_is_written_only_when_missing(capsys, tmp_path):
    (tmp_path / "NOTICE.md").write_text("kept as it is\n", encoding="utf-8")
    assert cli.record([sample("a")], stub_live(), environ={}, recordings_dir=tmp_path) == 0
    assert (tmp_path / "NOTICE.md").read_text(encoding="utf-8") == "kept as it is\n"
    assert "Wrote" not in capsys.readouterr().out


def test_replaying_the_recordings_reproduces_every_trace_and_band_offline(
    capsys, monkeypatch, tmp_path, no_network
):
    assert cli.record(ABC, stub_live(), environ={}, recordings_dir=tmp_path) == 0
    out = capsys.readouterr().out
    recorded = blocks(out)
    assert out.split("Replay from ")[1].split("\n")[1:4] == [
        "  a: the same trace and band (high_risk)",
        "  b: the same trace and band (consistent_with_genuine)",
        "  c: the same trace and band (high_risk)",
    ]

    # Every search the planner sent is found again by its own key-less params.
    files = {p.name: json.loads(p.read_text()) for p in tmp_path.rglob("*.json")}
    replay = ReplaySearchProvider(tmp_path)
    found = 0
    for route in ROUTES:
        name = f"{route['params']['engine']}-{cache_key(route['params'])[:12]}.json"
        if name in files:
            served = replay.search(route["params"])
            assert (served.cache, served.retrieved_at) == ("replay", files[name]["recordedAt"])
            assert served.data == files[name]["response"]
            found += 1
    assert found == len(files) == 14

    # a and b through `investigate --provider replay`, exactly as a judge without a key runs it:
    # b's whole trace, and a's up to its decisive stop, where the person's remaining checks begin.
    monkeypatch.setitem(cli.PROVIDERS, "replay", lambda: ReplaySearchProvider(tmp_path))
    for name, whole in (("a", False), ("b", True)):
        code = cli.main(["investigate", str(SAMPLES / f"{name}.txt"), "--provider", "replay"], {})
        replayed = trace(capsys.readouterr().out.split("\n"))
        assert code == 0
        ran = as_replayed(trace(recorded[name]))
        assert replayed == (ran if whole else ran[: len(replayed)])
        assert [x for x in replayed if not x.startswith(" ")][-1] == "stop     investigate · agent"

    # a and c with the person's remaining checks, through the same calls the recorder made.
    for name in "ac":
        store = Store(ReplaySearchProvider(tmp_path))
        played = cli.play(store, sample(name).text, remaining=True)
        assert played.error is None
        assert [verb for verb, _ in played.outcomes] == ["investigate", "run_remaining_checks"]
        assert cli.trace_lines(played.outcomes[-1][1]) == as_replayed(trace(recorded[name]))
        assert [f"{verb}: {cli.budget_line(o)}" for verb, o in played.outcomes] == [
            line for line in recorded[name] if ": band " in line
        ]
    assert no_network == []


def test_a_response_from_the_local_cache_is_recorded_with_the_date_it_was_fetched(
    capsys, tmp_path, serpapi_stub, no_network
):
    routes = {r["check"]: r for r in ROUTES if r["sample"] == "a"}
    lookup, notice = routes.pop("lookup_official_site"), routes.pop("find_fraud_notice")
    # The remaining checks, in the order the planner runs them after the decisive stop.
    remaining = [
        routes[check]
        for check in (
            "confirm_sender_domain",
            "check_job_listings",
            "check_office",
            "check_scam_reports",
        )
    ]
    stored_at = "2026-10-01T04:30:00+00:00"
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{cache_key(lookup['params'])}.json").write_text(
        json.dumps(
            {"storedAt": stored_at, "params": lookup["params"], "response": fixture(lookup)}
        )
    )
    account = {**ACCOUNT, "api_key": serpapi_stub.api_key, "this_month_usage": 3}
    # The counts before, the planner's quota read, the one search investigate spends, the
    # quota read again for the remaining checks, their four searches, the counts after.
    serpapi_stub.reply(account).reply(account).reply(as_sent("google", fixture(notice)))
    serpapi_stub.reply(account)
    for route in remaining:
        serpapi_stub.reply(as_sent(route["params"]["engine"], fixture(route)))
    serpapi_stub.reply({**account, "this_month_usage": 8})
    now = datetime.fromisoformat(stored_at).timestamp() + 3600
    live = SerpApiSearchProvider(
        client=serpapi_stub.client, cache_dir=cache, no_cache=False, clock=lambda: now
    )

    code = cli.record([sample("a")], live, environ={}, recordings_dir=tmp_path / "recordings")
    out, err = capsys.readouterr()

    assert (code, err) == (0, "")
    assert len(serpapi_stub.urls) == 9 and serpapi_stub._replies == []
    records = {
        cache_key(json.loads(p.read_text())["params"]): json.loads(p.read_text())
        for p in (tmp_path / "recordings" / "a").glob("*.json")
    }
    assert records[cache_key(lookup["params"])]["recordedAt"] == "2026-10-01T10:00:00+05:30"
    assert records[cache_key(notice["params"])]["recordedAt"] == "2026-10-01T11:00:00+05:30"
    assert records[cache_key(lookup["params"])]["response"]["knowledge_graph"]["title"] == "Brand"
    assert len(records) == 6
    for route in remaining:
        assert records[cache_key(route["params"])]["recordedAt"] == "2026-10-01T11:00:00+05:30"
    assert out.count("· cache hit · 2026-10-01T10:00:00+05:30") == 1
    assert out.count("· cache miss · 2026-10-01T11:00:00+05:30") == 5
    assert "Spent: 5 searches, and 1 more served from the local cache at no cost" in out
    assert "  a: the same trace and band (high_risk)" in out
    assert serpapi_stub.api_key not in out and "owner@" not in out
    assert no_network == []


def test_a_cache_hit_costs_the_recording_run_a_search_so_it_stops_where_replay_does(
    capsys, tmp_path, serpapi_stub, no_network
):
    """A cache hit costs SerpApi nothing, but replay charges every response 1 search. Charged
    0, the recording run would reach a check replay never reaches once the budget binds: here
    Brand's office is found, so its reviews are scanned for "fee", and the 4 searches saved run
    out before the news search."""
    routes = {r["check"]: r for r in ROUTES if r["sample"] == "a"}
    lookup = routes["lookup_official_site"]
    place_id = "ChIJsyntheticBrandNoida01"
    office = {
        "local_results": [
            {
                "title": "Brand Noida Office",
                "place_id": place_id,
                "type": "Corporate office",
                "address": "Sector 62, Noida, Uttar Pradesh 201309",
                "rating": 4.1,
                "reviews": 120,
            }
        ]
    }
    reviews = fixture({"fixture": "google_maps_reviews/reviews_feedback_only.json"})
    stored_at = "2026-10-01T04:30:00+00:00"
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{cache_key(lookup['params'])}.json").write_text(
        json.dumps(
            {"storedAt": stored_at, "params": lookup["params"], "response": fixture(lookup)}
        )
    )
    account = {**ACCOUNT, "api_key": serpapi_stub.api_key, "this_month_usage": 3}
    # The counts before, the planner's quota read, the one search investigate spends, the
    # quota read again for the remaining checks, the four searches they can afford, the counts
    # after. A fifth, the news search, would find no reply.
    serpapi_stub.reply(account).reply(account)
    serpapi_stub.reply(as_sent("google", fixture(routes["find_fraud_notice"])))
    serpapi_stub.reply(account)
    for engine, data in (
        ("google", fixture(routes["confirm_sender_domain"])),
        ("google_jobs", fixture(routes["check_job_listings"])),
        ("google_maps", office),
        ("google_maps_reviews", reviews),
    ):
        serpapi_stub.reply(as_sent(engine, data))
    serpapi_stub.reply({**account, "this_month_usage": 8})
    now = datetime.fromisoformat(stored_at).timestamp() + 3600
    live = SerpApiSearchProvider(
        client=serpapi_stub.client, cache_dir=cache, no_cache=False, clock=lambda: now
    )

    code = cli.record([sample("a")], live, environ={}, recordings_dir=tmp_path / "recordings")
    out, err = capsys.readouterr()

    assert (code, err) == (0, "")
    assert len(serpapi_stub.urls) == 9 and serpapi_stub._replies == []
    records = [json.loads(p.read_text()) for p in (tmp_path / "recordings" / "a").glob("*.json")]
    assert sorted(r["engine"] for r in records) == [
        "google",
        "google",
        "google",
        "google_jobs",
        "google_maps",
        "google_maps_reviews",
    ]
    [scanned] = [r for r in records if r["engine"] == "google_maps_reviews"]
    assert (scanned["params"]["place_id"], scanned["params"]["query"]) == (place_id, "fee")
    shown = blocks(out)["a"]
    assert next(line for line in shown if line.startswith("step 1 ")) == (
        "step 1   lookup_official_site · google (live, cache hit) · 1 search · agent"
    )
    assert [line for line in shown if ": band " in line] == [
        "investigate: band high_risk · 2 searches spent of 6 · 4 saved · stopped: decisive",
        "run_remaining_checks: band high_risk · 6 searches spent of 6 · stopped: budget",
    ]
    assert "Spent: 5 searches, and 1 more served from the local cache at no cost" in out
    assert "  a: the same trace and band (high_risk)" in out
    assert no_network == []


# ---- refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "says"),
    [
        ({}, "record searches SerpApi live and needs SERPAPI_KEY, in the environment or .env"),
        ({"SERPAPI_KEY": "   "}, "needs SERPAPI_KEY"),
        ({**WITH_KEY, "OFFER_CHECKPOST_PROVIDER": "fake"}, "OFFER_CHECKPOST_PROVIDER is 'fake'"),
        ({**WITH_KEY, "OFFER_CHECKPOST_PROVIDER": "replay"}, "is 'replay': unset it, or set"),
    ],
)
def test_record_refuses_without_the_live_provider(capsys, monkeypatch, tmp_path, environ, says):
    made = []
    monkeypatch.setattr(cli, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(cli, "provider_from_env", lambda environ: made.append(environ))

    code = cli.main(["record", f"a={SAMPLES / 'a.txt'}"], environ)
    out, err = capsys.readouterr()

    assert code == 1
    assert (out, err.count("\n")) == ("", 1)
    assert err.startswith("offer_checkpost: record refused: ") and says in err
    assert err.endswith("; nothing was searched\n")
    assert made == [] and not (tmp_path / "recordings").exists()


@pytest.mark.parametrize(
    ("account", "max_total", "says"),
    [
        (
            {"this_month_usage": 11},
            40,
            "this month's usage is 11, and the worst case of 30 more would bring it to 41, past "
            "--max-total 40",
        ),
        ({"this_month_usage": 1}, 30, "would bring it to 31, past --max-total 30"),
        (
            {"plan_searches_left": 49},
            100,
            "49 searches are left this month, and the worst case of 30 would leave 19, under the "
            "quota reserve of 20",
        ),
        ({"this_hour_searches": 21}, 40, "would pass the limit of 50 an hour"),
    ],
)
def test_record_refuses_before_any_search_when_the_worst_case_is_too_much(
    capsys, tmp_path, account, max_total, says
):
    live = stub_live(**account)
    code = cli.record(ABC, live, environ={}, max_total=max_total, recordings_dir=tmp_path)
    out, err = capsys.readouterr()

    assert code == 1
    assert err.startswith("offer_checkpost: record refused: ") and says in err
    assert err.endswith("; nothing was searched\n") and err.count("\n") == 1
    assert live.calls == [] and list(tmp_path.iterdir()) == []
    [counts] = account_lines(out)
    assert sorted(counts) == FIVE
    assert KEY not in out and "owner@" not in out


def test_the_worst_case_counts_a_and_c_twice_only_when_they_are_recorded(capsys, tmp_path):
    live = stub_live(this_month_usage=22)
    assert cli.record([sample("a"), sample("b")], live, environ={}, recordings_dir=tmp_path) == 0
    assert (
        "Worst case: 18 searches (6 for each of 2 samples, and 6 more for the remaining checks "
        "of each of a)\n"
    ) in capsys.readouterr().out

    live = stub_live(this_month_usage=34)
    assert cli.record([sample("b")], live, environ={}, recordings_dir=tmp_path / "b") == 0
    assert "Worst case: 6 searches (6 for each of 1 sample)\n" in capsys.readouterr().out


def test_record_refuses_when_the_account_api_fails(capsys, tmp_path, serpapi_stub):
    serpapi_stub.reply({"error": "Invalid API key."}, status=401)
    live = SerpApiSearchProvider(client=serpapi_stub.client, cache_dir=tmp_path / "cache")
    assert cli.record(ABC, live, environ={}, recordings_dir=tmp_path / "recordings") == 1
    assert capsys.readouterr().err == (
        "offer_checkpost: record refused: the Account API failed: key rejected; nothing was "
        "searched\n"
    )
    assert len(serpapi_stub.urls) == 1 and not (tmp_path / "recordings").exists()


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        (["record"], "the following arguments are required"),
        (["record", "a.txt"], "is not SAMPLE=FILE"),
        (["record", "A=a.txt"], "'A' is not a sample name"),
        (["record", "../a=a.txt"], "is not a sample name"),
        (["record", f"a={SAMPLES / 'a.txt'}", f"a={SAMPLES / 'b.txt'}"], "a is named twice"),
        (["record", "a=missing.txt"], "cannot read missing.txt"),
        (["record", f"a={SAMPLES / 'a.txt'}", "--max-total", "0"], "'0' is not a whole number"),
    ],
)
def test_bad_record_arguments_are_usage_errors(capsys, monkeypatch, argv, says):
    monkeypatch.setattr(cli, "provider_from_env", lambda environ: pytest.fail("no provider"))
    with pytest.raises(SystemExit) as exit_:
        cli.main(argv, WITH_KEY)
    assert exit_.value.code == 2
    assert says in capsys.readouterr().err


def test_only_a_live_provider_is_recorded(tmp_path):
    for provider in (FakeSearchProvider(), ReplaySearchProvider(tmp_path)):
        with pytest.raises(ValueError, match="only live SerpApi responses are recorded"):
            cli.RecordingProvider(provider, tmp_path)


# ---- a recording that can't be replayed as it ran -------------------------------------------


def test_a_replay_that_differs_from_the_recording_run_is_reported(capsys, tmp_path):
    """A LinkedIn profile naming the firm is a web footprint to the live reader, and the scrub
    drops it from the recording, so the replay finds none: the recorder must say so."""
    lookup = next(r for r in ROUTES if r["sample"] == "c" and r["check"] == "lookup_official_site")
    profile = {"title": "A Person - Zorvanta Support Services | LinkedIn"}
    profile["link"] = "https://in.linkedin.com/in/a-person"
    response = fixture(lookup)
    response["organic_results"].append(profile)
    routes = [(r["params"], r["fixture"]) for r in ROUTES] + [(lookup["params"], response)]
    live = StubLive(routes, account=ACCOUNT)

    code = cli.record([sample("c")], live, environ={}, recordings_dir=tmp_path)
    out = capsys.readouterr().out

    assert code == 1
    [verdict] = [line for line in out.split("\n") if line.startswith("  c: ")]
    assert verdict.startswith("  c: DIFFERS: ")
    assert "linkedin" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))
