"""The three search providers. Nothing here reaches the network: the fake and replay providers
never open a socket, and the live provider runs the real ``serpapi`` client over canned
replies (``serpapi_stub``). Only ``test_live_account_and_one_search`` talks to SerpApi, and
only under ``-m live``."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest
import requests
import serpapi

from offer_checkpost import providers
from offer_checkpost.providers import (
    CACHE_TTL_SECONDS,
    FIXTURES_DIR,
    FakeSearchProvider,
    ReplaySearchProvider,
    SearchError,
    SerpApiSearchProvider,
    cache_key,
    keyless,
    params_key,
    provider_from_env,
    write_recording,
)

GOOGLE = {
    "engine": "google",
    "q": "Brand careers",
    "gl": "in",
    "hl": "en",
    "google_domain": "google.co.in",
    "location": "Noida, Uttar Pradesh, India",
    "json_restrictor": "knowledge_graph.{title,website},organic_results[].{title,link,snippet}",
}
GOOGLE_RESPONSE = {
    "_synthetic": "Synthetic, fictional: shaped like a trimmed google response.",
    "knowledge_graph": {"title": "Brand", "website": "https://www.brand.example/"},
    "organic_results": [{"title": "Careers", "link": "https://careers.brand.example/"}],
}
ACCOUNT = {
    "account_id": "synthetic",
    "account_email": "owner@mail.example",
    "plan_name": "Free Plan",
    "searches_per_month": 250,
    "plan_searches_left": 231,
    "total_searches_left": 231,
    "this_month_usage": 19,
    "this_hour_searches": 4,
    "last_hour_searches": 4,
    "account_rate_limit_per_hour": 50,
}
COUNTS = {
    "plan_searches_left": 231,
    "searches_per_month": 250,
    "this_month_usage": 19,
    "this_hour_searches": 4,
    "account_rate_limit_per_hour": 50,
}


class Clock:
    def __init__(self, now=1_790_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def live(stub, tmp_path, **kwargs):
    kwargs.setdefault("no_cache", False)
    return SerpApiSearchProvider(client=stub.client, cache_dir=tmp_path / "cache", **kwargs)


# ---- the socket guard -----------------------------------------------------------------------


@pytest.mark.blocked_connection
def test_every_test_runs_behind_the_socket_guard_without_asking_for_it():
    with pytest.raises(OSError, match="network access is blocked"):
        socket.getaddrinfo("serpapi.com", 443)
    with pytest.raises(OSError, match="network access is blocked"):
        socket.create_connection(("serpapi.com", 443))


def test_the_socket_guard_fails_a_test_that_swallows_a_refused_connection(tmp_path):
    (tmp_path / "conftest.py").write_text((Path(__file__).parent / "conftest.py").read_text())
    (tmp_path / "test_guarded.py").write_text(
        textwrap.dedent(
            """
            import socket

            import pytest


            def test_swallows_a_refused_lookup():
                try:
                    socket.getaddrinfo("serpapi.example", 443)
                except OSError:
                    pass


            @pytest.mark.blocked_connection
            def test_provokes_one_on_purpose(no_network):
                with pytest.raises(OSError):
                    socket.create_connection(("serpapi.example", 443))
                assert no_network


            def test_stays_offline(no_network):
                assert no_network == []
            """
        )
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert "ERROR at teardown of test_swallows_a_refused_lookup" in run.stdout
    assert "the test tried to reach the network: ('serpapi.example', 443)" in run.stdout
    assert "3 passed, 1 error" in run.stdout


# ---- params keys ----------------------------------------------------------------------------


def test_params_key_ignores_order_whitespace_number_types_none_and_no_cache():
    a = {"engine": "google_maps", "q": "Contoso  office", "z": 14, "type": "search"}
    b = {"type": "search", "z": "14", "q": " Contoso office ", "engine": "google_maps"}
    c = {**b, "no_cache": True, "location": None}
    assert params_key(a) == params_key(b) == params_key(c)
    assert params_key(a) != params_key({**a, "z": 15})
    assert params_key({"engine": "g", "flag": True}) == params_key({"engine": "g", "flag": "true"})


def test_cache_key_is_the_sha256_of_the_params_key():
    assert cache_key(GOOGLE) == hashlib.sha256(params_key(GOOGLE).encode()).hexdigest()
    assert cache_key(GOOGLE) != cache_key({**GOOGLE, "q": "Brand jobs"})


def test_params_carrying_an_api_key_are_refused_everywhere(tmp_path, serpapi_stub):
    with_key = {**GOOGLE, "api_key": serpapi_stub.api_key}
    for call in (
        keyless,
        params_key,
        cache_key,
        FakeSearchProvider([(GOOGLE, GOOGLE_RESPONSE)]).search,
        ReplaySearchProvider(tmp_path).search,
        live(serpapi_stub, tmp_path).search,
    ):
        with pytest.raises(ValueError, match="never carry api_key"):
            call(with_key)
    assert serpapi_stub.urls == []


# ---- fake -----------------------------------------------------------------------------------


def test_the_default_fixture_directory_is_tests_fixtures_serp():
    assert FIXTURES_DIR == Path(__file__).resolve().parent / "fixtures" / "serp"


def test_fake_serves_a_fixture_file_by_normalised_params(tmp_path, no_network):
    (tmp_path / "google").mkdir()
    (tmp_path / "google" / "brand.json").write_text(json.dumps(GOOGLE_RESPONSE))
    fake = FakeSearchProvider(
        [(GOOGLE, "google/brand.json")], fixtures_dir=tmp_path, clock=Clock()
    )
    result = fake.search({**GOOGLE, "q": "Brand   careers"})
    assert result.retrieved_at == "2026-09-21T14:13:20+00:00"
    assert result.data == GOOGLE_RESPONSE
    assert (result.provider, result.cache, result.searches_spent, result.ms) == (
        "fake",
        "miss",
        1,
        0,
    )
    assert result.engine == "google"
    assert result.params == {**GOOGLE, "q": "Brand   careers"}
    assert no_network == []


def test_fake_serves_inline_responses_as_copies(no_network):
    fake = FakeSearchProvider([(GOOGLE, GOOGLE_RESPONSE)])
    fake.search(GOOGLE).data["organic_results"].clear()
    assert fake.search(GOOGLE).data == GOOGLE_RESPONSE
    assert no_network == []


def test_fake_raises_on_a_query_it_has_no_fixture_for(no_network):
    fake = FakeSearchProvider([(GOOGLE, GOOGLE_RESPONSE)])
    with pytest.raises(SearchError) as caught:
        fake.search({**GOOGLE, "q": "Other careers"})
    assert caught.value.kind == "no_fixture"
    assert '"q":"Other careers"' in caught.value.message
    assert fake.calls == [{**GOOGLE, "q": "Other careers"}]
    assert no_network == []


def test_fake_raises_a_routed_error():
    fake = FakeSearchProvider([(GOOGLE, SearchError("rate_limit", "rate limit"))])
    with pytest.raises(SearchError, match="rate limit"):
        fake.search(GOOGLE)


def test_fake_records_calls_and_spends_its_account():
    fake = FakeSearchProvider(
        [(GOOGLE, GOOGLE_RESPONSE)], account={**providers.FAKE_ACCOUNT, "plan_searches_left": 21}
    )
    fake.search(GOOGLE)
    fake.search(GOOGLE)
    assert fake.calls == [GOOGLE, GOOGLE]
    account = fake.account()
    assert (account["plan_searches_left"], account["this_month_usage"]) == (19, 2)
    account["plan_searches_left"] = 999
    assert fake.account()["plan_searches_left"] == 19


def test_fake_reads_its_routes_file(tmp_path):
    (tmp_path / "brand.json").write_text(json.dumps(GOOGLE_RESPONSE))
    routes = [{"params": GOOGLE, "fixture": "brand.json"}]
    (tmp_path / "routes.json").write_text(json.dumps(routes))
    assert FakeSearchProvider.from_fixtures(tmp_path).search(GOOGLE).data == GOOGLE_RESPONSE


# ---- live -----------------------------------------------------------------------------------


def test_live_searches_once_then_serves_the_disk_cache(tmp_path, serpapi_stub, no_network):
    serpapi_stub.reply(GOOGLE_RESPONSE)
    provider = live(serpapi_stub, tmp_path)

    first = provider.search(GOOGLE)
    assert (first.provider, first.cache, first.searches_spent) == ("live", "miss", 1)
    assert first.data == GOOGLE_RESPONSE
    assert first.params == GOOGLE
    assert len(serpapi_stub.urls) == 1
    sent = urlsplit(serpapi_stub.urls[0])
    assert sent.path == "/search"
    query = dict(parse_qsl(sent.query))
    assert {k: query[k] for k in GOOGLE} == GOOGLE
    assert "no_cache" not in query
    assert (tmp_path / "cache" / f"{cache_key(GOOGLE)}.json").exists()

    second = provider.search(dict(reversed(GOOGLE.items())))
    assert (second.cache, second.searches_spent) == ("hit", 0)
    assert second.retrieved_at == first.retrieved_at
    assert second.data == GOOGLE_RESPONSE
    assert len(serpapi_stub.urls) == 1
    assert no_network == []


def test_the_cache_lasts_24_hours(tmp_path, serpapi_stub):
    clock = Clock()
    provider = live(serpapi_stub, tmp_path, clock=clock)
    serpapi_stub.reply(GOOGLE_RESPONSE).reply({"organic_results": []})
    stored = provider.search(GOOGLE).retrieved_at

    clock.now += CACHE_TTL_SECONDS - 1
    hit = provider.search(GOOGLE)
    assert (hit.cache, hit.retrieved_at) == ("hit", stored)
    clock.now += 1
    refreshed = provider.search(GOOGLE)
    assert (refreshed.cache, refreshed.data) == ("miss", {"organic_results": []})
    assert refreshed.retrieved_at > stored
    assert len(serpapi_stub.urls) == 2


def test_no_cache_skips_the_cache_sends_no_cache_and_refreshes_it(tmp_path, serpapi_stub):
    serpapi_stub.reply(GOOGLE_RESPONSE).reply({"organic_results": []})
    live(serpapi_stub, tmp_path).search(GOOGLE)

    fresh = live(serpapi_stub, tmp_path, no_cache=True).search(GOOGLE)
    assert (fresh.cache, fresh.searches_spent) == ("miss", 1)
    assert fresh.params == {**GOOGLE, "no_cache": "true"}
    assert "no_cache=true" in serpapi_stub.urls[1]
    assert live(serpapi_stub, tmp_path).search(GOOGLE).data == {"organic_results": []}
    assert len(serpapi_stub.urls) == 2


@pytest.mark.parametrize(
    ("value", "sent"), [("1", True), ("true", True), ("0", False), ("", False)]
)
def test_no_cache_defaults_to_the_environment(tmp_path, serpapi_stub, monkeypatch, value, sent):
    monkeypatch.setenv("OFFER_CHECKPOST_NO_CACHE", value)
    serpapi_stub.reply(GOOGLE_RESPONSE)
    provider = SerpApiSearchProvider(client=serpapi_stub.client, cache_dir=tmp_path)
    provider.search(GOOGLE)
    assert ("no_cache=true" in serpapi_stub.urls[0]) is sent


def test_the_client_is_the_official_one_with_the_env_key_and_a_90_second_timeout(monkeypatch):
    made = []

    class SpyClient:
        def __init__(self, **kwargs):
            made.append(kwargs)

    key = hashlib.sha256(b"env key").hexdigest()
    monkeypatch.setattr(serpapi, "Client", SpyClient)
    monkeypatch.setenv("SERPAPI_KEY", key)
    SerpApiSearchProvider()
    # The recorded site: searches took 62-66 s, and SerpApi charges a search the client gave
    # up on: a shorter timeout spends the search and gets nothing.
    assert made == [{"api_key": key, "timeout": 90}]


@pytest.mark.parametrize("value", [None, "", "   "])
def test_live_without_a_key_refuses_to_start(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
    else:
        monkeypatch.setenv("SERPAPI_KEY", value)
    with pytest.raises(SearchError) as caught:
        SerpApiSearchProvider()
    assert caught.value.kind == "key_missing"


@pytest.mark.parametrize(
    ("status", "body", "kind", "message"),
    [
        (401, {"error": "Invalid API key."}, "key_rejected", "key rejected"),
        (429, {"error": "Your account has run out of searches."}, "rate_limit", "rate limit"),
        (
            400,
            {"error": "Missing query `q` parameter."},
            "bad_query",
            "bad query: Missing query `q` parameter.",
        ),
        (400, "<html>Bad Request</html>", "bad_query", "bad query"),
        (503, {"error": "Service unavailable"}, "http_error", "SerpApi answered HTTP 503"),
    ],
)
def test_http_errors_map_to_fixed_messages(tmp_path, serpapi_stub, status, body, kind, message):
    serpapi_stub.reply(body, status=status)
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).search(GOOGLE)
    assert (caught.value.kind, caught.value.message, str(caught.value)) == (kind, message, message)
    assert not (tmp_path / "cache").exists()


def test_a_timeout_maps_to_a_fixed_message(tmp_path, serpapi_stub):
    serpapi_stub.fail(lambda url: requests.exceptions.ReadTimeout(f"read timed out: {url}"))
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).search(GOOGLE)
    assert (caught.value.kind, caught.value.message) == (
        "timeout",
        "SerpApi did not answer in time",
    )


@pytest.mark.blocked_connection
def test_a_connection_failure_maps_to_a_fixed_message(tmp_path, no_network):
    client = serpapi.Client(api_key=hashlib.sha256(b"k").hexdigest(), timeout=20)
    client.session.trust_env = False
    with pytest.raises(SearchError) as caught:
        SerpApiSearchProvider(client=client, cache_dir=tmp_path, no_cache=False).search(GOOGLE)
    assert (caught.value.kind, caught.value.message) == ("connection", "could not reach SerpApi")
    assert no_network, "the request should have been stopped at the socket guard"


def test_any_other_request_failure_maps_to_a_fixed_message(tmp_path, serpapi_stub):
    serpapi_stub.fail(lambda url: requests.exceptions.InvalidURL(url))
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).search(GOOGLE)
    assert caught.value.kind == "request_failed"


def test_a_response_that_is_not_json_is_an_error(tmp_path, serpapi_stub):
    serpapi_stub.reply("<html>captcha</html>", content_type="text/html")
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).search(GOOGLE)
    assert caught.value.kind == "bad_response"


def test_account_returns_only_the_five_counts(tmp_path, serpapi_stub):
    serpapi_stub.reply({**ACCOUNT, "api_key": serpapi_stub.api_key})
    assert live(serpapi_stub, tmp_path).account() == COUNTS
    assert urlsplit(serpapi_stub.urls[0]).path == "/account.json"


def test_account_errors_map_like_search_errors(tmp_path, serpapi_stub):
    serpapi_stub.reply({"error": "Invalid API key."}, status=401)
    with pytest.raises(SearchError) as caught:
        live(serpapi_stub, tmp_path).account()
    assert caught.value.kind == "key_rejected"


# ---- replay ---------------------------------------------------------------------------------


def test_replay_serves_a_scrubbed_recording_with_its_date(tmp_path, no_network):
    raw = {**GOOGLE_RESPONSE, "search_metadata": {"id": "synthetic"}}
    path = write_recording(
        "a",
        {**GOOGLE, "no_cache": "true"},
        raw,
        recorded_at="2026-10-03T10:02:11+05:30",
        recordings_dir=tmp_path,
    )
    assert path.parent == tmp_path / "a"
    assert path.name == f"google-{cache_key(GOOGLE)[:12]}.json"
    record = json.loads(path.read_text())
    assert record == {
        "recordedAt": "2026-10-03T10:02:11+05:30",
        "engine": "google",
        "params": GOOGLE,
        "response": {
            "knowledge_graph": {"title": "Brand", "website": "https://www.brand.example/"},
            "organic_results": [{"title": "Careers", "link": "https://careers.brand.example/"}],
        },
    }

    replay = ReplaySearchProvider(tmp_path)
    assert replay.recorded_dates == ("2026-10-03",)
    result = replay.search(GOOGLE)
    assert (result.provider, result.cache, result.searches_spent) == ("replay", "replay", 1)
    assert result.retrieved_at == "2026-10-03T10:02:11+05:30"
    assert result.data == record["response"]
    result.data.clear()
    assert replay.search(GOOGLE).data == record["response"]
    assert replay.account() is None
    assert no_network == []


def test_replay_never_makes_up_an_unrecorded_query(tmp_path, no_network):
    write_recording(
        "a", GOOGLE, GOOGLE_RESPONSE, recorded_at="2026-10-03", recordings_dir=tmp_path
    )
    with pytest.raises(SearchError) as caught:
        ReplaySearchProvider(tmp_path).search({**GOOGLE, "q": "Brand internships"})
    assert caught.value.kind == "not_recorded"
    assert "replay never makes one up" in caught.value.message
    assert no_network == []


def test_replay_prefers_the_later_recording_of_the_same_query(tmp_path):
    write_recording(
        "a", GOOGLE, {"organic_results": []}, recorded_at="2026-10-06", recordings_dir=tmp_path
    )
    write_recording(
        "b", GOOGLE, GOOGLE_RESPONSE, recorded_at="2026-10-03", recordings_dir=tmp_path
    )
    replay = ReplaySearchProvider(tmp_path)
    assert replay.search(GOOGLE).data == {"organic_results": []}
    assert replay.recorded_dates == ("2026-10-06",)


def test_replay_with_no_recordings_serves_nothing(tmp_path):
    replay = ReplaySearchProvider(tmp_path / "missing")
    assert replay.recorded_dates == ()
    with pytest.raises(SearchError):
        replay.search(GOOGLE)


# ---- choosing a provider --------------------------------------------------------------------


def test_without_a_key_the_default_is_replay():
    assert isinstance(provider_from_env({}), ReplaySearchProvider)
    assert isinstance(provider_from_env({"SERPAPI_KEY": ""}), ReplaySearchProvider)


def test_with_a_key_the_default_is_live_and_the_setting_wins():
    key = hashlib.sha256(b"env").hexdigest()
    assert isinstance(provider_from_env({"SERPAPI_KEY": key}), SerpApiSearchProvider)
    chosen = {"SERPAPI_KEY": key, "OFFER_CHECKPOST_PROVIDER": "replay"}
    assert isinstance(provider_from_env(chosen), ReplaySearchProvider)


def test_live_without_a_key_and_an_unknown_provider_are_refused():
    with pytest.raises(SearchError):
        provider_from_env({"OFFER_CHECKPOST_PROVIDER": "live"})
    with pytest.raises(ValueError, match="live, replay or fake"):
        provider_from_env({"OFFER_CHECKPOST_PROVIDER": "bing"})


# ---- real SerpApi, only on request ----------------------------------------------------------


@pytest.mark.live
def test_live_account_and_one_search(request, tmp_path):
    expression = request.config.getoption("markexpr")
    if "live" not in expression or "not live" in expression:
        pytest.skip("talks to SerpApi and spends a search: run with -m live")
    if not os.environ.get("SERPAPI_KEY"):
        pytest.skip("SERPAPI_KEY is not set")
    provider = SerpApiSearchProvider(cache_dir=tmp_path, no_cache=False)
    counts = provider.account()
    assert set(counts) == set(COUNTS)
    result = provider.search({"engine": "google", "q": "SerpApi", "gl": "in", "hl": "en"})
    assert (result.provider, result.cache, result.searches_spent) == ("live", "miss", 1)
    assert "organic_results" in result.data
